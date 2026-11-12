- na razie tylko gratka na MVP, ale w przyszłości musi być mozliwość dodania innych portali
- prawdziwe perełki będą na stronach pośredników nieruchomości, a nie na portalach - mozna zaczac od jednego, 
potem dodawac kolejne
-? jak zachować ciągłość ogłoszeń skrapowanych tzn. czy przechowywać je? czy po prostu nadpisywać? ale trochę to
zajmuje; 
-? jak to uruchomić "w terenie" jako aplikację? (jak to zdeployować dla uzytkownika)
-? jak to zapytanie do KLODA zrobić przy zdeployowanej?
- logi : co on tam pod spodem robi haha!
- jakiego modelu uzywac i na jakim effort, ile to kosztuje
- typowe, proponowane filtry
- interesujące mnie dzielnice, czy miejscowości

- bardziej generycznie - nie tylko domy, ale tez mieszkania (?)

- niszowe : miasto oferuje; licytacje; 

- jak sprawdzić czy dobrze wybiera ?

- odsiewanie

1. Limit do ~30 najlepszych wyników — szybka poprawka. Przy szerokich kryteriach odpowiedź zbliża się do limitu tokenów i się urwie: przy 141 wynikach zużyła 15 tys. z 16 tys.
2. Shortlista — przycisk „dodaj” na karcie ogłoszenia i status (do kontaktu → zadzwonione → odrzucone) zapisywany w bazie. Wpisy nie znikają razem z ogłoszeniem.
3. Codzienne odświeżanie — scraper Gratki uruchamiany automatycznie raz dziennie (launchd), żeby nowe ogłoszenia czekały, gdy otworzysz stronę.
4. Pełne opisy ogłoszeń — teraz Claude widzi tylko ~300-znakowe skróty, więc np. ogród czy stan domu ocenia na wyczucie. Pełny opis jest na stronie ogłoszenia, którą scraper i tak pobiera po numer, ale każde wyszukiwanie zużyje więcej tokenów.

1. Więcej źródeł i scalanie duplikatów. Dochodzą Adresowo, OLX/Otodom, Morizon (ta sama grupa co Gratka, więc pewnie podobna struktura) i Nieruchomosci-online. Ten sam dom pojawi się wtedy na kilku portalach, więc trzeba go rozpoznawać po zdjęciach, powierzchni i lokalizacji — tu przyda się baza wektorowa.
2. Alerty zamiast ręcznego szukania. Zapisane kryteria uruchamiają się codziennie po scrapingu, a Ty dostajesz powiadomienie o nowych pasujących ogłoszeniach. Do tego sygnały do negocjacji: historia cen, jak długo ogłoszenie wisi i czerwone flagi, np. „dom” na działce w ROD.
3. Agent dzwoniący — główny cel. Platforma głosowa (np. Twilio albo gotowe Vapi/Retell) z Claude'em jako „mózgiem” pyta o dostępność, cenę i termin oglądania, a transkrypcja i podsumowanie trafiają do shortlisty. Agent musi się przedstawić jako AI (AI Act, od sierpnia 2026) i uprzedzić o nagrywaniu, a numery agentów to dane osobowe (RODO).
4. Aplikacja dla wielu użytkowników. Potrzebne są hosting, Postgres, konta i własny klucz API zamiast firmowej bramki. Tu zmienia się sytuacja prawna: udostępnianie danych zebranych z portali innym osobom zwykle narusza ich regulaminy i prawo do baz danych, więc wtedy lepiej dogadać umowę albo feed z portalami.


1. Porządek w repo: git init, usunięcie prototypów OLX i wątku Adresowo z logowaniem, testy dla scrapera, bazy i matchera (z zamockowanym API) oraz FastAPI zamiast http.server — to standard w branży i daje za darmo dokumentację /docs.
2. Ewaluacja dopasowania — najmocniejszy punkt dla AI engineeringu. Kilkanaście kryteriów z ręcznie oznaczonymi ogłoszeniami „pasuje/nie pasuje”, skrypt liczący precision/recall i porównanie modeli (Haiku vs Sonnet) pod kątem jakości, kosztu i czasu. Tabela z wynikami trafia do README.
3. README jak case study: diagram architektury i decyzje z uzasadnieniem. Masz ich już sporo: 403 przy curl → odczyt danych JSON-LD/Nuxt bez przeglądarki, numer agenta zamiast agencji, Haiku ignorujący schemat → narzędzie ze strict, zmierzone tokeny i cache.
4. Demo: GIF albo nagranie w README i dane demonstracyjne z fałszywymi numerami. Prawdziwe numery agentów to dane osobowe — nie mogą trafić do publicznego repo ani demo.
5. Opcjonalnie agent: zamiast prawdziwych telefonów symulowana rozmowa (Claude gra agenta nieruchomości) albo lista pytań do konkretnego ogłoszenia. Pokazuje projektowanie agenta bez kosztów i ryzyka prawnego.