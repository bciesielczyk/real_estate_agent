import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import scraper


CARD = """
<div data-offer-card data-id="123">
  <img src="/house.webp" alt="Dom Szczecin Osów">
  <p class="flex-auto"><span>1&nbsp;070&nbsp;000</span> zł</p>
  <p class="flex-auto"><span>80,5</span> m²</p>
  <p class="flex-auto"><span>4</span> pok.</p>
  <h2><a data-track="offer-link" href="/o/example">
    <span class="line-clamp-1">Szczecin</span>
    <span class="line-clamp-1">Osów</span>
  </a></h2>
  <p class="line-clamp-4">Dom z ogrodem.\n  Blisko lasu.</p>
</div>
"""


def page(cards=CARD, next_url=None):
    next_link = f'<link rel="next" href="{next_url}">' if next_url else ""
    return f'<html><head>{next_link}</head><body><div id="offer-list-results">{cards}</div></body></html>'


def response(html):
    result = MagicMock()
    result.__enter__.return_value = result
    result.read.return_value = html.encode("utf-8")
    result.headers.get_content_charset.return_value = "utf-8"
    return result


class ScraperTests(unittest.TestCase):
    def test_parse_listing_and_pagination(self):
        records, next_url = scraper.parse_page(page(next_url="_l2"), scraper.URL)
        self.assertEqual(next_url, scraper.URL + "_l2")
        self.assertEqual(records, [{
            "id": "123",
            "url": "https://adresowo.pl/o/example",
            "title": "Dom Szczecin Osów",
            "location": "Szczecin Osów",
            "price_pln": 1070000,
            "area_m2": 80.5,
            "rooms": 4,
            "image_url": "https://adresowo.pl/house.webp",
            "description_preview": "Dom z ogrodem. Blisko lasu.",
        }])

    def test_missing_optional_values(self):
        card = CARD.replace('<img src="/house.webp" alt="Dom Szczecin Osów">', "")
        card = card.replace("1&nbsp;070&nbsp;000", "Zapytaj o cenę")
        records, next_url = scraper.parse_page(page(card), scraper.URL)
        self.assertIsNone(next_url)
        self.assertIsNone(records[0]["price_pln"])
        self.assertIsNone(records[0]["image_url"])
        self.assertEqual(records[0]["title"], "Szczecin Osów")

    def test_empty_or_broken_page_fails(self):
        for html in (page(""), page(CARD.replace('data-track="offer-link"', ""))):
            with self.subTest(html=html), self.assertRaises(ValueError):
                scraper.parse_page(html, scraper.URL)

    @patch("scraper.time.sleep")
    @patch("scraper.urlopen")
    def test_pagination_and_deduplication(self, urlopen, sleep):
        second_card = CARD.replace('data-id="123"', 'data-id="456"')
        urlopen.side_effect = [response(page(next_url="_l2")), response(page(CARD + second_card))]
        records = scraper.scrape()
        self.assertEqual([record["id"] for record in records], ["123", "456"])
        self.assertEqual([call.args[0].full_url for call in urlopen.call_args_list], [
            scraper.URL, scraper.URL + "_l2",
        ])
        sleep.assert_called_once_with(1)

    def test_rerun_replaces_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "listings.json"
            scraper.write_json([{"id": "old"}], output)
            scraper.write_json([{"id": "new", "location": "Osów"}], output)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), [
                {"id": "new", "location": "Osów"},
            ])
            self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_failed_write_preserves_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "listings.json"
            output.write_text('[{"id": "old"}]\n', encoding="utf-8")
            with patch("scraper.json.dump", side_effect=OSError("Disk full")):
                with self.assertRaises(OSError):
                    scraper.write_json([{"id": "new"}], output)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), [{"id": "old"}])
            self.assertEqual(list(Path(directory).iterdir()), [output])

    @patch("scraper.time.sleep")
    @patch("scraper.urlopen")
    def test_failed_later_page_preserves_previous_snapshot(self, urlopen, sleep):
        urlopen.side_effect = [response(page(next_url="_l2")), response(page(""))]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "listings.json"
            original = '[{"id": "old"}]\n'
            output.write_text(original, encoding="utf-8")
            with patch("sys.argv", ["scraper.py", "--output", str(output)]):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    scraper.main()
            self.assertEqual(error.exception.code, 1)
            self.assertEqual(output.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
