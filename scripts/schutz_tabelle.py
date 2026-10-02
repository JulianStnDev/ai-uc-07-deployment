"""Vorher-/Nachher-Tabelle für den Schutz im Code (UC6, Branch b). Ohne API.

Schickt jeden Fall aus tests/schutz_faelle.py durch den PreToolUse-Hook, den agent.baue_optionen gerade
einrichtet, und, wenn der Hook erlaubt, durch das Werkzeug. Läuft auf dem alten Code (2c8cc86, nur
Werkzeugnamen-Hook) und auf dem neuen, deshalb ohne Annahmen über neue Parameter.

    .venv/bin/python scripts/schutz_tabelle.py
"""

import asyncio
import inspect
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from schutz_faelle import FAELLE  # noqa: E402
from uc4_agent import agent  # noqa: E402
from uc4_agent.werkzeuge import Werkzeugkasten  # noqa: E402


def kasten_fuer(absender: str, runs_dir: Path) -> Werkzeugkasten:
    if "absender_id" in inspect.signature(Werkzeugkasten).parameters:
        return Werkzeugkasten(run_id=f"fall-{absender}", runs_dir=runs_dir, absender_id=absender)
    return Werkzeugkasten(run_id=f"fall-{absender}", runs_dir=runs_dir)


def pre_tool_hook(kasten: Werkzeugkasten):
    """Genau der PreToolUse-Hook, den die SDK-Konfiguration einrichtet."""
    optionen = agent.baue_optionen(kasten, "sk-ant-platzhalter", [])
    (matcher,) = optionen.hooks["PreToolUse"]
    (hook,) = matcher.hooks
    return hook


def fall_ausfuehren(absender: str, werkzeug: str, eingabe: dict, runs_dir: Path) -> tuple[str, str]:
    kasten = kasten_fuer(absender, runs_dir)
    antwort = asyncio.run(pre_tool_hook(kasten)({"tool_name": agent.PREFIX + werkzeug, "tool_input": eingabe}, None, None))
    out = antwort["hookSpecificOutput"]
    if out["permissionDecision"] == "deny":
        return "hook", out["permissionDecisionReason"]
    ergebnis, fehler = kasten.aufrufen(werkzeug, dict(eingabe))
    if fehler:
        return "werkzeug", ergebnis["fehler"]
    return "durch", ""


def main() -> None:
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    print(f"Stand: Commit `{commit}`\n")
    print("| Fall | Lücke | Absender | Aufruf | Ergebnis | Meldung |")
    print("|---|---|---|---|---|---|")
    with tempfile.TemporaryDirectory() as tmp:
        for fid, luecke, absender, werkzeug, eingabe, _ in FAELLE:
            ergebnis, meldung = fall_ausfuehren(absender, werkzeug, eingabe, Path(tmp))
            aufruf = f"`{werkzeug}({json.dumps(eingabe, ensure_ascii=False)})`"
            print(f"| {fid} | {luecke} | {absender} | {aufruf} | {ergebnis} | {meldung.replace('|', '/')} |")


if __name__ == "__main__":
    main()
