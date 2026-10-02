"""Gilt für alle Tests außerhalb von tests/uc4: kein echter API-Aufruf durch Judge oder Antwort-Schreiber."""
import pytest

AUFRUFE = {"judge": [], "antwort": []}


def fake_judge(client, ticket, text, ereignisse, entscheidung=None, modell="claude-sonnet-5"):
    AUFRUFE["judge"].append({"text": text, "entscheidung": entscheidung})
    return {"keine_spekulation": "Spekulation" not in text, "keine_spekulation_begruendung": "fake",
            "keine_zusage": "wird erstattet" not in text, "keine_zusage_begruendung": "fake",
            "judge_version": "u1", "modell": modell, "input_tokens": 1000, "output_tokens": 200, "kosten_usd": 0.02, "dauer_s": 0.01}


def fake_antwort(client, lauf, empfehlungen):
    AUFRUFE["antwort"].append({"run_id": lauf["run_id"], "entscheidungen": [e["entscheidung"] for e in empfehlungen]})
    wort = "freigegeben" if all(e["entscheidung"] == "bestaetigt" for e in empfehlungen) else "abgelehnt"
    return {"text": f"Hallo, deine Erstattung ist {wort}.", "kosten_usd": 0.01, "modell": "claude-haiku-4-5",
            "input_tokens": 5000, "output_tokens": 300}


def fake_ohne_zusage(client, lauf, kommentar):
    AUFRUFE["antwort"].append({"run_id": lauf["run_id"], "entscheidungen": ["zusage_gestrichen"]})
    return {"text": "Hallo, eine Erstattung ist dafür nicht vorgesehen.", "kosten_usd": 0.01, "modell": "claude-haiku-4-5",
            "input_tokens": 5000, "output_tokens": 300}


@pytest.fixture(autouse=True)
def kein_echter_llm_aufruf(monkeypatch):
    AUFRUFE["judge"].clear(); AUFRUFE["antwort"].clear()
    monkeypatch.setattr("app.antwort.ohne_zusage_schreiben", fake_ohne_zusage)
    monkeypatch.setattr("app.pruefung.judge", fake_judge)
    monkeypatch.setattr("app.antwort.antwort_schreiben", fake_antwort)
    return AUFRUFE
