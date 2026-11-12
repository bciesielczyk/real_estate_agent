# Firecrawl-type crawl tools for the listing connectors

Written 2026-11-12. Question: which "crawl + structured extraction" tool, if any, should replace hand-written parsers for agency sites?
Prices and limits are "as of today, per the cited source". Most are third-party summaries of vendor pages: the vendor sites (firecrawl.dev, jina.ai, spider.cloud, apify.com) could not be loaded from this sandbox. Licences and versions below were read from GitHub raw files and PyPI, which are primary.

## What this project needs

Small Polish agency sites (mostly server-rendered, some JS), a few now and tens later, at most hundreds of ad pages per day and only new ads extracted. Fields against a schema (price, area, plot, rooms, location, full description, agent name and phone). Phones and names are personal data, so a third party seeing the pages is a cost. The owner's rule is no anti-bot evasion. The project already has an Anthropic key.

## Comparison

| Tool | Runs locally | Licence | Own LLM key for schema extraction | Cost at ~300 pages/day (~9k/month) | Anti-bot angle | Third party sees pages |
|---|---|---|---|---|---|---|
| Firecrawl hosted | no | (service) | no, they run it | JSON format is +4 credits/page on top of 1 [1][2]; 9k pages x 5 = 45k credits, so a mid tier (Hobby ~3k and Standard ~100k credits, prices disagree: $16-19 and $83-99/mo) [1][3] | "Fire-engine" handles blocks and robot detection [4] | yes |
| Firecrawl self-hosted | yes (Docker Compose) | AGPL-3.0 [5] | yes: OpenAI-compatible or Ollama; no documented Anthropic route [4] | server + your LLM tokens | no Fire-engine, no anti-bot layer [4] | no |
| Crawl4AI | yes (pip) | Apache-2.0 [6] | optional: LLM strategy via LiteLLM (incl. Anthropic) [7]; CSS/XPath strategy needs none [8] | free; compute only | has stealth / `magic` options, off by default [8] | no |
| Jina Reader | API (open-source repo is Apache-2.0 [9] but is not the hosted engine) | Apache-2.0 repo | no extraction: markdown only | free tier ~10M tokens per key, 500 req/min with key, 20 without [10] | none advertised | yes |
| ScrapeGraphAI (library) | yes | MIT [11] | yes, always (LLM does the extraction); OpenAI, Ollama, Gemini, Groq... [12] | LLM tokens per page | none | only the LLM provider |
| ScrapeGraphAI (hosted) | no | service | no | SmartScraper 10 credits/page; Starter 10k credits for $20 [12] | - | yes |
| Spider | hosted (Rust core open) | MIT client [13] | no | usage-based, ~$0.001/min render + $1/GB; vendor claims $8-15 per 100k pages [14] | proxy rotation is a selling point | yes |
| Tavily Extract | no | service | no schema, returns content | 1 credit per 5 URLs basic, 2 per 5 advanced; free 1,000 credits/month [15] | none | yes |
| Apify Website Content Crawler | no (Apify platform; crawlee is the OSS part) | Apache-2.0 client | no schema | pay-per-use compute units, no per-page price found [16] | proxies available | yes |
| Scrapfly / ZenRows style APIs | no | service | no | not researched | anti-bot bypass is the product: a minus for this project | yes |

## Candidates in short

**Firecrawl hosted.** The most polished: one call returns markdown or JSON for a schema, with a Python SDK (`firecrawl-py` 4.55.0, MIT, PyPI). Cost is small at this volume. Against it: every agent name and phone passes through a US vendor, JSON extraction is billed at several times the plain scrape [2], and part of the value is its block-handling [4], which we do not want. Could not confirm a Polish/EU data-processing agreement.

**Firecrawl self-hosted.** AGPL-3.0 [5] is fine for a personal tool but would matter if this were ever offered as a service. The docs say it lacks Fire-engine and the cloud-only features, and extraction needs an OpenAI-compatible endpoint or Ollama [4]; I found no source for a working Anthropic setup. Several containers (API, Redis, workers, Playwright) for what we need is heavy.

**Crawl4AI.** A Python library: `pip install`, async crawler, markdown output, and two extraction modes: `JsonCssExtractionStrategy` (no LLM, deterministic) and `LLMExtractionStrategy` (any LiteLLM provider, so Anthropic works). `JsonCssExtractionStrategy.generate_schema()` can use an LLM once per site to draft a CSS schema that is then reused without any LLM [8]. Runs fully locally, Apache-2.0 (v0.9.5 on PyPI today). It has an HTTP-only strategy (no browser) for server-rendered sites and a Playwright strategy for JS sites. Its stealth/magic options exist but are opt-in. Minuses seen in practice: heavy install (about 96 packages, including scipy, a LiteLLM fork `unclecode-litellm`, and `patchright`, a stealth Playwright fork); a third-party article reports a release that had to replace a compromised litellm dependency [7a] (not verified); rough edges (see `docs/proto-crawl-tool.md`: the HTTP strategy crashed when a user agent was set in the run config, and ignores `HTTPS_PROXY` unless told).

**Jina Reader.** Turns a URL into markdown. Good for reading, but it does no schema extraction, so we would still need an LLM or parser after it, and each page goes through Jina. Out.

