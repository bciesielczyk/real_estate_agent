#!/usr/bin/env python3
"""Local web app: lists listings from listings.db.

Run: uv run python app.py  ->  http://127.0.0.1:8000
"""
import json
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from db import DB_PATH, connect

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "static" / "index.html"
HOST, PORT = "127.0.0.1", 8000  # local only: the data contains phone numbers


def list_listings(include_inactive=False):
    with connect(DB_PATH) as connection:
        rows = connection.execute(
            "SELECT * FROM listings" + ("" if include_inactive else " WHERE active = 1")
            + " ORDER BY first_seen DESC, price_pln"
        ).fetchall()
    listings = []
    for row in rows:
        data = json.loads(row["data"])
        listings.append({
            "source": row["source"], "id": row["id"], "url": row["url"], "title": row["title"],
            "price_pln": row["price_pln"], "area_m2": row["area_m2"], "rooms": row["rooms"],
            "location": row["location"], "phone": row["phone"], "agent": row["agent"],
            "agency": data.get("agency"), "image_url": data.get("image_url"),
            "description": row["description"], "first_seen": row["first_seen"],
            "last_seen": row["last_seen"], "active": bool(row["active"]),
        })
    return listings


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urlparse(self.path).path != "/api/match":
            return self.send(404, b"Not found", "text/plain")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            criteria = (json.loads(self.rfile.read(length) or b"{}").get("criteria") or "").strip()
            if not criteria:
                return self.send_json(400, {"error": "Podaj kryteria"})
            listings = list_listings()
            if not listings:
                return self.send_json(400, {"error": "Brak ogłoszeń w bazie"})
            from matcher import match_listings
            result = match_listings(criteria[:2000], listings)
            print(f"match: {len(result['matches'])} results, usage {result['usage']}")
            self.send_json(200, result)
        except json.JSONDecodeError:
            self.send_json(400, {"error": "Niepoprawne żądanie"})
        except Exception as error:  # surface API/auth errors to the page
            print(f"match failed: {error!r}")
            self.send_json(502, {"error": f"{type(error).__name__}: {error}"})

    def send_json(self, status, payload):
        self.send(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        url = urlparse(self.path)
        try:
            if url.path == "/":
                self.send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/listings":
                inactive = parse_qs(url.query).get("inactive") == ["1"]
                body = json.dumps(list_listings(inactive), ensure_ascii=False).encode()
                self.send(200, body, "application/json; charset=utf-8")
            else:
                self.send(404, b"Not found", "text/plain")
        except (OSError, sqlite3.Error) as error:
            self.send(500, f"Error: {error}".encode(), "text/plain; charset=utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # log lines appear immediately, even when redirected
    print(f"http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
