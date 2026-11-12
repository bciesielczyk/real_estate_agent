#!/usr/bin/env python3
"""Save Gratka.pl house listings for Szczecin (with seller phone numbers) as JSON.

Plain HTTP, no browser: Gratka embeds schema.org JSON-LD in its pages. The results
pages list the ads; each ad page carries the seller's name and telephone.
"""
import argparse
import json
import os
import re
import sqlite3
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

from connectors import normalize_phone

URL = "https://gratka.pl/nieruchomosci/domy/szczecin"
MAX_PAGE = 10  # robots.txt allows ?page=2..10 only
HEADERS = {"User-Agent": "GratkaScraper/1.0 (personal use)"}
LD_JSON = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


def fetch(url):
    with urlopen(Request(url, headers=HEADERS), timeout=30) as response:
        return response.read().decode(response.headers.get_content_charset() or "utf-8")


def ld_nodes(html):
    for block in LD_JSON.findall(html):
        data = json.loads(block)
        yield from data.get("@graph", [data]) if isinstance(data, dict) else data


def number(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_list(html):
    for node in ld_nodes(html):
        offers = (node.get("offers") or {}).get("offers")
        if node.get("additionalType") == "RealEstateListing" and isinstance(offers, list):
            return offers
    raise ValueError("No listing data found; refusing to save incomplete results")


def last_page(html):
    return max((int(n) for n in re.findall(r'href="[^"]*[?&]page=(\d+)"', html)), default=1)


def to_record(offer):
    item = offer.get("itemOffered") or {}
    address = item.get("address") or {}
    url = offer["url"]
    return {
        "id": re.search(r"/o[bi]/(\d+)", url).group(1),
        "url": url,
        "title": offer.get("name"),
        "price_pln": number(offer.get("price")) if offer.get("priceCurrency") == "PLN" else None,
        "area_m2": number((item.get("floorSize") or {}).get("value")),
        "rooms": item.get("numberOfRooms"),
        "location": ", ".join(x for x in (address.get("streetAddress"), address.get("addressLocality")) if x),
        "image_url": offer.get("image"),
        "description_preview": item.get("description"),
    }


NUXT_DATA = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)


def nuxt_value(table, index, depth=0):
    """Resolve Nuxt's flattened payload (values reference each other by array index)."""
    value = table[index]
    if depth > 8:
        return None
    if isinstance(value, list) and len(value) == 2 and value[0] in ("Reactive", "ShallowReactive", "Ref", "ShallowRef"):
        return nuxt_value(table, value[1], depth + 1)
    if isinstance(value, dict):
        return {k: nuxt_value(table, v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [nuxt_value(table, v, depth + 1) if isinstance(v, int) else v for v in value]
    return value


def first_phone(contact):
    for phone in (contact or {}).get("phones") or []:
        if normalized := normalize_phone(phone):
            return normalized
    return None


def parse_contact(html, ad_id):
    """Return the ad's own agent and agency contacts (not those of recommended ads)."""
    match = NUXT_DATA.search(html)
    if not match:
        return None
    table = json.loads(match.group(1))
    data = nuxt_value(table, table[1]["data"])
    for key, entry in data.items():
        if key.startswith("property-details-") and isinstance(entry, dict):
            prop = (entry.get("data") or {}).get("propertyData") or {}
            if str(prop.get("idOnFrontend")) == ad_id:
                contact = prop.get("contact") or {}
                person, company = contact.get("person") or {}, contact.get("company") or {}
                return {
                    "agent": person.get("name"),
                    "agent_phone": first_phone(person),
                    "agency": company.get("name"),
                    "agency_phone": first_phone(company),
                }
    return None


def parse_seller(html, ad_id):
    contact = parse_contact(html, ad_id)
    if contact:
        contact["phone"] = contact["agent_phone"] or contact["agency_phone"]
        return contact
    for node in ld_nodes(html):  # fallback: JSON-LD only has the agency's general number
        if node.get("@type") == "Offer" and node.get("seller"):
            seller = node["seller"]
            phone = normalize_phone(seller.get("telephone"))
            return {"agent": None, "agent_phone": None, "agency": seller.get("name"),
                    "agency_phone": phone, "phone": phone}
    return {"agent": None, "agent_phone": None, "agency": None, "agency_phone": None, "phone": None}


CONTACT_FIELDS = ("agent", "agent_phone", "agency", "agency_phone", "phone")


def load_known_phones(output):
    """Reuse seller/phone from the previous run so only new ads need a request."""
    try:
        previous = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    # Ads already checked (with or without a phone) are not fetched again.
    # Records from older versions (no "agent_phone" field) are refetched.
    return {r["id"]: {k: r.get(k) for k in CONTACT_FIELDS} for r in previous if "agent_phone" in r}


def scrape(max_pages, phones, phone_limit, known=None):
    known = known or {}
    records, page, total = {}, 1, 1
    while page <= min(total, max_pages, MAX_PAGE):
        if page > 1:
            time.sleep(1)
        html = fetch(URL if page == 1 else f"{URL}?page={page}")
        offers = parse_list(html)
        total = max(total, last_page(html))
        for offer in offers:
            record = to_record(offer)
            records[record["id"]] = record
        print(f"Page {page}/{total}: {len(offers)} listings")
        page += 1
    records = list(records.values())
    if phones:
        for record in records:
            if record["id"] in known:
                record.update(known[record["id"]])
        todo = [r for r in records if r["id"] not in known]
        print(f"Phones: {len(records) - len(todo)} reused from previous run, {len(todo)} to fetch")
        if phone_limit is not None:
            todo = todo[:phone_limit]
        for i, record in enumerate(todo, 1):
            time.sleep(1)
            record.update(parse_seller(fetch(record["url"]), record["id"]))
            source = "agent" if record["agent_phone"] else "agency" if record["phone"] else "none"
            print(f"Phone {i}/{len(todo)}: {source} (listing {record['id']})")
    return records


def write_json(records, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(records, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("gratka_listings.json"))
    parser.add_argument("--max-pages", type=int, default=MAX_PAGE)
    parser.add_argument("--phones", action="store_true", help="Also fetch each ad page for the seller phone")
    parser.add_argument("--phone-limit", type=int, help="Fetch at most N ad pages (for testing)")
    args = parser.parse_args()
    try:
        known = load_known_phones(args.output) if args.phones else {}
        records = scrape(args.max_pages, args.phones, args.phone_limit, known)
        write_json(records, args.output)
        print(f"Saved {len(records)} listings to {args.output}")
        if args.max_pages >= MAX_PAGE:  # only a complete scrape may mark listings as gone
            from db import save_listings
            save_listings("gratka", records)
        else:
            print("Partial scrape (--max-pages); database not updated")
    except (OSError, ValueError, KeyError, AttributeError, sqlite3.Error) as error:
        parser.exit(1, f"Scrape failed, previous file left untouched: {error}\n")


if __name__ == "__main__":
    main()
