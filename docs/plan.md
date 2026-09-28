# UC7 – Recherche und Plan (Stand 2026-09-28)

Ziel: Den UC4-Support-Agent (Claude Agent SDK, Haiku 4.5, eigener MCP-Server, Repo `ai-uc-04-agents-mcp`) als Web-Demo online bringen. Dieses Dokument ist das Ergebnis von Recherche und Planung, **noch kein Code, kein Deployment**. Alle Plattform-Angaben wurden am 2026-09-28 auf den verlinkten Seiten nachgelesen; wo etwas nicht belegbar war, steht „nicht verifiziert".

## Feststehende Entscheidungen (Vorgabe)

- Zugang nur mit Zugangscode.
- Harte Budgetgrenze **5 USD pro Monat**, in der App erzwungen: Kosten je Lauf aus `ResultMessage.total_cost_usd` summieren, bei Erreichen freundliche Abschaltung.
- Autonomie-Matrix aus UC4 bleibt unverändert (Erstattung nur Empfehlung, Antwort nur Entwurf).
- Neu: Freigabe-Seite, auf der ein Mensch Erstattungsempfehlungen bestätigt oder ablehnt. Die Entscheidung wird protokolliert (Datenbasis für die spätere Autonomie-Frage).

## Ausgangswerte aus UC4 (gemessen, 45 Läufe v3)

| Größe | Wert |
|---|---|
| Kosten je Ticket | 0,027 USD (Median 0,027, Max 0,044) |
| Dauer je Ticket | p50 26 s, p95 34 s, Max 40 s |
| Tokens je Lauf (Mittel) | 3.400 Input, 8.000 Cache-Write, 11.200 Cache-Read, 2.300 Output |
| Judge (j3, mit Lauf-Kontext) | ca. 0,02 USD je Urteil |
| Deckel je Lauf | `max_budget_usd = 0.50`, `max_turns = 25` |

Quelle: `../ai-uc-04-agents-mcp/evals/laeufe/v3/*/lauf.json`, README dort.

## Was Anthropic zum Hosting sagt

Quelle: https://code.claude.com/docs/en/agent-sdk/hosting (platform.claude.com leitet dorthin um) und Cookbook https://github.com/anthropics/claude-cookbooks/blob/main/claude_agent_sdk/07_Hosting_the_agent.ipynb

