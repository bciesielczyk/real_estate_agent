#!/usr/bin/env python3
"""Local web app: lists listings from listings.db.

Run: uv run --with-requirements requirements.txt python app.py  ->  http://127.0.0.1:8000
"""
import json
import os
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import calls
from db import DB_PATH, connect

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "static" / "index.html"
HOST, PORT = "127.0.0.1", 8000  # local only: the data contains phone numbers


run_thread = None  # background thread running caller.run_pending()
run_error = None   # why the last run could not start at all (e.g. a missing package)
run_lock = threading.Lock()


def is_running():
    return run_thread is not None and run_thread.is_alive()


def call_mode():
    return "real" if os.environ.get("CALL_MODE") == "real" else "simulate"


def run_calls():
    global run_error
    run_error = None
    try:
        import caller  # lazy: only needed when calls are started
        caller.run_pending()
    except Exception as error:  # keep the server alive; the caller records per-call errors itself
        run_error = f"{type(error).__name__}: {error}"
        print(f"calls run failed: {error!r}")


def start_run():
    """Start the background run; False if one is already running."""
    global run_thread
    with run_lock:
        if is_running():
            return False
        run_thread = threading.Thread(target=run_calls, daemon=True)
        run_thread.start()
        return True


def list_listings(include_inactive=False):
    with connect(DB_PATH) as connection:
        rows = connection.execute(
            "SELECT * FROM listings" + ("" if include_inactive else " WHERE active = 1")
            + " ORDER BY first_seen DESC, price_pln"
        ).fetchall()
    statuses = {(c["source"], c["id"]): c["status"] for c in calls.list_calls(path=DB_PATH)}
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
            "call_status": statuses.get((row["source"], row["id"])),
        })
    return listings


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/calls":
            return self.mark_call()
        if path == "/api/calls/run":
            if not start_run():
                return self.send_json(409, {"error": "Rozmowy już trwają"})
            return self.send_json(202, {"running": True})
        if path != "/api/match":
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

    def mark_call(self):
        try:
            body = self.read_json()
            questions = body.get("questions") or []
            if not isinstance(questions, list) or not all(isinstance(q, str) for q in questions):
                raise ValueError("questions must be a list of strings")
            source, listing_id = body.get("source"), body.get("id")
            if not isinstance(source, str) or not isinstance(listing_id, str):
                raise ValueError("source and id are required")
            calls.mark_to_call(source, listing_id, questions, path=DB_PATH)
            self.send_json(200, {"ok": True})
        except KeyError as error:
            self.send_json(404, {"error": f"Nieznane ogłoszenie: {error.args[0]}"})
        except ValueError as error:  # includes JSONDecodeError
            self.send_json(400, {"error": f"Niepoprawne żądanie: {error}"})
        except sqlite3.Error as error:
            self.send_json(500, {"error": str(error)})

    def do_DELETE(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path != "/api/calls":
            return self.send(404, b"Not found", "text/plain")
        if "source" not in query or "id" not in query:
            return self.send_json(400, {"error": "Podaj source i id"})
        try:
            calls.unmark(query["source"][0], query["id"][0], path=DB_PATH)
            self.send_json(200, {"ok": True})
        except sqlite3.Error as error:
            self.send_json(500, {"error": str(error)})

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
            elif url.path == "/api/calls":
                self.send_json(200, {"calls": calls.list_calls(path=DB_PATH),
                                     "default_questions": calls.DEFAULT_QUESTIONS,
                                     "mode": call_mode(), "running": is_running(),
                                     "run_error": run_error})
            else:
                self.send(404, b"Not found", "text/plain")
        except (OSError, sqlite3.Error) as error:
            self.send(500, f"Error: {error}".encode(), "text/plain; charset=utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # log lines appear immediately, even when redirected
    print(f"http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
