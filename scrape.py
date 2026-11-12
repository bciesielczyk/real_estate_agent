#!/usr/bin/env python3
"""Run a source connector and import only complete, nonempty snapshots."""
import argparse
from pathlib import Path

from connectors import available
from db import DB_PATH, save_listings
from scrape_gratka import write_json


def run(connector, *, max_pages=None, phones=False, output=None, database=DB_PATH):
    if max_pages is not None and max_pages < 1:
        raise ValueError('max_pages must be positive')
    result = connector.scrape(max_pages=max_pages, phones=phones)
    if not result.records:
        raise ValueError('Empty scrape; previous snapshot and database left untouched')
    ids = [str(record.get('id') or '') for record in result.records]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError('Missing or duplicate listing IDs')
    if any(not record.get('url') for record in result.records):
        raise ValueError('Missing listing URL')
    if result.complete:
        save_listings(connector.name, result.records, database)
    else:
        print('Partial scrape; database not updated')
    if output is not None:
        write_json(result.records, Path(output))
    return result


def main(argv=None):
    plugins = available()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sources', nargs='*', metavar='SOURCE')
    parser.add_argument('--list', action='store_true', help='List installed connectors')
    parser.add_argument('--max-pages', type=int, help='Limit requests; partial results do not update the database')
    parser.add_argument('--phones', action='store_true')
    parser.add_argument('--output', type=Path, help='Optional JSON snapshot (use ignored data/ directory)')
    parser.add_argument('--database', type=Path, default=DB_PATH)
    args = parser.parse_args(argv)
    if args.list or not args.sources:
        for name, plugin in plugins.items():
            print(f'{name}: {plugin.description}')
        return
    unknown = set(args.sources) - plugins.keys()
    if unknown:
        parser.error(f'Unknown sources: {", ".join(sorted(unknown))}')
    if args.output is not None and len(args.sources) > 1:
        parser.error('--output requires exactly one source')
    failed = False
    for source in args.sources:
        try:
            result = run(plugins[source], max_pages=args.max_pages, phones=args.phones,
                         output=args.output, database=args.database)
            print(f'{source}: {len(result.records)} listings; complete={result.complete}')
        except Exception as error:  # A broken site must not stop other sources (PR #1).
            failed = True
            print(f'{source}: scrape failed: {error}')
    if failed:
        parser.exit(1)


if __name__ == '__main__':
    main()