- Das SDK startet je Session einen `claude`-CLI-Subprozess mit eigenem Arbeitsverzeichnis. Zitat: „1 GiB RAM, 5 GiB disk, and 1 CPU per agent is a reasonable starting point … a floor, not the ceiling."
- Python-SDK 0.2.x (aktuell 0.2.160) **bündelt die CLI-Binary**, kein Node.js im Container nötig. Alpine/musl vermeiden, `python:3.12-slim` nehmen. (Das Cookbook-Dockerfile ist älter und installiert noch Node 20 + `@anthropic-ai/claude-code`.)
- Es gibt **keinen eingebauten Session-Timeout**; `max_turns` und `max_budget_usd` sind die Grenzen. Lokaler State (`CLAUDE_CONFIG_DIR`) überlebt keinen Container-Neustart.
- Vier Session-Muster; für uns passt **„ephemeral"**: ein Ticket = ein `query()`-Aufruf = ein Subprozess, danach nichts behalten. Alles Wichtige landet in der Datenbank.
- Das Cookbook zeigt genau unser Muster: FastAPI + SSE (`sse-starlette`) + `async for msg in query(...)`. Deploy-Ziele im Cookbook: lokales Docker, **Modal**, Kubernetes; im Anhang Fly Machines, E2B, Daytona, Cloudflare Containers, Vercel Sandbox. Cloud Run, Render, Railway, HF Spaces werden nicht erwähnt.
- `total_cost_usd` ist eine **clientseitige Schätzung** aus einer eingebauten Preistabelle, keine Abrechnung (https://code.claude.com/docs/en/agent-sdk/cost-tracking). Für die 5-USD-Grenze reicht das, mit Sicherheitsabschlag (siehe unten). Die Usage-&-Cost-Admin-API gibt es nur für Organisations-Konten, nicht für Einzelkonten.
- Haiku-4.5-Preise (https://platform.claude.com/docs/en/about-claude/pricing): 1 USD Input, 5 USD Output, 1,25 USD Cache-Write, 0,10 USD Cache-Read je Mio. Tokens.

## 1. Hosting-Vergleich

Anforderung: Docker, ≥1 GB RAM und 1 CPU je gleichzeitigem Lauf, Anfragen von 30–90 s mit SSE-Streaming, Secrets, wenige Dutzend Läufe im Monat, EU-Region.

| Kriterium | Google Cloud Run | Fly.io | Railway | Modal | Render | HF Spaces (Docker) |
|---|---|---|---|---|---|---|
| Eigenes Dockerfile | ja | ja (Firecracker-VM) | ja | bedingt: Python-Runtime wird injiziert, Server über `@modal.web_server` | ja | ja, aber non-root, ein Port |
| ≥1 GB / 1 CPU | 1 vCPU / 1 GiB frei wählbar | shared-cpu-1x 1 GB ≈ 6,57 USD/Mo (fra) | nutzungsbasiert (10 USD/GB-Mo, 20 USD/vCPU-Mo, nur tatsächlicher Verbrauch) | `cpu=1, memory=1024` ≈ 0,055 USD/h | kleinste Stufe ≥1 GB: 1 CPU / 2 GB = **25 USD/Mo** | CPU Basic 2 vCPU / 16 GB |
| Free Tier | 180.000 vCPU-s + 360.000 GiB-s/Mo, dazu 300 USD Startguthaben | **kein Free Tier** (Trial 2 h / 7 Tage) | Trial 5 USD einmalig, dann Free 1 USD/Mo | 30 USD Guthaben/Mo | 512 MB / 0,1 CPU, schläft nach 15 min | **seit ~07/2026 nur mit PRO (9 USD/Mo)** |
| Request-Timeout / SSE | Default 5 min, **max 60 min**, SSE offiziell | 60 s Idle-Timeout, resettet bei jedem Byte, per `idle_timeout` erhöhbar | 15 min hart, **5 min ohne Daten** (Heartbeat-Guide vorhanden) | **150 s hart** für Web-Endpoints | bis 100 min; SSE nicht explizit dokumentiert | keine offizielle Zahl (Community: ~60 s ohne Streaming) |
| Secrets | Env vars + Secret Manager | `fly secrets set` | Variables, Sealed Variables | `modal.Secret` | Env vars, Secret Files | Space Secrets |
| Inaktivität | Scale-to-zero, Kaltstart nicht beziffert (Community 1–5 s); Warmhalten 13–18 USD/Mo | Autostop/Suspend, Resume „wenige 100 ms" bis ~2 s | opt-in „Serverless", erster Request kann 502 liefern | Scale-to-zero, Boot ~1 s; Warmhalten ≈ 40 USD/Mo | bezahlte Instanz läuft immer | schläft nach 48 h |
| Dauerhafter Speicher | kein Disk (GCS-FUSE ohne Locking) | Volume 0,15 USD/GB, an eine Maschine gebunden | Volume 0,15 USD/GB | Volume 0,09 USD/GiB, 1 TiB frei, kein Locking | Disk 0,25 USD/GB, nur bezahlt | Add-on abgeschafft |
| Kosten/Mo bei uns | **≈ 0 USD** (weit im Free Tier) | ≈ 0,30–0,50 USD mit Autostop, 6,60 USD immer an | ≈ 5 USD (Hobby) | ≈ 0 USD (im Guthaben) | 25 USD | 9 USD |
| Kreditkarte | ja (Billing-Konto, auch für Free Tier) | ja nach Trial | Trial nein, Hobby ja | ja, auch für Free | Free vermutlich nein, 25-USD-Plan ja | ja (PRO) |
| EU-Region | Belgien/Niederlande (Tier 1), **Frankfurt (Tier 2, +40 %)** | fra, ams | Amsterdam | `region="eu"` +15 %, Eingaben laufen über US-Server | Frankfurt | nur mit Team/Enterprise-Org; PRO läuft in den USA |

Quellen (Auszug, vollständige Liste unten): Cloud Run https://cloud.google.com/run/pricing, https://docs.cloud.google.com/run/docs/configuring/request-timeout, https://docs.cloud.google.com/free/docs/free-cloud-features · Fly https://docs.fly.io/about/pricing/, https://docs.fly.io/about/free-trial/, https://docs.fly.io/reference/configuration/ · Railway https://docs.railway.com/reference/pricing/plans, https://docs.railway.com/networking/public-networking/specs-and-limits · Modal https://modal.com/pricing, https://modal.com/docs/guide/webhook-timeouts, https://modal.com/docs/guide/region-selection · Render https://render.com/pricing, https://render.com/docs/free · HF https://huggingface.co/docs/hub/spaces-overview, https://huggingface.co/docs/hub/spaces-gpus

### Empfehlung: Google Cloud Run, Region europe-west1 (Belgien)

> Freigegeben am 2026-09-28 mit Frankfurt (`europe-west3`) als bevorzugter Region, siehe Abschnitt Freigabe.

Begründung:

1. **Einzige Plattform, die alle Kriterien ohne Kompromiss erfüllt:** beliebiges Docker-Image, 1 vCPU / 1 GiB explizit setzbar, Request-Timeout bis 60 min mit offiziell unterstütztem SSE, Secret Manager, echte EU-Region, Scale-to-zero.
2. **Kosten ≈ 0.** 100 Läufe × 45 s × 1 vCPU = 4.500 vCPU-s, das Free Tier hat 180.000. Selbst ohne Free Tier wären es rund 0,15 USD.
3. **Harte Obergrenzen als zweites Sicherheitsnetz zum 5-USD-Budget:** `--max-instances 2` deckelt gleichzeitige Läufe (und damit RAM und Kosten), `--concurrency 1` garantiert 1 GiB je Lauf, dazu ein GCP-Budget-Alarm.
4. **Datenschutz-Argument für UC9:** Belgien oder Frankfurt, Daten bleiben in der EU. Belgien statt Frankfurt, weil Tier 1 und bei unserem Volumen kein Unterschied in der Sache; Frankfurt bleibt wählbar.

Haken, ehrlich benannt:

- Das GCP-Setup (Billing-Konto, Projekt, Artifact Registry, IAM) ist der größte Aufwand und für Einsteiger unübersichtlicher als `fly launch`. Gegenmittel: genau ein Befehl für den Deploy (`gcloud run deploy --source .`), dokumentiert in Branch (b).
- Kaltstart ist offiziell nicht beziffert. Bei 26 s je Lauf sind 2–5 s Kaltstart vertretbar; wird in Branch (b) gemessen.
- CPU ist standardmäßig nur während einer Anfrage zugeteilt. Deshalb läuft alles (Agent, Judge) innerhalb der SSE-Anfrage, nichts im Hintergrund.
- Kreditkarte nötig. Ob die Kombination Cloud-Run-Sandbox + CLI-Subprozess sauber läuft, wird als Erstes per Smoke-Test geprüft.

**Zweite Wahl: Fly.io (fra).** Einfachstes CLI-Erlebnis, im Cookbook-Anhang genannt, Volumes verfügbar. Kostet mit Autostop unter 1 USD, immer an 6,60 USD. Fallback, falls Cloud Run beim Smoke-Test Probleme macht. Nachteil: 60-s-Idle-Timeout zwingt zu Heartbeats (unkritisch bei SSE), kein Free Tier.

Ausgeschieden: **Render** (25 USD/Mo für ≥1 GB, kein Scale-to-zero), **HF Spaces** (Free Tier für Docker Spaces weggefallen, USA-only auf PRO, undokumentiertes Timeout), **Modal** (150-s-Limit, kein echtes Docker, Eingaben über US-Server, trotz Cookbook-Rückenwind), **Railway** (5 USD/Mo Hobby und 5-min-Datentimeout, technisch ok, aber teurer als Cloud Run/Fly ohne Vorteil).

## 2. Speicherort für Protokoll und Freigaben

Anforderung: ein paar tausend Zeilen im Monat (Läufe mit Kosten/Latenz, Werkzeugaufrufe als JSON, Freigaben, Judge-Urteile), Python-Zugriff ohne ORM, EU, kostenlos, ohne Inaktivitäts-Falle.

| Kandidat | Free Tier | Inaktivität | EU | Karte | Python |
|---|---|---|---|---|---|
| **Neon** (Postgres) | 0,5 GB, 100 CU-h/Mo | Scale-to-zero nach 5 min, Kaltstart wenige 100 ms, **keine Pausierung/Löschung** | Frankfurt, London | nein | `psycopg` |
| Turso (SQLite) | 5 GB, 10 Mio. Writes/Mo | **Archiv nach 10 Tagen**, manuell reaktivieren | `fra` (teilweise verifiziert) | nein | `libsql` |
| Supabase (Postgres) | 500 MB, 2 Projekte | **Pause nach 7 Tagen**, dann schlagen Writes fehl | Frankfurt | nein | `psycopg` (Pooler bei IPv4) |
| Render Postgres | 1 GB | **Löschung nach 30+14 Tagen** | Frankfurt | nein | `psycopg` |
| SQLite auf Volume | Fly/Railway 0,15 USD/GB | – | ja | ja | `sqlite3` |
| Cloudflare D1 | 5 GB | keine | Jurisdiction `eu` | nein | nur REST |
| MongoDB Atlas M0 | 512 MB | Pause nach 30 Tagen | kein Frankfurt | nein | `pymongo` |

Quellen: https://neon.com/pricing, https://neon.com/docs/introduction/scale-to-zero, https://neon.com/docs/introduction/regions · https://turso.tech/pricing · https://supabase.com/docs/guides/platform/free-project-pausing · https://render.com/docs/free · https://developers.cloudflare.com/d1/platform/pricing/ · https://www.mongodb.com/docs/atlas/reference/free-shared-limitations/

### Empfehlung: Neon Postgres, Free-Plan, Region Frankfurt

- Einziges Gratisangebot, das eine wochenlang unbenutzte Demo **nicht** pausiert oder löscht. Supabase (7 Tage) und Render (30 Tage) sind für ein sporadisch genutztes Protokoll Fallen.
- Postgres mit JSONB passt zu Trajektorien und Judge-Urteilen; die drei README-Pflichtzahlen sind je ein SQL-Statement.
- Zugriff: `pip install "psycopg[binary]"`, ein `DATABASE_URL`, kein ORM, kein Pooler nötig. Dashboard mit SQL-Editor und Tabellenansicht.
- **Neon-Branches** lösen lokal-vs-produktiv elegant: Branch `main` für die Demo, Branch `dev` für lokale Entwicklung. Gleicher Code, nur eine andere URL. Kein SQLite-Sonderweg.
- 0,5 GB reichen bei ~5 KB je Lauf für Jahre.

Vier Tabellen genügen:

| Tabelle | Inhalt |
|---|---|
| `laeufe` | run_id, Zeit, Ticket-Text, Absender, Kosten, Dauer, Turns, Tokens, Status, Schlusstext, Trajektorie (JSONB) |
| `empfehlungen` | run_id, Zahlungs-ID, Betrag, Begründung des Agents |
| `freigaben` | Empfehlungs-ID, Entscheidung (bestätigt/abgelehnt), Kommentar, Zeit |
| `judge_urteile` | run_id, Judge-Version, Urteil (JSONB), Kosten |

Das Monatsbudget wird nicht gespeichert, sondern berechnet: `SUM(kosten_usd)` über `laeufe` und `judge_urteile` im laufenden Monat.

## 3. Plan für drei Branches

### Web-Framework: FastAPI + Jinja2 + htmx (SSE-Extension)

Begründung:

- Genau das Muster des Anthropic-Hosting-Cookbooks (FastAPI + SSE + `query()`-Generator). `query()` ist ein async-Generator, der 1:1 in einen SSE-Endpoint fließt, ohne Thread-Brücke.
- Wenig Magie: jeder Schritt ist ein sichtbarer HTTP-Request (Formular-POST, `/stream/{id}` als SSE, Freigabe-POST, Dashboard-GET). Im Browser-Netzwerk-Tab nachvollziehbar.
- Zugangscode als Cookie plus eine `Depends`-Funktion, etwa 15 Zeilen.
- FastAPI ≥ 0.135 hat natives SSE (`fastapi.sse.EventSourceResponse`) inklusive Keep-alive-Ping alle 15 s. Aktuell 0.141.1. htmx 2.0.11 + SSE-Extension 2.2.4 (htmx 4 ist da, aber laut Homepage nicht als „latest" markiert).
- Abgelehnt: Streamlit (Rerun bei jedem Klick kollidiert mit 30–90 s laufender Session und Freigabe-Buttons; Passwortschutz nur noch über OIDC dokumentiert), Gradio (Queue-Limit 1, ML-Demo-Layout), Flask (WSGI/synchron, Bruch zum async-SDK), NiceGUI (gut, aber WebSocket-Magie; zweite Wahl).

Quellen: https://fastapi.tiangolo.com/tutorial/server-sent-events/, https://htmx.org/extensions/sse/, https://docs.streamlit.io/get-started/fundamentals/main-concepts, https://www.gradio.app/guides/queuing

### Übernahme des UC4-Agents

`agent.py`, `mcp_server.py`, `werkzeuge.py`, `corpus/` und `data/` werden als Paket `agent/` in dieses Repo **kopiert**, mit Commit-Hash von UC4 in einer Notiz. Kein Submodul, kein Path-Install: am wenigsten Magie, und UC4 ist abgeschlossen. Einzige Änderung: `Werkzeugkasten` schreibt zusätzlich in die Datenbank statt nur in `runs/`.

### Branch (a) `feature/web-app` – lokal lauffähig + Dockerfile

1. Projektstruktur: `app/main.py` (Routen), `app/agent_runner.py` (kapselt `query()`, liefert Events), `app/db.py` (psycopg, ein paar Funktionen, `schema.sql`), `app/templates/`, `agent/` (aus UC4).
2. Seiten: `/` Ticket-Eingabe (Auswahl aus den 15 UC4-Tickets oder Freitext, nur erfundene Kundendaten), `/lauf/{id}` Live-Anzeige der Werkzeugaufrufe per SSE mit Abschluss (Kosten, Dauer, Entwurf), `/freigaben` offene Erstattungsempfehlungen mit Bestätigen/Ablehnen + Kommentar.
3. Jeder Lauf wird vollständig in Neon (Branch `dev`) protokolliert.
4. `Dockerfile` (`python:3.12-slim`, venv, `claude-agent-sdk`, `CLAUDE_CONFIG_DIR=/data`, non-root, Port aus `$PORT`), `docker-compose.yml` für lokal, `.env.example`.
5. Tests ohne API: Freigabe-Logik, Budget-Berechnung, SSE-Event-Serialisierung.
6. Nachweis: `docker compose up`, ein Ticket durchlaufen lassen, Freigabe ausführen, Zeilen in Neon sichtbar.

Geschätzter API-Verbrauch: ~30 Läufe × 0,027 USD ≈ **0,80 USD**.

### Branch (b) `feature/deploy` – Secrets, Zugangscode, Budgetgrenze

1. Zugangscode: Login-Seite, Vergleich mit `hmac.compare_digest`, signiertes Cookie, Middleware schützt alles außer `/login` und `/health`.
2. Budgetgrenze: in Branch (a) bereits eingebaut (siehe Abschnitt Drei Sicherungen), in (b) nur auf Neon umgestellt.
3. Secrets im GCP Secret Manager: `ANTHROPIC_API_KEY`, `DATABASE_URL`, `ZUGANGSCODE`, `SESSION_SECRET`. Per `--set-secrets` als Env vars in den Container.
4. Deploy: `gcloud run deploy uc7 --source . --region europe-west1 --memory 1Gi --cpu 1 --concurrency 1 --max-instances 2 --timeout 300 --min-instances 0`. Der Befehl steht in `docs/deploy.md`.
5. GCP-Budget-Alarm bei 5 USD als zweites Netz.
6. Smoke-Test: 5 Läufe, Kaltstart und Dauer messen, Ergebnis in `docs/decisions.md` festhalten. Falls die CLI im Cloud-Run-Sandbox nicht startet: Fallback Fly.io (fra), Dockerfile bleibt gleich.

Geschätzter API-Verbrauch: ~10 Läufe ≈ **0,30 USD**.

### Branch (c) `feature/monitoring` – Protokoll, Stichproben-Judge, Dashboard

1. `/protokoll`: Tabelle aller Läufe (Zeit, Ticket, Kosten, Dauer, Turns, Status, Freigabe-Status), Detailseite mit Trajektorie.
2. Stichproben-Judge: UC4-Judge j3 (`score.py`) übernehmen. Jeder dritte Lauf wird direkt im Anschluss innerhalb derselben Anfrage bewertet (Cloud Run teilt CPU nur während der Anfrage zu). Zusätzlich ein Button „jetzt bewerten" je Lauf. Kosten ≈ 0,02 USD je Urteil, zählen ins Budget.
3. `/dashboard`: die drei Pflichtzahlen (Kosten je 1000 Requests, p95-Latenz, Judge-Quote) plus die neue Schattenmodus-Zahl: **Anteil bestätigter Erstattungsempfehlungen**. Dazu Budget-Verbrauch des Monats. Reine HTML-Tabellen, kein Chart-Framework.
4. Judge-Kalibrierung: 10–20 Urteile von Hand gegenprüfen (Learning aus UC4: Judge braucht dasselbe Datum und denselben Kontext wie der Agent).

Geschätzter API-Verbrauch: ~20 Judge-Urteile ≈ **0,40 USD**.

## 4. Kostenschätzung (erst schätzen, dann laufen)

### Einrichtung (einmalig)

| Posten | Schätzung |
|---|---|
| Hosting: Cloud Run, Cloud Build, Artifact Registry | 0 USD (Free Tier; Registry-Speicher über 0,5 GB kostet Cent-Beträge) |
| Datenbank: Neon Free | 0 USD |
| API: Branch (a) 0,80 + (b) 0,30 + (c) 0,40 | **≈ 1,50 USD**, Obergrenze 2,50 USD |
| **Summe Einrichtung** | **≈ 1,50 USD** |

### Ein Monat Betrieb bei geringer Nutzung (angenommen: 50 Läufe, 17 Judge-Urteile)

| Posten | Schätzung | Worst Case |
|---|---|---|
| Hosting: Cloud Run (50 × 45 s × 1 vCPU ≈ 2.250 vCPU-s) | 0 USD | 0,50 USD (Egress, Registry) |
| Datenbank: Neon Free (100 CU-h, 0,5 GB) | 0 USD | 0 USD |
| API: 50 × 0,027 + 17 × 0,02 | **≈ 1,70 USD** | **4,50 USD** (App-Deckel), 5,00 USD (Budget) |
| **Summe Betrieb** | **≈ 1,70 USD** | **≈ 5,50 USD** |

Zum Vergleich Fly.io als Fallback: plus 0,50 USD (Autostop) bzw. 6,60 USD (immer an) im Monat.

Was der 5-USD-Deckel bedeutet: bei 0,027 USD je Lauf und jedem dritten Lauf mit Judge sind das rund **130 Läufe im Monat**, danach schaltet die App ab.

Nicht in der Schätzung: Warmhalten von Cloud Run (13–18 USD/Mo, bewusst nicht vorgesehen), Judge-Neubewertungen ganzer Läufe (in UC4 der teuerste ungeplante Posten, 2,69 USD).

## Freigabe (2026-09-28)

- **Hosting:** Cloud Run, Frankfurt (`europe-west3`) bevorzugt, sonst Belgien (`europe-west1`).
- **Datenbank:** Neon Postgres, Frankfurt. In Branch (a) lokal zunächst SQLite, Neon kommt in Branch (b).
- **Tickets:** Besucher wählt einen der 15 fiktiven Kunden aus einer Liste und schreibt freien Text (max. 1000 Zeichen). Der Absender ergibt sich immer aus dem gewählten Kunden und ist nie frei wählbar. Freitext ist eine Angriffsfläche (Prompt Injection), die in UC6 gezielt getestet wird.
- **Dritte Budgetsicherung:** Budgetalarm im Google-Cloud-Konto, richtet der Projektinhaber selbst ein (Anleitung unten).

## Drei Sicherungen gegen Kosten

| Ebene | Was | Wirkung |
|---|---|---|
| 1. Je Lauf | `max_budget_usd = 0.50`, `max_turns = 25` im SDK | Ein einzelner Lauf kann nicht ausufern |
| 2. Je Monat, in der App | Deckel 4,50 USD: Summe der Laufkosten im Kalendermonat plus 0,50 USD Reserve je laufendem Lauf. Ist der Deckel erreicht, startet kein neuer Lauf | Obergrenze 4,50 + 0,50 = 5,00 USD API-Kosten im Monat |
| 3. Google-Cloud-Budgetalarm | E-Mail bei 50 / 90 / 100 % von 5 USD | Nur Warnung, stoppt nichts. Deckt Hosting-Kosten ab, die die App nicht sieht |

Ebene 1 und 2 beziehen sich auf die Anthropic-API. Ebene 3 sieht nur Google-Cloud-Kosten, nicht die API-Kosten. Für die API empfiehlt sich zusätzlich ein Ausgabenlimit in der Claude Console.

### Anleitung: Budgetalarm in Google Cloud einrichten

Quelle: https://docs.cloud.google.com/billing/docs/how-to/budgets (abgerufen 2026-09-28). Nötige Rolle: Billing Account Administrator oder Billing Account Costs Manager.

1. In der Google Cloud Console **Billing → Budgets & alerts → Create budget** öffnen.
2. **Typ:** „Alerts only", Name z. B. `uc7-monatsbudget`, weiter.
3. **Scope:** Zeitraum „Monthly", unter Projects nur das UC7-Projekt wählen, weiter.
4. **Betrag:** „Specified amount", **5** (in der Währung des Billing-Kontos, meist EUR), weiter.
5. **Schwellen:** 50 %, 90 %, 100 % auf „Actual". Optional eine weitere Schwelle 100 % auf „Forecasted", die schon warnt, wenn die Prognose den Betrag überschreitet.
6. **Benachrichtigung:** „Email alerts to billing admins and users" angehakt lassen, **Finish**.

Wichtig laut Doku: Budgets „do not automatically cap … usage". Sie verschicken nur E-Mails, und die erste kann nach dem Anlegen einige Stunden dauern. Die harte Grenze bleibt Ebene 2 in der App, plus `--max-instances` bei Cloud Run.

## Offene Punkte für die Umsetzung

- Smoke-Test CLI-Subprozess in Cloud Run (gen2-Laufzeit) als erster Schritt in Branch (b); Fallback Fly.io.
- Kaltstartzeit messen und im README ausweisen.

## Quellen (alle abgerufen 2026-09-28)

Anthropic: https://code.claude.com/docs/en/agent-sdk/hosting · https://code.claude.com/docs/en/agent-sdk/python · https://code.claude.com/docs/en/agent-sdk/cost-tracking · https://github.com/anthropics/claude-cookbooks/tree/main/claude_agent_sdk/hosting · https://platform.claude.com/docs/en/about-claude/pricing · https://pypi.org/project/claude-agent-sdk/

Cloud Run: https://cloud.google.com/run/pricing · https://docs.cloud.google.com/run/docs/configuring/request-timeout · https://docs.cloud.google.com/run/docs/triggering/https-request · https://docs.cloud.google.com/run/docs/configuring/secrets · https://docs.cloud.google.com/run/docs/configuring/min-instances · https://docs.cloud.google.com/run/docs/locations · https://docs.cloud.google.com/free/docs/free-cloud-features

Fly.io: https://docs.fly.io/about/pricing/ · https://docs.fly.io/about/free-trial/ · https://docs.fly.io/reference/configuration/ · https://docs.fly.io/launch/autostop-autostart/ · https://docs.fly.io/volumes/overview/

Railway: https://railway.com/pricing · https://docs.railway.com/reference/pricing/plans · https://docs.railway.com/networking/public-networking/specs-and-limits · https://docs.railway.com/guides/sse-vs-websockets · https://docs.railway.com/deployments/serverless

Modal: https://modal.com/pricing · https://modal.com/docs/guide/webhook-timeouts · https://modal.com/docs/guide/existing-images · https://modal.com/docs/guide/region-selection · https://modal.com/docs/guide/volumes

Render: https://render.com/pricing · https://render.com/docs/compute-plans · https://render.com/docs/free · https://render.com/docs/disks · https://render.com/docs/regions

Hugging Face: https://huggingface.co/docs/hub/spaces-overview · https://huggingface.co/docs/hub/spaces-gpus · https://huggingface.co/pricing · https://huggingface.co/docs/hub/storage-regions

Datenbanken: https://neon.com/pricing · https://neon.com/docs/introduction/plans · https://neon.com/docs/introduction/scale-to-zero · https://neon.com/docs/introduction/regions · https://neon.com/docs/guides/python · https://turso.tech/pricing · https://supabase.com/pricing · https://supabase.com/docs/guides/platform/free-project-pausing · https://render.com/docs/free · https://developers.cloudflare.com/d1/platform/pricing/ · https://www.mongodb.com/docs/atlas/reference/free-shared-limitations/ · https://litestream.io/

Frameworks: https://fastapi.tiangolo.com/tutorial/server-sent-events/ · https://pypi.org/project/fastapi/ · https://htmx.org/extensions/sse/ · https://docs.streamlit.io/get-started/fundamentals/main-concepts · https://www.gradio.app/guides/queuing · https://nicegui.io/documentation/section_configuration_deployment

Nicht verifiziert: Cloud-Run-Kaltstartdauer (nur Community-Werte); Render-Kartenpflicht im Free Tier; Turso-EU-Standorte für neue Gruppen; Verhalten laufender Modal-SSE-Streams über 150 s; HF-Spaces-Proxy-Timeout.
