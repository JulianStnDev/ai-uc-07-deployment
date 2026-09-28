# UC7 — Deployment: Der Support-Agent als Web-Demo

> Stand: Branch (a), lokal lauffähig mit Dockerfile. Deployment (Cloud Run + Neon) folgt in Branch (b), Monitoring in (c).

## Problem
Der Support-Agent aus UC4 (Claude Agent SDK, Haiku 4.5, eigener MCP-Server) lief bisher nur als Skript. UC7 bringt ihn als Web-Demo online: Besucher mit Zugangscode schicken Tickets, sehen live, welche Werkzeuge der Agent aufruft, und ein Mensch entscheidet auf einer Freigabe-Seite über Erstattungsempfehlungen. Diese Entscheidungen sind die Datenbasis für die spätere Frage, ob Erstattungen autonom werden dürfen.

## PM-Entscheidung
Siehe [docs/plan.md](docs/plan.md) (Hosting-Recherche mit Quellen) und [docs/decisions.md](docs/decisions.md). Kurz: Google Cloud Run in Frankfurt, Protokoll in Neon Postgres, Web-Framework FastAPI + Jinja2 + htmx mit Server-Sent Events. Harte Budgetgrenze 5 USD API-Kosten im Monat, in der App erzwungen (Deckel 4,50 USD plus 0,50 USD Reserve je laufendem Ticket).

## Architekturskizze
```
Browser ──POST /lauf──▶ FastAPI ──asyncio-Task──▶ uc4_agent (Agent SDK ─▶ gebündelte Claude-CLI ─▶ Haiku 4.5)
   ▲                       │                              │ Werkzeugaufrufe (MCP in-process)
   └──── SSE /lauf/{id}/stream ◀── Beobachter ◀───────────┘
                           │
                           └──▶ SQLite (Branch a) / Neon (Branch b): Läufe, Kosten, Empfehlungen, Freigaben
```
- `uc4_agent/`: Kopie des UC4-Agents (Prompt v3, Stop-Hook, Autonomie-Matrix unverändert), Herkunft in `uc4_agent/HERKUNFT.md`.
- `app/`: Web-App. `lauf.py` führt ein Ticket aus, `budget.py` rechnet den Monatsdeckel, `speicher.py` ist das Protokoll, `main.py` die Routen.

## Lokal starten

Voraussetzung: Python 3.10+ oder Docker. `.env.example` nach `.env` kopieren und ausfüllen (API-Key, Zugangscode, Session-Secret).

```bash
# ohne Docker
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn --factory app.main:create_app --reload
# → http://localhost:8000

# mit Docker (auf dem Mac z. B. mit Colima: brew install colima docker && colima start)
docker build -t uc7-web .
docker run --rm -p 8080:8080 --memory 1g --cpus 1 --env-file .env -v uc7-daten:/daten uc7-web
# → http://localhost:8080

# Tests ohne LLM (kosten nichts)
.venv/bin/python -m pytest
```

Jedes Ticket kostet echtes Geld (ca. 0,03 USD, höchstens 0,50 USD).

## Evaluationsergebnisse
Folgt in Branch (c): Stichproben-Judge auf echten Anfragen. Kontrollläufe in Branch (a) siehe PR.

## Kosten & Latenz
- Kosten pro 1000 Requests: folgt (UC4-Referenz 27 USD, Kontrollläufe hier 31 und 36 USD je 1000)
- p95-Latenz: folgt (UC4-Referenz 37 s)
- Qualitätsmetrik: folgt (Judge-Quote und Anteil bestätigter Erstattungsempfehlungen)

## Learnings
Folgt.

## Was ich anders machen würde
Folgt.
