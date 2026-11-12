# Prototype: Otodom through a ready-made Apify actor

Status: in progress. Branch `claude/proto-apify-otodom`. Code: `connectors/otodom_apify.py`, tests: `test_otodom_apify.py`.

## Actor survey (Apify Store API, 2026-11-12)

`GET /v2/store?search=otodom` returns 108 actors (37 listed on the first page; the rest have 2 or fewer users). All are pay-per-event or pay-per-result; only one is free (`skubadev`, 3 users). None needs a monthly rental. Run stats are the store's public 30-day numbers.

| Actor | Price (FREE plan) | Users (30 d) / runs (30 d) / success | Last modified | Phone and agent in the output (from README/schema) | Plot area |
|---|---|---|---|---|---|
| `trev0n/otodom-scraper` | $0.002 / result | 174 (38) / 1306 / 96% | 2026-10-28 | `sellerPhone`, `agencyName`; no separate agent name | not listed |
| `fatihtahta/otodom-pl-scraper` | $0.00199 / result | 149 (6) / 79 / 91% | 2026-06-12 | `advertOwner.name`, `contacts[]` (phone not shown in the README sample) | not listed |
| **`memo23/otodom-scraper`** | $0.009 start + $0.002 / result (+ $0.001 "additional data") | 53 (15) / 230 / 98% | 2026-11-08 | `contactName` + `contactPhones[]` (agent's own mobile) and `agency_name` + `agency_phones[]` | `plotAreaSqm` |
| `abotapi/otodom-pl-scraper` | $0.08 start + $0.0015 / result | 35 (5) / 99 / 96% | 2026-11-03 | `contactName`, `phone` | not listed |
| `one-api/otodom-scraper` | $0.00005 start + $0.003 / result | 15 (3) / 5050 / 100% | 2026-08-10 | "Agency / Owner", "Phone" columns | not listed |
| `ribtools/otodom-scraper` | $0.001 start + $0.003 / result + $0.002 seller add-on | 2 (1) / 31 / 100% | 2026-11-03 | agency office phone only; deliberately no agent or private-owner data (GDPR) | yes |

## Choice: `memo23/otodom-scraper`

It is the only one of the credible actors whose documentation promises both the listing agent's name and direct mobile (separate from the agency line), the plot area, the full description, photos, and a stable `listingId`/`canonicalUrl`, and it takes the portal's search URL as is. It is actively maintained (modified 4 days ago, 98% success over 230 runs) and cheap: about $0.003 per listing with details. `trev0n` is bigger and cheaper but gives no agent name and no plot area.

(Results of the real run, cost, fields filled, extrapolation, and the opinion section are added below once the run is done.)
