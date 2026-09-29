"""Kalibrierung des Judges (Fassung u1) an 10 UC4-Läufen: Sonnet 5 und Haiku 4.5 gegen die UC4-Urteile (j2).

Aufruf: .venv/bin/python scripts/kalibrierung.py ../ai-uc-04-agents-mcp
Kosten (geschätzt 2026-09-28): Sonnet 10 × ~0,02 USD + Haiku 10 × ~0,007 USD ≈ 0,27 USD. Abbruch über 0,60 USD.
Ergebnis: evals/kalibrierung.jsonl (Rohdaten) und evals/kalibrierung.md (Vergleich).
"""
import json
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.pruefung import judge  # noqa: E402

LAEUFE = ["T01_lauf1", "T03_lauf1", "T04_lauf1", "T08_lauf1", "T15_lauf1",       # UC4: keine_spekulation true
          "T01_lauf3", "T06_lauf1", "T07_lauf1", "T11_lauf1", "T13_lauf2"]       # UC4: keine_spekulation false
MODELLE = ["claude-sonnet-5", "claude-haiku-4-5"]
ABBRUCH_USD = 0.60


def lese_jsonl(p: Path) -> list[dict]:
    return [json.loads(z) for z in p.read_text(encoding="utf-8").splitlines() if z.strip()] if p.exists() else []


def main(uc4: Path):
    load_dotenv()
    client = anthropic.Anthropic()
    aufgaben = {a["id"]: a for a in json.loads((uc4 / "evals" / "aufgaben.json").read_text())["aufgaben"]}
    uc4_urteile = {z["run_id"]: z for z in lese_jsonl(uc4 / "evals" / "scores_v3.jsonl")}
    out = Path("evals/kalibrierung.jsonl"); out.write_text("")
    summe = 0.0
    for run_id in LAEUFE:
        d = uc4 / "evals" / "laeufe" / "v3" / run_id
        trajektorie = [{**e, "art": "werkzeug"} for e in lese_jsonl(d / "trajektorie.jsonl")]
        entwurf = lese_jsonl(d / "antwortentwuerfe.jsonl")[-1]["text"]
        ticket = aufgaben[run_id.split("_")[0]]["text"]
        u = uc4_urteile[run_id]
        zeile = {"run_id": run_id, "entwurf": entwurf,
                 "uc4_j2": {"keine_spekulation": u["keine_spekulation"], "begruendung": u["keine_spekulation_begruendung"]}}
        for m in MODELLE:
            if summe > ABBRUCH_USD:
                raise SystemExit(f"Abbruch: {summe:.4f} USD überschreitet {ABBRUCH_USD} USD")
            r = judge(client, ticket, entwurf, trajektorie, modell=m)
            summe += r["kosten_usd"]
            zeile[m] = r
            print(f"{run_id} {m}: spek={r['keine_spekulation']} zusage={r['keine_zusage']} {r['kosten_usd']:.4f} USD", flush=True)
        with out.open("a") as f:
            f.write(json.dumps(zeile, ensure_ascii=False) + "\n")
    print(f"Summe: {summe:.4f} USD")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
