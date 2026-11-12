"""Otodom (otodom.pl): houses for sale in Szczecin, via a ready-made Apify actor.

PROTOTYPE. Nothing is scraped by this code: it starts the Apify actor `memo23/otodom-scraper`
(pay per result, no monthly rental) and maps its dataset to the connector contract. Apify's
servers fetch Otodom, through the actor's built-in Polish residential proxy; see
docs/proto-apify-otodom.md for what that means and what it costs.

Needs the APIFY_TOKEN environment variable (Apify console -> Settings -> API & Integrations).

Cost is bounded by MAX_ITEMS (sent to the actor as `maxItems` and to Apify as `maxItems` plus
`maxTotalChargeUsd`). db.save_listings treats a scrape as complete and deactivates everything
missing from it, so a run that hit the cap is NOT returned: scrape() raises PartialScrape and
scrape.py then leaves the database untouched. The search is complete only if the actor returned
fewer items than MAX_ITEMS. Raise MAX_ITEMS (or set OTODOM_MAX_ITEMS) above the number of houses
for sale for a real scrape. To look at a capped sample use `run()`:

    APIFY_TOKEN=... uv run python -m connectors.otodom_apify /tmp/sample.json
"""
import json
import os
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from connectors import normalize_phone

API = "https://api.apify.com/v2"
ACTOR = "memo23~otodom-scraper"
SEARCH_URL = "https://www.otodom.pl/pl/wyniki/sprzedaz/dom/zachodniopomorskie/szczecin/szczecin/szczecin"
MAX_ITEMS = int(os.environ.get("OTODOM_MAX_ITEMS", 25))
MAX_COST_USD = 0.25    # hard limit Apify enforces per run (pay-per-event stops charging at it)
RUN_TIMEOUT_SECS = 600  # Apify aborts the run after this
POLL_SECS = 5
WAIT_LIMIT_SECS = RUN_TIMEOUT_SECS + 60  # how long we wait before aborting the run ourselves


class PartialScrape(RuntimeError):
    """The run hit MAX_ITEMS, so listings are probably missing; it must not be saved as complete."""


def api(method, path, params=None, body=None):
    """Call the Apify API and return the parsed JSON. The token is read here, so a missing one fails before any request."""
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise RuntimeError("APIFY_TOKEN is not set (Apify console -> Settings -> API & Integrations)")
    url = f"{API}{path}" + (f"?{urlencode(params)}" if params else "")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urlopen(Request(url, data=data, headers=headers, method=method), timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:  # keep Apify's message (e.g. "not enough credit"), never the headers
        raise RuntimeError(f"Apify {method} {path}: HTTP {error.code} {error.read()[:300].decode('utf-8', 'replace')}") from None


def actor_input(max_items):
    return {
        "startUrls": [SEARCH_URL],
        "maxItems": max_items,
        "fetchDetails": True,   # phones, description and plot area only come with the detail pages
        "sortBy": "newest",
    }


def run(max_items=None):
    """Run the actor once. Returns (items, run_info) where run_info has status, seconds, cost_usd, run_id."""
    max_items = max_items or MAX_ITEMS
    started = api("POST", f"/acts/{ACTOR}/runs", {
        "maxItems": max_items, "maxTotalChargeUsd": MAX_COST_USD, "timeout": RUN_TIMEOUT_SECS,
    }, actor_input(max_items))["data"]
    run_id, deadline = started["id"], time.time() + WAIT_LIMIT_SECS
    while started["status"] in ("READY", "RUNNING"):
        if time.time() > deadline:
            api("POST", f"/actor-runs/{run_id}/abort")
            raise TimeoutError(f"Apify run {run_id} still running after {WAIT_LIMIT_SECS} s; aborted it")
        time.sleep(POLL_SECS)
        started = api("GET", f"/actor-runs/{run_id}")["data"]
    info = {"run_id": run_id, "status": started["status"], "cost_usd": started.get("usageTotalUsd"),
            "seconds": started.get("stats", {}).get("runTimeSecs"), "charged": started.get("chargedEventCounts")}
    print(f"Apify run {run_id}: {info['status']}, {info['seconds']} s, cost {info['cost_usd']} USD")
    if info["status"] != "SUCCEEDED":
        raise RuntimeError(f"Apify run {run_id} ended as {info['status']}: {started.get('statusMessage')}")
    items = api("GET", f"/datasets/{started['defaultDatasetId']}/items", {"format": "json", "clean": "true"})
    return items, info


def first(*values):
    return next((value for value in values if value not in (None, "", [])), None)


def phone_of(numbers):
    """First usable number of a list of phone strings."""
    return next((p for p in map(normalize_phone, numbers or []) if p), None)


def parse_item(item):
    photos = item.get("photos") or []
    photo = photos[0] if photos else None
    contact_phone = phone_of(item.get("contactPhones"))
    agency_phone = phone_of(item.get("agency_phones"))
    price = item.get("priceExact") if item.get("priceCurrency") in (None, "PLN") and not item.get("hidePrice") else None
    return {
        "id": str(item["listingId"]),
        "url": item.get("canonicalUrl"),
        "title": item.get("title"),
        "price_pln": price,
        "area_m2": item.get("areaSqm"),
        "plot_m2": item.get("plotAreaSqm"),
        "rooms": item.get("rooms"),
        "location": first(item.get("address_displayAddress"),
                          ", ".join(x for x in (item.get("address_street"), item.get("address_district"),
                                                item.get("address_city")) if x)),
        "description": item.get("description"),
        "image_url": photo.get("url") if isinstance(photo, dict) else photo,
        "agent": item.get("contactName"),
        "agency": item.get("agency_name"),
        "phone": first(contact_phone, agency_phone),  # the agent's own number, else the agency office line
        "agent_phone": contact_phone,
        "agency_phone": agency_phone,
        "advertiser_type": item.get("advertiserType"),  # agency / private / developer
        "is_private_owner": item.get("isPrivateOwner"),
        "date_created": item.get("dateCreated"),
        "date_modified": item.get("dateModified"),
        "latitude": item.get("latitude"),
        "longitude": item.get("longitude"),
    }


def scrape():
    items, info = run()
    if len(items) >= MAX_ITEMS:
        raise PartialScrape(f"Run {info['run_id']} returned {len(items)} items = MAX_ITEMS; the search is probably "
                            f"not exhausted, so not saving it as a complete scrape. Raise MAX_ITEMS.")
    return [parse_item(item) for item in items if item.get("listingId")]


if __name__ == "__main__":
    # Capped sample: write the raw dataset items (personal data!) to the given file, outside the repo.
    items, info = run()
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w", encoding="utf-8") as out:
            json.dump({"run": info, "items": items}, out, ensure_ascii=False, indent=1)
    print(f"{len(items)} items")
