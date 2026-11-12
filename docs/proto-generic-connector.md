# Prototype 1: our own generic connector (plain HTTP + Claude)

Branch `claude/proto-generic-connector`. Goal: add an agency site by giving a start URL instead of writing a parser.

## How it works

1. `connectors/llm_extract.py` fetches the results page with the existing `connectors.fetch` (plain HTTP, 1 request/second, no evasion).
2. The page is reduced to its links (`[n] /path | text`, same host only, `onclick="...('/offer/1')"` paths included). Claude returns the *numbers* of the ad links and of the next-page link, so it can never invent a URL. Pagination stops at `max_pages` (and raises if more pages remain).
3. Each ad page is reduced to visible text (no script/style/nav/header/footer/form, max 8000 chars) plus up to 4 image URLs. Claude fills the strict tool `submit_listing` (null for anything not on the page, "never invent"); the answer is validated with pydantic, retried once if invalid.
4. `id` and `url` are set in code (id = last URL path segment, or a hash if the URL has a query string); the phone goes through `normalize_phone`; `image_url` must be one of the URLs the model was shown.
5. `known` (records from the last run, keyed by URL) skips both the fetch and the model call, so repeat runs pay only for new ads. The returned list is always complete; any failure, page cap or ad cap raises instead of returning a partial list.
6. `connectors/universum_llm.py` is only configuration (Universum start URL + `known` loaded from `listings.db`), stored as source `universum_llm` next to the hand-written `universum`.

## Run

```sh
export ANTHROPIC_API_KEY=...
uv run --with-requirements requirements.txt python scrape.py universum_llm   # ~20 ads
uv run --with-requirements requirements.txt python eval_extract.py --limit 10
uv run --with-requirements requirements.txt python -m unittest
```

`eval_extract.py` compares, for the same ad pages, the hand-written parser with the Claude extraction: per-field agreement (numbers, phone, agent, url, image: exact; title/location/description: normalised, reported as same/close/differs and not counted as errors), listings fully correct, tokens, tokens per ad, estimated cost per ad and per 100 ads. It also checks that Claude finds the same ad links on results page 1. The prices at the top of the file are **placeholders**, not checked against current pricing. Nothing is written to disk.

## Measured on real Universum pages (no model involved)

Fetched 2 results pages and 6 ad pages (8 requests, 1/s, kept outside the repo). Tokens are estimated at about 3 characters per token for Polish text and URLs; there is no tokenizer here, so treat them as rough.

| Input to Claude | Raw HTML | Reduced | + instructions | ≈ tokens |
|---|---|---|---|---|
| Results page 1 (10 ads, 36 links) | 98,935 chars | 2,343 chars | ~2,800 chars | ~900 |
| Ad page, smallest of 6 | 84,857 | 2,536 | ~3,500 | ~1,200 |
| Ad page, typical (median of 6) | ~90,000 | ~5,000 | ~6,000 | ~2,000 |
| Ad page, largest of 6 | 93,095 | 8,285 | ~9,200 | ~3,100 |

Reduction is about 95%. Output is dominated by the description (up to 1,500 characters, roughly 500-700 tokens), which is the biggest cost lever (see DESCRIPTION_CHARS). On the real structure: all 10 ads of page 1 are found by the link reducer, including one that has no `<a>` link at all, only an `onclick` handler (the reason for the onclick handling); page 2 offers only a "previous page" link, so there is no next page.

## What was verified

- `uv run --with-requirements requirements.txt python -m unittest`: 94 tests pass (32 of them new, in `test_llm_extract.py`, no network, fake Claude client and fake fetcher with synthetic HTML): reduction, links/onclick, pagination, staying on host, page and ad caps, circular next-page, validation and retry, phone normalisation, nulls, the `known` cache (no fetch, no model call), failures midway raising, `known` loaded from the right source in the database, eval comparison logic.
- The reducers and the eval script ran end to end on the real saved pages. For that dry run the "model" was a scratch fake that returned the hand-written parser's values, so it tests the plumbing (page selection, link discovery, comparison, report), not the model.
- `scrape.py` without arguments lists `universum, universum_llm` (helper modules such as `llm_extract` are not listed).

## What could not be verified

- **Claude was never called.** There is no API key here. Extraction quality, the real token counts and the cost are unmeasured: run `eval_extract.py` first. Also unknown: whether `tool_choice` forcing, strict tools and nullable types behave as written against the live API (they follow the shape used in `matcher.py`, but nullable `["string","null"]` types in strict mode are untested here).
- `scrape.py universum_llm` end to end (needs the key; also writes to `listings.db`).
- Pricing: placeholders only.

## Strengths and weaknesses versus hand-written parsers

- (+) New site = a start URL; survives cosmetic markup changes; no per-site regex.
- (-) Costs money on every new ad and the quality is probabilistic (a wrong price is silently wrong); the eval is how you find out.
- (-) Slower: the same 1 request/second, plus one model call per new ad.
- (-) Findings from real pages: the hand-written parser returns the bogus image URL `https://www.universum.nieruchomosci.pl#` for ads without a photo (its `mainThumb` regex runs past the empty element); the generic version is shown a placeholder image (`_offerThumb.jpg`) as a candidate and must choose null. The eval will count that as a disagreement; look at it rather than trusting the number.
- (-) Known ads are never re-read, so a later price change is not noticed (add a refresh policy if that matters). Only same-host links are followed, so a site whose ads live on another host would find none (it raises "no offers").
- (-) Sites that load ads with JavaScript (no ads in the plain HTML) are out of reach; no browser, by design.
- (-) Text cut at 8000 characters per ad: a phone number placed after a very long description could be lost.

## Adding a second site

Create `connectors/<site>.py` with a start URL and `scrape()` copied from `universum_llm.py` (change `SOURCE`/`START_URL`; the source name must be unique). Then run the eval-style spot check by hand against 5-10 ads, since there is no hand-written ground truth for a new site: open the stored records in `listings.db` next to the pages. Check the robots.txt and terms of the site first.
