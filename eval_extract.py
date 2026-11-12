#!/usr/bin/env python3
"""How good is the generic (Claude) extraction? Ground truth = the hand-written Universum connector.

For the same Universum ad pages, each fetched once, this compares the record from
`connectors.universum.parse_offer` with the one from `connectors.llm_extract.extract_ad`,
field by field, and prints agreement, token use and an estimated cost.
It also checks that Claude finds the same ad links on the first results page.

Needs ANTHROPIC_API_KEY. Makes about 1 + N model calls and 2 + N HTTP requests (1 per second).
Nothing is written to disk: the pages and records contain personal data (agent names, phones).

Usage: uv run --with-requirements requirements.txt python eval_extract.py --limit 10
"""
import argparse
import math
import re
import sys
import unicodedata

from connectors import fetch
from connectors import llm_extract as L
from connectors import universum

# USD per million tokens for MODEL. PLACEHOLDERS, NOT FACTS: check them against the current
# Anthropic pricing page before trusting any cost figure printed below.
PRICE_INPUT_PER_MTOK = 1.00
PRICE_OUTPUT_PER_MTOK = 5.00

EXACT_NUMBERS = ["price_pln", "area_m2", "plot_m2", "rooms"]
EXACT_TEXT = ["phone", "agent", "url", "image_url"]  # compared after trimming/case only
EXACT_FIELDS = EXACT_NUMBERS + EXACT_TEXT
FREE_TEXT = ["title", "location", "description"]     # wording may legitimately differ


def squash(value):
    """Case-, accent-, punctuation- and whitespace-insensitive form of a text."""
    text = unicodedata.normalize("NFKD", str(value or "")).casefold().replace("ł", "l")
    return re.sub(r"[^a-z0-9]+", "", text)


def exact_equal(field, truth, guess):
    if field in EXACT_NUMBERS:
        return (truth is None and guess is None) or (
            truth is not None and guess is not None and math.isclose(truth, guess, abs_tol=0.01))
    def tidy(value):
        return re.sub(r"\s+", " ", value or "").strip().casefold()
    return tidy(truth) == tidy(guess)


def text_verdict(truth, guess):
    """'same' (equal after normalising), 'close' (one contains the other, e.g. a shortened text), 'differs'."""
    a, b = squash(truth), squash(guess)
    if a == b:
        return "same"
    return "close" if a and b and (a in b or b in a) else "differs"


def compare(truth, guess):
    """-> (set of exact fields that differ, {free-text field: verdict})"""
    wrong = {f for f in EXACT_FIELDS if not exact_equal(f, truth.get(f), guess.get(f))}
    return wrong, {f: text_verdict(truth.get(f), guess.get(f)) for f in FREE_TEXT}


def cost(usage):
    return (usage["input_tokens"] * PRICE_INPUT_PER_MTOK + usage["output_tokens"] * PRICE_OUTPUT_PER_MTOK) / 1e6


def pick_ads(limit, fetcher):
    """First `limit` offer codes from the hand-written connector's results pages (+ the first page's HTML)."""
    codes, page, total, first = [], 1, 1, None
    while len(codes) < limit and page <= total:
        url = universum.LIST.format(page=page)
        results = fetcher(url)
        first = first or (url, results)
        codes += [c for c in universum.parse_codes(results) if c not in codes]
        total = universum.last_page(results)
        page += 1
    return codes[:limit], first


def run(limit, client, fetcher=fetch):
    codes, (first_url, first_page) = pick_ads(limit, fetcher)
    # 1. link discovery on the first results page
    link_usage = L.new_usage()
    links = L.reduce_links(first_page, first_url)
    found, _ = L.find_links(client, links, first_url, link_usage)
    page_codes = set(universum.parse_codes(first_page))
    found_codes = {u.rsplit("/", 1)[-1] for u in found}
    # 2. field-by-field comparison
    ad_usage, wrong_exact, verdicts, correct, details = L.new_usage(), {f: 0 for f in EXACT_FIELDS}, {f: {} for f in FREE_TEXT}, 0, []
    for code in codes:
        page = fetcher(f"{universum.BASE}/oferta/{code}")
        truth = universum.parse_offer(page, code)
        guess = L.extract_ad(client, page, truth["url"], ad_usage)
        wrong, texts = compare(truth, guess)
        correct += not wrong
        for f in wrong:
            wrong_exact[f] += 1
        for f, verdict in texts.items():
            verdicts[f][verdict] = verdicts[f].get(verdict, 0) + 1
        if wrong:
            shown = [f"{f}: expected {truth.get(f)!r}, got {guess.get(f)!r}" if f in EXACT_NUMBERS else f"{f}: differs"
                     for f in sorted(wrong)]  # text fields may be personal data: names only
            details.append(f"  {code}: " + "; ".join(shown))
    return {"n": len(codes), "correct": correct, "wrong": wrong_exact, "verdicts": verdicts, "details": details,
            "ad_usage": ad_usage, "link_usage": link_usage, "page_ads": len(page_codes),
            "links_found": len(found_codes), "links_hit": len(found_codes & page_codes)}


def report(result):
    n = result["n"]
    ad, link = result["ad_usage"], result["link_usage"]
    ad_tokens = ad["input_tokens"] + ad["output_tokens"]
    per_ad = cost(ad) / n
    page_cost = cost(link)
    per_100 = 100 * per_ad + math.ceil(100 / max(result["page_ads"], 1)) * page_cost
    lines = [f"Ads compared: {n}", "", "Exact fields (agreement with the hand-written connector)"]
    lines += [f"  {f:<10} {n - result['wrong'][f]:>3}/{n}" for f in EXACT_FIELDS]
    lines += [f"  fully correct listings: {result['correct']}/{n}", "",
              "Free-text fields (normalised; wording may differ legitimately, not counted as errors)"]
    for f in FREE_TEXT:
        v = result["verdicts"][f]
        lines.append(f"  {f:<12} same {v.get('same', 0)}, close {v.get('close', 0)}, differs {v.get('differs', 0)}")
    lines += ["", f"Ad links on results page 1: found {result['links_found']}, correct {result['links_hit']}, "
                  f"actual {result['page_ads']}", "",
              f"Tokens, ad pages: input {ad['input_tokens']}, output {ad['output_tokens']}  ({ad_tokens / n:.0f} per ad)",
              f"Tokens, one results page: input {link['input_tokens']}, output {link['output_tokens']}",
              f"Estimated cost per ad: ${per_ad:.4f}   per 100 ads (incl. results pages): ${per_100:.2f}",
              f"  (prices in this file are placeholders: ${PRICE_INPUT_PER_MTOK}/${PRICE_OUTPUT_PER_MTOK} per million "
              f"input/output tokens, model {L.MODEL}; check them against current pricing)"]
    if result["details"]:
        lines += ["", "Disagreements on exact fields:"] + result["details"]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=10, help="number of ads to compare (default 10)")
    args = parser.parse_args(argv)
    import anthropic
    try:
        client = anthropic.Anthropic()
    except Exception as error:
        sys.exit(f"Cannot create the Anthropic client (is ANTHROPIC_API_KEY set?): {error}")
    print(report(run(args.limit, client)))


if __name__ == "__main__":
    main()
