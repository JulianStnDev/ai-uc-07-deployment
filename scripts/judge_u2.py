"""Vergleich Judge u2 gegen u1 (Sonnet 5): die 10 Kalibrierungsfälle plus der Kontrolllauf T03 vom 2026-09-29.

u2 ändert nur eines: Die Begründung für den Kunden in <entscheidung> zählt als Beleg. Die 10 Kalibrierungsfälle
haben keine Entscheidung, dort sollte u2 wie u1 urteilen (Regressionsprobe). T03 ist der Fall, um den es geht.

Aufruf: .venv/bin/python scripts/judge_u2.py ../ai-uc-04-agents-mcp
Kosten (geschätzt 2026-09-29): 11 Sonnet-Urteile, u1 kostete dafür 0,239 USD, also ca. 0,24 USD. Abbruch über 0,45 USD.
u1-Werte: evals/kalibrierung.jsonl (Sonnet) und die gespeicherte Prüfung des T03-Laufs in Neon.
Ergebnis: evals/judge_u2.jsonl
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
from kalibrierung import LAEUFE, lese_jsonl  # noqa: E402

T03_LAUF = "20260929-045000-656c12"
MODELL = "claude-sonnet-5"
ABBRUCH_USD = 0.45


def faelle(uc4: Path) -> list[dict]:
    aufgaben = {a["id"]: a for a in json.loads((uc4 / "evals" / "aufgaben.json").read_text())["aufgaben"]}
    u1 = {z["run_id"]: z[MODELL] for z in lese_jsonl(Path("evals/kalibrierung.jsonl"))}
    liste = []
    for run_id in LAEUFE:
        d = uc4 / "evals" / "laeufe" / "v3" / run_id
        liste.append({"fall": run_id, "ticket": aufgaben[run_id.split("_")[0]]["text"],
                      "text": lese_jsonl(d / "antwortentwuerfe.jsonl")[-1]["text"],
                      "ereignisse": [{**e, "art": "werkzeug"} for e in lese_jsonl(d / "trajektorie.jsonl")],
                      "entscheidung": None, "u1": u1[run_id]})
    sp = Speicher(database_url=os.environ["DATABASE_URL"])
    lauf = sp.lauf(T03_LAUF)
    u1_t03 = next(p["judge"] for p in sp.pruefungen() if p["run_id"] == T03_LAUF and p["art"] == "antwort")
    liste.append({"fall": f"T03_live ({T03_LAUF})", "ticket": lauf["text"], "text": sp.antwort(T03_LAUF)["text"],
                  "ereignisse": lauf["ereignisse"],
                  "entscheidung": antwort.entscheidungen_als_text(sp.empfehlungen_zum_lauf(T03_LAUF)), "u1": u1_t03})
    sp.backend.schliessen()
    return liste


def main(uc4: Path):
    load_dotenv()
    assert pruefung.JUDGE_VERSION == "u2"
    client = anthropic.Anthropic()
    out = Path("evals/judge_u2.jsonl"); out.write_text("")
    summe = 0.0
    for f in faelle(uc4):
        if summe > ABBRUCH_USD:
            raise SystemExit(f"Abbruch: {summe:.4f} USD überschreitet {ABBRUCH_USD} USD")
        r = pruefung.judge(client, f["ticket"], f["text"], f["ereignisse"], f["entscheidung"], modell=MODELL)
        summe += r["kosten_usd"]
        u1 = {k: f["u1"][k] for k in ("keine_spekulation", "keine_zusage")}
        zeile = {"fall": f["fall"], "entscheidung": f["entscheidung"], "u1": {**u1, "begruendung": f["u1"]["keine_spekulation_begruendung"]}, "u2": r}
        with out.open("a") as fh:
            fh.write(json.dumps(zeile, ensure_ascii=False) + "\n")
        print(f"{f['fall']}: u1 spek={u1['keine_spekulation']} zusage={u1['keine_zusage']} | "
              f"u2 spek={r['keine_spekulation']} zusage={r['keine_zusage']} | {r['kosten_usd']:.4f} USD", flush=True)
    print(f"Summe: {summe:.4f} USD")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
