# Deployment auf Cloud Run (Frankfurt)

Stand 2026-09-28. Projekt `focusflow-demo-510014` (Anzeigename „focusflow-demo“), Region `europe-west3`, Dienst `uc7`.
Öffentliche URL: https://uc7-807149335205.europe-west3.run.app (Zugang nur mit Zugangscode).

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

### Dienstkonten

- **Laufzeit:** eigenes Konto `uc7-run`, darf nur die vier Secrets lesen (`roles/secretmanager.secretAccessor` je Secret). Kein Editor, kein Zugriff auf andere Dienste.
- **Build:** Das Compute-Standardkonto `807149335205-compute@…` baut per Cloud Build. Es brauchte zusätzlich `roles/run.builder` und Leserechte auf dem Quell-Bucket `run-sources-focusflow-demo-510014-europe-west3`. Die ersten zwei Deploys scheiterten mit `PERMISSION_DENIED … could not resolve source`. Nach beiden Rechten und rund zwei Minuten Wartezeit lief es. Welche der beiden Maßnahmen gewirkt hat oder ob es nur die Verteilungszeit frisch aktivierter APIs war, ist offen.

## Deploy

```bash
gcloud run deploy uc7 --source . --region europe-west3 \
  --service-account uc7-run@focusflow-demo-510014.iam.gserviceaccount.com \
  --cpu 1 --memory 1Gi --max-instances 2 --concurrency 1 --min-instances 0 --timeout 600 \
  --execution-environment gen2 --allow-unauthenticated \
  --set-secrets ANTHROPIC_API_KEY=uc7-anthropic-api-key:1,ZUGANGSCODE=uc7-zugangscode:1,SESSION_SECRET=uc7-session-secret:1,DATABASE_URL=uc7-database-url:1 \
  --set-env-vars COOKIE_SECURE=1,MAX_PARALLELE_LAEUFE=2
```

- `--source .` lädt den Code hoch (was nicht mit soll, steht in `.gcloudignore`, vor allem `.env`), baut mit dem Dockerfile per Cloud Build und legt das Image in Artifact Registry (`cloud-run-source-deploy`) ab.
- `--allow-unauthenticated` heißt nur: Google verlangt kein Google-Konto. Die App selbst lässt ohne Zugangscode nur `/login` und `/health` zu.
- `--concurrency 1` und 1 GiB: ein Agent-Lauf je Instanz (Anthropic-Empfehlung 1 GiB/1 CPU je Lauf). `--max-instances 2` deckelt gleichzeitige Läufe und Kosten.
- `--timeout 600`: Ein Lauf dauert 30–45 s, die SSE-Verbindung bleibt so lange offen.

## Prüfen

```bash
curl https://uc7-807149335205.europe-west3.run.app/health           # {"ok": true}
# /diagnose (nach Login): startet die gebündelte CLI mit --version, ohne API-Kosten
gcloud run services logs read uc7 --region europe-west3 --limit 50
```
