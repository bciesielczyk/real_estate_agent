# Real estate agent

Collects house-for-sale listings in Szczecin, with agent phone numbers, into a local SQLite database and shows them on a local web page.

**MVP:** Gratka is the main data source (`scrape_gratka.py` → `listings.db` → `app.py`). Adresowo and OLX are secondary and not part of the MVP flow.

Requires [uv](https://docs.astral.sh/uv/) and Python 3.10+.

## Quick start

```sh
uv run python scrape_gratka.py --phones   # scrape Gratka into gratka_listings.json + listings.db
uv run python app.py                      # open http://127.0.0.1:8000
```

## Gratka scraper (`scrape_gratka.py`) — main source

Scrapes <https://gratka.pl/nieruchomosci/domy/szczecin> with plain HTTP — no browser, no extra dependencies, no paid services.

- **Listings** come from the schema.org JSON-LD embedded in each results page. Pagination is followed up to page 10, the maximum `robots.txt` allows.
- **Phones (`--phones`)** come from each ad page's embedded Nuxt data: the listing agent's own number (`agent_phone`) and the agency's general number (`agency_phone`). `phone` is the number to call: the agent's, or the agency's if there is no agent.
- **Speed:** requests are sequential with a one-second pause. The first run with `--phones` takes about 6–7 minutes for ~170 ads. Later runs reuse contacts from the previous `gratka_listings.json` and only fetch new ads, so they take seconds. Ads checked without a phone are not rechecked.
- **Output:** `gratka_listings.json` is replaced on each successful run; a failed run leaves it untouched.

Options: `--output FILE`, `--max-pages N`, `--phones`, `--phone-limit N` (testing).

Fields: `id`, `url`, `title`, `price_pln`, `area_m2`, `rooms`, `location`, `image_url`, `description_preview`, and with `--phones`: `agent`, `agent_phone`, `agency`, `agency_phone`, `phone`.

## Database (`db.py`, `listings.db`)

One SQLite file, one `listings` table keyed by `(source, id)`. After every **complete** Gratka scrape:

- new listings are inserted (`first_seen`);
- listings seen again are updated (`last_seen`);
- listings missing from the scrape are marked `active = 0`, never deleted;
- listings that reappear are reactivated.

Runs with `--max-pages` and empty scrapes don't touch the database, so they can't mark everything as gone. A phone number is kept if a later scrape doesn't return one.

Import an existing JSON file: `uv run python db.py import gratka gratka_listings.json`

Inspect it: `sqlite3 -header -column listings.db "SELECT id, price_pln, area_m2, location, agent, phone, active FROM listings LIMIT 20;"`, or open `listings.db` with the VS Code **SQLite Viewer** extension.

Duplicates: the same house can appear under several IDs (re-posted, or listed by several agencies). They are not merged yet.

## Web page (`app.py`, `static/index.html`)

`uv run python app.py`, then open <http://127.0.0.1:8000>. Lists active listings as cards (photo, price, area, price per m², location, agent with phone, link) with sorting, a "new" badge for the last 2 days, and a toggle for listings that disappeared. Listens on `127.0.0.1` only, because the data contains phone numbers.

## Other sources (not in the MVP)

- **Adresowo (`scraper.py`)** — lists <https://adresowo.pl/domy/szczecin/> via plain HTTP into `listings.json`. Not connected to the database. Phone numbers (`--login`, then `--phones`, using Playwright and your own session) only work for ads your account can see: free ads need paid "Pełny Dostęp".
- **OLX (`prototypes/olx_playwright/`)** — prototype. Plain HTTP gets 403, so it uses headless Playwright to read the listing data embedded in the page (82 ads). About 85% are Otodom ads; their phones (`--phones`) only work with a visible browser and take about 15 s per ad. OLX-only ads need a login for phones and are skipped.
- `prototypes/olx_saved_html/` is a superseded experiment.

## Privacy

`listings.db`, `gratka_listings.json`, `olx_listings.json`, `listings.json`, `data/` and `.auth/` contain phone numbers or sessions and are excluded from Git. Don't share or commit them.

## Tests

```sh
uv run --with-requirements requirements.txt python -m unittest -v
```

Covers the Adresowo scraper only; Gratka, the database and the web page have no automated tests yet.
