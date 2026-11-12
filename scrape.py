#!/usr/bin/env python3
"""Run connectors and store their listings in listings.db.

Usage: uv run python scrape.py universum     (several names allowed; no name = list them)
"""
import importlib
import pkgutil
import sys
from pathlib import Path

import connectors
from db import save_listings


def main():
    # Only modules that define scrape(); helpers such as llm_extract are not sites.
    folder = Path(connectors.__path__[0])
    available = sorted(m.name for m in pkgutil.iter_modules(connectors.__path__)
                       if "def scrape(" in (folder / f"{m.name}.py").read_text(encoding="utf-8"))
    names = sys.argv[1:]
    if not names or set(names) - set(available):
        sys.exit(f"Usage: python scrape.py NAME...   Available connectors: {', '.join(available)}")
    failed = False
    for name in names:
        try:
            records = importlib.import_module(f"connectors.{name}").scrape()
            save_listings(name, records)
        except Exception as error:  # one broken site must not stop the others
            failed = True
            print(f"{name}: scrape failed, database left untouched: {error!r}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
