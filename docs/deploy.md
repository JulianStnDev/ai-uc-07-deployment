# Deployment auf Cloud Run (Frankfurt)

Stand 2026-10-02 (Revision `uc7-00009-pv4`, Commit `d86b433` mit dem Schutz im Code aus UC6; Rollback-Ziel `uc7-00008-qtl`; Datenbankrolle `uc7_app`). Projekt `focusflow-demo-510014` (Anzeigename „focusflow-demo“), Region `europe-west3`, Dienst `uc7`.
Öffentliche URL: https://uc7-807149335205.europe-west3.run.app (ohne Login: aufgezeichneter Lauf; live nur mit persönlichem Link oder Admin-Zugangscode).

## Einmalige Einrichtung

```bash
brew install --cask gcloud-cli
gcloud auth login                                   # im Browser
gcloud config set project focusflow-demo-510014     # Projekt-ID, nicht der Anzeigename
gcloud config set run/region europe-west3

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com billingbudgets.googleapis.com
```

### Budgetalarm (Sicherung 3)

```bash
gcloud billing budgets create --billing-account=010944-24E9CC-B52F37 --display-name="uc7-monatsbudget" \
  --budget-amount=5EUR --filter-projects=projects/focusflow-demo-510014 --calendar-period=month \
  --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0
```
Das Billing-Konto rechnet in EUR ab, daher 5 EUR statt 5 USD (die Währung muss laut gcloud-Referenz der des Kontos entsprechen). Der Alarm schickt nur E-Mails, er stoppt nichts.

### Secrets

Die Werte kommen aus der lokalen `.env`, per stdin, ohne Anzeige:
```bash
for NAME in ANTHROPIC_API_KEY ZUGANGSCODE SESSION_SECRET DATABASE_URL; do
  SID="uc7-$(echo $NAME | tr 'A-Z_' 'a-z-')"
  gcloud secrets create $SID --replication-policy=automatic --labels=app=uc7
  .venv/bin/python -c "import sys;from dotenv import dotenv_values;sys.stdout.write(dotenv_values('.env')['$NAME'])" \
    | gcloud secrets versions add $SID --data-file=-
done
```
Neue Version eines Secrets (z. B. nach Passwort-Reset): `versions add` wie oben, dann im Deploy-Befehl die Versionsnummer erhöhen. Die Versionen sind bewusst fest (nicht `latest`), weil Cloud Run Umgebungsvariablen nur beim Start einer Instanz liest.

### Datenbankrolle `uc7_app` (seit 2026-09-30)

`DATABASE_URL` nutzt nicht den Neon-Owner, sondern eine eigene Rolle nur für die UC7-Datenbank `neondb` (Begründung in docs/decisions.md). Einmalig als Owner angelegt, das Passwort per `secrets.token_urlsafe(32)` erzeugt und nie angezeigt:
```sql
CREATE ROLE uc7_app LOGIN PASSWORD '…';
GRANT CONNECT ON DATABASE neondb TO uc7_app;
GRANT USAGE ON SCHEMA public TO uc7_app;
GRANT SELECT, INSERT, UPDATE ON laeufe, empfehlungen, pruefungen, antworten, freigaben, zugangslinks TO uc7_app;
GRANT DELETE ON pruefungen TO uc7_app;   -- einzige Löschung der App: Prüfung neu schreiben
```
Die Rolle darf kein DDL (keine Tabellen anlegen, ändern oder löschen, kein `TRUNCATE`, keine Schemas, keine Rollen). Sie kommt nicht in die Datenbanken `analytics` und `uc5_app`, weil dort PUBLIC kein `CONNECT` hat.

- **Schemaänderungen:** Die App prüft beim Start, ob alle Tabellen und nachgerüsteten Spalten da sind, und führt dann kein DDL aus. Kommt eine Tabelle oder Spalte dazu, die App einmal lokal mit einer Owner-`DATABASE_URL` starten (oder das SQL als Owner ausführen), danach `GRANT` für die neue Tabelle an `uc7_app`, dann erst deployen.
- **Postgres-Tests** (`UC7_PG_TEST=1`) legen ein eigenes Schema an und brauchen dafür eine Rolle mit `CREATE` auf der Datenbank, also nicht `uc7_app`.
- **Passwort wechseln:** `ALTER ROLE uc7_app PASSWORD '…'` als Owner, neue Secret-Version, Versionsnummer im Deploy-Befehl erhöhen, lokale `.env` anpassen.

### Dienstkonten

