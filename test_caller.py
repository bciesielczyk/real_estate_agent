import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import caller
import calls
import db

QUESTIONS = ["Czy oferta jest aktualna?", "Czy cena jest do negocjacji?", "Kiedy można obejrzeć dom?"]
ELEVEN_ENV = {"ELEVENLABS_API_KEY": "key", "ELEVENLABS_AGENT_ID": "agent", "ELEVENLABS_PHONE_NUMBER_ID": "phnum"}


def tool_response(name, data):
    return SimpleNamespace(
        stop_reason="tool_use", content=[SimpleNamespace(type="tool_use", name=name, input=data)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                              cache_read_input_tokens=0, cache_creation_input_tokens=0))


class FakeClient:
    """messages.create answers by tool name from a queue; an Exception in the queue is raised."""

    def __init__(self, conversations=(), reports=()):
        self.queues = {"submit_conversation": list(conversations), "submit_report": list(reports)}
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        item = self.queues[kwargs["tools"][0]["name"]].pop(0)
        if isinstance(item, Exception):
            raise item
        return tool_response(kwargs["tools"][0]["name"], item)


CONVERSATION = {"transcript": [
    {"role": "caller", "text": "Dzień dobry, jestem asystentem AI, rozmowa jest nagrywana."},
    {"role": "broker", "text": "Dzień dobry, oferta aktualna."},
]}


def report(*answers, summary="Podsumowanie."):
    return {"summary": summary, "answers": [{"question": q, "answer": a} for q, a in answers]}


class CallerTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = os.path.join(directory.name, "test.db")
        db.save_listings("fake", [
            {"id": str(n), "title": f"Dom {n}", "price_pln": 900000, "area_m2": 120, "location": "Szczecin",
             "description": f"Opis domu {n}", "phone": "+48500000000"} for n in (1, 2)], path=self.path)
        for n in (1, 2):
            calls.mark_to_call("fake", str(n), QUESTIONS, path=self.path)
        patcher = mock.patch.dict(os.environ, {"CALL_MODE": "simulate"})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in caller.ELEVENLABS_VARS:
            os.environ.pop(name, None)

    def get(self, n):
        return next(c for c in calls.list_calls(path=self.path) if c["id"] == str(n))


class SimulateTests(CallerTestCase):
    def test_done_with_aligned_answers(self):
        good = report(*zip(QUESTIONS, ["Tak", "Nie ustalono", "W sobotę"]))
        client = FakeClient([CONVERSATION] * 2, [good] * 2)
        results = caller.run_pending(path=self.path, client=client)
        self.assertEqual([r["status"] for r in results], ["done", "done"])
        self.assertGreater(results[0]["usage"]["input_tokens"], 0)
        row = self.get(1)
        self.assertEqual(row["status"], "done")
        self.assertIsNone(row["error"])
        self.assertEqual(row["transcript"][0]["role"], "caller")
        self.assertEqual(row["report"]["summary"], "Podsumowanie.")
        self.assertEqual([a["question"] for a in row["report"]["answers"]], QUESTIONS)
        self.assertEqual(row["report"]["answers"][1]["answer"], "Nie ustalono")
        # the simulation prompt contains the listing data, incl. the description from the table
        self.assertIn("Opis domu 1", client.requests[0]["messages"][0]["content"])
        self.assertEqual(calls.list_calls("to_call", path=self.path), [])

    def test_status_is_calling_during_conversation(self):
        seen = []
        client = FakeClient([CONVERSATION], [report(*zip(QUESTIONS, "abc"))])
        original = client.create

        def spy(**kwargs):
            seen.append(self.get(1)["status"])
            return original(**kwargs)
        client.messages = SimpleNamespace(create=spy)
        calls.unmark("fake", "2", path=self.path)
        caller.run_pending(path=self.path, client=client)
        self.assertEqual(seen[0], "calling")

    def test_missing_and_extra_answers(self):
        missing = report((QUESTIONS[0], "Tak"), (QUESTIONS[2], "Jutro"))  # middle one omitted
        extra = report(*zip(QUESTIONS, "abc"), ("Dodatkowe pytanie?", "x"))
        client = FakeClient([CONVERSATION] * 2, [missing, extra])
        caller.run_pending(path=self.path, client=client)
        answers = self.get(1)["report"]["answers"]
        self.assertEqual([a["question"] for a in answers], QUESTIONS)
        self.assertEqual([a["answer"] for a in answers], ["Tak", "Nie ustalono", "Jutro"])
        answers = self.get(2)["report"]["answers"]
        self.assertEqual([a["answer"] for a in answers], ["a", "b", "c"])

    def test_empty_answer_becomes_not_established(self):
        client = FakeClient([CONVERSATION] * 2, [report(*zip(QUESTIONS, ["Tak", " ", "Jutro"]))] * 2)
        caller.run_pending(path=self.path, client=client)
        self.assertEqual(self.get(1)["report"]["answers"][1]["answer"], "Nie ustalono")

    def test_failure_does_not_stop_next_call(self):
        client = FakeClient([RuntimeError("boom"), CONVERSATION], [report(*zip(QUESTIONS, "abc"))])
        results = caller.run_pending(path=self.path, client=client)
        self.assertEqual([r["status"] for r in results], ["failed", "done"])
        self.assertEqual(self.get(1)["status"], "failed")
        self.assertIn("Nieoczekiwany błąd", self.get(1)["error"])
        self.assertEqual(self.get(2)["status"], "done")

    def test_invalid_model_output_fails_with_polish_error(self):
        client = FakeClient([{"transcript": [{"role": "robot", "text": "x"}]}, CONVERSATION],
                            [report(*zip(QUESTIONS, "abc"))])
        caller.run_pending(path=self.path, client=client)
        self.assertEqual(self.get(1)["status"], "failed")
        self.assertIn("nieprawidłowym formacie", self.get(1)["error"])
        self.assertEqual(self.get(2)["status"], "done")

    def test_simulate_never_uses_http(self):
        client = FakeClient([CONVERSATION] * 2, [report(*zip(QUESTIONS, "abc"))] * 2)
        with mock.patch.object(caller, "http_json", side_effect=AssertionError("network")):
            caller.run_pending(path=self.path, client=client)


