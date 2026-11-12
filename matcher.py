"""Match listings against free-text criteria with the Claude API.

All active listings are sent in one request; Claude returns the ones that fit,
with a score and a short reason. Requires ANTHROPIC_API_KEY (or `ant auth login`).
"""
import json

import anthropic
from pydantic import BaseModel, Field, ValidationError

MODEL = "claude-haiku-5-5"  # cheapest current model

SYSTEM = """Jesteś asystentem osoby kupującej dom w Szczecinie i okolicach.
Dostajesz kryteria użytkownika i listę ogłoszeń (JSON, jedno na linię).
Wybierz ogłoszenia, które pasują do kryteriów, i przekaż je narzędziem submit_matches.

Zasady:
- Twarde warunki (cena, powierzchnia, liczba pokoi, lokalizacja) sprawdzaj dokładnie na polach liczbowych.
- Miękkie warunki (np. ogród, stan, okolica) oceniaj na podstawie tytułu, lokalizacji i opisu.
  Opisy to krótkie skróty, więc jeśli czegoś nie da się z nich stwierdzić, nie odrzucaj
  ogłoszenia - obniż ocenę i napisz w uzasadnieniu, co trzeba sprawdzić.
- Nie wymyślaj informacji, których nie ma w danych.
- Uzasadnienie po polsku, jedno-dwa krótkie zdania.
- Zwracaj tylko ogłoszenia z oceną co najmniej 5.

Odpowiedz wyłącznie wywołaniem narzędzia submit_matches."""


class Match(BaseModel):
    id: str = Field(description="Listing id exactly as given")
    score: int = Field(ge=1, le=10, description="How well it fits, 1-10")
    reasoning: str = Field(description="Short reason in Polish, incl. what is uncertain")


class MatchResult(BaseModel):
    summary: str = Field(description="One-sentence summary in Polish of how criteria were interpreted")
    matches: list[Match]


TOOL = {
    "name": "submit_matches",
    "description": "Submit the listings that match the user's criteria.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "One sentence in Polish: how the criteria were interpreted"},
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Listing id exactly as given"},
                        "score": {"type": "integer", "description": "How well it fits, 1-10"},
                        "reasoning": {"type": "string", "description": "Short reason in Polish, incl. what is uncertain"},
                    },
                    "required": ["id", "score", "reasoning"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary", "matches"],
        "additionalProperties": False,
    },
}


def listing_line(listing):
    return json.dumps({
        "id": listing["id"],
        "title": listing["title"],
        "price_pln": listing["price_pln"],
        "area_m2": listing["area_m2"],
        "rooms": listing["rooms"],
        "location": listing["location"],
        "description": listing["description"],
    }, ensure_ascii=False)


def ask(client, criteria, listings):
    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        output_config={"effort": "low"},
        tools=[TOOL],
        tool_choice={"type": "auto"},
        system=[{"type": "text", "text": SYSTEM}],
        messages=[{
            "role": "user",
            "content": [
                # Listings first and cached: repeated searches with new criteria reuse them.
                {"type": "text", "text": "Ogłoszenia:\n" + "\n".join(map(listing_line, listings)),
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": f"Kryteria użytkownika:\n{criteria}"},
            ],
        }],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude odmówił odpowiedzi na te kryteria")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Odpowiedź Claude'a była niepełna; spróbuj zawęzić kryteria")
    call = next((b for b in response.content if b.type == "tool_use" and b.name == TOOL["name"]), None)
    if call is None:
        raise ValueError("Model did not call submit_matches")
    return MatchResult.model_validate(call.input), response.usage


def match_listings(criteria, listings, client=None):
    client = client or anthropic.Anthropic()
    usage = []
    for attempt in range(2):  # the small model occasionally answers in prose; retry once
        try:
            result, used = ask(client, criteria, listings)
            usage.append(used)
            break
        except (ValueError, ValidationError) as error:
            if attempt:
                raise RuntimeError(f"Model nie zwrócił poprawnej listy: {error}") from error
    known = {listing["id"] for listing in listings}
    matches = sorted((m for m in result.matches if m.id in known), key=lambda m: -m.score)
    return {
        "summary": result.summary,
        "matches": [{"id": m.id, "score": m.score, "reason": m.reasoning} for m in matches],
        "model": MODEL,
        "usage": {
            "input_tokens": sum(u.input_tokens for u in usage),
            "cache_read_input_tokens": sum(u.cache_read_input_tokens or 0 for u in usage),
            "cache_creation_input_tokens": sum(u.cache_creation_input_tokens or 0 for u in usage),
            "output_tokens": sum(u.output_tokens for u in usage),
        },
    }
