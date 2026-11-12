"""Generic listing extractor: plain HTTP fetches the pages, Claude reads them.

Not a connector itself (no `scrape()`): a site connector is a few lines of configuration
that call `scrape_site(start_url, known=...)`, see `universum_llm.py`.

How it works
- The results page is reduced to its links (one line each) and Claude picks the ad links
  and the next-page link by number. Claude never writes a URL, so it cannot invent one.
- Each ad page is reduced to its visible text plus a few image URLs and Claude fills a
  strict tool (`submit_listing`); the answer is validated with pydantic.
- `id` and `url` come from code. Ads already in `known` are not fetched or sent to Claude.
- The returned list is always complete or the call raises; the database deactivates
  listings missing from a scrape, so a partial list must never be returned.
"""
import hashlib
import html
import re
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from connectors import fetch, normalize_phone

MODEL = "claude-haiku-5-5"  # cheapest current model, same as matcher.py
MAX_LINKS = 300         # links shown to the model per results page
MAX_LINK_TEXT = 60      # characters of anchor text per link
MAX_AD_CHARS = 8000     # visible text sent per ad page (about 2500 tokens); a hard cost bound
MAX_IMAGES = 4          # image URL candidates shown per ad page
DESCRIPTION_CHARS = 1500  # the model is asked to copy at most this much of the description

NOISE_TAGS = ["script", "style", "noscript", "svg", "iframe", "form", "nav", "header", "footer",
              "head", "select", "button", "template"]
ONCLICK_URL = re.compile(r"""['"]((?:https?:)?/[^'"\s]+)['"]""")
IMAGE_EXT =re.compile(r"\.(jpe?g|png|webp)(\?|$)", re.I)

LINKS_SYSTEM = """You read the results page of a real-estate agency website. You get its links, one per line, as `[number] path | text`.
Call submit_links with:
- ad_links: the numbers of links that open ONE property offer page (not categories, sorting, filters, printing, contact or other navigation). Include every offer on the page, once.
- next_page: the number of the link to the NEXT page of results, or null if there is none (never the previous or current page)."""

LISTING_SYSTEM = f"""You read the text of one property offer page from a real-estate agency website and record it with submit_listing.
Rules:
- Use only what the page says. Never invent, guess or calculate a value; use null for anything the page does not state.
- Keep text in the page's language.
- price_pln: the asking price in PLN as a number. area_m2: the usable (living) area, or the total area if no usable area is given. plot_m2: plot size. rooms: number of rooms, an integer.
- location: city and district/street as written. title: a short title (the page's own headline if there is one).
- description: the offer's description copied from the page, at most {DESCRIPTION_CHARS} characters, without paraphrasing.
- agent and phone: the person responsible for this offer and their phone number exactly as written (not the agency's general number unless it is the only one).
- image_url: one of the image URLs listed under IMAGES that is the main photo of the property, or null."""

NULLABLE = lambda kind, text: {"type": [kind, "null"], "description": text}  # noqa: E731

LINKS_TOOL = {
    "name": "submit_links",
    "description": "Submit which links are offer pages and which is the next results page.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "ad_links": {"type": "array", "items": {"type": "integer"}, "description": "Numbers of offer-page links"},
            "next_page": NULLABLE("integer", "Number of the next-results-page link, or null"),
        },
        "required": ["ad_links", "next_page"],
        "additionalProperties": False,
    },
}

LISTING_FIELDS = {
    "title": NULLABLE("string", "Short title"),
    "price_pln": NULLABLE("number", "Asking price in PLN"),
    "area_m2": NULLABLE("number", "Usable area in square metres"),
    "plot_m2": NULLABLE("number", "Plot area in square metres"),
    "rooms": NULLABLE("integer", "Number of rooms"),
    "location": NULLABLE("string", "City and district/street"),
    "description": NULLABLE("string", "Description copied from the page"),
    "image_url": NULLABLE("string", "Main photo, one of the listed IMAGES"),
    "agent": NULLABLE("string", "Agent responsible for the offer"),
    "phone": NULLABLE("string", "Agent's phone as written on the page"),
}
LISTING_TOOL = {
    "name": "submit_listing",
    "description": "Submit the data of one property offer; null for anything not on the page.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": LISTING_FIELDS,
        "required": list(LISTING_FIELDS),
        "additionalProperties": False,
    },
}


class LinksAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ad_links: list[int]
    next_page: int | None


class ListingAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None
    price_pln: float | None = Field(ge=0)
    area_m2: float | None = Field(ge=0)
    plot_m2: float | None = Field(ge=0)
    rooms: int | None = Field(ge=0)
    location: str | None
    description: str | None
    image_url: str | None
    agent: str | None
    phone: str | None

    @field_validator("title", "location", "description", "image_url", "agent", "phone")
    @classmethod
    def blank_is_none(cls, value):
        value = (value or "").strip()
        return value or None


# --- HTML reduction -------------------------------------------------------------------

def host_of(url):
    return urlsplit(url).netloc.lower().removeprefix("www.")


def reduce_links(page, base_url):
    """Results page -> [(absolute url, anchor text)] of http(s) links on the same host, deduplicated."""
    links = {}

    def add(href, label):
        url = urldefrag(urljoin(base_url, href.strip()))[0]
        if urlsplit(url).scheme not in ("http", "https") or host_of(url) != host_of(base_url):
            return
        label = re.sub(r"\s+", " ", label or "").strip()[:MAX_LINK_TEXT]
        if url not in links or (label and not links[url]):
            links[url] = label

    soup = BeautifulSoup(page, "html.parser")
    for tag in soup.find_all(True):
        if tag.name == "a" and tag.get("href"):
            image = tag.find("img")
            add(tag["href"], tag.get_text(" ", strip=True) or tag.get("title") or (image.get("alt") if image else ""))
        # Some sites open rows with onclick="row_click('/offer/123')" instead of a link.
        for href in ONCLICK_URL.findall(tag.get("onclick") or ""):
            add(href, tag.get_text(" ", strip=True))
    return list(links.items())[:MAX_LINKS]


def links_text(links, base_url):
    """The model's view of a results page: `[n] /path?query | text`."""
    def short(url):
        parts = urlsplit(url)
        return parts.path + (f"?{parts.query}" if parts.query else "")
    return "\n".join(f"[{i}] {short(url)} | {label}" for i, (url, label) in enumerate(links))


def reduce_ad(page, base_url):
    """Ad page -> (visible text without scripts/navigation noise, image URL candidates)."""
    soup = BeautifulSoup(page, "html.parser")
    images = []
    for tag, attr in [(t, "src") for t in soup.find_all("img")] + [(t, "href") for t in soup.find_all("a")]:
        url = urldefrag(urljoin(base_url, (tag.get(attr) or "").strip()))[0]
        if IMAGE_EXT.search(url) and url not in images:
            images.append(url)
    meta = soup.find("meta", attrs={"property": "og:image"})
    if meta and meta.get("content"):
        images.insert(0, urljoin(base_url, meta["content"]))
    for tag in soup(NOISE_TAGS):
        tag.decompose()
    for tag in soup.find_all(attrs={"role": ["navigation", "banner", "contentinfo"]}):
        tag.decompose()
    text = html.unescape(soup.get_text(" "))  # some sites double-escape, e.g. `&amp;sup2;`
    text = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return text[:MAX_AD_CHARS], list(dict.fromkeys(images))[:MAX_IMAGES]


def ad_message(text, images):
    return "IMAGES:\n" + "\n".join(images) + "\n\nPAGE TEXT:\n" + text


# --- stable ids -----------------------------------------------------------------------

def make_id(url):
    """Stable id from the URL: the last path segment, or a hash when the URL has a query string."""
    parts = urlsplit(urldefrag(url)[0])
    if parts.query:
        return hashlib.sha1(f"{parts.path}?{parts.query}".encode()).hexdigest()[:12]
    segment = parts.path.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"\.\w+$", "", segment) or hashlib.sha1(url.encode()).hexdigest()[:12]


# --- model calls ----------------------------------------------------------------------

def new_usage():
    return {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0, "model_calls": 0, "pages_fetched": 0, "ads_cached": 0}


