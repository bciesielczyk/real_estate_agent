# OLX Playwright — PROTOTYPE

Pobiera wszystkie ogłoszenia z
<https://www.olx.pl/nieruchomosci/domy/sprzedaz/szczecin/?search%5Border%5D=created_at:desc>
do jednego pliku JSON. Uruchom z katalogu głównego projektu:

```sh
uv run --with-requirements requirements.txt python prototypes/olx_playwright/scrape_olx.py --headless
```

Flagi: `--output` (domyślnie `olx_listings.json`), `--max-pages` (domyślnie 10), `--headless` (bez okna).

## Jak działa

- Dane pochodzą z JSON-a, który OLX osadza w stronie (`window.__PRERENDERED_STATE__`). Aplikacja usuwa go po załadowaniu, więc skrypt zapisuje kopię skryptem startowym.
- Przechodzi `&page=2…` aż do `totalPages` zgłoszonego przez OLX, z przerwą 2,5 s między stronami.
- Każde uruchomienie **zastępuje** plik. Zapis następuje tylko, jeśli pobrano wszystkie strony; przy błędzie poprzedni plik zostaje.
- Brak danych na stronie traktuje jako blokadę: w trybie `--headless` kończy się błędem, w trybie z oknem czeka, aż rozwiążesz problem ręcznie i naciśniesz Enter.

## Pola

`id`, `url` (strona OLX), `external_url` (np. Otodom), `source` (`olx` lub partner, np. `otodom_pl`), `title`, `price_pln`, `negotiable`, `area_m2`, `plot_m2`, `price_per_m2`, `built_type`, `market`, `location`, `district`, `lat`, `lon`, `is_business`, `created_at`, `refreshed_at`, `image_url`, `description` (pełny opis jako tekst).

Większość wyników (ok. 85%) to ogłoszenia Otodom wyświetlane przez OLX — mają `external_url` i `source: otodom_pl`.

## Numery telefonów (`--phones`)

```sh
uv run --with-requirements requirements.txt python prototypes/olx_playwright/scrape_olx.py --phones
```

Tylko dla ofert z Otodom (`source: otodom_pl`). Dla każdej: otwiera ogłoszenie na OLX → klika link do Otodom → klika widoczny „Pokaż numer” → czyta link `tel:`. Po kolei, 5 s przerwy między ofertami. Do testów: `--phone-limit 3`.

- Działa z **widocznym oknem**; z `--headless` Otodom zwracał 403.
- Dodaje `phone` (format `+48…`) i `phone_status`: `ok`, `olx_login_required` (oferta tylko na OLX — numer wymaga logowania, pomijana), `not_fetched`, `no_phone_button`, `no_otodom_link`, `error`, `blocked`.
- Przy pierwszej blokadzie przestaje pobierać numery, ale zapisuje ogłoszenia z dotychczasowymi wynikami.
- Plik wynikowy zawiera numery telefonów — **nie udostępniaj go i nie commituj**.

## Zasady

Bez stealth/spoofingu, bez rozwiązywania CAPTCHA, bez ciasteczek/sesji użytkownika, bez logowania.
