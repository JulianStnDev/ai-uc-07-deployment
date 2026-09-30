🇬🇧 [English version](README.md)

# UC7 — Deployment: Der Support-Agent als Web-Demo

> Stand: online auf Cloud Run in Frankfurt (https://uc7-807149335205.europe-west3.run.app): aufgezeichneten Lauf ohne Login ansehen oder bei mir einen persönlichen Live-Zugangslink anfragen. Protokoll in Neon Postgres (Frankfurt), Betriebsseite mit Kosten, Latenz und Stichproben-Judge. Deploy-Anleitung: [docs/deploy.md](docs/deploy.md).

| Aufgezeichneter Lauf (Startseite ohne Login) |
|---|
| ![Replay](docs/screenshots/besucher/replay-desktop.png) |

| Anliegen mit Ergebnis | Support-Konsole |
|---|---|
| ![Anliegen](docs/screenshots/app/lauf-ergebnis-desktop.png) | ![Konsole](docs/screenshots/app/konsole-desktop.png) |

| Zwischenbescheid (Variante A) | Betriebsseite |
|---|---|
| ![Zwischenbescheid](docs/screenshots/betrieb/1-zwischenbescheid.png) | ![Betrieb](docs/screenshots/betrieb/4-betrieb.png) |

## Problem
Der Support-Agent aus UC4 (Claude Agent SDK, Haiku 4.5, eigener MCP-Server) lief bisher nur als Skript. UC7 bringt ihn als Web-Demo online: Besucher schicken Tickets, sehen live, welche Werkzeuge der Agent aufruft, und ein Mensch entscheidet auf einer Freigabe-Seite über Erstattungsempfehlungen. Diese Entscheidungen sind die Datenbasis für die spätere Frage, ob Erstattungen autonom werden dürfen.

## PM-Entscheidung
Siehe [docs/plan.md](docs/plan.md) (Hosting-Recherche mit Quellen) und [docs/decisions.md](docs/decisions.md). Kurz: Google Cloud Run in Frankfurt, Protokoll in Neon Postgres, Web-Framework FastAPI + Jinja2 + htmx mit Server-Sent Events. Harte Budgetgrenze 5 USD API-Kosten im Monat, in der App erzwungen (Deckel 4,50 USD plus 0,50 USD Reserve je laufendem Ticket).

Branch (c): Agent-Entwürfe sagten Erstattungen zu, bevor ein Mensch entschieden hatte (Judge: 4 von 5 Entwürfen mit Empfehlung). Deshalb **Variante A**: Der Kunde sieht erst einen festen Zwischenbescheid, die endgültige Antwort schreibt Haiku 4.5 nach der Freigabe. In der Konsole gibt es dafür „Reason for the customer“ (Begründung für den Kunden, geht in die Antwort) und „Internal note“ (interne Notiz, geht nie an ein Modell). Qualität misst ein **Judge mit Sonnet 5 auf 20 % der Läufe**, kalibriert gegen UC4 und eine Handprüfung (20/20, Haiku 15/20).

Branch (e), Zugang für Portfolio-Besucher: Ohne Login spielt die Startseite einen **aufgezeichneten echten Lauf** ab (T01, Doppelabbuchung, inklusive Entscheidung in der Konsole und endgültiger Antwort), in derselben Ansicht wie live. Er stammt aus dem Protokoll in Neon, einmalig ins Repo exportiert, und braucht weder Datenbank noch API. **Persönliche Links** (`?code=<Pseudonym>-<6 Zufallszeichen>`) geben Live-Zugang mit eigenem Kontingent von 5 Läufen, 60 Tage gültig, einzeln sperrbar. Der Monatsdeckel gilt zusätzlich. Besucher mit Link sehen nur Läufe, die mit ihrem eigenen Code gestartet wurden. Gespeichert werden je Link Code, Zeitpunkt, Anzahl Läufe und Sperrvermerk, keine personenbezogenen Daten. Die Oberfläche für Besucher ist englisch, das Kundengespräch bleibt deutsch, weil der UC4-Agent deutsch ist.

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
- Seiten: `/` aufgezeichneter Lauf (ohne Zugang) oder Start, `/?code=…` persönlicher Link, `/replay` aufgezeichneter Lauf (SSE aus `app/replay/aufzeichnung.json`), `/anliegen` Kundenportal, `/lauf/{id}` Portal + „Behind the scenes“ (live per SSE), `/freigaben` Support-Konsole, `/betrieb` Kosten, Latenz, Fehler, Regeln und Judge (nur Admin).
- Zugang: Admin-Zugangscode (`/login`) sieht alles. Persönliche Links verwaltet `app/links.py` (anlegen, auflisten mit Nutzung, sperren). Sichtbar sind nur Läufe mit dem eigenen Code, auch beim direkten Aufruf einer fremden Lauf-ID.

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

# persönliche Links für Besucher (Datenbank aus .env, wie die App)
.venv/bin/python -m app.links anlegen acme      # gibt den fertigen Link aus
.venv/bin/python -m app.links liste             # Nutzung x/5, Ablauf, Status
.venv/bin/python -m app.links sperren acme-k7m2qx
```

Jedes Ticket kostet echtes Geld (ca. 0,03 USD, höchstens 0,50 USD).

## Evaluationsergebnisse
Details: [evals/results.md](evals/results.md), Judge-Kalibrierung: [evals/kalibrierung.md](evals/kalibrierung.md).

- Judge u1 (Sonnet 5) auf echten Anfragen: keine Spekulation 3/4, keine Zusage 4/4. Die eine Abweichung wertet eine vom Mitarbeiter gegebene Begründung als unbelegt, das Kriterium ist dafür zu eng (siehe results.md). Regelprüfungen ohne LLM: 5 Regeln, alle Läufe erfüllt.
- Variante A über die öffentliche URL geprüft: Bestätigung, Ablehnung, Fall ohne Empfehlung.
- Kontrolllauf T03 mit Ablehnung (2026-09-29): Die Begründung für den Kunden steht sinngemäß in der Antwort. Die interne Notiz erscheint nirgends beim Kunden, nur in der Konsole.
- Kaltstart (5 Messungen + 1 Agent-Lauf, jeweils per Log bestätigt): Seite kalt Median 3,4 s (2,3–5,5 s), warm 0,03 s. Agent-Lauf aus dem Kaltstart: erster Schritt nach 9,8 s (warm 8,1 s).
- Die Stichprobe ist klein (7 Läufe im Betrieb). Das ist ein Betriebsnachweis, die Qualitätsaussage zum Agent stammt aus UC4 (45 Läufe).

## Kosten & Latenz
- **Kosten pro 1000 Requests: 34,1 USD** (Agent, Judge-Stichprobe und endgültige Antworten zusammen, 8 Läufe auf Cloud Run). Cloud Run selbst lag im Freikontingent.
- **p95-Latenz: 39,7 s** (Median 20,0 s, n = 8, p95 entspricht hier dem längsten Lauf). Kaltstart kostet zusätzlich ca. 3 s.
- **Qualitätsmetrik: Judge-Quote keine Spekulation 3/4, keine Zusage 4/4.** Der eine Verstoß ist ein Messfehler des Kriteriums, nicht der Antwort. Der Judge stimmt in der Kalibrierung zu 20/20 mit der Handprüfung überein.
- Kosten UC7 insgesamt für Test- und Messläufe: 0,98 USD. Branch (e) (aufgezeichneter Lauf, Links) kostete kein API-Geld: kein neuer Lauf, Tests und Kontrolle ohne LLM.
- **Je voll genutztem persönlichem Link: ca. 0,17 USD** (5 × 34,1 USD/1000). Der Monatsdeckel von 4,50 USD reicht damit für rund 26 voll genutzte Links. Der aufgezeichnete Lauf kostet kein API-Geld und stellt keine Datenbankabfragen (nur der Start einer neuen Instanz greift einmal auf Neon zu).

## Bekannte Grenzen
- **Die endgültige Antwort liest kein Mensch, nur die Entscheidung.** Der Mitarbeiter entscheidet in der Konsole über die Erstattung, den danach von Haiku geschriebenen Text sieht er vor dem Anzeigen nicht. Deshalb steht im Portal „Decided by support“ (vom Support entschieden) und nicht „approved“ (freigegeben). Für die Demo ist das bewusst akzeptiert. Überwacht wird es über die Judge-Stichprobe (20 %). Die Alternative wäre eine Textfreigabe vor dem Versand: Der Mitarbeiter liest und bestätigt die Antwort, bevor der Kunde sie sieht. Das kostet einen weiteren Klick je Fall und verzögert die Antwort.
- **Der Judge ist nicht vollständig und schwankt.** Bei T03 übersah er einen plausiblen, aber aus dem Lauf nicht belegten Satz einmal (u1 ohne Entscheidungsschritt) und später in 1 von 3 Wiederholungen (evals/results.md).
- **Support-Entscheidungen sieht der Judge als eigenen Schritt im Ablauf** (Status und Begründung für den Kunden, nie die interne Notiz). Der Judge-Prompt bleibt u1. Ein erster Versuch mit einer Prompt-Ausnahme (u2) verschlechterte die Kalibrierung auf 17/20 und wurde verworfen.
- **Die Oberfläche ist englisch, das Kundengespräch deutsch.** Die Kunden von FocusFlow und der UC4-Agent (Prompt v3, Evals) sind deutsch. Sie zu übersetzen würde den Agent verändern und die Evals entwerten. Die Seiten sagen das dazu. Die Screenshots in den Tabellen über dem aufgezeichneten Lauf stammen von vor Branch (e) und zeigen die frühere deutsche Oberfläche.
- **Ein persönlicher Link ist ein Inhaber-Token.** Wer den Link hat, hat den Zugang (höchstens 5 Läufe). Deshalb Zufallszeichen im Code, jederzeit sperrbar, und das Kontingent als Kostengrenze.

## Learnings
- **Was immer gelten muss, gehört in den Code (aus UC4 bestätigt).** Prompt v3 verbietet Zusagen, trotzdem sagten 4 von 5 Entwürfen Erstattungen zu. Erst Variante A löst das strukturell.
- **Ein Kommentarfeld für zwei Zwecke ist eine Datenschutzlücke.** Was intern gemeint war, wäre zum Kunden gelangt. Die Trennung ist per Test belegt: Der Test schneidet den kompletten API-Aufruf mit.
- **Kalibrieren vor dem Kostensparen.** Haiku als Judge wäre 3,6-mal billiger, aber bei Spekulation zu nachsichtig (15/20 gegen 20/20).
- **Messwerkzeuge brauchen selbst eine Gegenprobe.** Das Kaltstart-Skript zählte leere Monitoring-Daten als Leerlauf und suchte den Instanzstart im falschen Zeitfenster. Erst beim Abgleich mit den Logs fiel das auf.
- **Abrechnung pro Instanz ist für Agents Pflicht:** Nur so läuft ein Lauf zu Ende, wenn der Tab geschlossen wird. Bei min. 0 Instanzen kostet das im Leerlauf nichts.
- **Gegen den echten Datenbanktyp testen.** Ein `?` in einem SQL-Kommentar der neuen Tabelle bestand alle SQLite-Tests. In Postgres wird es zum Platzhalter, die App wäre auf Neon nicht gestartet. Der Postgres-Test im eigenen Schema fand es vor dem Deploy.

## Was ich anders machen würde
- Die Kundenantwort von Anfang an vom Agent-Entwurf trennen (Variante A gleich in Branch (a)), statt Entwürfe mit Zusagen erst anzuzeigen.
- Konsolenfelder nach Empfänger benennen („für den Kunden“, „intern“), nicht nach Form („Kommentar“).
- Messskripte zuerst mit einer einzelnen Messung gegen die Logs prüfen, bevor sie zwei Stunden laufen.
- Den Judge von Anfang an mit dem Fall „Mensch liefert den Grund“ kalibrieren. Kriterium u1 zählt nur Werkzeugaufrufe als Beleg und straft so eine korrekt übernommene Begründung ab. Nächster Schritt wäre u2.
- Den Hinweis „Draft“ über der endgültigen Antwort nach der Freigabe ausblenden.