def call_tool(client, system, tool, content, usage, max_tokens, answer_class):
    """One forced tool call, validated. Retries once if the answer is invalid; API errors propagate."""
    for attempt in range(2):
        response = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            system=system,
            messages=[{"role": "user", "content": content}],
        )
        usage["model_calls"] += 1
        for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            usage[key] += getattr(response.usage, key, 0) or 0
        try:
            if response.stop_reason == "max_tokens":
                raise ValueError("answer was cut off")
            call = next((b for b in response.content if b.type == "tool_use" and b.name == tool["name"]), None)
            if call is None:
                raise ValueError(f"model did not call {tool['name']}")
            return answer_class.model_validate(call.input)
        except (ValueError, ValidationError) as error:  # ValidationError is a ValueError; kept explicit
            if attempt:
                raise RuntimeError(f"{tool['name']}: invalid answer twice: {error}") from error


def find_links(client, links, base_url, usage):
    """Ask Claude which links are ads and which is the next page. Returns (ad urls, next url or None)."""
    answer = call_tool(client, LINKS_SYSTEM, LINKS_TOOL, links_text(links, base_url), usage, 4000, LinksAnswer)
    wanted = [*answer.ad_links, *([answer.next_page] if answer.next_page is not None else [])]
    if any(not 0 <= n < len(links) for n in wanted):
        raise RuntimeError(f"Model returned a link number that is not on the page: {wanted}")
    ads = list(dict.fromkeys(links[n][0] for n in answer.ad_links))
    nxt = links[answer.next_page][0] if answer.next_page is not None else None
    return ads, nxt


def extract_ad(client, page, url, usage):
    """One ad page -> contract record. id and url come from code; phone is normalised in code."""
    text, images = reduce_ad(page, url)
    answer = call_tool(client, LISTING_SYSTEM, LISTING_TOOL, ad_message(text, images), usage, 4000, ListingAnswer)
    record = answer.model_dump()
    record["description"] = (record["description"] or "")[:DESCRIPTION_CHARS] or None
    if record["image_url"] not in images:  # the model may only choose among the URLs it was shown
        record["image_url"] = None
    record["phone"] = normalize_phone(record["phone"])
    return {"id": make_id(url), "url": url, **record}


# --- the engine -----------------------------------------------------------------------

def scrape_site(start_url, known=None, client=None, max_pages=5, max_ads=100, fetcher=fetch):
    """Complete scrape of a results listing -> (records, usage). Raises rather than return a partial list.

    known: {ad url: record} from a previous run; those ads are neither fetched nor sent to Claude.
    """
    if client is None:
        import anthropic  # imported here so that other connectors don't need the SDK
        client = anthropic.Anthropic()
    known, usage = known or {}, new_usage()
    ad_urls, visited, page_url = [], set(), urldefrag(start_url)[0]
    while page_url:
        if len(visited) >= max_pages:
            raise RuntimeError(f"More result pages after the {max_pages}-page cap; refusing to return a partial list")
        visited.add(page_url)
        links = reduce_links(fetcher(page_url), page_url)
        usage["pages_fetched"] += 1
        ads, nxt = find_links(client, links, page_url, usage)
        if not ads:
            raise ValueError(f"No offers found on results page {page_url}; refusing to save incomplete results")
        fresh = [url for url in ads if url not in ad_urls]
        ad_urls += fresh
        print(f"Results page {len(visited)}: {len(ads)} offers, {len(fresh)} new")
        # A "next" page that was already visited, or that shows nothing new, means we went in a circle.
        page_url = nxt if nxt and nxt not in visited and fresh else None
    if len(ad_urls) > max_ads:
        raise RuntimeError(f"{len(ad_urls)} offers found, more than max_ads={max_ads}; refusing to return a partial list")
    records = []
    for i, url in enumerate(ad_urls, 1):
        if url in known:
            records.append(known[url])
            usage["ads_cached"] += 1
            continue
        records.append(extract_ad(client, fetcher(url), url, usage))
        usage["pages_fetched"] += 1
        print(f"Offer {i}/{len(ad_urls)}: {records[-1]['id']}")
    return records, usage
