"""Offline tests for connectors/otodom_apify.py. The HTTP layer (urlopen) is mocked; all data is synthetic."""
import io
import json
import os
import unittest
from unittest import mock
from urllib.error import HTTPError

from connectors import otodom_apify as o

# Shaped like the real (flattened) actor output; every name, number and URL is fake.
ITEM = {
    "dataSource": "detail", "listingId": 11111111,
    "canonicalUrl": "https://www.otodom.pl/pl/oferta/dom-testowy-IDtest1",
    "title": "Dom testowy", "advertiserType": "agency", "isPrivateOwner": False,
    "rooms": 5, "areaSqm": 180, "plotAreaSqm": 578,
    "priceExact": 1500000, "priceCurrency": "PLN", "hidePrice": False,
    "description": "Opis testowy domu.",
    "address_displayAddress": "ul. Testowa, Pogodno, Zachód, Szczecin, zachodniopomorskie",
    "address_street": "ul. Testowa", "address_district": "Zachód", "address_city": "Szczecin",
    "latitude": 53.4, "longitude": 14.5,
    "photos": ["https://example.invalid/1.jpg", "https://example.invalid/2.jpg"],
    "contactName": "Jan Testowy", "contactPhones": ["+48500000000"],
    "agency_name": "Testowe Nieruchomości", "agency_phones": ["+48 91 000 00 00"],
    "dateCreated": "2026-01-01T10:00:00Z", "dateModified": "2026-01-02T10:00:00Z",
}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def response(payload):
    return FakeResponse(json.dumps(payload).encode())


def run_object(status="RUNNING", **extra):
    return {"data": {"id": "RUN1", "status": status, "defaultDatasetId": "DS1",
                     "stats": {"runTimeSecs": 21.5}, "usageTotalUsd": 0.059, **extra}}


class Api:
    """Replacement for urlopen: answers by (method, path) and records the requests."""

    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def __call__(self, request, timeout=None):
        path = request.full_url.split("/v2", 1)[1].split("?")[0]
        self.requests.append(request)
        answer = self.routes[(request.get_method(), path)]
        if isinstance(answer, tuple):  # successive answers
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, Exception):
            raise answer
        return response(answer)


def routes(items, final="SUCCEEDED"):
    return {("POST", "/acts/memo23~otodom-scraper/runs"): run_object("RUNNING"),
            ("GET", "/actor-runs/RUN1"): (run_object(final),),
            ("GET", "/datasets/DS1/items"): items,
            ("POST", "/actor-runs/RUN1/abort"): run_object("ABORTING")}


@mock.patch.dict(os.environ, {"APIFY_TOKEN": "fake-token"})
@mock.patch.object(o.time, "sleep", lambda seconds: None)
class RunTest(unittest.TestCase):
    def call(self, answers, **patches):
        fake = Api(answers)
        with mock.patch.object(o, "urlopen", fake):
            return fake, o.run(**patches)

    def test_request_shape_and_actor_input(self):
        fake, (items, info) = self.call(routes([ITEM]), max_items=7)
        start = fake.requests[0]
        self.assertEqual(start.get_method(), "POST")
        self.assertTrue(start.full_url.startswith("https://api.apify.com/v2/acts/memo23~otodom-scraper/runs?"))
        self.assertIn("maxItems=7", start.full_url)
        self.assertIn("maxTotalChargeUsd=0.25", start.full_url)
        self.assertEqual(start.get_header("Authorization"), "Bearer fake-token")
        self.assertNotIn("token=", start.full_url)
        sent = json.loads(start.data)
        self.assertEqual(sent["startUrls"], [o.SEARCH_URL])
        self.assertEqual(sent["maxItems"], 7)
        self.assertTrue(sent["fetchDetails"])
        self.assertIn("/dom/zachodniopomorskie/szczecin/", sent["startUrls"][0])
        self.assertEqual(items, [ITEM])
        self.assertEqual((info["status"], info["cost_usd"], info["seconds"]), ("SUCCEEDED", 0.059, 21.5))

    def test_failed_run_is_an_error(self):
        with self.assertRaisesRegex(RuntimeError, "FAILED"):
            self.call(routes([], final="FAILED"))

    def test_timeout_aborts_the_run(self):
        answers = routes([])
        answers[("GET", "/actor-runs/RUN1")] = (run_object("RUNNING"),)
        fake = Api(answers)
        clock = iter(range(0, 100000, 400))
        with mock.patch.object(o, "urlopen", fake), mock.patch.object(o.time, "time", lambda: next(clock)):
            with self.assertRaises(TimeoutError):
                o.run()
        self.assertEqual(fake.requests[-1].full_url.split("/v2")[1], "/actor-runs/RUN1/abort")

    def test_http_error_message_has_no_token(self):
        error = HTTPError("https://api.apify.com/v2/x", 402, "Payment Required", {}, io.BytesIO(b'{"error":"credit"}'))
        answers = routes([])
        answers[("POST", "/acts/memo23~otodom-scraper/runs")] = error
        with self.assertRaises(RuntimeError) as caught:
            self.call(answers)
        self.assertIn("402", str(caught.exception))
        self.assertNotIn("fake-token", str(caught.exception))

    def test_missing_token_fails_before_any_request(self):
        fake = Api({})
        with mock.patch.dict(os.environ, clear=True), mock.patch.object(o, "urlopen", fake):
            with self.assertRaisesRegex(RuntimeError, "APIFY_TOKEN"):
                o.scrape()
        self.assertEqual(fake.requests, [])


