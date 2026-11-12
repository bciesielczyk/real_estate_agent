"""Phone calls to brokers and reports from them (Task B in PLAN.md).

run_pending() takes every call marked "to_call", holds the conversation, stores the
transcript and a report (status "done"), or stores status "failed" with a Polish error.

CALL_MODE (default "simulate"):
  simulate  Claude writes the conversation. No phone call, only the Claude API is contacted.
  real      Outbound call via ElevenLabs Conversational AI + Twilio number. Needs
            ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID, ELEVENLABS_PHONE_NUMBER_ID.

What the ElevenLabs agent's own prompt (set in their dashboard) must contain:
  - it is an AI assistant calling on behalf of a private buyer, and says so at the start;
  - it says the call is recorded and transcribed, and asks whether the broker agrees to go on;
  - it speaks Polish, is polite and brief, and does not invent facts or promise anything;
  - it uses {{listing}} (the listing summary) and {{questions}} (numbered questions to ask),
    and ends the call once all questions were asked or the broker declines.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

import anthropic
from pydantic import BaseModel, Field, ValidationError

import calls
import db

MODEL = "claude-haiku-5-5"
NOT_ESTABLISHED = "Nie ustalono"

ELEVENLABS_URL = "https://api.elevenlabs.io/v1/convai"
ELEVENLABS_VARS = ("ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID", "ELEVENLABS_PHONE_NUMBER_ID")
POLL_INTERVAL = 5      # seconds between status checks
CALL_TIMEOUT = 15 * 60  # seconds until a call that never finishes becomes "failed"


class CallError(Exception):
    """Failure with a short human-readable (Polish) message."""


# ---------------------------------------------------------------- Claude: schemas

class Turn(BaseModel):
    role: str = Field(pattern="^(caller|broker)$")
    text: str


class Conversation(BaseModel):
    transcript: list[Turn]


class Answer(BaseModel):
    question: str
    answer: str


class Report(BaseModel):
    summary: str
    answers: list[Answer]


CONVERSATION_TOOL = {
    "name": "submit_conversation",
    "description": "Submit the simulated phone conversation.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "transcript": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string", "enum": ["caller", "broker"]},
                        "text": {"type": "string"},
                    },
                    "required": ["role", "text"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["transcript"],
        "additionalProperties": False,
    },
}

REPORT_TOOL = {
    "name": "submit_report",
    "description": "Submit the report from the call.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "2-4 sentences in Polish: what was learned"},
            "answers": {
                "type": "array",
                "description": "Exactly one entry per question, in the order of the questions",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "The question exactly as given"},
                        "answer": {"type": "string", "description": f"Answer in Polish, or '{NOT_ESTABLISHED}'"},
                    },
                    "required": ["question", "answer"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary", "answers"],
        "additionalProperties": False,
    },
}

SIMULATE_SYSTEM = """Piszesz realistyczną rozmowę telefoniczną po polsku między asystentem AI (caller)
a pośrednikiem nieruchomości (broker), o konkretnym ogłoszeniu. To symulacja do testów aplikacji.

Zasady:
- Rozmowę zaczyna caller: przedstawia się jako asystent AI dzwoniący w imieniu prywatnego kupującego
  i uprzedza, że rozmowa jest nagrywana i transkrybowana. Dopiero potem zadaje pytania.
- Caller zadaje pytania z listy, po kolei, krótko i uprzejmie. Nie podaje własnych faktów o domu
  i nie wymyśla odpowiedzi ani ustaleń; nie negocjuje ani nie składa ofert.
- Broker odpowiada na większość pytań na podstawie danych ogłoszenia lub prawdopodobnie, w naturalnym
  języku potocznym. Co najmniej na jedno pytanie (w miarę możliwości) odpowiada wymijająco, mówi, że
  nie wie, albo odmawia odpowiedzi przez telefon - wtedy caller przyjmuje to do wiadomości i idzie dalej.
- Całość to zwykle 10-20 krótkich wypowiedzi, kończy się grzecznym pożegnaniem.

Odpowiedz wyłącznie wywołaniem narzędzia submit_conversation."""

REPORT_SYSTEM = """Dostajesz transkrypcję rozmowy telefonicznej (caller = asystent AI, broker = pośrednik)
oraz listę pytań. Sporządź raport narzędziem submit_report.

Zasady:
- Dokładnie jedna odpowiedź na każde pytanie, w tej samej kolejności, z pytaniem przepisanym bez zmian.
- Odpowiadaj po polsku, krótko, wyłącznie na podstawie tego, co powiedział broker.
  Niczego nie dodawaj od siebie i nie zgaduj. Wypowiedzi calleru nie są źródłem faktów.
