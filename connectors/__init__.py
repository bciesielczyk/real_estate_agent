"""Connectors: one small scraper per listing site.

A connector is a module in this package with one function:

    scrape() -> list[dict]   # one complete scrape of the site, or raise

Each dict needs `id` and should have `url`, `title`, `price_pln`, `area_m2`, `rooms`,
`location`, `description`, `image_url`, `agent`, `phone`; any extra keys are kept in
the database's `data` column. The module name is the source name in the database.

Run one with: uv run python scrape.py <name>
"""
import re
import time
from urllib.request import Request, urlopen

HEADERS = {"User-Agent": "RealEstateAgent/1.0 (personal use)"}


def fetch(url, pause=1.0):
    """GET a page as text, after a polite pause."""
    time.sleep(pause)
    with urlopen(Request(url, headers=HEADERS), timeout=30) as response:
        return response.read().decode(response.headers.get_content_charset() or "utf-8")


def normalize_phone(text):
    """Return the number as +48123456789, or None if it doesn't look like a phone."""
    phone = re.sub(r"[^\d+]", "", text or "")
    if phone.startswith("00"):
        phone = "+" + phone[2:]
    elif re.fullmatch(r"\d{9}", phone):
        phone = "+48" + phone
    elif re.fullmatch(r"48\d{9}", phone):
        phone = "+" + phone
    return phone if re.fullmatch(r"\+[1-9]\d{7,14}", phone) else None
