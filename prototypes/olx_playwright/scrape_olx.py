#!/usr/bin/env python3
"""PROTOTYPE: scrape OLX.pl house listings for Szczecin with Playwright.

Reads the listing data OLX embeds in the page (window.__PRERENDERED_STATE__).
No stealth, no CAPTCHA solving, no cookies/session reuse, no phone numbers.
"""
import argparse
import html
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

START = "https://www.olx.pl/nieruchomosci/domy/sprzedaz/szczecin/?search%5Border%5D=created_at:desc"
PAUSE_MS = 2500


class Blocked(RuntimeError):
    pass


def page_url(n):
    return START if n == 1 else f"{START}&page={n}"


def number(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def params(ad):
    return {p.get("key"): p for p in ad.get("params") or []}


def plain_text(markup):
    text = re.sub(r"<(br|/p|/li)\s*/?>", "\n", markup or "", flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def to_record(ad):
    p = params(ad)
    price = (ad.get("price") or {}).get("regularPrice") or {}
    location = ad.get("location") or {}
    city = location.get("cityName")
    district = location.get("districtName")
    photos = ad.get("photos") or []
    partner = (ad.get("partner") or {}).get("code")
    return {
        "id": str(ad["id"]),
        "url": ad.get("url"),
        "external_url": ad.get("externalUrl"),
        "source": partner or "olx",
        "title": ad.get("title"),
        "price_pln": price.get("value") if price.get("currencyCode") == "PLN" else None,
        "negotiable": price.get("negotiable"),
        "area_m2": number(p.get("m", {}).get("normalizedValue")),
        "plot_m2": number(p.get("area", {}).get("normalizedValue")),
        "price_per_m2": number(p.get("price_per_m", {}).get("normalizedValue")),
        "built_type": p.get("builttype", {}).get("value"),
        "market": p.get("market", {}).get("value"),
        "location": ", ".join(x for x in (city, district) if x) or None,
        "district": district,
        "lat": (ad.get("map") or {}).get("lat"),
        "lon": (ad.get("map") or {}).get("lon"),
        "is_business": ad.get("isBusiness"),
        "created_at": ad.get("createdTime"),
        "refreshed_at": ad.get("lastRefreshTime"),
        "image_url": photos[0] if photos else None,
        "description": plain_text(ad.get("description")),
    }


# OLX deletes window.__PRERENDERED_STATE__ right after hydration; keep a copy.
CAPTURE_STATE = """
Object.defineProperty(window, '__PRERENDERED_STATE__', {
  configurable: true,
  get() { return window.__capturedState; },
  set(v) { if (v) window.__capturedState = v; },
});
"""


def read_state(page):
    raw = page.evaluate("() => window.__capturedState ?? null")
    if not raw:
        return None
    listing = (json.loads(raw) if isinstance(raw, str) else raw)["listing"]["listing"]
    return listing


def load_page(page, n, headless):
    response = page.goto(page_url(n), wait_until="domcontentloaded")
    status = response.status if response else None
    if status in (403, 429):
        raise Blocked(f"HTTP {status} on page {n}")
    try:
        page.wait_for_selector('[data-cy="l-card"], [data-testid="listing-grid"]', timeout=15000)
    except PlaywrightError:
        pass
    listing = read_state(page)
    if listing is None or not isinstance(listing.get("ads"), list):
        if headless:
            raise Blocked(f"No listing data on page {n} (title: {page.title()!r}); try without --headless")
        input(f"Strona {n} nie zawiera ogłoszeń (blokada?). Rozwiąż ręcznie w oknie i naciśnij Enter... ")
        listing = read_state(page)
        if listing is None:
            raise Blocked(f"Still no listing data on page {n}")
    return listing


PHONE_PAUSE_MS = 5000
SHOW_NUMBER = 'button[data-cy="phone-number.show-full-number-button"]:visible'


def normalize_phone(href):
    phone = re.sub(r"[^\d+]", "", (href or "").removeprefix("tel:"))
    if phone.startswith("00"):
        phone = "+" + phone[2:]
    elif re.fullmatch(r"\d{9}", phone):
        phone = "+48" + phone
    elif re.fullmatch(r"48\d{9}", phone):
        phone = "+" + phone
    return phone if re.fullmatch(r"\+[1-9]\d{7,14}", phone) else None


def dismiss_cookies(page):
    for name in (r"odrzu[ćc]|tylko niezb", r"akceptuj"):
        try:
            button = page.get_by_role("button", name=re.compile(name, re.I)).first
            if button.is_visible(timeout=1500):
                button.click()
                return
        except PlaywrightError:
            pass


def read_otodom_phone(context, record):
    """OLX ad -> click its Otodom link -> click visible 'Pokaż numer' -> read tel: link."""
    page = context.new_page()
    target = None
    try:
        page.goto(record["url"], wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        dismiss_cookies(page)
        link = page.locator('a[href*="otodom.pl"]').first
        if not link.count():
            return None, "no_otodom_link"
        with context.expect_page(timeout=10000) as new_page:
            link.click()
        target = new_page.value
        target.wait_for_load_state("domcontentloaded")
        target.wait_for_timeout(2500)
        if "could not be satisfied" in target.title().lower():
            raise Blocked("Otodom returned a block page")
        dismiss_cookies(target)
        button = target.locator(SHOW_NUMBER).first
        if not button.count():
            return None, "no_phone_button"
        button.click(timeout=8000)
        tel = target.locator('a[href^="tel:"]').first
        tel.wait_for(timeout=8000)
        phone = normalize_phone(tel.get_attribute("href"))
        return phone, "ok" if phone else "unrecognized_format"
    finally:
        if target:
            target.close()
        page.close()


def add_phones(context, records, limit):
    todo = [r for r in records if r["source"] == "otodom_pl"]
    for r in records:
        r["phone"] = None
        r["phone_status"] = "not_fetched" if r["source"] == "otodom_pl" else "olx_login_required"
    if limit is not None:
        todo = todo[:limit]
    for i, record in enumerate(todo, 1):
        if i > 1:
            context.pages[0].wait_for_timeout(PHONE_PAUSE_MS)
        try:
            record["phone"], record["phone_status"] = read_otodom_phone(context, record)
        except Blocked as error:
            record["phone_status"] = "blocked"
            print(f"Phone {i}/{len(todo)}: blocked ({error}); stopping phone lookups", file=sys.stderr)
            return
        except PlaywrightError as error:
            record["phone_status"] = "error"
            print(f"Phone {i}/{len(todo)}: error {str(error).splitlines()[0][:100]}", file=sys.stderr)
            continue
        print(f"Phone {i}/{len(todo)}: {record['phone_status']} (listing {record['id']})", file=sys.stderr)


def scrape(max_pages, headless, phones=False, phone_limit=None):
    records = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        try:
            context = browser.new_context(locale="pl-PL")
            context.add_init_script(CAPTURE_STATE)
            page = context.new_page()
            n, total_pages = 1, 1
            while n <= min(total_pages, max_pages):
                if n > 1:
                    page.wait_for_timeout(PAUSE_MS)
                listing = load_page(page, n, headless)
                total_pages = listing.get("totalPages") or 1
                ads = listing["ads"]
                for ad in ads:
                    records[str(ad["id"])] = to_record(ad)
                print(f"Page {n}/{total_pages}: {len(ads)} ads "
                      f"(total on OLX: {listing.get('totalElements')})", file=sys.stderr)
                n += 1
            if total_pages > max_pages:
                print(f"Stopped at --max-pages {max_pages}; {total_pages - max_pages} pages skipped",
                      file=sys.stderr)
            if phones:
                add_phones(context, list(records.values()), phone_limit)
        finally:
            browser.close()
    return list(records.values())


def write_json(records, output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, output)
    finally:
        Path(tmp).unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", default="olx_listings.json")
    ap.add_argument("--max-pages", type=int, default=10)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--phones", action="store_true", help="Also reveal phone numbers of Otodom-backed ads")
    ap.add_argument("--phone-limit", type=int, help="Fetch at most N phone numbers (for testing)")
    args = ap.parse_args()
    if args.phones and args.headless:
        print("Warning: phone lookup was only verified with a visible browser", file=sys.stderr)
    try:
        records = scrape(args.max_pages, args.headless, args.phones, args.phone_limit)
        if not records:
            raise Blocked("Zero listings")
        write_json(records, args.output)
    except (Blocked, PlaywrightError, OSError, KeyError, ValueError) as error:
        ap.exit(1, f"Scrape failed, output left untouched: {error}\n")
    print(f"Saved {len(records)} listings to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
