import unittest

from connectors.universum import number, parse_codes, parse_offer, last_page

RESULTS = """
<tr id="row_hint_1"><td onclick="return row_click('/oferta/UNI-DS-1');">
  <a href="/oferta/UNI-DS-1#mainPageContent"></a></td><td onclick="row_click('/oferta/UNI-DS-1')">Szczecin</td></tr>
<tr id="row_hint_2"><td onclick="return row_click('/oferta/SNS-DS-22');"></td></tr>
<a href="/pl/lista_ofert.php?szukaj=tak&amp;porcja=3&amp;kategoria=3">3</a>
"""

OFFER = """
<div id="offerAgent"><b>Makler odpowiedzialny za ofertę:</b><br />
  <b class="blue2">Jan Testowy</b> |tel. <b>48 500 000 000</b> | email: <a href="#">x</a></div>
<div id="mainThumb"><a rel="prettyPhoto[gallery]" href="/gallery/1.jpg"><img src="/gallery/1.jpg" /></a></div>
<table id="offerMainDetails">
  <tr id="headRow"><th>Numer oferty</th><td>UNI-DS-1</td></tr>
  <tr><th>miejscowość:</th><td><a href="/x">Szczecin</a></td></tr>
  <tr><th>dzielnica - osiedle:</th><td>Pogodno</td></tr>
  <tr><th>typ domu:</th><td>wolnostojący</td></tr>
  <tr><th class="bold">cena ofertowa PLN:</th><td class="red">3 700 000</td></tr>
  <tr><th>pow. całkowita [m&sup2;]:</th><td>447</td></tr>
  <tr><th>pow. użytkowa [m&sup2;]:</th><td>297,5000</td></tr>
  <tr><th>pow. działki [m&sup2;]:</th><td>716</td></tr>
  <tr><th>liczba pokoi:</th><td>5</td></tr>
</table>
<div class="boxBG"><img src="/images/pl/boxOpisOferty.gif" /><p>
Dom 297 m&amp;sup2; z ogrodem.</p></div>
"""


class UniversumTest(unittest.TestCase):
    def test_results_page(self):
        self.assertEqual(parse_codes(RESULTS), ["UNI-DS-1", "SNS-DS-22"])
        self.assertEqual(last_page(RESULTS), 3)

    def test_offer_page(self):
        record = parse_offer(OFFER, "UNI-DS-1")
        self.assertEqual(record["url"], "https://www.universum.nieruchomosci.pl/oferta/UNI-DS-1")
        self.assertEqual(record["title"], "Dom wolnostojący, Pogodno")
        self.assertEqual(record["location"], "Szczecin, Pogodno")
        self.assertEqual(record["price_pln"], 3700000)
        self.assertEqual(record["area_m2"], 297.5)  # usable area preferred over total
        self.assertEqual(record["plot_m2"], 716)
        self.assertEqual(record["rooms"], 5)
        self.assertEqual(record["agent"], "Jan Testowy")
        self.assertEqual(record["phone"], "+48500000000")
        self.assertEqual(record["image_url"], "https://www.universum.nieruchomosci.pl/gallery/1.jpg")
        self.assertEqual(record["description"], "Dom 297 m² z ogrodem.")

    def test_missing_fields_become_none(self):
        record = parse_offer('<table id="offerMainDetails"><tr><th>x:</th><td>y</td></tr></table>', "A-1")
        self.assertEqual(record["title"], "Dom")
        self.assertIsNone(record["price_pln"])
        self.assertIsNone(record["phone"])

    def test_offer_without_photo_has_no_image(self):
        page = OFFER.replace('<a rel="prettyPhoto[gallery]" href="/gallery/1.jpg"><img src="/gallery/1.jpg" /></a>', "")
        self.assertIn('<a href="#">', page)  # a later link must not be taken for the photo
        self.assertIsNone(parse_offer(page, "UNI-DS-1")["image_url"])

    def test_page_without_details_is_an_error(self):
        with self.assertRaises(ValueError):
            parse_offer("<html>blocked</html>", "A-1")

    def test_number(self):
        self.assertEqual(number("352,2000"), 352.2)
        self.assertIsNone(number(None))
        self.assertIsNone(number("*****"))


if __name__ == "__main__":
    unittest.main()
