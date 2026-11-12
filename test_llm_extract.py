"""Tests for the generic extractor. No network; the Claude client and the fetcher are fakes.

The HTML below is synthetic. Never paste real pages, agent names or phone numbers here.
"""
import os
import tempfile
import unittest
from types import SimpleNamespace

import db
import eval_extract
from connectors import llm_extract as L
from connectors import universum_llm

BASE = "https://shop.example.pl"
START = BASE + "/domy?page=1"

RESULTS_1 = """
<html><head><title>Domy</title><script>var x = "/domy/hidden";</script></head><body>
<nav><a href="/kontakt">Kontakt</a></nav>
<a href="/oferta/A-1#top"><img src="/t/1.jpg" alt="Dom A"></a>
<a href="/oferta/A-1">Dom A</a>
<a href="/oferta/A-2">Dom B</a>
<a href="https://other.example.com/oferta/X-9">Reklama</a>
<a href="mailto:biuro@example.pl">Mail</a>
<a href="javascript:void(0)">Dalej</a>
<tr onclick="return row_click('/oferta/A-3')"><td>Dom C</td></tr>
<a href="/domy?page=2">Następna</a>
</body></html>"""
RESULTS_2 = """<body><a href="/oferta/A-4">Dom D</a><a href="/domy?page=1">Poprzednia</a></body>"""

AD = """
<html><head><title>Dom</title><style>.a{color:red}</style><script>track("x")</script></head>
<body><header>Menu glowne</header><nav>Start O firmie</nav>
<h1>Dom w Testowie</h1><p>Cena: 900&nbsp;000 PLN. Dom 120 m&amp;sup2;.</p>
<p>Makler: Jan Testowy, tel. 500 000 000</p>
<img src="/img/logo.svg"><img src="/gallery/main.jpg"><a href="/gallery/two.jpg">foto</a>
<footer>Copyright Example</footer><!-- ukryty komentarz --></body></html>"""

FULL = {"title": "Dom w Testowie", "price_pln": 900000, "area_m2": 120, "plot_m2": 600, "rooms": 5,
        "location": "Testowo", "description": "Ladny dom.", "image_url": BASE + "/gallery/main.jpg",
        "agent": "Jan Testowy", "phone": "500 000 000"}
EMPTY = {key: None for key in FULL}


def response(data, stop_reason="tool_use", name=None, input_tokens=100, output_tokens=20):
    return SimpleNamespace(
        stop_reason=stop_reason, content=[SimpleNamespace(type="tool_use", name=name, input=data)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                              cache_read_input_tokens=0, cache_creation_input_tokens=0))


class FakeClient:
    """messages.create pops the next answer for the requested tool; an Exception in the queue is raised."""

    def __init__(self, links=(), listings=()):
        self.queues = {"submit_links": list(links), "submit_listing": list(listings)}
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        name = kwargs["tools"][0]["name"]
        item = self.queues[name].pop(0)
        if isinstance(item, Exception):
            raise item
        return response(item, name=name)

    def calls(self, name):
        return [r for r in self.requests if r["tools"][0]["name"] == name]


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def __call__(self, url):
        self.urls.append(url)
        return self.pages[url]


def ad_url(code):
    return f"{BASE}/oferta/{code}"


def site():
    pages = {START: RESULTS_1, BASE + "/domy?page=2": RESULTS_2}
    pages.update({ad_url(code): AD for code in ("A-1", "A-2", "A-3", "A-4")})
    return FakeFetcher(pages)


def number_of(url, page=RESULTS_1, base=START):
    return [u for u, _ in L.reduce_links(page, base)].index(url)


# Links 0..: the on-host links of RESULTS_1 are /kontakt, A-1, A-2, A-3 (onclick), page 2 -- see test_links.
LINKS_1 = {"ad_links": [number_of(ad_url(c)) for c in ("A-1", "A-2", "A-3")],
           "next_page": number_of(BASE + "/domy?page=2")}
