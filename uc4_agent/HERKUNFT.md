# Herkunft

Kopie aus `ai-uc-04-agents-mcp`, Commit `7997be7` (Merge PR #2, Abschluss UC4), übernommen am 2026-09-28.

Übernommen: `agent.py`, `werkzeuge.py`, `mcp_server.py`, `data/`, `corpus/`, `evals/aufgaben.json` und die Tests ohne LLM (`tests/uc4/`).

Änderungen gegenüber UC4, bewusst minimal:

- Imports relativ (`from .werkzeuge import …`), weil die Dateien jetzt ein Paket sind.
- `agent.py`: Die SDK-Konfiguration ist aus `bearbeite_ticket` in `baue_optionen` herausgelöst, damit die Web-App dieselbe Konfiguration nutzt. Neu ist nur der optionale Parameter `env_extra` für Umgebungsvariablen des CLI-Prozesses.

Unverändert: Modell Haiku 4.5, Prompt v3, Stop-Hook (Pflichten), PreToolUse-Hook (nur `mcp__focusflow__*`), `max_budget_usd = 0.50`, `max_turns = 25`, alle sieben Werkzeuge und damit die Autonomie-Matrix. Es gibt weiterhin kein Werkzeug, das eine Erstattung auslöst oder eine Antwort versendet.
