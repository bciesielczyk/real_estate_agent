# Prototype: Otodom through a ready-made Apify actor

Branch `claude/proto-apify-otodom`. Code: `connectors/otodom_apify.py`, tests: `test_otodom_apify.py` (offline, mocked HTTP, synthetic data).

**Bottom line:** the actor works and is cheap, and it gives price, areas, rooms, description, photos, GPS and the listing agent's *name*. But on the owner's FREE Apify plan **it returned no phone numbers at all (0 of 25)**, and a free account is capped at 100 items per run while the Szczecin house search has 329 listings. Without phones the data is of limited use for this product, so this option is not yet viable as it stands (see "Decisions for the owner").

## Actor survey (Apify Store API, 2026-11-12)

`GET /v2/store?search=otodom` lists 108 actors (the first 37 were read; the rest have 2 or fewer users). Nearly all are pay-per-event; one is free (`skubadev`, 3 users). None needs a monthly rental. Run stats are the store's public 30-day numbers.

| Actor | Price (FREE plan) | Users (30 d) / runs (30 d) / success | Last modified | Phone and agent in the output (README/schema claims) | Plot area |
|---|---|---|---|---|---|
| `trev0n/otodom-scraper` | $0.002 / result | 174 (38) / 1306 / 96% | 2026-10-28 | `sellerPhone`, `agencyName`; no agent name | not listed |
| `fatihtahta/otodom-pl-scraper` | $0.00199 / result | 149 (6) / 79 / 91% | 2026-06-12 | `advertOwner.name`, `contacts[]` | not listed |
| **`memo23/otodom-scraper`** (chosen) | $0.009 start + $0.002 / result | 53 (15) / 230 / 98% | 2026-11-08 | `contactName` + `contactPhones[]` (agent's own mobile), `agency_*` | `plotAreaSqm` |
| `abotapi/otodom-pl-scraper` | $0.08 start + $0.0015 / result | 35 (5) / 99 / 96% | 2026-11-03 | `contactName`, `phone` | not listed |
| `one-api/otodom-scraper` | $0.00005 start + $0.003 / result | 15 (3) / 5050 / 100% | 2026-08-10 | "Agency / Owner", "Phone" columns | not listed |
| `ribtools/otodom-scraper` | $0.001 start + $0.003 / result + $0.002 seller add-on | 2 (1) / 31 / 100% | 2026-11-03 | agency office phone only; no agent or private-owner data on purpose (GDPR) | yes |

## Choice: `memo23/otodom-scraper`

On paper it is the only credible actor that promises the listing agent's name and direct mobile (separate from the agency line) together with plot area, full description, photos, a stable `listingId`/`canonicalUrl`, and that takes the portal's search URL unchanged. It is actively maintained (98% success over 230 runs in 30 days) and costs about $0.002 per listing. `trev0n` is bigger and slightly cheaper but has no agent name and no plot area. **The phone promise did not hold in practice** (below); the choice was made on documentation, which is all the store API shows.

## How to run

```sh
export APIFY_TOKEN=...   # Apify console -> Settings -> API & Integrations
uv run python -m connectors.otodom_apify /somewhere/outside/the/repo/sample.json   # capped sample (MAX_ITEMS=25), raw items to a file
uv run python scrape.py otodom_apify                                               # save to listings.db (only if complete, see below)
```

- Input sent to the actor: `startUrls = [https://www.otodom.pl/pl/wyniki/sprzedaz/dom/zachodniopomorskie/szczecin/szczecin/szczecin]` (the actor honours the URL's filters, so houses/for sale/Szczecin is expressed by the URL), `fetchDetails: true` (phones, description and plot area only come with detail pages), `sortBy: newest`, `maxItems`. Also sent as run options: `maxItems`, `maxTotalChargeUsd=0.25`, `timeout=600`.
- The connector starts the run, polls every 5 s, aborts it itself after 11 minutes, then reads the dataset. A missing `APIFY_TOKEN` fails before any request. The token is sent as `Authorization: Bearer`.
- `MAX_ITEMS` (default 25, or env `OTODOM_MAX_ITEMS`) bounds cost. **A real full scrape needs it raised above the number of listings** (329 on 2026-11-12; use e.g. 1000).
- **Completeness rule.** `db.save_listings` treats each scrape as complete and marks everything missing as inactive, so a capped run must never be saved. `scrape()` therefore raises `PartialScrape` (and `scrape.py` leaves the database untouched) when the actor returned `MAX_ITEMS` items or exactly 100 (the actor's cap on non-paying accounts, so a raised `MAX_ITEMS` cannot make a truncated free-plan run look complete). The run is paid for either way; this is the simplest correct option, mirroring how `scrape_gratka.py` refuses to save `--max-pages` runs. The sample entry point (`python -m ...`) never touches the database.

## What was actually run

Three actor runs in total (the limit), all for the Szczecin house search, all within the free plan. Account usage after the runs: $0.088 of the $5 monthly limit.

| # | Actor | Cap | Result | Time | Cost |
|---|---|---|---|---|---|
| 1 | memo23 | 5 | 5 items, same shape as run 2 | 17 s | $0.019 (1 start + 5 results) |
| 2 | memo23 | 25 | 25 items | 21.5 s | $0.059 (1 start + 25 results; no "additional data" charges) |
| 3 | trev0n | 5 | 5 items, no phone, no agent name | 29 s | $0.010 |

(Cost on the run object lags a few seconds behind `SUCCEEDED`: the first read of run 2 said $0.045. The connector waits one poll interval before the final read; the numbers above come from the run objects afterwards and match the price list exactly.)

**Run 2 (25 items, newest first), fields filled:**

| Field | Filled | Note |
|---|---|---|
| id, url, title, description, location, image, GPS, dates | 25/25 | descriptions 804 to thousands of characters, median about 2500 |
| agent name (`contactName`) | 25/25 | includes private owners' names (personal data) |
| agency name | 18/25 | the 7 without it are private sellers |
| price, area, rooms, plot area | 21/25 | the 4 gaps are developer/investment ads with hidden price |
| **phone (agent or agency)** | **0/25** | `contactPhones` and `agency_phones` were empty lists in every row; the actor log says "no phone" for each |

- Mix: 13 agency, 7 private, 5 developer ads; 21 houses and 4 "apartment" rows (developer ads, hidden price, no area), one of them in Rajkowo, outside Szczecin. The connector keeps everything and records `advertiser_type`; filtering developer ads is a decision for the owner.
- Duplicates: ids and URLs were unique; one pair of different ids looked like the same house (same district, area and price). No cross-listing dedup is done.
- Run 3 with a second actor (`trev0n`, the one that documents `sellerPhone`) also returned **0/5 phones**, and `sellerType` was "business" with no agent name. So the missing phone is probably not one actor's bug: Otodom does not put the number in the page data (the project's own OLX notes say it needs a visible browser and a click on "Pokaż numer"), and neither actor gets it on a free account. I could not tell whether a paid Apify plan or different proxy settings would change that; the memo23 README promises direct mobiles and I could not confirm it.
- Not seen: blocked runs or failed detail pages (`detailsFailed: 0`, no 403s).

**Not saved to `listings.db`:** run 2 was capped (25 of 329) and has no phones, so by the completeness rule it must not be saved. The real output was kept outside the repo (in a temporary directory) and no real names, numbers or listing data are committed.

## Cost extrapolation

Assumptions: 329 listings in the search (the number the actor logged), $0.009 per run + $0.002 per item, no extra charges (none appeared), same price on a paid plan (a paid plan may lower tiered prices on some other actors, not on this one).

- Full Szczecin scrape: $0.009 + 329 × $0.002 ≈ **$0.67**, about 5 minutes (0.86 s per item in run 2).
- Daily refresh for a month: 30 × $0.67 ≈ **$20/month**. The free credit is $5/month, i.e. about seven full scrapes. And a free account cannot do a full scrape at all (100-item cap), so any real use needs a paid Apify plan (check Apify's current plan prices).
- Cheaper refresh (not tested): the actor has a `monitoringMode` that returns, and bills, only new and re-priced listings. It returns only changes, so it does not fit "one complete scrape" in `db.save_listings` without extra code.

## Not verified

- Phones: whether any Apify plan/proxy setup returns them; whether the numbers would be correct.
- A complete run (329 items), the real duration of one, and whether a bigger run triggers blocking (DataDome).
- Monitoring mode, `fatihtahta`, `abotapi`, `one-api` (only their documentation was read).
- How stable the output field names are (the actor's author can change them at any time).
- `scrape.py otodom_apify` end to end against the real API with an uncapped run.

## Apify actor versus hand-written parsers

For: no parsing code to maintain, and the work of getting past Otodom's anti-bot system (DataDome) is someone else's; one HTTP call returned 25 clean, uniform records in 20 seconds; cost is small ($0.67 per full scrape). Against: it is a dependency on one stranger's actor (a hobby author can change fields, price or disappear; the store shows dozens of near-identical Otodom actors), it needs a paid plan for a complete scrape, and for this product the decisive field, the phone, was missing, which a hand-written approach with a visible browser (the OLX prototype) got at about 15 s per ad. The hand-written Gratka and Universum connectors cost nothing per run and return phones. My view: Apify is a good way to get listing *data* for Otodom quickly, but it does not solve the phone problem here, and it should not be the only source.

**Proxy and terms.** The actor uses its own built-in Polish residential proxy ("at no extra cost to you") and retries blocked pages on fresh proxy sessions; that is the actor circumventing the portal's bot protection on your behalf, which is exactly what the owner's rule for hand-written scrapers ("no stealth, no CAPTCHA solving") excludes. Having a third party scrape Otodom does not make Otodom's terms of use go away, and the data includes agents' and private owners' names (personal data under GDPR); this is not legal advice, but the owner should read Otodom's terms and decide whether they are comfortable with it. The actor's own disclaimer puts that responsibility on the user.

## Decisions for the owner

1. Is Otodom data without phones useful (e.g. to find houses, then get the number from the ad by hand or with the visible-browser OLX script)? If phones are a must, this route needs more investigation or a paid plan, with no guarantee.
2. Is it acceptable that the actor uses residential proxies to get past the portal's protection?
3. Is about $20 a month (plus a paid Apify plan) for a daily full refresh acceptable, or should the refresh use monitoring mode?
4. Keep developer/investment ads (about 20% of the sample, no price or area) or filter them out?
