"""Read contact numbers through Adresowo's normal, authenticated browser UI."""

import re
from contextlib import contextmanager
from urllib.parse import unquote, urljoin, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

PHONE_LINK = '#modal-contact-phone a[href^="tel:"]:visible'
PHONE_READY = ", ".join((
    PHONE_LINK,
    "#modal-contact-phone-error:visible",
    "#modal-register:visible",
))
CALLBACK = "#realtor-callback-container:visible"


@contextmanager
def browser_context(session_file=None):
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            try:
                context = browser.new_context(
                    storage_state=str(session_file) if session_file else None,
                )
                context.set_default_timeout(30000)
                yield context
            finally:
                browser.close()
    except PlaywrightError as error:
        raise RuntimeError(
            f"Browser error: {error}\n"
            "If Chromium is missing, run the browser installation command in README.md. "
            "If the site shows a challenge or limit, complete it manually or stop and try later; "
            "the scraper does not bypass it."
        ) from error


def require_login(page):
    if not page.evaluate("Boolean(window.re?.userLoggedIn) && !window.re?.registrationRequired"):
        raise PermissionError("Session missing or expired; run scraper.py --login again")


def site_session(state):
    """Keep only Adresowo state, never Google or other identity-provider cookies."""
    def is_adresowo(host):
        host = host.lstrip(".")
        return host == "adresowo.pl" or host.endswith(".adresowo.pl")

    return {
        "cookies": [cookie for cookie in state["cookies"] if is_adresowo(cookie["domain"])],
        "origins": [
            origin for origin in state["origins"]
            if is_adresowo(urlsplit(origin["origin"]).hostname or "")
        ],
    }


def login(source_url):
    with browser_context() as context:
        page = context.new_page()
        page.goto(urljoin(source_url, "/login/"), wait_until="domcontentloaded")
        print("Zaloguj się ręcznie w otwartym Chromium przez Google lub e-mail.")
        print("Link z e-maila otwórz w TYM oknie Chromium (wklej go w pasek adresu).")
        try:
            input("Po zakończeniu logowania wróć tutaj i naciśnij Enter: ")
        except EOFError as error:
            raise ValueError("Login requires an interactive terminal") from error
        page.goto(source_url, wait_until="domcontentloaded")
        offer = page.locator('#offer-list-results a[data-track="offer-link"]').first
        offer_url = urljoin(source_url, offer.get_attribute("href") or "")
        validate_offer_url(offer_url)
        page.goto(offer_url, wait_until="domcontentloaded")
        require_login(page)
        return site_session(context.storage_state())


def validate_offer_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "adresowo.pl" or not parsed.path.startswith("/o/"):
        raise ValueError(f"Not an Adresowo advertisement URL: {url}")


def normalize_phone(href):
    if not href or not href.startswith("tel:"):
        raise ValueError("Missing telephone link")
    phone = unquote(href[4:]).split(";", 1)[0].split("?", 1)[0]
    phone = re.sub(r"[\s().-]", "", phone)
    if phone.startswith("00"):
        phone = "+" + phone[2:]
    elif re.fullmatch(r"\d{9}", phone):
        phone = "+48" + phone
    elif re.fullmatch(r"48\d{9}", phone):
        phone = "+" + phone
    if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
        raise ValueError("Unrecognized telephone number format")
    return phone


def read_phone(page, url):
    validate_offer_url(url)
    response = page.goto(url, wait_until="domcontentloaded")
    if response and response.status >= 400:
        raise RuntimeError(f"HTTP {response.status} at {url}; stopping without retries")
    require_login(page)

    reject_cookies = page.locator('[data-cky-tag="reject-button"]')
    if reject_cookies.is_visible():
        reject_cookies.click()
    page.locator("[data-contact-phone]").first.click()
    page.locator(f"{PHONE_READY}, {CALLBACK}").first.wait_for(state="visible")

    if page.locator(CALLBACK).is_visible():
        reveal = page.locator('#modal-contact-phone [onclick*="revealContactPhone"]')
        if not reveal.is_visible():
            return None, "callback_only"
        reveal.click()
        page.locator(PHONE_READY).first.wait_for(state="visible")

    if page.locator("#modal-register:visible").is_visible():
        raise PermissionError("Login required to reveal the number; run scraper.py --login again")
    if page.locator("#modal-contact-phone-error:visible").is_visible():
        message = page.locator("#modal-contact-phone-error-message").inner_text()
        raise RuntimeError(f"Cannot reveal phone at {url}: {message}")
    return normalize_phone(page.locator(PHONE_LINK).first.get_attribute("href")), "ok"


def add_phone_numbers(records, session_file):
    if not session_file.is_file():
        raise PermissionError("No saved session; run scraper.py --login first")
    with browser_context(session_file) as context:
        page = context.new_page()
        for index, record in enumerate(records):
            if index:
                page.wait_for_timeout(2000)
            phone, status = read_phone(page, record["url"])
            record["phone"] = phone
            record["phone_status"] = status
            print(f"Phone {index + 1}/{len(records)}: {status} (listing {record['id']})")