class RealModeTests(CallerTestCase):
    def setUp(self):
        super().setUp()
        calls.unmark("fake", "2", path=self.path)
        os.environ["CALL_MODE"] = "real"

    def test_missing_variables(self):
        os.environ["ELEVENLABS_API_KEY"] = "key"
        with mock.patch.object(caller, "http_json", side_effect=AssertionError("no request expected")):
            results = caller.run_pending(path=self.path, client=FakeClient())
        self.assertEqual(results[0]["status"], "failed")
        self.assertIn("ELEVENLABS_AGENT_ID", self.get(1)["error"])

    def test_call_polls_until_done_and_maps_roles(self):
        os.environ.update(ELEVEN_ENV)
        responses = [
            {"conversation_id": "conv_1"},
            {"status": "in-progress", "transcript": []},
            {"status": "done", "transcript": [
                {"role": "agent", "message": "Dzień dobry, asystent AI."},
                {"role": "user", "message": "Halo?"},
                {"role": "user", "message": "  "},
                {"role": "agent", "message": None},
            ]},
        ]
        http = mock.Mock(side_effect=responses)
        stored = []
        original = calls.update_call

        def spy(*args, **kwargs):
            if "conversation_id" in kwargs:
                stored.append(len(http.call_args_list))
            return original(*args, **kwargs)

        client = FakeClient(reports=[report(*zip(QUESTIONS, "abc"))])
        with mock.patch.object(caller, "http_json", http), mock.patch.object(caller.time, "sleep") as sleep, \
                mock.patch.object(calls, "update_call", spy):
            results = caller.run_pending(path=self.path, client=client)
        self.assertEqual(results[0]["status"], "done", results[0]["error"])
        method, url, headers, body = http.call_args_list[0].args
        self.assertEqual((method, url), ("POST", "https://api.elevenlabs.io/v1/convai/twilio/outbound-call"))
        self.assertEqual(headers, {"xi-api-key": "key"})
        self.assertEqual(body["agent_id"], "agent")
        self.assertEqual(body["agent_phone_number_id"], "phnum")
        self.assertEqual(body["to_number"], "+48500000000")
        variables = body["conversation_initiation_client_data"]["dynamic_variables"]
        self.assertIn("Dom 1", variables["listing"])
        self.assertIn(QUESTIONS[1], variables["questions"])
        self.assertEqual(http.call_args_list[1].args[:2],
                         ("GET", "https://api.elevenlabs.io/v1/convai/conversations/conv_1"))
        self.assertEqual(http.call_count, 3)
        sleep.assert_called_once_with(caller.POLL_INTERVAL)
        self.assertEqual(stored, [1])  # saved right after the first request, before polling
        row = self.get(1)
        self.assertEqual(row["conversation_id"], "conv_1")
        self.assertEqual(row["transcript"], [{"role": "caller", "text": "Dzień dobry, asystent AI."},
                                             {"role": "broker", "text": "Halo?"}])

    def test_timeout_becomes_failed(self):
        os.environ.update(ELEVEN_ENV)
        http = mock.Mock(side_effect=[{"conversation_id": "c"}] + [{"status": "in-progress"}] * 50)
        clock = iter(range(0, 100000, 400))
        with mock.patch.object(caller, "http_json", http), mock.patch.object(caller.time, "sleep"), \
                mock.patch.object(caller.time, "monotonic", lambda: next(clock)):
            results = caller.run_pending(path=self.path, client=FakeClient())
        self.assertEqual(results[0]["status"], "failed")
        self.assertIn("limicie czasu", self.get(1)["error"])
        self.assertEqual(self.get(1)["conversation_id"], "c")

    def test_failed_conversation_status(self):
        os.environ.update(ELEVEN_ENV)
        http = mock.Mock(side_effect=[{"conversation_id": "c"}, {"status": "failed"}])
        with mock.patch.object(caller, "http_json", http):
            caller.run_pending(path=self.path, client=FakeClient())
        self.assertEqual(self.get(1)["status"], "failed")
        self.assertIn("nie powiodło", self.get(1)["error"])

    def test_only_exact_real_places_calls(self):
        os.environ["CALL_MODE"] = "REAL"
        os.environ.update(ELEVEN_ENV)
        client = FakeClient([CONVERSATION], [report(*zip(QUESTIONS, "abc"))])
        with mock.patch.object(caller, "http_json", side_effect=AssertionError("network")):
            results = caller.run_pending(path=self.path, client=client)
        self.assertEqual(results[0]["status"], "done")


if __name__ == "__main__":
    unittest.main()
