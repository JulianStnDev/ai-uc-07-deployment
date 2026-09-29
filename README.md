# UC7 — Deployment: Der Support-Agent als Web-Demo

> Stand: online auf Cloud Run in Frankfurt (https://uc7-807149335205.europe-west3.run.app, Zugang nur mit Zugangscode), Protokoll in Neon Postgres (Frankfurt), Betriebsseite mit Kosten, Latenz und Stichproben-Judge. Deploy-Anleitung: [docs/deploy.md](docs/deploy.md).

| Anliegen mit Ergebnis | Support-Konsole |
|---|---|
| ![Anliegen](docs/screenshots/app/lauf-ergebnis-desktop.png) | ![Konsole](docs/screenshots/app/konsole-desktop.png) |

| Zwischenbescheid (Variante A) | Betriebsseite |
|---|---|
| ![Zwischenbescheid](docs/screenshots/betrieb/1-zwischenbescheid.png) | ![Betrieb](docs/screenshots/betrieb/4-betrieb.png) |

## Problem
Der Support-Agent aus UC4 (Claude Agent SDK, Haiku 4.5, eigener MCP-Server) lief bisher nur als Skript. UC7 bringt ihn als Web-Demo online: Besucher mit Zugangscode schicken Tickets, sehen live, welche Werkzeuge der Agent aufruft, und ein Mensch entscheidet auf einer Freigabe-Seite über Erstattungsempfehlungen. Diese Entscheidungen sind die Datenbasis für die spätere Frage, ob Erstattungen autonom werden dürfen.

## PM-Entscheidung
Siehe [docs/plan.md](docs/plan.md) (Hosting-Recherche mit Quellen) und [docs/decisions.md](docs/decisions.md). Kurz: Google Cloud Run in Frankfurt, Protokoll in Neon Postgres, Web-Framework FastAPI + Jinja2 + htmx mit Server-Sent Events. Harte Budgetgrenze 5 USD API-Kosten im Monat, in der App erzwungen (Deckel 4,50 USD plus 0,50 USD Reserve je laufendem Ticket).

Branch (c): Agent-Entwürfe sagten Erstattungen zu, bevor ein Mensch entschieden hatte (Judge: 4 von 5 Entwürfen mit Empfehlung). Deshalb **Variante A**: Der Kunde sieht erst einen festen Zwischenbescheid, die endgültige Antwort schreibt Haiku 4.5 nach der Freigabe. In der Konsole gibt es dafür „Begründung für den Kunden“ (geht in die Antwort) und „Interne Notiz“ (geht nie an ein Modell). Qualität misst ein **Judge mit Sonnet 5 auf 20 % der Läufe**, kalibriert gegen UC4 und eine Handprüfung (20/20, Haiku 15/20).

## Architekturskizze
```
Browser ──POST /lauf──▶ FastAPI ──asyncio-Task──▶ uc4_agent (Agent SDK ─▶ gebündelte Claude-CLI ─▶ Haiku 4.5)
   ▲                       │                              │ Werkzeugaufrufe (MCP in-process)
   └──── SSE /lauf/{id}/stream ◀── Beobachter ◀───────────┘
                           │
                           └──▶ Neon Postgres (lokal ohne DATABASE_URL: SQLite): Läufe, Kosten, Empfehlungen, Freigaben
```
- `uc4_agent/`: Kopie des UC4-Agents (Prompt v3, Stop-Hook, Autonomie-Matrix unverändert), Herkunft in `uc4_agent/HERKUNFT.md`.
- `app/`: Web-App. `lauf.py` führt ein Anliegen aus, `budget.py` rechnet den Monatsdeckel, `speicher.py` ist das Protokoll, `main.py` die Routen, `darstellung.py` die Texte der Zeitleiste, `hinweise.py` die Info-Hinweise und die Titelregel, `pruefung.py` Regeln und Judge, `antwort.py` Zwischenbescheid und endgültige Antwort.
- Seiten: `/` Start, `/anliegen` Kundenportal, `/lauf/{id}` Portal + „Hinter den Kulissen“ (live per SSE), `/freigaben` Support-Konsole, `/betrieb` Kosten, Latenz, Fehler, Regeln und Judge.

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
Details: [evals/results.md](evals/results.md), Judge-Kalibrierung: [evals/kalibrierung.md](evals/kalibrierung.md).

- Judge u1 (Sonnet 5) auf echten Anfragen: keine Spekulation 3/3, keine Zusage 3/3. Regelprüfungen ohne LLM: 5 Regeln, alle Läufe erfüllt.
- Variante A über die öffentliche URL geprüft: Bestätigung, Ablehnung, Fall ohne Empfehlung.
- Kaltstart (5 Messungen + 1 Agent-Lauf, jeweils per Log bestätigt): Seite kalt Median 3,4 s (2,3–5,5 s), warm 0,03 s. Agent-Lauf aus dem Kaltstart: erster Schritt nach 9,8 s (warm 8,1 s).
- Die Stichprobe ist klein (7 Läufe im Betrieb). Das ist ein Betriebsnachweis, die Qualitätsaussage zum Agent stammt aus UC4 (45 Läufe).

## Kosten & Latenz
- **Kosten pro 1000 Requests: 31,9 USD** (Agent, Judge-Stichprobe und endgültige Antworten zusammen, 7 Läufe auf Cloud Run). Cloud Run selbst lag im Freikontingent.
- **p95-Latenz: 39,7 s** (Median 20,0 s, n = 7, p95 entspricht hier dem längsten Lauf). Kaltstart kostet zusätzlich ca. 3 s.
- **Qualitätsmetrik: Judge-Quote 3/3** bei beiden Kriterien (keine Spekulation, keine Zusage). Der Judge stimmt in der Kalibrierung zu 20/20 mit der Handprüfung überein.
- Kosten UC7 insgesamt für Test- und Messläufe: 0,61 USD.

## Learnings
- **Was immer gelten muss, gehört in den Code (aus UC4 bestätigt).** Prompt v3 verbietet Zusagen, trotzdem sagten 4 von 5 Entwürfen Erstattungen zu. Erst Variante A löst das strukturell.
- **Ein Kommentarfeld für zwei Zwecke ist eine Datenschutzlücke.** Was intern gemeint war, wäre zum Kunden gelangt. Die Trennung ist per Test belegt: Der Test schneidet den kompletten API-Aufruf mit.
- **Kalibrieren vor dem Kostensparen.** Haiku als Judge wäre 3,6-mal billiger, aber bei Spekulation zu nachsichtig (15/20 gegen 20/20).
- **Messwerkzeuge brauchen selbst eine Gegenprobe.** Das Kaltstart-Skript zählte leere Monitoring-Daten als Leerlauf und suchte den Instanzstart im falschen Zeitfenster. Erst beim Abgleich mit den Logs fiel das auf.
- **Abrechnung pro Instanz ist für Agents Pflicht:** Nur so läuft ein Lauf zu Ende, wenn der Tab geschlossen wird. Bei min. 0 Instanzen kostet das im Leerlauf nichts.

## Was ich anders machen würde
- Die Kundenantwort von Anfang an vom Agent-Entwurf trennen (Variante A gleich in Branch (a)), statt Entwürfe mit Zusagen erst anzuzeigen.
- Konsolenfelder nach Empfänger benennen („für den Kunden“, „intern“), nicht nach Form („Kommentar“).
- Messskripte zuerst mit einer einzelnen Messung gegen die Logs prüfen, bevor sie zwei Stunden laufen.
- Ob Haiku die Begründung für den Kunden jetzt zuverlässig übernimmt, ist live noch nicht gemessen. Das wäre der nächste Kontrolllauf.