- **Laufzeit:** eigenes Konto `uc7-run`, darf nur die vier Secrets lesen (`roles/secretmanager.secretAccessor` je Secret). Kein Editor, kein Zugriff auf andere Dienste.
- **Build:** Das Compute-Standardkonto `807149335205-compute@…` baut per Cloud Build. Es hat seit 2026-09-28 **nur `roles/run.builder`** (Editor entzogen, siehe unten).

### Build-Rechte: was wirklich nötig war

Die ersten zwei Deploys scheiterten mit `PERMISSION_DENIED … could not resolve source`. Danach wurden zwei Rechte gesetzt: `roles/run.builder` auf das Projekt und `roles/storage.objectViewer` auf den Quell-Bucket `run-sources-focusflow-demo-510014-europe-west3`. Nachgeprüft mit dem Policy Troubleshooter (`gcloud policy-intelligence troubleshoot-policy iam`), nachdem die Bucket-Freigabe wieder entfernt war:

| Berechtigung des Build-Kontos | gewährt durch |
|---|---|
| Quellcode lesen (`storage.objects.get` am Bucket) | automatische Projekt-Freigaben des Buckets (`legacyObjectReader/Owner` für Projekt-Editoren) **und** `run.builder` |
| Image hochladen (`artifactregistry.repositories.uploadArtifacts`) | `roles/editor` **und** `run.builder` |
| Logs schreiben (`logging.logEntries.create`) | `roles/editor` **und** `run.builder` |

Ergebnis:
- **Die Bucket-Freigabe war überflüssig** und ist entfernt (2026-09-28, 18:31 UTC). Das folgende Deployment (Revision `uc7-00002`) lief ohne sie durch.
- **Auch `run.builder` war streng genommen nicht nötig,** weil `roles/editor` alles abdeckt. Die ersten Fehlschläge lagen an der Verteilungszeit: APIs, Build-Konto und Bucket waren erst Minuten alt, und IAM-Änderungen brauchen einige Minuten, bis sie überall gelten.
- `run.builder` bleibt, weil es die von Google dokumentierte Mindestrolle für Source-Deploys ist.
- **Editor entzogen (2026-09-28, 18:50 UTC):** `gcloud projects remove-iam-policy-binding focusflow-demo-510014 --member=serviceAccount:807149335205-compute@developer.gserviceaccount.com --role=roles/editor`. Nach 12 Minuten Test-Deployment (Revision `uc7-00003`, 19:03 UTC): erfolgreich, Build lief unter diesem Konto. Keine weitere Rolle nötig.

## Deploy

```bash
gcloud run deploy uc7 --source . --region europe-west3 \
  --service-account uc7-run@focusflow-demo-510014.iam.gserviceaccount.com \
  --cpu 1 --memory 1Gi --max-instances 2 --concurrency 4 --min-instances 0 --timeout 600 \
  --no-cpu-throttling --execution-environment gen2 --allow-unauthenticated \
  --set-secrets ANTHROPIC_API_KEY=uc7-anthropic-api-key:1,ZUGANGSCODE=uc7-zugangscode:1,SESSION_SECRET=uc7-session-secret:1,DATABASE_URL=uc7-database-url:2 \
  --set-env-vars COOKIE_SECURE=1,MAX_PARALLELE_LAEUFE=2,AGENT_LAEUFE_PRO_INSTANZ=1,AGENT_WARTEZEIT_S=90
```

- `uc7-database-url:2` ist die Verbindung als `uc7_app`. Version 1 (Owner) ist deaktiviert.
- `--source .` lädt den Code hoch (was nicht mit soll, steht in `.gcloudignore`, vor allem `.env`), baut mit dem Dockerfile per Cloud Build und legt das Image in Artifact Registry (`cloud-run-source-deploy`) ab.
- `--allow-unauthenticated` heißt nur: Google verlangt kein Google-Konto. Die App selbst zeigt ohne Zugang nur die Aufzeichnung (`/`, `/replay`), `/login`, `/health` und `/robots.txt`.
- Keine neuen Secrets für Branch (e). Die Tabelle `zugangslinks` und die Spalte `laeufe.zugang` legt die App beim Start selbst an. `REPLAY_TAKT_S` (Standard 2 s je Schritt) ist optional.
- `--no-cpu-throttling`: Abrechnung pro Instanz. CPU bleibt zugeteilt, solange die Instanz lebt, auch ohne offene Anfrage. Nur so läuft ein Agent zu Ende, wenn der Besucher den Tab schließt (siehe docs/decisions.md).
- `--concurrency 4` plus `AGENT_LAEUFE_PRO_INSTANZ=1`: bis zu vier Anfragen je Instanz (Seiten, Konsole, Live-Anzeige), aber höchstens ein Agent-Lauf. Ist der Platz belegt, zeigt die Seite „Wartet auf einen freien Platz“ (bis `AGENT_WARTEZEIT_S`). `--max-instances 2` deckelt gleichzeitige Läufe und Kosten.
- `--timeout 600`: Ein Lauf dauert 30–45 s, die SSE-Verbindung bleibt so lange offen.

