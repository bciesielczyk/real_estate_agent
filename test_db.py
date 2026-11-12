import contextlib
import io
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import calls
import db


def listing(listing_id, **fields):
    record = {
        "id": listing_id,
        "url": f"https://example.test/oferta/{listing_id}",
        "title": f"Dom {listing_id}",
        "price_pln": 1000000,
        "area_m2": 120.5,
        "rooms": 4,
        "location": "Testowo, Centrum",
        "phone": "+48500000000",
        "agent": "Jan Testowy",
    }
    record.update(fields)
    return record


class TempDbTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "test_listings.db"

    def tearDown(self):
        self._tmp.cleanup()

    def save(self, source, records):
        # save_listings prints a summary line; keep the test output clean.
        with contextlib.redirect_stdout(io.StringIO()):
            db.save_listings(source, records, path=self.path)

    def rows(self, sql, args=()):
        with closing(db.connect(self.path)) as connection:
            return [dict(row) for row in connection.execute(sql, args).fetchall()]

    def listing_row(self, source, listing_id):
        found = self.rows("SELECT * FROM listings WHERE source = ? AND id = ?", (source, listing_id))
        self.assertEqual(len(found), 1)
        return found[0]


class SaveListingsTest(TempDbTest):
    def test_first_save_inserts_active_listings(self):
        self.save("gratka", [listing("1"), listing("2")])
        for listing_id in ("1", "2"):
            row = self.listing_row("gratka", listing_id)
            self.assertEqual(row["active"], 1)
            self.assertEqual(row["first_seen"], row["last_seen"])

    def test_missing_record_becomes_inactive_but_is_kept(self):
        self.save("gratka", [listing("1"), listing("2")])
        self.save("gratka", [listing("1")])
        self.assertEqual(self.listing_row("gratka", "1")["active"], 1)
        gone = self.listing_row("gratka", "2")  # still present, not deleted
        self.assertEqual(gone["active"], 0)
        self.assertEqual(gone["title"], "Dom 2")

    def test_returning_listing_becomes_active_again(self):
        self.save("gratka", [listing("1"), listing("2")])
        self.save("gratka", [listing("1")])
        self.assertEqual(self.listing_row("gratka", "2")["active"], 0)
        self.save("gratka", [listing("1"), listing("2")])
        self.assertEqual(self.listing_row("gratka", "2")["active"], 1)

    def test_empty_save_raises_and_changes_nothing(self):
        self.save("gratka", [listing("1")])
        before = self.rows("SELECT * FROM listings")
        with self.assertRaises(ValueError):
            self.save("gratka", [])
        self.assertEqual(self.rows("SELECT * FROM listings"), before)
        self.assertEqual(self.listing_row("gratka", "1")["active"], 1)

    def test_missing_phone_and_agent_keep_earlier_values(self):
        self.save("gratka", [listing("1", phone="+48500000000", agent="Jan Testowy")])
        self.save("gratka", [listing("1", phone=None, agent=None)])
        row = self.listing_row("gratka", "1")
        self.assertEqual(row["phone"], "+48500000000")
        self.assertEqual(row["agent"], "Jan Testowy")

    def test_new_phone_replaces_old_one(self):
        self.save("gratka", [listing("1", phone="+48500000000")])
        self.save("gratka", [listing("1", phone="+48511111111")])
        self.assertEqual(self.listing_row("gratka", "1")["phone"], "+48511111111")

    def test_later_save_updates_other_fields(self):
        self.save("gratka", [listing("1", price_pln=1000000, title="Dom stary")])
        self.save("gratka", [listing("1", price_pln=950000, title="Dom nowy")])
        row = self.listing_row("gratka", "1")
        self.assertEqual(row["price_pln"], 950000)
        self.assertEqual(row["title"], "Dom nowy")

    def test_sources_do_not_affect_each_other(self):
        self.save("a", [listing("1"), listing("2")])
        self.save("b", [listing("1")])
        self.save("a", [listing("2")])  # "1" of source a is gone
        self.assertEqual(self.listing_row("a", "1")["active"], 0)
        self.assertEqual(self.listing_row("b", "1")["active"], 1)
        self.assertEqual(self.listing_row("a", "2")["active"], 1)

    def test_description_falls_back_to_preview(self):
        self.save("gratka", [
            listing("1", description="Pełny opis", description_preview="Krótki"),
            listing("2", description_preview="Tylko podgląd"),
        ])
        self.assertEqual(self.listing_row("gratka", "1")["description"], "Pełny opis")
        self.assertEqual(self.listing_row("gratka", "2")["description"], "Tylko podgląd")

    def test_data_column_holds_full_record_as_json(self):
        record = listing("1", plot_m2=716, image_url="https://example.test/1.jpg")
        self.save("gratka", [record])
        stored = json.loads(self.listing_row("gratka", "1")["data"])
        self.assertEqual(stored, record)
        self.assertEqual(stored["plot_m2"], 716)


