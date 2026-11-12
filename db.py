#!/usr/bin/env python3
"""Store scraped listings in one SQLite file (listings.db).

Each scrape run updates the table for its source:
- new listing        -> inserted, first_seen = now
- listing seen again -> fields updated, last_seen = now
- listing gone       -> active = 0 (kept, never deleted)

Usage: python db.py import gratka gratka_listings.json
"""
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "listings.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    source      TEXT NOT NULL,
    id          TEXT NOT NULL,
    url         TEXT,
    title       TEXT,
    price_pln   REAL,
    area_m2     REAL,
    rooms       INTEGER,
    location    TEXT,
    description TEXT,
    phone       TEXT,
    agent       TEXT,
    data        TEXT NOT NULL,   -- full scraped record as JSON
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (source, id)
)
"""

UPSERT = """
INSERT INTO listings (source, id, url, title, price_pln, area_m2, rooms, location,
                      description, phone, agent, data, first_seen, last_seen, active)
VALUES (:source, :id, :url, :title, :price_pln, :area_m2, :rooms, :location,
        :description, :phone, :agent, :data, :now, :now, 1)
ON CONFLICT (source, id) DO UPDATE SET
    url = excluded.url, title = excluded.title, price_pln = excluded.price_pln,
    area_m2 = excluded.area_m2, rooms = excluded.rooms, location = excluded.location,
    description = excluded.description,
    phone = COALESCE(excluded.phone, listings.phone),
    agent = COALESCE(excluded.agent, listings.agent),
    data = excluded.data, last_seen = excluded.last_seen, active = 1
"""


def connect(path=DB_PATH):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute(SCHEMA)
    return connection


def save_listings(source, records, path=DB_PATH):
    """Upsert one complete scrape of `source`; listings missing from it become inactive."""
    if not records:
        raise ValueError("Refusing to save an empty scrape (would deactivate everything)")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect(path) as connection:
        before = {row[0] for row in connection.execute(
            "SELECT id FROM listings WHERE source = ? AND active = 1", (source,))}
        known = {row[0] for row in connection.execute(
            "SELECT id FROM listings WHERE source = ?", (source,))}
        for record in records:
            connection.execute(UPSERT, {
                "source": source,
                "id": str(record["id"]),
                "url": record.get("url"),
                "title": record.get("title"),
                "price_pln": record.get("price_pln"),
                "area_m2": record.get("area_m2"),
                "rooms": record.get("rooms"),
                "location": record.get("location"),
                "description": record.get("description") or record.get("description_preview"),
                "phone": record.get("phone"),
                "agent": record.get("agent"),
                "data": json.dumps(record, ensure_ascii=False),
                "now": now,
            })
        seen = {str(r["id"]) for r in records}
        gone = before - seen
        connection.executemany(
            "UPDATE listings SET active = 0 WHERE source = ? AND id = ?",
            [(source, listing_id) for listing_id in gone])
    print(f"DB [{source}]: {len(seen - known)} new, {len((seen & known) - before)} back, "
          f"{len(gone)} gone, {len(seen & before)} still listed")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser("import", help="Import a scraper JSON file as one complete scrape")
    load.add_argument("source", help="e.g. gratka, adresowo, olx")
    load.add_argument("file", type=Path)
    args = parser.parse_args()
    save_listings(args.source, json.loads(args.file.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