- Jeśli broker nie odpowiedział, był wymijający, odmówił albo pytanie nie padło, odpowiedz dokładnie: "Nie ustalono".
  Jeśli broker podał częściową informację, podaj ją i zaznacz, czego brakuje.
- summary: 2-4 zdania po polsku o tym, czego dowiedział się kupujący (w tym o odmowach i niejasnościach).

Odpowiedz wyłącznie wywołaniem narzędzia submit_report."""


def ask_tool(client, system, user_text, tool):
    """One Claude request that must end in a call to `tool`; returns (tool input, usage)."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        system=[{"type": "text", "text": system}],
        messages=[{"role": "user", "content": user_text}],
    )
    if response.stop_reason == "refusal":
        raise CallError("Claude odmówił wykonania tego zadania")
    if response.stop_reason == "max_tokens":
        raise CallError("Odpowiedź Claude'a była niepełna")
    block = next((b for b in response.content if b.type == "tool_use" and b.name == tool["name"]), None)
    if block is None:
        raise CallError("Claude nie zwrócił oczekiwanego wyniku")
    return block.input, response.usage


def add_usage(total, usage):
    for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        total[key] = total.get(key, 0) + (getattr(usage, key, 0) or 0)


# ---------------------------------------------------------------- listing data

def listing_text(call, description):
    """Plain-text summary of the listing, used in prompts and as the ElevenLabs {{listing}}."""
    price = f"{call['price_pln']:,.0f} zł".replace(",", " ") if call.get("price_pln") else "brak"
    area = f"{call['area_m2']:g} m²" if call.get("area_m2") else "brak"
    return (f"Tytuł: {call.get('title') or 'brak'}\nLokalizacja: {call.get('location') or 'brak'}\n"
            f"Cena: {price}\nPowierzchnia: {area}\nOpis: {description or 'brak'}")


def numbered(questions):
    return "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))


def read_description(call, path):
    with db.connect(path) as connection:
        row = connection.execute("SELECT description FROM listings WHERE source = ? AND id = ?",
                                 (call["source"], call["id"])).fetchone()
    return row["description"] if row else None


# ---------------------------------------------------------------- simulate

def simulate_conversation(client, call, description, usage):
    text = (f"Ogłoszenie:\n{listing_text(call, description)}\n\n"
            f"Pytania, które caller ma zadać:\n{numbered(call['questions'])}")
    data, used = ask_tool(client, SIMULATE_SYSTEM, text, CONVERSATION_TOOL)
    add_usage(usage, used)
    turns = Conversation.model_validate(data).transcript
    transcript = [{"role": t.role, "text": t.text.strip()} for t in turns if t.text.strip()]
    if not transcript:
        raise CallError("Symulowana rozmowa jest pusta")
    return transcript


# ---------------------------------------------------------------- real (ElevenLabs)