## Rollback

Cloud Run behält die alten Revisionen. Zurück ohne Neubau, nur den Traffic umlegen:
```bash
gcloud run revisions list --service uc7 --region europe-west3 --limit 5      # welche Revision lief vorher?
gcloud run services update-traffic uc7 --region europe-west3 --to-revisions <REVISION>=100
```
Aktuell live: `uc7-00009-pv4` (Commit `d86b433`, seit 2026-10-02). **Rollback-Ziel:** `uc7-00008-qtl` (Commit `2c8cc86`,
vor dem Schutz im Code aus UC6, PR #11):
```bash
gcloud run services update-traffic uc7 --region europe-west3 --to-revisions uc7-00008-qtl=100
```
Das Schema ist bei diesem Schritt gleich geblieben, die alte Revision startet also ohne Datenbankänderung. Vorher
unbedingt die Falle unten beachten.

### Falle: Rollback hinter den Schutz im Code (PR #11)

Der alte Code kennt die Zusage-Prüfung nicht. Bei Läufen **ohne Erstattungsempfehlung** zeigt er dem Kunden immer den
**Original-Entwurf des Agents**. Nach einem Rollback sähe der Kunde also wieder genau die Zusage, die die Prüfung
zurückgehalten hat, auch wenn in der Konsole schon „Remove the promise“ gewählt wurde (die Antwort ohne Zusage liegt
dann zwar in `antworten`, der alte Code zeigt sie bei diesen Läufen aber nicht an).

Vorgehen vor einem Rollback hinter PR #11:
1. In der Support-Konsole den Abschnitt „Promise without a recommendation“ ansehen. Jede offene Karte entscheiden.
   Wo die Zusage falsch ist, „Remove the promise“ wählen, auch wenn der Rollback danach kommt (die Entscheidung bleibt
   für ein späteres Vorwärts-Deploy erhalten).
2. Betroffene Läufe notieren (lesend, als `uc7_app`):
   ```sql
   SELECT l.run_id, l.kunden_id, l.zugang, a.quelle
   FROM laeufe l LEFT JOIN antworten a USING (run_id)
   WHERE l.ergebnis LIKE '%"zusage_saetze": ["%'
     AND NOT EXISTS (SELECT 1 FROM empfehlungen e WHERE e.run_id = l.run_id);
   ```
   Diese Läufe zeigen nach dem Rollback die Zusage wieder. In der Demo heißt das: Wer die Seite eines dieser Läufe
   öffnet, sieht den alten Entwurf. Steht bei einem dieser Läufe ein persönlicher Link (`zugang`), den Link bis zum Vorwärts-Deploy
   sperren (`python -m app.links sperren <code>`), statt die Daten zu ändern.
3. Erst dann den Traffic umlegen. Danach so bald wie möglich wieder vorwärts deployen.

Die übrigen neuen Daten sind für den alten Code harmlos: Einen von der Konto-Bindung blockierten Schritt zeigt er als
„Blocked a tool that is not allowed“ (ungenau, aber ohne Folgen), eine Antwort mit `quelle = 'entwurf'` (freigegebener Entwurf) wie jede andere Antwort.

## Persönliche Links für Besucher

Lokal gegen Neon (liest `DATABASE_URL` aus `.env`), gibt den fertigen Link aus:
```bash
.venv/bin/python -m app.links anlegen acme          # 5 Läufe, 60 Tage; Pseudonym statt Personenname
.venv/bin/python -m app.links liste                 # Code, Läufe x/5, angelegt, gültig bis, Status
.venv/bin/python -m app.links sperren acme-k7m2qx   # wirkt bei der nächsten Anfrage
```

## Prüfen

```bash
curl https://uc7-807149335205.europe-west3.run.app/health           # {"ok": true}
curl -s https://uc7-807149335205.europe-west3.run.app/ | grep "Recording of a real run"   # Aufzeichnung ohne Login
# /diagnose (nur Admin): startet die gebündelte CLI mit --version, ohne API-Kosten
gcloud run services logs read uc7 --region europe-west3 --limit 50
```

## Offen

- **Startguthaben:** Welches der beiden Billing-Konten „Mein Rechnungskonto“ das Startguthaben hat, zeigt die API nicht. In der Console: Abrechnung → Konto wählen → „Guthaben“.
