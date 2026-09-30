"""Variante A (docs/decisions.md): Bei einer Erstattungsempfehlung sieht der Kunde erst einen festen
Zwischenbescheid. Die endgültige Antwort entsteht, wenn ein Mensch in der Support-Konsole entschieden hat.

Der Agent-Entwurf bleibt gespeichert (intern sichtbar) und dient als Vorlage für Ton und weitere Inhalte.
Geschrieben wird die endgültige Antwort von Haiku 4.5 in einem einzelnen Aufruf. Ist das Budget erschöpft
oder schlägt der Aufruf fehl, greift eine feste Vorlage je Entscheidung.
"""

import json

from .darstellung import usd_de as usd  # Kundengespräch und Prompt bleiben deutsch
from .pruefung import PREISE, trajektorie_als_kontext

ANTWORT_MODELL = "claude-haiku-4-5"
ANTWORT_MAX_TOKENS = 1500

ANTWORT_SYSTEM = """Du schreibst die endgültige Antwort des Supports der Habit-Tracker-App FocusFlow an einen Kunden. Ein KI-Agent hat den Fall bearbeitet und eine Erstattung empfohlen. Ein Mitarbeiter hat inzwischen entschieden. Du bekommst das Ticket, alle Werkzeugaufrufe des Agents mit Ergebnissen, den ursprünglichen Entwurf des Agents und die Entscheidung.

Regeln:
- Teile die Entscheidung klar mit: bestätigt heißt, die Erstattung über den genannten Betrag ist freigegeben. Abgelehnt heißt, es gibt keine Erstattung.
- Steht bei einer Entscheidung eine Begründung für den Kunden, gib sie in der Antwort sinngemäß wieder. Das gilt vor allem bei einer Ablehnung. Fehlt sie bei einer Ablehnung, schreib neutral, dass die Prüfung keine Erstattung ergeben hat, ohne einen Grund zu erfinden.
- Übernimm aus dem Entwurf des Agents nur, was durch die Werkzeugaufrufe gedeckt ist (z. B. eine erfolgte Kündigung). Streiche Vermutungen über Ursachen.
- Nenne keine Fristen, Zeitpunkte oder Abläufe, die nicht wörtlich in den Werkzeugaufrufen stehen (z. B. Hilfeartikel). Keine Zusagen, die niemand gegeben hat.
- Deutsch, per Du, freundlich, knapp. Anrede mit Vornamen, Gruß „FocusFlow Support“. Kein Markdown.
Gib nur den Text der Antwort aus."""


def vorname(name: str) -> str:
    return name.split()[0]


def zwischenbescheid(name: str) -> str:
    """Fester Text, solange ein Mensch noch nicht entschieden hat. Enthält bewusst keine Zusage und keine Frist."""
    return (f"Hallo {vorname(name)},\n\n"
            "danke für deine Nachricht. Wir haben uns dein Konto angesehen. Über eine Erstattung entscheidet bei uns "
            "immer ein Mitarbeiter persönlich, deshalb haben wir deinen Fall zur Prüfung weitergegeben. "
            "Sobald die Entscheidung gefallen ist, bekommst du von uns eine Antwort.\n\n"
            "Viele Grüße\nFocusFlow Support")


def entscheidungen_als_text(empfehlungen: list[dict]) -> str:
    zeilen = []
    for e in empfehlungen:
        wort = {"bestaetigt": "bestätigt", "abgelehnt": "abgelehnt"}.get(e.get("entscheidung"), "offen")
        # Nur die Begründung für den Kunden. Die interne Notiz (freigaben.notiz) kommt hier nie an.
        k = f" Begründung für den Kunden: {e['kommentar']}" if e.get("kommentar") else " Keine Begründung angegeben."
        zeilen.append(f"Erstattung über {usd(e['betrag_usd'])} für Zahlung {e['zahlungs_id']}: {wort}.{k}")
    return "\n".join(zeilen)


def vorlage(name: str, empfehlungen: list[dict]) -> str:
    """Rückfallebene ohne LLM."""
    teile = []
    for e in empfehlungen:
        if e.get("entscheidung") == "bestaetigt":
            teile.append(f"Die Erstattung über {usd(e['betrag_usd'])} für deine Zahlung {e['zahlungs_id']} ist freigegeben.")
        else:
            teile.append(f"Eine Erstattung für deine Zahlung {e['zahlungs_id']} können wir leider nicht vornehmen."
                         + (f" {e['kommentar']}" if e.get("kommentar") else ""))
    return f"Hallo {vorname(name)},\n\n" + "\n\n".join(teile) + "\n\nViele Grüße\nFocusFlow Support"


def antwort_inhalt(lauf: dict, empfehlungen: list[dict]) -> str:
    entwurf = (lauf.get("ergebnis") or {}).get("entwurf") or "(kein Entwurf)"
    return (f"<ticket>\n{lauf['text']}\n</ticket>\n\n<trajektorie>\n{trajektorie_als_kontext(lauf['ereignisse'])}\n</trajektorie>\n\n"
            f"<entwurf_des_agents>\n{entwurf}\n</entwurf_des_agents>\n\n<entscheidung>\n{entscheidungen_als_text(empfehlungen)}\n</entscheidung>\n\n"
            "Schreib jetzt die endgültige Antwort an den Kunden.")


def antwort_schreiben(client, lauf: dict, empfehlungen: list[dict]) -> dict:
    """Ein Haiku-Aufruf. Rückgabe: {text, kosten_usd, modell, input_tokens, output_tokens}."""
    r = client.messages.create(model=ANTWORT_MODELL, max_tokens=ANTWORT_MAX_TOKENS, system=ANTWORT_SYSTEM,
                               messages=[{"role": "user", "content": antwort_inhalt(lauf, empfehlungen)}])
    p = PREISE[ANTWORT_MODELL]
    kosten = (r.usage.input_tokens * p["input"] + r.usage.output_tokens * p["output"]) / 1e6
    if r.stop_reason != "end_turn":
        raise RuntimeError(f"stop_reason={r.stop_reason} (Kosten {kosten:.4f} USD)")
    text = "".join(b.text for b in r.content if b.type == "text").strip()
    return {"text": text, "kosten_usd": round(kosten, 6), "modell": ANTWORT_MODELL,
            "input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens}


def json_kurz(d) -> str:
    return json.dumps(d, ensure_ascii=False)