class ScrapeTest(unittest.TestCase):
    def scrape(self, items, max_items):
        with mock.patch.dict(os.environ, {"APIFY_TOKEN": "fake-token"}), \
                mock.patch.object(o.time, "sleep", lambda seconds: None), \
                mock.patch.object(o, "MAX_ITEMS", max_items), \
                mock.patch.object(o, "urlopen", Api(routes(items))):
            return o.scrape()

    def test_complete_run_is_returned(self):
        records = self.scrape([ITEM, dict(ITEM, listingId=22222222)], max_items=25)
        self.assertEqual([r["id"] for r in records], ["11111111", "22222222"])

    def test_run_that_hit_the_cap_is_not_returned(self):
        with self.assertRaises(o.PartialScrape):
            self.scrape([dict(ITEM, listingId=n) for n in range(3)], max_items=3)

    def test_free_plan_cap_is_treated_as_partial(self):
        with self.assertRaises(o.PartialScrape):
            self.scrape([dict(ITEM, listingId=n) for n in range(100)], max_items=1000)


class ParseTest(unittest.TestCase):
    def test_full_item(self):
        r = o.parse_item(ITEM)
        self.assertEqual(r["id"], "11111111")
        self.assertEqual(r["url"], ITEM["canonicalUrl"])
        self.assertEqual((r["price_pln"], r["area_m2"], r["plot_m2"], r["rooms"]), (1500000, 180, 578, 5))
        self.assertEqual(r["location"], ITEM["address_displayAddress"])
        self.assertEqual(r["image_url"], "https://example.invalid/1.jpg")
        self.assertEqual((r["agent"], r["agency"]), ("Jan Testowy", "Testowe Nieruchomości"))
        self.assertEqual((r["phone"], r["agent_phone"], r["agency_phone"]), ("+48500000000",) * 2 + ("+48910000000",))
        self.assertEqual(r["advertiser_type"], "agency")

    def test_agency_phone_is_the_fallback(self):
        r = o.parse_item(dict(ITEM, contactPhones=[]))
        self.assertEqual((r["phone"], r["agent_phone"]), ("+48910000000", None))

    def test_missing_optional_fields_become_none(self):
        r = o.parse_item({"listingId": "5", "canonicalUrl": "https://example.invalid/5", "title": "Dom"})
        self.assertEqual(r["id"], "5")
        for key in ("price_pln", "area_m2", "plot_m2", "rooms", "description", "image_url", "agent",
                    "agency", "phone", "location"):
            self.assertIsNone(r[key], key)

    def test_hidden_price_and_foreign_currency_are_not_prices(self):
        self.assertIsNone(o.parse_item(dict(ITEM, hidePrice=True, priceExact=0))["price_pln"])
        self.assertIsNone(o.parse_item(dict(ITEM, priceCurrency="EUR"))["price_pln"])

    def test_photo_given_as_object_and_location_built_from_parts(self):
        item = {k: v for k, v in ITEM.items() if k != "address_displayAddress"}
        r = o.parse_item(dict(item, photos=[{"url": "https://example.invalid/p.jpg"}]))
        self.assertEqual(r["image_url"], "https://example.invalid/p.jpg")
        self.assertEqual(r["location"], "ul. Testowa, Zachód, Szczecin")

    def test_unparseable_phone_is_dropped(self):
        self.assertIsNone(o.parse_item(dict(ITEM, contactPhones=["brak"], agency_phones=[]))["phone"])


if __name__ == "__main__":
    unittest.main()
