"""Universum (universum.nieruchomosci.pl): houses for sale in Szczecin.

Plain HTTP. The results pages only give the offer codes; everything else, including
the agent's name and phone, is read from each offer page.
"""
import html
import re

from connectors import fetch, normalize_phone

BASE = "https://www.universum.nieruchomosci.pl"
LIST = BASE + "/pl/lista_ofert.php?kategoria=3&szukaj=tak&prop_id_48=szczecin&porcja={page}"  # 3 = houses for sale
MAX_PAGES = 20


def text(fragment):
    """HTML fragment -> plain text. Unescaped twice: descriptions contain `&amp;sup2;`."""
    plain = re.sub(r"<[^>]+>", " ", fragment or "")
    plain = html.unescape(html.unescape(plain)).replace("\xa0", " ")
    return re.sub(r"\s+", " ", plain).strip()


def number(value):
    """'3 700 000' -> 3700000.0, '352,2000' -> 352.2"""
    try:
        return float(re.sub(r"\s", "", value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse_codes(page):
    """Offer codes on a results page, in page order."""
    return list(dict.fromkeys(re.findall(r"row_click\('/oferta/([^'?#]+)'\)", page)))


def last_page(page):
    return max((int(n) for n in re.findall(r"porcja=(\d+)", page)), default=1)


def parse_offer(page, code):
    table = re.search(r'id="offerMainDetails".*?</table>', page, re.S)
    if not table:
        raise ValueError(f"Offer {code}: details table not found")
    details = {text(key).rstrip(":"): text(value) for key, value in
               re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", table.group(0), re.S)}
    agent = re.search(r'id="offerAgent".*?<b class="blue2">(.*?)</b>.*?tel\.\s*<b>(.*?)</b>', page, re.S)
    image = re.search(r'id="mainThumb">\s*<a[^>]*href="(/gallery/[^"]+)"', page)  # absent when no photo
    description = re.search(r"boxOpisOferty.*?<p>(.*?)</p>", page, re.S)
    district, kind = details.get("dzielnica - osiedle"), details.get("typ domu")
    rooms = number(details.get("liczba pokoi"))
    return {
        "id": code,
        "url": f"{BASE}/oferta/{code}",
        "title": ", ".join(x for x in ("Dom" if kind in (None, "dom", "inny") else f"Dom {kind}", district) if x),
        "price_pln": number(details.get("cena ofertowa PLN")),
        "area_m2": number(details.get("pow. użytkowa [m²]")) or number(details.get("pow. całkowita [m²]")),
        "plot_m2": number(details.get("pow. działki [m²]")),
        "rooms": int(rooms) if rooms else None,
        "location": ", ".join(x for x in (details.get("miejscowość"), district) if x),
        "image_url": BASE + image.group(1) if image else None,
        "description": text(description.group(1)) if description else None,
        "agent": text(agent.group(1)) if agent else None,
        "phone": normalize_phone(text(agent.group(2))) if agent else None,
        "details": details,
    }


def scrape():
    codes, page, total = [], 1, 1
    while page <= min(total, MAX_PAGES):
        results = fetch(LIST.format(page=page))
        found = parse_codes(results)
        if not found:
            raise ValueError(f"No offers on results page {page}; refusing to save incomplete results")
        codes += [code for code in found if code not in codes]
        total = max(total, last_page(results))
        print(f"Page {page}/{total}: {len(found)} offers")
        page += 1
    records = []
    for i, code in enumerate(codes, 1):
        records.append(parse_offer(fetch(f"{BASE}/oferta/{code}"), code))
        print(f"Offer {i}/{len(codes)}: {code}")
    return records
