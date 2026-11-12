#!/usr/bin/env python3
"""Save all Szczecin house listings from Adresowo as a fresh JSON snapshot."""

import argparse
import json
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

URL = "https://adresowo.pl/domy/szczecin/"
SESSION = Path(__file__).resolve().parent / ".auth" / "adresowo.json"


def text(element):
    return " ".join(element.get_text(" ", strip=True).split()) if element else None


def number(value):
    match = re.search(r"\d[\d\s]*(?:[.,]\d+)?", value)
    if not match:
        return None
    value = float(re.sub(r"\s+", "", match.group()).replace(",", "."))
    return int(value) if value.is_integer() else value


def parse_page(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("#offer-list-results [data-offer-card]")
    if not cards:
        raise ValueError(f"No listing cards found at {page_url}; refusing to save incomplete results")

    records = []
    for card in cards:
        link = card.select_one('a[data-track="offer-link"]')
        facts = card.select("p.flex-auto")
        if not card.get("data-id") or not link or not link.get("href") or len(facts) < 3:
            raise ValueError(f"Unrecognized listing markup at {page_url}")
        image = card.find("img")
        location = " ".join(part.get_text(" ", strip=True) for part in link.select(".line-clamp-1"))
        records.append({
            "id": card["data-id"],
            "url": urljoin(page_url, str(link["href"])),
            "title": (image.get("alt") if image else None) or location,
            "location": location,
            "price_pln": number(text(facts[0])),
            "area_m2": number(text(facts[1])),
            "rooms": number(text(facts[2])),
            "image_url": urljoin(page_url, str(image["src"])) if image and image.get("src") else None,
            "description_preview": text(card.select_one("p.line-clamp-4")),
        })

    next_link = soup.select_one('link[rel~="next"]')
    next_url = urljoin(page_url, str(next_link["href"])) if next_link else None
    return records, next_url


def scrape():
    records = {}
    visited = set()
    page_url = URL
    while page_url:
        if page_url in visited or not page_url.startswith(URL):
            raise ValueError(f"Unexpected or repeated pagination URL: {page_url}")
        visited.add(page_url)
        request = Request(page_url, headers={"User-Agent": "AdresowoScraper/1.0 (personal use)"})
        with urlopen(request, timeout=30) as response:
            html = response.read().decode(response.headers.get_content_charset() or "utf-8")
        page_records, page_url = parse_page(html, page_url)
        for record in page_records:
            records[record["id"]] = record
        print(f"Page {len(visited)}: {len(page_records)} listings")
        if page_url:
            time.sleep(1)
    return list(records.values())


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
    parser.add_argument("--output", type=Path, default=Path("listings.json"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--login", action="store_true", help="Log in manually and save a local browser session")
    mode.add_argument("--phones", action="store_true", help="Also read phone numbers using the saved session")
    args = parser.parse_args()
    try:
        if args.login:
            from phones import login
            state = login(URL)
            SESSION.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            SESSION.parent.chmod(0o700)
            write_json(state, SESSION)
            print(f"Saved login session to {SESSION}. Treat this file like a password.")
            return
        if args.phones and not SESSION.is_file():
            raise PermissionError("No saved session; run scraper.py --login first")
        records = scrape()
        if args.phones:
            from phones import add_phone_numbers
            add_phone_numbers(records, SESSION)
        write_json(records, args.output)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Scrape failed: {error}\n")
    print(f"Saved {len(records)} unique listings to {args.output}")


if __name__ == "__main__":
    main()
