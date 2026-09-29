"""T03 live dreimal mit Judge u1, die Support-Entscheidung ist als eigener Schritt im Ablauf (kein neuer Prompt).

Erwartung: Die übernommene Begründung für den Kunden wird nicht mehr als Spekulation gewertet.
Aufruf: .venv/bin/python scripts/judge_t03_schritt.py
Kosten (geschätzt 2026-09-29): 3 × ca. 0,018 USD = ca. 0,055 USD. Abbruch über 0,10 USD. Ergebnis: evals/judge_t03_schritt.jsonl
"""
import json
import os
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import antwort, pruefung  # noqa: E402
from app.speicher import Speicher  # noqa: E402

T03_LAUF = "20260929-045000-656c12"
WIEDERHOLUNGEN = 3
ABBRUCH_USD = 0.10


def main():
    load_dotenv()
    assert pruefung.JUDGE_VERSION == "u1"
    sp = Speicher(database_url=os.environ["DATABASE_URL"])
    lauf, empfehlungen = sp.lauf(T03_LAUF), sp.empfehlungen_zum_lauf(T03_LAUF)
    text = sp.antwort(T03_LAUF)["text"]
    sp.backend.schliessen()
    ablauf = pruefung.mit_entscheidung(lauf["ereignisse"], empfehlungen)   # wie in app/main.py
    entscheidung = antwort.entscheidungen_als_text(empfehlungen)
    client = anthropic.Anthropic()
    out = Path("evals/judge_t03_schritt.jsonl"); out.write_text("")
    summe = 0.0
    for i in range(1, WIEDERHOLUNGEN + 1):
        if summe > ABBRUCH_USD:
            raise SystemExit(f"Abbruch: {summe:.4f} USD überschreitet {ABBRUCH_USD} USD")
        r = pruefung.judge(client, lauf["text"], text, ablauf, entscheidung)
        summe += r["kosten_usd"]
        with out.open("a") as f:
            f.write(json.dumps({"lauf": T03_LAUF, "wiederholung": i, "urteil": r}, ensure_ascii=False) + "\n")
        print(f"{i}: spek={r['keine_spekulation']} zusage={r['keine_zusage']} {r['kosten_usd']:.4f} USD\n"
              f"   spek: {r['keine_spekulation_begruendung']}", flush=True)
    print(f"Summe: {summe:.4f} USD")


if __name__ == "__main__":
    main()
