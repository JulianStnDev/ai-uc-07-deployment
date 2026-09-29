"""Kaltstart-Messung: wartet, bis Cloud Run nachweislich leer ist, und misst dann die erste Anfrage.
5 Messungen der Seite /login, danach 1 Messung mit Agent-Lauf (T08, ca. 0,025 USD). Ergebnisse: kaltstart.jsonl"""
import json, subprocess, sys, time
from datetime import datetime, timedelta, timezone
import httpx

URL = "https://uc7-807149335205.europe-west3.run.app"
PROJ = "focusflow-demo-510014"
CODE = sys.argv[1]
OUT = sys.argv[2]
T08 = "Bitte kündigt mein Pro-Abo zum nächstmöglichen Zeitpunkt. Danke."

def token():
    return subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True).stdout.strip()

def reihe(metric, minuten, aligner):
    ende = datetime.now(timezone.utc); start = ende - timedelta(minutes=minuten)
    r = httpx.get(f"https://monitoring.googleapis.com/v3/projects/{PROJ}/timeSeries", headers={"Authorization": f"Bearer {token()}"},
                  params={"filter": f'metric.type="run.googleapis.com/{metric}" AND resource.labels.service_name="uc7"',
                          "interval.startTime": start.isoformat(), "interval.endTime": ende.isoformat(),
                          "aggregation.alignmentPeriod": "60s", "aggregation.perSeriesAligner": aligner}, timeout=30)
    werte = []
    for ts in r.json().get("timeSeries", []):
        for p in ts["points"]:
            v = p["value"]; werte.append(float(v.get("int64Value", v.get("doubleValue", 0))))
    return werte

def logs(filter_, minuten):
    r = subprocess.run(["gcloud", "logging", "read", f'resource.type="cloud_run_revision" AND resource.labels.service_name="uc7" AND {filter_}',
                        f"--freshness={minuten}m", "--format=value(timestamp)", "--limit=50"], capture_output=True, text=True)
    return sorted(datetime.fromisoformat(z.strip().replace("Z", "+00:00")) for z in r.stdout.splitlines() if z.strip())


def leer():
    """Leer = letzte Anfrage (Request-Log, fast ohne Verzögerung) liegt mindestens 20 min zurück."""
    anfragen = logs('logName:"run.googleapis.com%2Frequests"', 25)
    if not anfragen:
        return True, None, None
    abstand = (datetime.now(timezone.utc) - anfragen[-1]).total_seconds() / 60
    return abstand >= 20, round(abstand, 1), anfragen[-1].strftime("%H:%M:%S")


def kalt_bestaetigt(t_anfrage: datetime) -> bool:
    """Instanzstart ("Started server process") höchstens 15 s um die Messanfrage herum.
    Der Prozess startet erst durch die Anfrage, sein Log-Eintrag steht also meist 0–3 s danach.
    (Die erste Fassung suchte nur davor und meldete die Messungen 2–6 fälschlich als unbestätigt.)"""
    time.sleep(20)  # Logs brauchen ein paar Sekunden
    starts = logs('textPayload:"Started server process"', 10)
    return any(abs((s - t_anfrage).total_seconds()) <= 15 for s in starts)


def schreibe(d):
    with open(OUT, "a") as f:
        f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(d, flush=True)

def messe_seite(nr):
    c = httpx.Client(timeout=120)
    t_anfrage = datetime.now(timezone.utc)
    t = time.perf_counter(); r = c.get(URL + "/login"); kalt = time.perf_counter() - t
    t = time.perf_counter(); c.get(URL + "/login"); warm = time.perf_counter() - t
    schreibe({"nr": nr, "art": "seite", "zeit": t_anfrage.isoformat(timespec="seconds"), "status": r.status_code,
              "kalt_s": round(kalt, 2), "warm_s": round(warm, 2), "kaltstart_bestaetigt": kalt_bestaetigt(t_anfrage)})

def messe_agent(nr):
    c = httpx.Client(timeout=300, follow_redirects=False)
    t_anfrage = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    c.post(URL + "/login", data={"code": CODE}); t_login = time.perf_counter() - t0
    r = c.post(URL + "/lauf", data={"kunden_id": "K002", "text": T08, "beispiel": "T08"})
    run_id = r.headers["location"].rsplit("/", 1)[1]
    erster = ende = None
    with c.stream("GET", f"{URL}/lauf/{run_id}/stream") as s:
        for zeile in s.iter_lines():
            if zeile == "event: schritt" and erster is None:
                erster = time.perf_counter() - t0
            if zeile == "event: ende":
                ende = time.perf_counter() - t0; break
    schreibe({"nr": nr, "art": "agent", "zeit": datetime.now(timezone.utc).isoformat(timespec="seconds"), "run_id": run_id,
              "login_kalt_s": round(t_login, 2), "erster_schritt_s": round(erster or -1, 2), "fertig_s": round(ende or -1, 2),
              "kaltstart_bestaetigt": kalt_bestaetigt(t_anfrage)})

start = time.time(); nr = int(sys.argv[3]) if len(sys.argv) > 3 else 1
while nr <= 6 and time.time() - start < 7 * 3600:
    ok, a, i = leer()
    if not ok:
        print(f"{datetime.now():%H:%M} noch nicht leer (letzte Anfrage {i}, vor {a} min)", flush=True)
        time.sleep(120); continue
    (messe_seite if nr <= 5 else messe_agent)(nr); nr += 1
    time.sleep(60)  # eigene Anfragen erscheinen im Log, danach beginnt die 20-min-Wartezeit
print("fertig", flush=True)
