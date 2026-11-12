"""PROTOTYPE: parse saved OLX.pl search-results HTML into JSON.
Usage: python parse_olx.py page1.html [page2.html ...] --output olx_listings.json
Prefers window.__PRERENDERED_STATE__; falls back to div[data-cy=l-card]. Selectors unverified vs live OLX."""
import argparse, json, os, re, sys, tempfile
from bs4 import BeautifulSoup

BASE = "https://www.olx.pl"

def absurl(u):
    return BASE + u if u and u.startswith("/") else u

def num(s):
    m = re.search(r"(\d[\d\s ]*(?:[.,]\d+)?)", s or "")
    if not m: return None
    t = re.sub(r"[\s ]", "", m.group(1)).replace(",", ".")
    try:
        v = float(t)
    except ValueError:
        return None
    return int(v) if v == int(v) else v

def area_from_title(t):
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:m2|m²|m\^2|mkw)", t or "", re.I)
    return num(m.group(1)) if m else None

def extract_state(html):
    i = html.find("__PRERENDERED_STATE__")
    if i < 0: return None
    j = html.find("=", i)
    if j < 0: return None
    try:
        val, _ = json.JSONDecoder().raw_decode(html[j + 1:].lstrip())
        return json.loads(val) if isinstance(val, str) else val
    except ValueError:
        return None

def find_ads(o):
    if isinstance(o, dict):
        ads = o.get("ads")
        if isinstance(ads, list) and ads and isinstance(ads[0], dict) and "id" in ads[0]:
            return ads
        for v in o.values():
            r = find_ads(v)
            if r: return r
    elif isinstance(o, list):
        for v in o:
            r = find_ads(v)
            if r: return r
    return []

def from_state(ad):
    p = ad.get("price") or {}
    price = (p.get("regularPrice") or {}).get("value") if isinstance(p, dict) else None
    if price is None and isinstance(p, dict): price = num(p.get("displayValue"))
    area = None
    for prm in ad.get("params") or []:
        if prm.get("key") in ("m", "area"):
            area = num(prm.get("normalizedValue")) or num(prm.get("value"))
    if area is None: area = area_from_title(ad.get("title"))
    loc = ad.get("location") or {}
    loc_s = ", ".join(x for x in (loc.get("cityName"), loc.get("districtName")) if x) if isinstance(loc, dict) else str(loc)
    photos = ad.get("photos") or []
    img = photos[0] if photos else None
    if isinstance(img, str):
        img = re.sub(r";s=.*$", "", img)
    return dict(id=str(ad["id"]), url=absurl(ad.get("url")), title=ad.get("title"),
                price_pln=int(price) if isinstance(price, (int, float)) else None,
                area_m2=area, location=loc_s or None, created_at=ad.get("createdTime"), image_url=img)

def from_cards(soup):
    out = []
    for c in soup.select('div[data-cy="l-card"]'):
        a = c.select_one('a[href*="/d/oferta/"]') or c.find("a", href=True)
        if not a: continue
        href = a["href"]
        h = c.find(["h4", "h6", "h3"])
        title = (h or a).get_text(" ", strip=True)
        pr = c.select_one('[data-testid="ad-price"]')
        loc = c.select_one('[data-testid="location-date"]')
        loc_s = loc.get_text(" ", strip=True) if loc else None
        loc_part = re.split(r"\s+-\s+", loc_s, 1)[0] if loc_s else None
        img = c.find("img")
        rid = c.get("id") or (re.search(r"ID(\w+)\.html", href) or [None, href])[1]
        out.append(dict(id=str(rid), url=absurl(href), title=title,
                        price_pln=(lambda v: int(v) if v is not None else None)(num(pr.get_text(" ", strip=True)) if pr else None),
                        area_m2=area_from_title(title), location=loc_part,
                        created_at=None, image_url=img.get("src") if img else None))
    return out

def parse_file(path):
    html = open(path, encoding="utf-8", errors="replace").read()
    st = extract_state(html)
    ads = find_ads(st) if st else []
    if ads:
        return [from_state(a) for a in ads], "state"
    return from_cards(BeautifulSoup(html, "html.parser")), "cards"

def main():
    ap = argparse.ArgumentParser(description="PROTOTYPE OLX saved-HTML parser")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--output", default="olx_listings.json")
    a = ap.parse_args()
    seen = {}
    for f in a.files:
        recs, src = parse_file(f)
        print(f"{f}: {len(recs)} ogłoszeń (źródło: {src})", file=sys.stderr)
        for r in recs: seen.setdefault(r["id"], r)
    if not seen:
        sys.exit("BŁĄD: nie znaleziono żadnych ogłoszeń (zły plik? zapisz stronę jako 'HTML only').")
    d = os.path.dirname(os.path.abspath(a.output))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(list(seen.values()), fh, ensure_ascii=False, indent=2)
    os.replace(tmp, a.output)
    print(f"Zapisano {len(seen)} rekordów do {a.output}", file=sys.stderr)

if __name__ == "__main__":
    main()
