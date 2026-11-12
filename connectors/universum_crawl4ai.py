"""Universum again, but fetched and extracted with Crawl4AI instead of urllib + regexes.

Prototype for comparing tools; the hand-written `universum` connector is the reference.
Crawl4AI does the fetching (its plain-HTTP strategy, no browser) and the extraction,
declaratively: two CSS schemas below, no LLM and no API key. Python only maps the
extracted dicts to the connector contract.

Install the extra package first (see requirements-crawl4ai.txt), then:
    uv run --with-requirements requirements.txt --with-requirements requirements-crawl4ai.txt \
        python scrape.py universum_crawl4ai

Politeness as in the other connector: one request per second, honest User-Agent,
robots.txt is checked by Crawl4AI. No stealth, magic or user simulation is switched on.
"""
import asyncio
import json
import os
import re

from connectors import HEADERS, normalize_phone
from connectors.universum import BASE, LIST, MAX_PAGES, number, text

# One row per offer link on a results page; the offer code sits in the onclick handler.
LIST_SCHEMA = {
    "name": "results",
    "baseSelector": "td[onclick*='row_click']",
    "fields": [{"name": "onclick", "type": "attribute", "attribute": "onclick"}],
}

# One record per offer page. `rows` is the th/td table, turned into a dict by map_offer.
OFFER_SCHEMA = {
    "name": "offer",
    "baseSelector": "body",
    "fields": [
        {"name": "agent", "selector": "#offerAgent b.blue2", "type": "text"},
        {"name": "agent_phone", "selector": "#offerAgent b:nth-of-type(3)", "type": "text"},
        {"name": "image", "selector": "#mainThumb a", "type": "attribute", "attribute": "href"},
        {"name": "description", "selector": "div.boxBG p", "type": "text"},
        {"name": "rows", "selector": "#offerMainDetails tr", "type": "list", "fields": [
            {"name": "key", "selector": "th", "type": "text"},
            {"name": "value", "selector": "td", "type": "text"},
        ]},
    ],
}


def parse_codes(extracted):
    """Offer codes from the list schema's output, in page order, without duplicates."""
    codes = (re.search(r"row_click\('/oferta/([^'?#]+)'\)", row.get("onclick") or "") for row in extracted)
    return list(dict.fromkeys(m.group(1) for m in codes if m))


def last_page(page_html):
    return max((int(n) for n in re.findall(r"porcja=(\d+)", page_html or "")), default=1)


def map_offer(extracted, code):
    """Offer schema output (a list with one dict, or the dict) -> connector record."""
    item = extracted[0] if isinstance(extracted, list) and extracted else extracted
    rows = (item or {}).get("rows") or []
    details = {text(r.get("key")).rstrip(":"): text(r.get("value")) for r in rows if r.get("key")}
    if not details:
        raise ValueError(f"Offer {code}: details table not found")
    district, kind = details.get("dzielnica - osiedle"), details.get("typ domu")
    rooms = number(details.get("liczba pokoi"))
    return {
        "id": code,
        "url": f"{BASE}/oferta/{code}",
        "title": ", ".join(x for x in ("Dom" if kind in (None, "dom", "inny") else f"Dom {kind}", district) if x),
        "price_pln": number(details.get("cena ofertowa PLN")),
        "area_m2": number(details.get("pow. użytkowa [m²]")) or number(details.get("pow. całkowita [m²]")),
        "plot_m2": number(details.get("pow. działki [m²]")),
        "rooms": int(rooms) if rooms else None,
        "location": ", ".join(x for x in (details.get("miejscowość"), district) if x),
        "image_url": BASE + item["image"] if item.get("image") else None,
        "description": text(item.get("description")) or None,
        "agent": text(item.get("agent")) or None,
        "phone": normalize_phone(text(item.get("agent_phone"))),
        "details": details,
    }


async def _crawl(urls, schema, pause=1.0):
    """Yield (url, CrawlResult) one at a time, `pause` seconds apart."""
    from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig, JsonCssExtractionStrategy
    from crawl4ai.async_configs import HTTPCrawlerConfig, ProxyConfig
    from crawl4ai.async_crawler_strategy import AsyncHTTPCrawlerStrategy

    strategy = AsyncHTTPCrawlerStrategy(browser_config=HTTPCrawlerConfig(headers=HEADERS))
    # aiohttp ignores HTTPS_PROXY on its own; pass the environment's proxy (if any) on explicitly.
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    config = CrawlerRunConfig(extraction_strategy=JsonCssExtractionStrategy(schema), cache_mode=CacheMode.BYPASS,
                              check_robots_txt=True, verbose=False,  # the UA goes out in the HTTP headers above
                              proxy_config=ProxyConfig(server=proxy) if proxy else None)
    async with AsyncWebCrawler(crawler_strategy=strategy) as crawler:
        for i, url in enumerate(urls):
            if i:
                await asyncio.sleep(pause)
            result = await crawler.arun(url, config=config)
            if not result.success:
                raise RuntimeError(f"{url}: {result.status_code} {result.error_message}")
            yield url, result


async def _scrape():
    codes, page, total = [], 1, 1
    while page <= min(total, MAX_PAGES):
        url = LIST.format(page=page)
        async for _, result in _crawl([url], LIST_SCHEMA):
            found = parse_codes(json.loads(result.extracted_content or "[]"))
            if not found:
                raise ValueError(f"No offers on results page {page}; refusing to save incomplete results")
            codes += [code for code in found if code not in codes]
            total = max(total, last_page(result.html))
            print(f"Page {page}/{total}: {len(found)} offers")
        page += 1
        await asyncio.sleep(1.0)
    records = []
    urls = [f"{BASE}/oferta/{code}" for code in codes]
    async for code, (_, result) in _zip_codes(codes, _crawl(urls, OFFER_SCHEMA)):
        records.append(map_offer(json.loads(result.extracted_content or "[]"), code))
        print(f"Offer {len(records)}/{len(codes)}: {code}")
    return records


async def _zip_codes(codes, results):
    for code in codes:
        yield code, await anext(results)


def scrape():
    return asyncio.run(_scrape())