LINKS_2 = {"ad_links": [number_of(ad_url("A-4"), RESULTS_2)], "next_page": None}


class ReductionTest(unittest.TestCase):
    def test_ad_page_keeps_visible_text_only(self):
        text, images = L.reduce_ad(AD, BASE + "/oferta/A-1")
        self.assertIn("Dom w Testowie", text)
        self.assertIn("Jan Testowy, tel. 500 000 000", text)
        self.assertIn("Dom 120 m².", text)  # double-escaped entity decoded
        for noise in ("track(", "color:red", "Menu glowne", "Start O firmie", "Copyright", "ukryty"):
            self.assertNotIn(noise, text)
        self.assertLess(len(text), len(AD) // 2)

    def test_ad_page_images_are_absolute_and_skip_non_photos(self):
        _, images = L.reduce_ad(AD, BASE + "/oferta/A-1")
        self.assertEqual(images, [BASE + "/gallery/main.jpg", BASE + "/gallery/two.jpg"])

    def test_ad_page_text_is_capped(self):
        text, _ = L.reduce_ad("<body>" + "slowo " * 5000 + "</body>", BASE)
        self.assertEqual(len(text), L.MAX_AD_CHARS)

    def test_links(self):
        urls = [u for u, _ in L.reduce_links(RESULTS_1, START)]
        self.assertEqual(urls, [BASE + "/kontakt", BASE + "/oferta/A-1", BASE + "/oferta/A-2",
                                BASE + "/oferta/A-3", BASE + "/domy?page=2"])  # off-host, mailto, javascript, script dropped
        labels = dict(L.reduce_links(RESULTS_1, START))
        self.assertEqual(labels[BASE + "/oferta/A-1"], "Dom A")  # empty image link text replaced by the later label
        self.assertEqual(labels[BASE + "/oferta/A-3"], "Dom C")  # onclick-only row

    def test_links_text_is_short(self):
        links = L.reduce_links(RESULTS_1, START)
        self.assertEqual(L.links_text(links, START).splitlines()[1], "[1] /oferta/A-1 | Dom A")

    def test_make_id(self):
        self.assertEqual(L.make_id(BASE + "/oferta/UNI-DS-1"), "UNI-DS-1")
        self.assertEqual(L.make_id(BASE + "/oferta/UNI-DS-1/"), "UNI-DS-1")
        self.assertEqual(L.make_id(BASE + "/dom-12.html"), "dom-12")
        query = L.make_id(BASE + "/oferta.php?id=5")
        self.assertEqual(query, L.make_id(BASE + "/oferta.php?id=5#x"))
        self.assertNotEqual(query, L.make_id(BASE + "/oferta.php?id=6"))

    def test_tools_are_strict_and_complete(self):
        for tool in (L.LINKS_TOOL, L.LISTING_TOOL):
            schema = tool["input_schema"]
            self.assertTrue(tool["strict"])
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(set(L.LISTING_FIELDS), set(L.ListingAnswer.model_fields))


class ExtractAdTest(unittest.TestCase):
    def extract(self, *answers):
        client = FakeClient(listings=list(answers))
        return L.extract_ad(client, AD, BASE + "/oferta/A-1", L.new_usage()), client

    def test_record(self):
        record, client = self.extract(FULL)
        self.assertEqual(record["id"], "A-1")
        self.assertEqual(record["url"], BASE + "/oferta/A-1")
        self.assertEqual(record["phone"], "+48500000000")  # normalised in code
        self.assertEqual(record["agent"], "Jan Testowy")
        self.assertEqual(record["rooms"], 5)
        self.assertEqual(record["image_url"], BASE + "/gallery/main.jpg")
        request = client.requests[0]
        self.assertEqual(request["tool_choice"], {"type": "tool", "name": "submit_listing"})
        self.assertIn("Never invent", request["system"])
        sent = request["messages"][0]["content"]
        self.assertIn("Jan Testowy", sent)
        self.assertNotIn("Menu glowne", sent)

    def test_missing_fields_stay_none(self):
        record, _ = self.extract(EMPTY)
        self.assertEqual({k: v for k, v in record.items() if k not in ("id", "url")}, EMPTY)

    def test_blank_strings_become_none_and_bad_phone_is_dropped(self):
        record, _ = self.extract({**EMPTY, "title": "  ", "phone": "brak"})
        self.assertIsNone(record["title"])
        self.assertIsNone(record["phone"])

    def test_image_not_on_the_page_is_dropped(self):
        record, _ = self.extract({**FULL, "image_url": "https://invented.example/x.jpg"})
        self.assertIsNone(record["image_url"])

    def test_long_description_is_cut(self):
        record, _ = self.extract({**FULL, "description": "x" * 5000})
        self.assertEqual(len(record["description"]), L.DESCRIPTION_CHARS)

    def test_invalid_answer_is_retried_once(self):
        record, client = self.extract({**FULL, "price_pln": -5}, FULL)
        self.assertEqual(record["price_pln"], 900000)
        self.assertEqual(len(client.requests), 2)

    def test_invalid_twice_raises(self):
        with self.assertRaises(RuntimeError):
            self.extract({**FULL, "price_pln": -5}, {**FULL, "rooms": "pięć"})

    def test_model_cannot_set_id(self):
        record, _ = self.extract({**FULL, "id": "HACK"}, FULL)  # extra key is invalid -> retry
        self.assertEqual(record["id"], "A-1")

    def test_cut_off_answer_is_invalid(self):
        client = FakeClient()
        client.create = lambda **kw: response(FULL, stop_reason="max_tokens", name="submit_listing")
        with self.assertRaises(RuntimeError):
            L.extract_ad(client, AD, BASE + "/oferta/A-1", L.new_usage())


class ScrapeSiteTest(unittest.TestCase):
    def run_site(self, client=None, fetcher=None, **kwargs):
        client = client or FakeClient(links=[LINKS_1, LINKS_2], listings=[FULL] * 4)
        fetcher = fetcher or site()
        records, usage = L.scrape_site(START, client=client, fetcher=fetcher, **kwargs)
        return records, usage, client, fetcher

    def test_follows_pagination_and_returns_all_ads_in_order(self):
        records, usage, client, fetcher = self.run_site()
        self.assertEqual([r["id"] for r in records], ["A-1", "A-2", "A-3", "A-4"])
        self.assertEqual(fetcher.urls[:2], [START, BASE + "/domy?page=2"])
        self.assertEqual(len(client.calls("submit_links")), 2)
        self.assertEqual(len(client.calls("submit_listing")), 4)
        self.assertEqual(usage["model_calls"], 6)
        self.assertEqual(usage["input_tokens"], 600)
        self.assertEqual(usage["output_tokens"], 120)

    def test_link_numbers_refer_to_on_host_links_only(self):
        _, _, client, _ = self.run_site()
        listing = client.calls("submit_links")[0]["messages"][0]["content"]
        self.assertNotIn("other.example.com", listing)
        self.assertNotIn("mailto", listing)

    def test_model_pointing_off_the_page_raises(self):
        for bad in ({"ad_links": [99], "next_page": None}, {"ad_links": [1], "next_page": -1}):
            with self.assertRaises(RuntimeError):
                self.run_site(client=FakeClient(links=[bad]))

    def test_page_cap_refuses_a_partial_list(self):
        with self.assertRaises(RuntimeError) as caught:
            self.run_site(max_pages=1)
        self.assertIn("cap", str(caught.exception))

    def test_ad_cap_refuses_a_partial_list(self):
        with self.assertRaises(RuntimeError):
            self.run_site(max_ads=3)

    def test_circular_next_page_stops(self):
        loop = {"ad_links": LINKS_2["ad_links"], "next_page": 1}  # on page 2, link 1 is page 1... visited
        client = FakeClient(links=[LINKS_1, loop], listings=[FULL] * 4)
        records, *_ = self.run_site(client=client)
        self.assertEqual(len(records), 4)

    def test_empty_results_page_raises(self):
        with self.assertRaises(ValueError):
            self.run_site(client=FakeClient(links=[{"ad_links": [], "next_page": None}]))

    def test_known_ads_cost_nothing(self):
        first, _, _, _ = self.run_site()
        known = {r["url"]: r for r in first[:3]}
        client = FakeClient(links=[LINKS_1, LINKS_2], listings=[FULL])
        fetcher = site()
        records, usage = L.scrape_site(START, known=known, client=client, fetcher=fetcher)
        self.assertEqual([r["id"] for r in records], ["A-1", "A-2", "A-3", "A-4"])  # complete list
        self.assertEqual(len(client.calls("submit_listing")), 1)  # only the new ad
        self.assertNotIn(ad_url("A-1"), fetcher.urls)
        self.assertIn(ad_url("A-4"), fetcher.urls)
        self.assertEqual(usage["ads_cached"], 3)

    def test_all_known_still_reads_results_pages_only(self):
        first, *_ = self.run_site()
        client = FakeClient(links=[LINKS_1, LINKS_2])
        records, usage = L.scrape_site(START, known={r["url"]: r for r in first}, client=client, fetcher=site())
        self.assertEqual(len(records), 4)
        self.assertEqual(client.calls("submit_listing"), [])

    def test_failure_midway_raises(self):
        client = FakeClient(links=[LINKS_1, LINKS_2], listings=[FULL, FULL, RuntimeError("API down")])
        with self.assertRaises(RuntimeError):
            self.run_site(client=client)

    def test_fetch_failure_midway_raises(self):
        fetcher = site()
        del fetcher.pages[ad_url("A-3")]
        with self.assertRaises(KeyError):
            self.run_site(fetcher=fetcher)

    def test_invalid_ad_answer_midway_raises(self):
        bad = {**FULL, "price_pln": -1}
        client = FakeClient(links=[LINKS_1, LINKS_2], listings=[FULL, bad, bad])
        with self.assertRaises(RuntimeError):
            self.run_site(client=client)


class EvalCompareTest(unittest.TestCase):
    def test_exact_fields_and_free_text_are_judged_separately(self):
        truth = {"price_pln": 100.0, "area_m2": 50.0, "rooms": 3, "phone": "+48500000000", "agent": "Jan Testowy",
                 "title": "Dom wolnostojący, Pogodno", "location": "Szczecin, Pogodno", "description": "Ładny dom."}
        guess = {**truth, "price_pln": 100.004, "agent": "jan  testowy", "rooms": 4, "title": "Willa",
                 "location": "SZCZECIN POGODNO", "description": "Ładny dom. Garaż."}
        wrong, texts = eval_extract.compare(truth, guess)
        self.assertEqual(wrong, {"rooms"})  # price within tolerance; None == None for the absent fields
        self.assertEqual(texts, {"title": "differs", "location": "same", "description": "close"})

    def test_null_versus_value_is_a_mismatch(self):
        wrong, _ = eval_extract.compare({"plot_m2": 600.0}, {"plot_m2": None})
        self.assertEqual(wrong, {"plot_m2"})


class ConnectorTest(unittest.TestCase):
    def test_known_comes_from_its_own_source_in_the_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "t.db")
            record = {"id": "A-1", "url": ad_url("A-1"), "title": "Dom", "phone": "+48500000000"}
            db.save_listings("universum_llm", [record], path=path)
            db.save_listings("universum", [{"id": "B-1", "url": ad_url("B-1")}], path=path)
            self.assertEqual(universum_llm.load_known(path=path), {ad_url("A-1"): record})

    def test_stored_under_its_own_source_name(self):
        self.assertEqual(universum_llm.SOURCE, "universum_llm")
        self.assertIn("universum.nieruchomosci.pl", universum_llm.START_URL)


if __name__ == "__main__":
    unittest.main()
