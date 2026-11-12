# Real estate agent

Collects house-for-sale listings in Szczecin, with agent phone numbers, into a local SQLite database and shows them on a local web page.

**MVP:** Gratka is the main data source (`scrape_gratka.py` → `listings.db` → `app.py`). Adresowo and OLX are secondary and not part of the MVP flow.

Requires [uv](https://docs.astral.sh/uv/) and Python 3.10+.

## Quick start

```sh
uv run python scrape_gratka.py --phones   # scrape Gratka into gratka_listings.json + listings.db
uv run python scrape.py universum         # add an agency site (see Connectors)
uv run --with-requirements requirements.txt python app.py   # open http://127.0.0.1:8000
```

## Connectors (`connectors/`, `scrape.py`) — agency sites

A connector is one small module per site with a single `scrape()` function that returns a list of listing dicts (the contract is described in `connectors/__init__.py`). `scrape.py` runs it and stores the result in `listings.db` under the module's name:

```sh
uv run python scrape.py universum   # no name = list available connectors
```

- **Universum (`connectors/universum.py`)** — houses for sale in Szczecin from <https://www.universum.nieruchomosci.pl>. Plain HTTP, one request per second; reads each offer page for the full description, plot area, and the agent's name and phone. About 20 offers, under a minute.
- To add a site, copy `universum.py`, adapt the parsing, and add a test like `test_universum.py`. If a scrape fails or finds nothing, the database is left untouched.

Gratka is also available as a connector; its original script (below) still works.

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

`uv run --with-requirements requirements.txt python app.py`, then open <http://127.0.0.1:8000>. Lists active listings as cards (photo, price, area, price per m², location, agent with phone, link) with sorting, a "new" badge for the last 2 days, and a toggle for listings that disappeared. Listens on `127.0.0.1` only, because the data contains phone numbers.

## Calls and reports (`calls.py`, `caller.py`)

On a listing card, **Do zadzwonienia** queues the listing with a list of questions (five defaults, editable). The **Rozmowy** tab lists queued listings; **Zadzwoń do oznaczonych** runs the calls in the background and then shows, per listing, a summary, the answer to each question ("Nie ustalono" when the broker didn't say) and the transcript. From the command line: `uv run --with-requirements requirements.txt python caller.py`.

- **Simulation (default):** no phone call is made. Claude writes a plausible conversation with the broker from the listing data, and a second request extracts the answers. Needs `ANTHROPIC_API_KEY`.
- **Real calls (`CALL_MODE=real`):** an outbound call through an ElevenLabs Conversational AI agent with a Twilio number; needs `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, `ELEVENLABS_PHONE_NUMBER_ID`. The agent's own prompt is set in the ElevenLabs dashboard; what it must contain is described at the top of `caller.py`. **Not yet tested against the real ElevenLabs API** — only against a mocked one.
- The agent must say it is an AI and that the call is recorded. Brokers' numbers are personal data.
- One call per listing; listings of the same broker are not combined into one call yet.

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

Covers Adresowo, Universum, connector discovery and imports, the database, and the call workflow and API with mocked services.

## Konektory źródeł

Wspólny runner automatycznie wykrywa moduły w `connectors/`:

```sh
uv run python scrape.py --list
uv run python scrape.py gratka --max-pages 1 --output data/gratka-preview.json
uv run python scrape.py gratka --phones
```

Ostatnia komenda importuje pełny wynik do istniejącej bazy; aplikacja pokazuje
oferty wszystkich źródeł. Dotychczasowe `scrape_gratka.py` nadal działa.
Runner obsługuje również `--database FILE` do pracy na osobnej bazie.
Pliki wynikowe zawierające kontakty zapisuj w ignorowanym `data/`.

Dodanie biura:

1. Utwórz `connectors/nazwa_biura.py`, eksportujący obiekt `connector`.
2. Zaimplementuj interfejs `Connector` z `connectors/__init__.py`: stałe
   `name`, `description` oraz `scrape(max_pages=None, phones=False)`.
3. Zwróć `ScrapeResult(records, complete)`. Każdy rekord musi mieć stabilne
   `id` i `url`; pozostałe pola mają format Gratki (`title`, `price_pln`,
   `area_m2`, `rooms`, `location`, `image_url`, `description`, `agency`,
   `agent`, `phone`). `name` jest trwałym kluczem źródła w SQLite.
4. Ogranicz dane do **domów na sprzedaż w Szczecinie**, sprawdź paginację
   i przypisuj telefon do właściwej oferty. `complete=True` oznacza, że
   pobrano wszystkie strony tego zakresu. Limit stron, błąd pobrania lub
   nierozpoznany HTML nie mogą oznaczać kompletnego przebiegu.
5. Dodaj testy parsera oparte na rzeczywistym HTML oraz test paginacji.
   Nowy moduł zostanie wykryty bez zmian runnera i bazy.

Tylko kompletny, niepusty i poprawny wynik aktualizuje bazę oraz oznacza
zniknięte oferty **danego źródła**. Przebieg częściowy może zapisać JSON,
ale nie zmienia bazy. Błędy pobierania i walidacji pozostawiają poprzednie dane.
JSON jest zapisywany atomowo, osobno od transakcji SQLite; błąd zapisu JSON
po imporcie nie cofa udanej transakcji bazy.

Moduły z PR #1 eksportujące `scrape() -> list[dict]` są nadal wykrywane pod nazwą modułu. Muszą zwracać kompletny wynik albo zgłaszać błąd. Obsługują kontakty w ramach pełnego pobrania; `--max-pages` wymaga nowego interfejsu `Connector`. Universum odrzuca wyniki przekraczające limit stron. Runner przyjmuje wiele źródeł (`uv run python scrape.py universum gratka`) i kontynuuje po błędzie pojedynczego źródła. `--output` jest dostępne tylko dla jednego źródła. Brak argumentów lub `--list` pokazuje listę konektorów.

Konektor Świat Nieruchomości nie jest jeszcze zaimplementowany.
