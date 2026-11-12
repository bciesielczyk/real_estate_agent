"""Offline tests with synthetic pages (fake agent, fake phone). Schema tests need crawl4ai and are skipped without it:
uv run --with-requirements requirements.txt --with-requirements requirements-crawl4ai.txt python -m unittest test_universum_crawl4ai
"""
import importlib.util
import unittest

from connectors.universum_crawl4ai import LIST_SCHEMA, OFFER_SCHEMA, last_page, map_offer, parse_codes

HAS_CRAWL4AI = importlib.util.find_spec("crawl4ai") is not None

RESULTS = """
<table><tr><td onclick="return row_click('/oferta/UNI-DS-1');"></td>
<td onclick="row_click('/oferta/UNI-DS-1')">Szczecin</td></tr>
<tr><td onclick="row_click('/oferta/SNS-DS-22')"></td></tr></table>
<a href="/pl/lista_ofert.php?szukaj=tak&amp;porcja=3&amp;kategoria=3">3</a>
"""

OFFER = """
<html><body>
<div id="offerAgent"><b>Makler odpowiedzialny za ofertę:</b><br />
  <b class="blue2">Jan Testowy</b> |tel. <b>48 500 000 000</b> | email: <a href="#">x</a></div>
<div id="mainThumb"><a rel="prettyPhoto[gallery]" href="/gallery/1.jpg"><img src="/gallery/1.jpg" /></a></div>
<table id="offerMainDetails">
  <tr id="headRow"><th>Numer oferty</th><td>UNI-DS-1</td></tr>
  <tr><th>miejscowość:</th><td><a href="/x">Szczecin</a></td></tr>
  <tr><th>dzielnica - osiedle:</th><td>Pogodno</td></tr>
  <tr><th>typ domu:</th><td>wolnostojący</td></tr>
  <tr><th>cena ofertowa PLN:</th><td>3 700 000</td></tr>
  <tr><th>pow. całkowita [m&sup2;]:</th><td>447</td></tr>
  <tr><th>pow. użytkowa [m&sup2;]:</th><td>297,5000</td></tr>
  <tr><th>pow. działki [m&sup2;]:</th><td>716</td></tr>
  <tr><th>liczba pokoi:</th><td>5</td></tr>
</table>
<div class="boxBG"><img src="/images/pl/boxOpisOferty.gif" /><p>Dom 297 m&amp;sup2; z ogrodem.</p></div>
<a href="#">top</a>
</body></html>
"""

# What the offer schema should return for OFFER (the shape Crawl4AI produces).
EXTRACTED = [{
    "agent": "Jan Testowy", "agent_phone": "48 500 000 000", "image": "/gallery/1.jpg",
    "description": "Dom 297 m&sup2; z ogrodem.",
    "rows": [{"key": "Numer oferty", "value": "UNI-DS-1"}, {"key": "miejscowość:", "value": "Szczecin"},
             {"key": "dzielnica - osiedle:", "value": "Pogodno"}, {"key": "typ domu:", "value": "wolnostojący"},
             {"key": "cena ofertowa PLN:", "value": "3 700 000"}, {"key": "pow. całkowita [m²]:", "value": "447"},
             {"key": "pow. użytkowa [m²]:", "value": "297,5000"}, {"key": "pow. działki [m²]:", "value": "716"},
             {"key": "liczba pokoi:", "value": "5"}],
}]


class MappingTest(unittest.TestCase):
    def test_list_helpers(self):
        rows = [{"onclick": "return row_click('/oferta/UNI-DS-1');"}, {"onclick": "row_click('/oferta/UNI-DS-1')"},
                {"onclick": "row_click('/oferta/SNS-DS-22')"}, {}]
        self.assertEqual(parse_codes(rows), ["UNI-DS-1", "SNS-DS-22"])
        self.assertEqual(last_page(RESULTS), 3)

    def test_offer_mapping(self):
        record = map_offer(EXTRACTED, "UNI-DS-1")
        self.assertEqual(record["url"], "https://www.universum.nieruchomosci.pl/oferta/UNI-DS-1")
        self.assertEqual(record["title"], "Dom wolnostojący, Pogodno")
        self.assertEqual(record["location"], "Szczecin, Pogodno")
        self.assertEqual(record["price_pln"], 3700000)
        self.assertEqual(record["area_m2"], 297.5)
        self.assertEqual(record["plot_m2"], 716)
        self.assertEqual(record["rooms"], 5)
        self.assertEqual(record["agent"], "Jan Testowy")
        self.assertEqual(record["phone"], "+48500000000")
        self.assertEqual(record["image_url"], "https://www.universum.nieruchomosci.pl/gallery/1.jpg")
        self.assertEqual(record["description"], "Dom 297 m² z ogrodem.")

    def test_missing_fields_become_none(self):
        record = map_offer([{"rows": [{"key": "x:", "value": "y"}]}], "A-1")
        self.assertEqual(record["title"], "Dom")
        self.assertIsNone(record["price_pln"])
        self.assertIsNone(record["phone"])
        self.assertIsNone(record["image_url"])  # no photo: None, not a stray link

    def test_page_without_details_is_an_error(self):
        for empty in ([], [{"rows": []}], None):
            with self.assertRaises(ValueError):
                map_offer(empty, "A-1")


@unittest.skipUnless(HAS_CRAWL4AI, "crawl4ai not installed")
class SchemaTest(unittest.TestCase):
    def extract(self, schema, page):
        from crawl4ai import JsonCssExtractionStrategy
        return JsonCssExtractionStrategy(schema).extract("https://example.invalid/", page)

    def test_list_schema(self):
        self.assertEqual(parse_codes(self.extract(LIST_SCHEMA, RESULTS)), ["UNI-DS-1", "SNS-DS-22"])

    def test_offer_schema_end_to_end(self):
        record = map_offer(self.extract(OFFER_SCHEMA, OFFER), "UNI-DS-1")
        self.assertEqual(record["agent"], "Jan Testowy")
        self.assertEqual(record["phone"], "+48500000000")
        self.assertEqual(record["area_m2"], 297.5)
        self.assertEqual(record["image_url"], "https://www.universum.nieruchomosci.pl/gallery/1.jpg")
        self.assertEqual(record["description"], "Dom 297 m² z ogrodem.")

    def test_offer_without_photo_has_no_image(self):
        page = OFFER.replace('<div id="mainThumb"><a rel="prettyPhoto[gallery]" href="/gallery/1.jpg">'
                             '<img src="/gallery/1.jpg" /></a></div>', '<div id="mainThumb"></div>')
        self.assertIsNone(map_offer(self.extract(OFFER_SCHEMA, page), "UNI-DS-1")["image_url"])


if __name__ == "__main__":
    unittest.main()