CALL_SOURCE = "gratka"


class CallsTest(TempDbTest):
    def setUp(self):
        super().setUp()
        self.save(CALL_SOURCE, [listing("1"), listing("2")])

    def call_row(self, listing_id):
        # calls.connect also creates the calls table, which db.connect does not.
        with closing(calls.connect(self.path)) as connection:
            found = connection.execute(
                "SELECT * FROM calls WHERE source = ? AND id = ?", (CALL_SOURCE, listing_id)).fetchall()
        return dict(found[0]) if found else None

    def test_mark_to_call_uses_default_questions(self):
        calls.mark_to_call(CALL_SOURCE, "1", path=self.path)
        row = self.call_row("1")
        self.assertEqual(row["status"], "to_call")
        self.assertEqual(json.loads(row["questions"]), calls.DEFAULT_QUESTIONS)

    def test_custom_questions_are_stripped_and_blanks_dropped(self):
        calls.mark_to_call(CALL_SOURCE, "1", questions=["  Czy cena?  ", "", "   ", "Kiedy?"], path=self.path)
        self.assertEqual(json.loads(self.call_row("1")["questions"]), ["Czy cena?", "Kiedy?"])

    def test_only_blank_questions_raise(self):
        with self.assertRaises(ValueError):
            calls.mark_to_call(CALL_SOURCE, "1", questions=["", "   "], path=self.path)
        self.assertIsNone(self.call_row("1"))

    def test_unknown_listing_raises_key_error(self):
        with self.assertRaises(KeyError):
            calls.mark_to_call(CALL_SOURCE, "999", path=self.path)
        self.assertIsNone(self.call_row("999"))

    def test_update_call_stores_and_lists_python_objects(self):
        calls.mark_to_call(CALL_SOURCE, "1", path=self.path)
        transcript = [{"role": "caller", "text": "Dzień dobry"}, {"role": "broker", "text": "Witam"}]
        report = {"summary": "Oferta aktualna.", "answers": [{"question": "Czy cena?", "answer": "Tak"}]}
        calls.update_call(CALL_SOURCE, "1", path=self.path,
                          status="done", transcript=transcript, report=report)
        (call,) = calls.list_calls(path=self.path)
        self.assertEqual(call["status"], "done")
        self.assertEqual(call["transcript"], transcript)
        self.assertEqual(call["report"], report)
        self.assertIsInstance(call["transcript"], list)
        self.assertIsInstance(call["report"], dict)

    def test_update_call_rejects_unknown_field(self):
        calls.mark_to_call(CALL_SOURCE, "1", path=self.path)
        with self.assertRaises(ValueError):
            calls.update_call(CALL_SOURCE, "1", path=self.path, colour="red")

    def test_mark_to_call_again_resets_previous_result(self):
        calls.mark_to_call(CALL_SOURCE, "1", path=self.path)
        calls.update_call(CALL_SOURCE, "1", path=self.path, status="failed",
                          transcript=[{"role": "caller", "text": "x"}],
                          report={"summary": "y", "answers": []},
                          error="timeout")
        calls.mark_to_call(CALL_SOURCE, "1", path=self.path)
        row = self.call_row("1")
        self.assertEqual(row["status"], "to_call")
        self.assertIsNone(row["transcript"])
        self.assertIsNone(row["report"])
        self.assertIsNone(row["error"])

    def test_list_calls_filters_by_status_and_includes_listing_fields(self):
        calls.mark_to_call(CALL_SOURCE, "1", path=self.path)
        calls.mark_to_call(CALL_SOURCE, "2", path=self.path)
        calls.update_call(CALL_SOURCE, "2", path=self.path, status="done")

        pending = calls.list_calls("to_call", path=self.path)
        self.assertEqual([c["id"] for c in pending], ["1"])
        done = calls.list_calls("done", path=self.path)
        self.assertEqual([c["id"] for c in done], ["2"])

        (call,) = calls.list_calls("to_call", path=self.path)
        self.assertEqual(call["title"], "Dom 1")
        self.assertEqual(call["url"], "https://example.test/oferta/1")
        self.assertEqual(call["price_pln"], 1000000)
        self.assertEqual(call["location"], "Testowo, Centrum")
        self.assertEqual(call["agent"], "Jan Testowy")
        self.assertEqual(call["phone"], "+48500000000")

    def test_unmark_removes_call_but_keeps_listing(self):
        calls.mark_to_call(CALL_SOURCE, "1", path=self.path)
        calls.unmark(CALL_SOURCE, "1", path=self.path)
        self.assertIsNone(self.call_row("1"))
        self.assertEqual(calls.list_calls(path=self.path), [])
        self.assertEqual(self.listing_row(CALL_SOURCE, "1")["title"], "Dom 1")


if __name__ == "__main__":
    unittest.main()
