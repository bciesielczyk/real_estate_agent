"""Agency/source plugins. Each module exports a `connector` implementing Connector."""
from dataclasses import dataclass
from importlib import import_module
from pkgutil import iter_modules
from typing import Protocol


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


def available() -> dict[str, Connector]:
    """Discover plugins without editing a central registry or the command runner."""
    found = {}
    for module in iter_modules(__path__):
        if module.name.startswith('_'):
            continue
        plugin = getattr(import_module(f'{__name__}.{module.name}'), 'connector', None)
        if plugin is None:
            continue
        if plugin.name in found:
            raise ValueError(f'Duplicate connector name: {plugin.name}')
        found[plugin.name] = plugin
    return found
