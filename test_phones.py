import contextlib
import io
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import phones
import scraper


OFFER = "https://adresowo.pl/o/dom-example"


def browser_page(*, logged_in=True, phone="tel:%2B48%20600%20000%20000"):
    page = MagicMock()
    page.evaluate.return_value = logged_in
    page.goto.return_value.status = 200
    page.locator.return_value.is_visible.return_value = False
    page.locator.return_value.first.get_attribute.return_value = phone
    return page


class PhoneTests(unittest.TestCase):
    def test_normalize_phone(self):
        cases = {
            "tel:600 000 000": "+48600000000",
            "tel:+48 (600) 000-000": "+48600000000",
            "tel:0048-600-000-000": "+48600000000",
            "tel:48600000000": "+48600000000",
            "tel:%2B48%20600%20000%20000;ext=12": "+48600000000",
            "tel:+49 151 00000000": "+4915100000000",
        }
        for href, expected in cases.items():
            with self.subTest(href=href):
                self.assertEqual(phones.normalize_phone(href), expected)

    def test_reject_invalid_phone(self):
        for href in (None, "https://adresowo.pl/", "tel:", "tel:123", "tel:+48not-a-number"):
            with self.subTest(href=href), self.assertRaises(ValueError):
                phones.normalize_phone(href)

    def test_reject_foreign_offer_urls(self):
        for url in (
            "https://other.example/o/dom",
            "http://adresowo.pl/o/dom",
            "https://adresowo.pl.evil.example/o/dom",
            "https://adresowo.pl/login/",
        ):
            page = browser_page()
            with self.subTest(url=url), self.assertRaises(ValueError):
                phones.read_phone(page, url)
            page.goto.assert_not_called()

    def test_read_phone_without_clicking_telephone_link(self):
        page = browser_page()
        self.assertEqual(phones.read_phone(page, OFFER), ("+48600000000", "ok"))
        page.goto.assert_called_once_with(OFFER, wait_until="domcontentloaded")
        page.locator.return_value.first.click.assert_called_once_with()
        page.locator.return_value.first.get_attribute.assert_called_once_with("href")

    def test_login_required_stops_before_reveal(self):
        page = browser_page(logged_in=False)
        with self.assertRaisesRegex(PermissionError, "--login"):
            phones.read_phone(page, OFFER)
        page.locator.return_value.first.click.assert_not_called()

    def test_rate_limit_stops_before_reveal(self):
        page = browser_page()
        page.goto.return_value.status = 429
        with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
            phones.read_phone(page, OFFER)
        page.evaluate.assert_not_called()

    def test_callback_only_does_not_submit_a_form(self):
        page = browser_page()
        default = page.locator.return_value
        callback = MagicMock()
        callback.is_visible.return_value = True
        page.locator.side_effect = lambda selector: callback if selector == phones.CALLBACK else default
        self.assertEqual(phones.read_phone(page, OFFER), (None, "callback_only"))
        default.first.click.assert_called_once_with()
        default.click.assert_not_called()
        default.fill.assert_not_called()

    def test_contact_error_stops_run(self):
        page = browser_page()
        default = page.locator.return_value
        error = MagicMock()
        error.is_visible.return_value = True
        default.inner_text.return_value = "Limit kontaktów"
        page.locator.side_effect = (
            lambda selector: error if selector == "#modal-contact-phone-error:visible" else default
        )
        with self.assertRaisesRegex(RuntimeError, "Limit kontaktów"):
            phones.read_phone(page, OFFER)

    def test_saved_session_excludes_identity_provider_cookies(self):
        state = {
            "cookies": [
                {"domain": ".adresowo.pl", "name": "session", "value": "test"},
                {"domain": "www.adresowo.pl", "name": "preferences", "value": "test"},
                {"domain": ".google.com", "name": "identity", "value": "test"},
                {"domain": "notadresowo.pl", "name": "other", "value": "test"},
            ],
            "origins": [
                {"origin": "https://adresowo.pl", "localStorage": []},
                {"origin": "https://accounts.google.com", "localStorage": []},
            ],
        }
        filtered = phones.site_session(state)
        self.assertEqual(filtered["cookies"], state["cookies"][:2])
        self.assertEqual(filtered["origins"], state["origins"][:1])

    @patch("phones.browser_context")
    def test_manual_login_is_checked_before_saving_session(self, browser_context):
        context = browser_context.return_value.__enter__.return_value
        page = context.new_page.return_value
        page.locator.return_value.first.get_attribute.return_value = "/o/dom-example"
        page.evaluate.return_value = True
        context.storage_state.return_value = {"cookies": [], "origins": []}
        with patch("builtins.input", return_value="") as prompt:
            self.assertEqual(phones.login(scraper.URL), {"cookies": [], "origins": []})
        prompt.assert_called_once()
        page.evaluate.assert_called_once()
        self.assertEqual(page.goto.call_args.args[0], OFFER)
        context.storage_state.assert_called_once_with()

    @patch("phones.browser_context")
    def test_invalid_manual_login_is_not_saved(self, browser_context):
        context = browser_context.return_value.__enter__.return_value
        page = context.new_page.return_value
        page.locator.return_value.first.get_attribute.return_value = "/o/dom-example"
        page.evaluate.return_value = False
        with patch("builtins.input", return_value=""), self.assertRaises(PermissionError):
            phones.login(scraper.URL)
        context.storage_state.assert_not_called()

    @patch("phones.browser_context")
    @patch("phones.read_phone")
    def test_phone_enrichment_reuses_browser_and_pauses(self, read_phone, browser_context):
        read_phone.side_effect = [("+48600000000", "ok"), (None, "callback_only")]
        records = [{"id": "1", "url": OFFER}, {"id": "2", "url": OFFER}]
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session.json"
            session.write_text("{}", encoding="utf-8")
            phones.add_phone_numbers(records, session)
        page = browser_context.return_value.__enter__.return_value.new_page.return_value
        page.wait_for_timeout.assert_called_once_with(2000)
        self.assertEqual(read_phone.call_count, 2)
        self.assertEqual(records[0]["phone"], "+48600000000")
        self.assertIsNone(records[1]["phone"])
        self.assertEqual(records[1]["phone_status"], "callback_only")

    @patch("phones.browser_context")
    def test_missing_session_does_not_open_browser(self, browser_context):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(PermissionError, "--login"):
                phones.add_phone_numbers([], Path(directory) / "missing.json")
        browser_context.assert_not_called()

    def test_cli_login_saves_private_session(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / ".auth" / "adresowo.json"
            state = {"cookies": [], "origins": []}
            with patch("scraper.SESSION", session), patch("phones.login", return_value=state):
                with patch("sys.argv", ["scraper.py", "--login"]), patch("scraper.scrape") as scrape:
                    scraper.main()
            scrape.assert_not_called()
            self.assertEqual(json.loads(session.read_text(encoding="utf-8")), state)
            self.assertEqual(stat.S_IMODE(session.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(session.parent.stat().st_mode), 0o700)

    def test_phone_failure_preserves_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session.json"
            session.write_text("{}", encoding="utf-8")
            output = Path(directory) / "listings.json"
            original = '[{"id": "old", "phone": "+48600000000"}]\n'
            output.write_text(original, encoding="utf-8")
            with patch("scraper.SESSION", session), patch("scraper.scrape", return_value=[]):
                with patch("phones.add_phone_numbers", side_effect=RuntimeError("Limit kontaktów")):
                    with patch("sys.argv", ["scraper.py", "--phones", "--output", str(output)]):
                        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                            scraper.main()
            self.assertEqual(error.exception.code, 1)
            self.assertEqual(output.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
