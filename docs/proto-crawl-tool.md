# Prototype: Universum via Crawl4AI

Tool choice and reasoning: `docs/research-crawl-tools.md`.

## What was built

- `connectors/universum_crawl4ai.py` — source name `universum_crawl4ai`, so it sits beside `universum` in `listings.db`. Crawl4AI fetches (plain-HTTP strategy, no browser, `check_robots_txt=True`, honest User-Agent, 1 s pause) and extracts with two CSS schemas (`LIST_SCHEMA`, `OFFER_SCHEMA`). No LLM, no key. Python only maps the extracted dicts to the contract (`map_offer`) and normalises the phone with `connectors.normalize_phone`. `crawl4ai` is imported lazily, so the module and `scrape.py` listing work without it.
- `requirements-crawl4ai.txt` — `crawl4ai>=0.9,<1` (kept out of `requirements.txt`).
- `test_universum_crawl4ai.py` — synthetic fixtures (Jan Testowy, 500 000 000). Mapping tests always run; schema tests run only if crawl4ai is installed.

## Install and run

```sh
uv run --with-requirements requirements.txt --with-requirements requirements-crawl4ai.txt python scrape.py universum_crawl4ai
uv run --with-requirements requirements.txt --with-requirements requirements-crawl4ai.txt python -m unittest test_universum_crawl4ai
```

No `playwright install` is needed in HTTP mode. The connector passes `HTTPS_PROXY` from the environment to Crawl4AI if set (aiohttp ignores it otherwise); behind a TLS-inspecting proxy also set `SSL_CERT_FILE`.

## What was run (2026-11-12, Universum, 2 results pages + 18 offers)

Each connector ran once against the live site (about 20 polite requests each; one extra failed attempt hit only the first results page).

| | hand-written `universum` | `universum_crawl4ai` |
|---|---|---|
| listings | 18 | 18 (same ids) |
| run time | 41.9 s | 39.8 s (1 s pause dominates both) |

Field agreement over the 18 listings: `url`, `title`, `price_pln`, `area_m2`, `plot_m2`, `rooms`, `location`, `description` (full text), `agent`, `phone`, and the raw `details` dict all 18/18 identical. `image_url`: 16/18; the two differences are an error in the hand-written connector, not in Crawl4AI: for offers with no photo its regex continues past `mainThumb` and returns `https://www.universum.nieruchomosci.pl#`, while the Crawl4AI schema returns `None`. (Not fixed here; it is outside this branch's scope.) Raw outputs are not committed (they contain real agent data).

## What broke

1. `CrawlerRunConfig(user_agent=...)` with the HTTP strategy crashes (`'AsyncHTTPCrawlerStrategy' object has no attribute 'update_user_agent'`) in 0.9.5. Fixed by sending the UA via `HTTPCrawlerConfig(headers=...)`.
2. aiohttp-based HTTP strategy ignores `HTTPS_PROXY` (DNS failure in this sandbox). Fixed by passing `proxy_config`.
3. An attribute field with `"selector": ":scope"` on the base element matched nothing; omitting the selector works.
4. Both bugs and the 96-package install (scipy, a LiteLLM fork, patchright) suggest a fast-moving library; the version is pinned `<1`, and a tighter pin would be wise.

## Not verified

- LLM extraction (`LLMExtractionStrategy`, `generate_schema`) — no key here. The recommended workflow of "Claude drafts the CSS schema once per site" is untested.
- Browser mode (Playwright / `/opt/pw-browsers/chromium`) for JS-rendered sites — not exercised; Universum is server-rendered.
- Hosted tools (Firecrawl cloud, Jina, etc.) — unreachable; all their prices come from secondary sources.
- Behaviour over time (site layout change), and the effort to onboard a second, different agency site.
- That `check_robots_txt` was effective: it did not block the requests, but I did not inspect Universum's robots.txt.

## Honest view

For Universum alone this is not better than the hand-written parser: same result, same amount of site-specific code, a much heavier dependency tree, and an API with rough edges. It did expose a real bug in the regex parser, and CSS selectors are more robust than regexes on HTML. The value only appears when onboarding many sites: a declarative schema (possibly drafted by Claude once, reviewed by a person) is easier to review and test than a regex module, and unlike a Claude-per-page extractor it costs nothing per page and is deterministic. Versus a home-made urllib + Claude extractor: this wins on cost and determinism, loses on setup weight, and wins nothing on JS-free sites that BeautifulSoup (already a dependency) plus the same selectors could do with far less machinery. I would try the cheap alternative of the same CSS-schema idea with BeautifulSoup before adopting Crawl4AI wholesale; adopt Crawl4AI if several sites need JS rendering.
