"""Adapter preserving the existing Gratka scraper and command line."""
import scrape_gratka
from connectors import ScrapeResult


class Gratka:
    name = 'gratka'
    description = 'Gratka — domy na sprzedaż w Szczecinie'

    def scrape(self, *, max_pages=None, phones=False):
        limit = max_pages if max_pages is not None else scrape_gratka.MAX_PAGE
        records = scrape_gratka.scrape(limit, phones, None)
        # Keep the existing conservative rule: limited runs never retire ads.
        return ScrapeResult(records, limit >= scrape_gratka.MAX_PAGE)


connector = Gratka()
