"""Universum through the generic Claude-based extractor, for comparison with universum.py.

Same site, no parser: only the start URL. Stored under its own source name (`universum_llm`)
so both versions can sit side by side in listings.db. Needs ANTHROPIC_API_KEY.
"""
import json

from connectors import llm_extract
from connectors.universum import LIST
from db import connect

START_URL = LIST.format(page=1)
SOURCE = "universum_llm"


def load_known(source=SOURCE, path=None):
    """Records of a previous run, keyed by URL, so that repeat runs only pay for new ads."""
    with (connect(path) if path else connect()) as connection:
        rows = connection.execute("SELECT data FROM listings WHERE source = ?", (source,)).fetchall()
    records = [json.loads(row[0]) for row in rows]
    return {record["url"]: record for record in records if record.get("url")}


def scrape():
    records, usage = llm_extract.scrape_site(START_URL, known=load_known())
    print(f"Claude usage: {usage}")
    return records
