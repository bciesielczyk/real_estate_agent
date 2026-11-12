"""Source plugins exporting a Connector object or a complete-snapshot scrape() function.

Records need stable id and url fields; other fields use the listing database format.
Legacy module names remain their SQLite source keys.
"""
from dataclasses import dataclass
from importlib import import_module
from pkgutil import iter_modules
import re
import time
from urllib.request import Request, urlopen
from typing import Protocol
from types import ModuleType


@dataclass(frozen=True)
class ScrapeResult:
    records: list[dict]
    # True only after exhausting the selected source's complete search results.
    complete: bool


class Connector(Protocol):
    name: str  # Stable SQLite source key; never rename after importing data.
    description: str

    def scrape(self, *, max_pages: int | None = None, phones: bool = False) -> ScrapeResult:
        """Raise on fetch/parse failure; never return an incomplete result as complete."""
        ...


@dataclass
class ModuleConnector:
    """Adapt PR #1's complete-snapshot scrape() modules without changing them."""
    module: ModuleType
    name: str

    @property
    def description(self):
        return (self.module.__doc__ or self.name).splitlines()[0]

    def scrape(self, *, max_pages=None, phones=False):
        if max_pages is not None:
            raise ValueError(f'{self.name}: --max-pages requires a Connector implementation')
        # Legacy modules already read contacts as part of the full scrape.
        return ScrapeResult(self.module.scrape(), complete=True)


def available() -> dict[str, Connector]:
    """Discover plugins without editing a central registry or the command runner."""
    found = {}
    for module in iter_modules(__path__):
        if module.name.startswith('_'):
            continue
        loaded = import_module(f'{__name__}.{module.name}')
        plugin = getattr(loaded, 'connector', None)
        if plugin is None and callable(getattr(loaded, 'scrape', None)):
            plugin = ModuleConnector(loaded, module.name)
        if plugin is None:
            continue
        if plugin.name in found:
            raise ValueError(f'Duplicate connector name: {plugin.name}')
        found[plugin.name] = plugin
    return found


HEADERS = {"User-Agent": "RealEstateAgent/1.0 (personal use)"}


def fetch(url, pause=1.0):
    """GET a page as text, after a polite pause."""
    time.sleep(pause)
    with urlopen(Request(url, headers=HEADERS), timeout=30) as response:
        return response.read().decode(response.headers.get_content_charset() or "utf-8")


def normalize_phone(text):
    """Return the number as +48123456789, or None if it doesn't look like a phone."""
    phone = re.sub(r"[^\d+]", "", text or "")
    if phone.startswith("00"):
        phone = "+" + phone[2:]
    elif re.fullmatch(r"\d{9}", phone):
        phone = "+48" + phone
    elif re.fullmatch(r"48\d{9}", phone):
        phone = "+" + phone
    return phone if re.fullmatch(r"\+[1-9]\d{7,14}", phone) else None