def http_json(method, url, headers, body=None):
    """Tiny urllib JSON client; tests replace this function."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        raise CallError(f"ElevenLabs odrzucił żądanie (HTTP {error.code})") from error
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise CallError(f"Brak połączenia z ElevenLabs: {error}") from error


def map_transcript(items):
    roles = {"agent": "caller", "user": "broker"}
    return [{"role": roles[i["role"]], "text": i["message"].strip()}
            for i in items or []
            if i.get("role") in roles and (i.get("message") or "").strip()]


def real_conversation(call, description, store_id):
    """Place the call, wait for it to finish, return the transcript. store_id(conversation_id) is called early."""
    missing = [name for name in ELEVENLABS_VARS if not os.environ.get(name)]
    if missing:
        raise CallError(f"Brak zmiennej środowiskowej {missing[0]}"
                        + (f" (oraz: {', '.join(missing[1:])})" if len(missing) > 1 else ""))
    if not call.get("phone"):
        raise CallError("Ogłoszenie nie ma numeru telefonu")
    headers = {"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}
    started = http_json("POST", f"{ELEVENLABS_URL}/twilio/outbound-call", headers, {
        "agent_id": os.environ["ELEVENLABS_AGENT_ID"],
        "agent_phone_number_id": os.environ["ELEVENLABS_PHONE_NUMBER_ID"],
        "to_number": call["phone"],
        "conversation_initiation_client_data": {"dynamic_variables": {
            "listing": listing_text(call, description),
            "questions": numbered(call["questions"]),
        }},
    })
    conversation_id = started.get("conversation_id")
    if not conversation_id:
        raise CallError("ElevenLabs nie zwrócił identyfikatora rozmowy"
                        + (f": {started.get('message')}" if started.get("message") else ""))
    store_id(conversation_id)
    deadline = time.monotonic() + CALL_TIMEOUT
    while True:
        info = http_json("GET", f"{ELEVENLABS_URL}/conversations/{conversation_id}", headers)
        if info.get("status") == "failed":
            raise CallError("Połączenie nie powiodło się (ElevenLabs zgłosił błąd)")
        if info.get("status") == "done":
            transcript = map_transcript(info.get("transcript"))
            if not any(t["role"] == "broker" for t in transcript):
                raise CallError("Rozmowa zakończona bez odpowiedzi rozmówcy (nikt nie odebrał?)")
            return transcript
        if time.monotonic() >= deadline:
            raise CallError("Rozmowa nie zakończyła się w limicie czasu")
        time.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------- report

def align_answers(questions, answers):
    """Exactly one answer per question: match by question text, else by position; gaps -> 'Nie ustalono'."""
    by_text = {}
    for a in answers:
        by_text.setdefault(a.question.strip(), a.answer)
    known = {q.strip() for q in questions}
    aligned = []
    for i, question in enumerate(questions):
        if question.strip() in by_text:
            answer = by_text[question.strip()]
        elif i < len(answers) and answers[i].question.strip() not in known:
            answer = answers[i].answer  # reworded question at the same position
        else:
            answer = NOT_ESTABLISHED
        aligned.append({"question": question, "answer": answer.strip() or NOT_ESTABLISHED})
    return aligned


def build_report(client, transcript, questions, usage=None):
    text = ("Transkrypcja:\n" + "\n".join(f"{t['role']}: {t['text']}" for t in transcript)
            + f"\n\nPytania:\n{numbered(questions)}")
    data, used = ask_tool(client, REPORT_SYSTEM, text, REPORT_TOOL)
    if usage is not None:
        add_usage(usage, used)
    report = Report.model_validate(data)
    return {"summary": report.summary, "answers": align_answers(questions, report.answers)}


# ---------------------------------------------------------------- orchestration

def process_call(call, path, client, usage):
    source, listing_id = call["source"], call["id"]
    calls.update_call(source, listing_id, path=path, status="calling", error=None)
    description = read_description(call, path)
    if os.environ.get("CALL_MODE", "simulate") == "real":
        transcript = real_conversation(
            call, description,
            lambda cid: calls.update_call(source, listing_id, path=path, conversation_id=cid))
        client = client or anthropic.Anthropic()
    else:
        client = client or anthropic.Anthropic()
        transcript = simulate_conversation(client, call, description, usage)
    report = build_report(client, transcript, call["questions"], usage)
    calls.update_call(source, listing_id, path=path, status="done",
                      transcript=transcript, report=report, error=None)


def run_pending(path=db.DB_PATH, client=None):
    """Process every "to_call" row; never raises. Returns [{source, id, status, error, usage}]."""
    try:
        pending = calls.list_calls("to_call", path=path)
    except Exception as error:  # e.g. database locked
        print(f"caller: nie można odczytać kolejki: {error}", file=sys.stderr)
        return []
    results = []
    for call in pending:
        usage = {}
        try:
            process_call(call, path, client, usage)
            status, error = "done", None
        except CallError as e:
            status, error = "failed", str(e)
        except ValidationError:
            status, error = "failed", "Model zwrócił dane w nieprawidłowym formacie"
        except anthropic.APIError as e:
            status, error = "failed", f"Błąd Claude API: {type(e).__name__}"
        except Exception as e:
            if "api_key" in str(e) or "authentication" in str(e).lower():  # the SDK's "no key" errors
                status, error = "failed", "Brak dostępu do Claude API (sprawdź ANTHROPIC_API_KEY)"
            else:
                status, error = "failed", f"Nieoczekiwany błąd: {type(e).__name__}"
        if status == "failed":
            try:
                calls.update_call(call["source"], call["id"], path=path, status="failed", error=error)
            except Exception as e:
                print(f"caller: nie można zapisać błędu: {e}", file=sys.stderr)
        results.append({"source": call["source"], "id": call["id"], "status": status,
                        "error": error, "usage": usage})
    return results


def main():
    print(f"Tryb: {os.environ.get('CALL_MODE', 'simulate')}")
    for r in run_pending():
        tokens = r["usage"].get("input_tokens", 0) + r["usage"].get("output_tokens", 0)
        print(f"{r['source']}:{r['id']} {r['status']}" + (f" - {r['error']}" if r["error"] else "")
              + f" ({tokens} tokenów)")


if __name__ == "__main__":
    main()