**ScrapeGraphAI.** Prompt-driven extraction through an LLM on every page: flexible, but a token cost per page forever, non-deterministic output, and needs Python >= 3.12 on PyPI. Its hosted API is a third party again. Weaker than Crawl4AI's "LLM once, then CSS" model for repeated sites.

**Spider, Tavily Extract, Apify WCC, Scrapfly/ZenRows.** Hosted. Spider and the Scrapfly/ZenRows family sell proxy rotation and bypassing protection, which counts against them under the owner's rule. Tavily Extract and Apify's crawler return page content rather than schema fields. None adds anything for the volume here, and all send agent data to a third party. (Apify actors are option 3 of the owner's comparison.)

## Recommendation

**Crawl4AI**, used mainly in its no-LLM mode: fetch with the HTTP strategy (browser strategy only for a JS-rendered site), extract with a per-site CSS schema, optionally let Claude draft the schema once per new site with `generate_schema` and a human reviews it. Reasons: local (agent data stays here), Apache-2.0, no key required for the working path, Python-native, fits the existing `connectors/` contract, pages cost nothing, and a broken site shows up as an empty field, not a silently different LLM answer. Where the LLM is wanted, LiteLLM covers the Anthropic key already in the project.

**Runner-up: Firecrawl self-hosted** if the owner wants one HTTP service for many tools (and accepts Docker, AGPL and the lack of a documented Anthropic route). Among hosted tools, Firecrawl cloud would be the pick, but it fails the "pages stay local" preference. If "no third party" were a hard rule, Crawl4AI is still the choice.

**Versus writing our own.** At the scale here, Crawl4AI's CSS schemas are in effect hand-written parsers in declarative form: the prototype needed the same amount of site-specific work (two selector schemas, about 25 lines) as the regex connector, and the Python mapping code is almost identical. Its real gains are (a) a more correct selector engine than regexes (it avoided a bug in the hand-written connector, see proto doc), (b) robots.txt checking and a JS-rendering path built in, and (c) the optional "LLM drafts the schema" step for onboarding new sites. Its costs are a large dependency tree and a young, fast-moving API. A home-made urllib + Claude extractor has no dependency weight but pays tokens per page and has no deterministic fallback. The comparison on real onboarding effort for tens of sites is not yet measured.

## Not confirmed from a primary source

All hosted prices (Firecrawl, Jina, ScrapeGraphAI, Spider, Tavily, Apify) come from search-result summaries and third-party/vendor-comparison blogs; several disagree. Firecrawl self-host Anthropic support, Crawl4AI's `magic` behaviour and the litellm incident are from secondary sources. Scrapfly/ZenRows were not researched in detail.

## Sources

1. [Firecrawl pricing (md)](https://www.firecrawl.dev/pricing.md) and [billing docs](https://docs.firecrawl.dev/billing) (search result, not loaded)
2. [Firecrawl pricing review, TinyFish](https://www.tinyfish.ai/blog/firecrawl-pricing)
3. [Firecrawl pricing, Apify blog](https://use-apify.com/blog/firecrawl-pricing-2026), [Decodo](https://decodo.com/blog/firecrawl-pricing)
4. [Firecrawl self-hosting docs](https://docs.firecrawl.dev/contributing/self-host.md) and [SELF_HOST.md](https://raw.githubusercontent.com/firecrawl/firecrawl/main/SELF_HOST.md)
5. [Firecrawl LICENSE (AGPL-3.0)](https://raw.githubusercontent.com/firecrawl/firecrawl/main/LICENSE)
6. [Crawl4AI LICENSE](https://raw.githubusercontent.com/unclecode/crawl4ai/main/LICENSE), [crawl4ai on PyPI](https://pypi.org/project/crawl4ai/) (0.9.5)
7. [Crawl4AI LLM strategies](https://docs.crawl4ai.com/extraction/llm-strategies/); [7a Crawl4AI guide mentioning the litellm hotfix](https://converter.brightcoding.dev/blog/crawl4ai-why-top-devs-ditch-paid-scrapers-for-this-open-source-beast)
8. [Crawl4AI no-LLM extraction](https://docs.crawl4ai.com/extraction/no-llm-strategies/)
9. [Jina reader LICENSE](https://raw.githubusercontent.com/jina-ai/reader/main/LICENSE)
10. [Jina Reader pricing summary](https://www.activepieces.com/blog/jina-ai-api-pricing-token-costs-limits-2026), [parallel.ai comparison](https://parallel.ai/articles/jina-ai-reader-vs-parallel)
11. [ScrapeGraphAI LICENSE (MIT)](https://raw.githubusercontent.com/ScrapeGraphAI/Scrapegraph-ai/main/LICENSE), [PyPI](https://pypi.org/project/scrapegraphai/) (2.3.1, Python >= 3.12)
12. [ScrapeGraphAI pricing blog](https://scrapegraphai.com/blog/scrapegraphai-pricing)
13. [spider-client on PyPI](https://pypi.org/project/spider-client/)
14. [Spider vs Firecrawl](https://spider.cloud/blog/spider-vs-firecrawl/) (vendor's own comparison)
15. [Tavily credits docs](https://docs.tavily.com/documentation/api-credits.md)
16. [Apify Website Content Crawler listing](https://apify.com/oneary/website-content-crawler/pricing)
