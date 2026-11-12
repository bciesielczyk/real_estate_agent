# PROTOTYPE: parser zapisanych stron OLX.pl

1. Otwórz w przeglądarce listę: https://www.olx.pl/nieruchomosci/domy/sprzedaz/szczecin/?search%5Border%5D=created_at:desc
2. Ctrl+S → "Strona internetowa, tylko HTML" (HTML only). Powtórz dla kolejnych stron (`&page=2`...).
3. Uruchom:

```
uv run --with-requirements ../../requirements.txt python parse_olx.py page1.html page2.html --output olx_listings.json
```

Wynik: lista rekordów (id, url, title, price_pln, area_m2, location, created_at, image_url), deduplikacja po id, plik zapisywany od nowa (atomowo). Brak ogłoszeń = błąd.

**Uwaga:** selektory i struktura `__PRERENDERED_STATE__` są niezweryfikowane na żywym OLX — `sample_page.html` jest SYNTETYCZNY. Po zapisaniu prawdziwej strony może być potrzebna korekta.
