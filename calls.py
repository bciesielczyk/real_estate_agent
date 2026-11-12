"""Calls: which listings to phone, the questions to ask, and what came back.

One row per listing in the `calls` table of listings.db:
  status      to_call -> calling -> done | failed
  questions   JSON list of strings
  transcript  JSON list of {"role": "caller" | "broker", "text": str}   (caller = our AI)
  report      JSON {"summary": str, "answers": [{"question": str, "answer": str}]}
"""
import json
from datetime import datetime, timezone

from db import DB_PATH, connect as connect_listings

DEFAULT_QUESTIONS = [
    "Czy oferta jest nadal aktualna?",
    "Czy cena jest do negocjacji?",
    "Jaki jest stan prawny nieruchomości (księga wieczysta, obciążenia)?",
    "Jaki jest stan techniczny domu i co wymaga remontu?",
    "Kiedy można obejrzeć dom?",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    source          TEXT NOT NULL,
    id              TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'to_call',
    questions       TEXT NOT NULL,
    transcript      TEXT,
    report          TEXT,
    error           TEXT,
    conversation_id TEXT,
    created         TEXT NOT NULL,
    updated         TEXT NOT NULL,
    PRIMARY KEY (source, id)
)
"""
JSON_FIELDS = ("questions", "transcript", "report")


def connect(path=DB_PATH):
    connection = connect_listings(path)
    connection.execute(SCHEMA)
    return connection


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def mark_to_call(source, listing_id, questions=None, path=DB_PATH):
    """Queue a listing for a call (again): resets any earlier result."""
    questions = [q.strip() for q in (questions or DEFAULT_QUESTIONS) if q and q.strip()]
    if not questions:
        raise ValueError("At least one question is required")
    with connect(path) as connection:
        if not connection.execute("SELECT 1 FROM listings WHERE source = ? AND id = ?",
                                  (source, listing_id)).fetchone():
            raise KeyError(f"Unknown listing {source}:{listing_id}")
        connection.execute(
            """INSERT INTO calls (source, id, status, questions, created, updated)
               VALUES (?, ?, 'to_call', ?, ?, ?)
               ON CONFLICT (source, id) DO UPDATE SET
                   status = 'to_call', questions = excluded.questions, transcript = NULL,
                   report = NULL, error = NULL, conversation_id = NULL, updated = excluded.updated""",
            (source, listing_id, json.dumps(questions, ensure_ascii=False), now(), now()))


def unmark(source, listing_id, path=DB_PATH):
    with connect(path) as connection:
        connection.execute("DELETE FROM calls WHERE source = ? AND id = ?", (source, listing_id))


def update_call(source, listing_id, path=DB_PATH, **fields):
    """Set any of: status, transcript, report, error, conversation_id."""
    allowed = {"status", "transcript", "report", "error", "conversation_id"}
    if not fields or set(fields) - allowed:
        raise ValueError(f"Fields must be among {sorted(allowed)}")
    values = {k: json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS and v is not None else v
              for k, v in fields.items()}
    with connect(path) as connection:
        connection.execute(
            f"UPDATE calls SET {', '.join(f'{k} = ?' for k in values)}, updated = ? WHERE source = ? AND id = ?",
            (*values.values(), now(), source, listing_id))


def list_calls(status=None, path=DB_PATH):
    """Calls as dicts, each with the listing's title, url, price_pln, location, agent, phone."""
    query = """SELECT c.*, l.title, l.url, l.price_pln, l.area_m2, l.location, l.agent, l.phone
               FROM calls c JOIN listings l ON l.source = c.source AND l.id = c.id"""
    with connect(path) as connection:
        rows = connection.execute(query + (" WHERE c.status = ?" if status else "") + " ORDER BY c.created",
                                  (status,) if status else ()).fetchall()
    calls = []
    for row in rows:
        call = dict(row)
        for field in JSON_FIELDS:
            call[field] = json.loads(call[field]) if call[field] else None
        calls.append(call)
    return calls
