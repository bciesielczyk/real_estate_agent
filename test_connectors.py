import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from connectors import ScrapeResult, available
from connectors.gratka import connector as gratka
from db import connect, save_listings
from scrape import run


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / 'listings.db'
        self.output = Path(self.temp.name) / 'snapshot.json'
        self.record = {'id': '1', 'url': 'https://agency.example/offer/1', 'title': 'Dom'}
        self.plugin = Mock(name='plugin')
        self.plugin.name = 'agency'

    def execute(self, records, complete=True):
        self.plugin.scrape.return_value = ScrapeResult(records, complete)
        return run(self.plugin, database=self.database, output=self.output)

    def test_discovery_and_gratka_adapter(self):
        self.assertIs(available()['gratka'], gratka)
        with patch('scrape_gratka.scrape', return_value=[self.record]) as fetch:
            self.assertFalse(gratka.scrape(max_pages=1).complete)
            fetch.assert_called_once_with(1, False, None)
            self.assertTrue(gratka.scrape().complete)

    def test_complete_import_and_source_isolation(self):
        save_listings('other', [self.record], self.database)
        self.execute([self.record])
        self.execute([dict(self.record, id='2')])
        with connect(self.database) as connection:
            actual = {tuple(row) for row in connection.execute('SELECT source,id,active FROM listings')}
        self.assertEqual(actual, {('other', '1', 1), ('agency', '1', 0), ('agency', '2', 1)})
        self.assertEqual(json.loads(self.output.read_text())[0]['id'], '2')

    def test_partial_run_does_not_touch_database(self):
        self.execute([self.record], complete=False)
        self.assertFalse(self.database.exists())
        self.assertTrue(self.output.exists())

    def test_failure_empty_and_invalid_records_preserve_previous_data(self):
        self.execute([self.record])
        previous = self.output.read_bytes()
        for records in ([], [self.record, self.record], [{'url': 'https://example.com'}], [{'id': '2'}]):
            with self.subTest(records=records), self.assertRaises(ValueError):
                self.execute(records)
            self.assertEqual(self.output.read_bytes(), previous)
        self.plugin.scrape.side_effect = OSError('network down')
        with self.assertRaises(OSError):
            run(self.plugin, database=self.database, output=self.output)
        with connect(self.database) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM listings WHERE active=1').fetchone()[0], 1)
        self.assertEqual(self.output.read_bytes(), previous)

    def test_invalid_limit_does_not_fetch(self):
        with self.assertRaises(ValueError):
            run(self.plugin, max_pages=0)
        self.plugin.scrape.assert_not_called()
