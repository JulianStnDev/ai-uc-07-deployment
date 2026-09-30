"""Exportiert einen echten, abgeschlossenen Lauf aus Neon als Aufzeichnung für die Startseite (app/replay/).

Nur lesend, keine API-Kosten. Exportiert wird, was der Besucher auch live sähe: Lauf, Ereignisse,
Empfehlungen samt Entscheidung und Begründung für den Kunden, endgültige Antwort, Prüfungen.
Die interne Notiz der Konsole wird nicht gelesen.

    .venv/bin/python scripts/replay_export.py 20260928-190527-a9c291
"""

import json
import sys
from pathlib import Path

from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.speicher import Speicher  # noqa: E402

ZIEL = Path(__file__).resolve().parents[1] / "app" / "replay" / "aufzeichnung.json"


def main(run_id: str) -> None:
    s = Speicher(database_url=dotenv_values(".env")["DATABASE_URL"])
    lauf = s.lauf(run_id)
    if lauf is None or lauf["status"] != "fertig":
        sys.exit(f"Lauf {run_id} fehlt oder ist nicht fertig.")
    empfehlungen = s.empfehlungen_zum_lauf(run_id)  # lädt die interne Notiz absichtlich nicht
    antwort = s.antwort(run_id)
    if not empfehlungen or any(e["entscheidung"] is None for e in empfehlungen) or antwort is None:
        sys.exit("Für die Aufzeichnung braucht der Lauf eine Entscheidung in der Konsole und eine endgültige Antwort.")
    daten = {
        "lauf": {k: lauf[k] for k in ("run_id", "erstellt", "kunden_id", "absender", "text", "titel", "status",
                                      "kosten_usd", "dauer_s", "ergebnis", "ereignisse")},
        "empfehlungen": empfehlungen,
        "antwort": {k: antwort[k] for k in ("erstellt", "text", "quelle", "modell", "kosten_usd")},
        "pruefungen": s.pruefungen_zum_lauf(run_id),
    }
    s.schliessen()
    ZIEL.write_text(json.dumps(daten, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{ZIEL.relative_to(Path.cwd())}: {len(lauf['ereignisse'])} Ereignisse, {len(empfehlungen)} Empfehlung(en)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "20260928-190527-a9c291")
