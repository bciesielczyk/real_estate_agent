# Plan MVP: agent nieruchomości (domy, Szczecin)

Przepływ: konektory → baza → przeglądanie i filtr Claude'a → „do zadzwonienia” → rozmowa → raport.

## Stan

| Krok | Stan |
|---|---|
| 1. Konektory agencji (`connectors/`, `scrape.py`) | zrobione dla Universum |
| 2. Przeglądanie i filtr Claude'a (`app.py`, `matcher.py`) | było |
| 3. Oznaczanie „do zadzwonienia” i pytania | zadanie A |
| 4. Rozmowa telefoniczna (ElevenLabs) | zadanie B |
| 5. Raport z rozmowy | zadania A (widok) i B (treść) |

## Wspólny kontrakt (`calls.py`, gotowe)

Tabela `calls`, jeden wiersz na ogłoszenie: `status` (`to_call` → `calling` → `done` | `failed`), `questions`, `transcript`, `report`, `error`. Funkcje: `mark_to_call`, `unmark`, `update_call`, `list_calls`. Domyślne pytania: `DEFAULT_QUESTIONS`.

## Zadania

**A. UI i API (Sonnet)** — `app.py`, `static/index.html`
- Przycisk „Do zadzwonienia” na karcie, z edycją listy pytań (domyślnie `DEFAULT_QUESTIONS`).
- Widok listy rozmów: status, pytania, raport (odpowiedź pod każdym pytaniem), transkrypcja.
- Przycisk „Zadzwoń” uruchamia `caller.run_pending()` w tle; strona odświeża statusy.

**B. Rozmowa i raport (Sonnet)** — `caller.py`, `test_caller.py`
- `run_pending()`: dla każdego `to_call` prowadzi rozmowę, zapisuje transkrypcję i raport.
- Tryb domyślny: **symulacja** — Claude gra pośrednika, żadnego prawdziwego połączenia.
- Tryb `CALL_MODE=real`: połączenie wychodzące przez ElevenLabs, potem pobranie transkrypcji.
- Raport: Claude wyciąga z transkrypcji odpowiedź na każde pytanie (albo „nie ustalono”).

**C. Testy bazy (Haiku)** — `test_db.py`
- Testy `db.save_listings` i `calls.py` na tymczasowej bazie.

Scalenie, przegląd i test całości: model główny.

## Decyzje i ograniczenia

- Prawdziwe połączenia tylko po świadomym ustawieniu `CALL_MODE=real`. Agent musi przedstawić się jako AI i uprzedzić o nagrywaniu; numery pośredników to dane osobowe.
- Tryb `real` wymaga konta ElevenLabs z agentem i numerem telefonu (`ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, `ELEVENLABS_PHONE_NUMBER_ID`). Do czasu ich podania kod nie będzie sprawdzony na prawdziwym połączeniu.
- Jedna rozmowa na ogłoszenie. Łączenie kilku ogłoszeń tego samego pośrednika w jedną rozmowę — później.

## Później

- Gratka jako konektor; kolejne agencje.
- Łączenie rozmów po numerze telefonu; scalanie duplikatów ogłoszeń.
- Mieszkania, działki, inne miejscowości; wyszukiwanie agentowe zamiast scraperów.
- Ewaluacja dopasowania, FastAPI, demo z fałszywymi danymi (lista portfolio w `POMYSly MOJE.md`).
