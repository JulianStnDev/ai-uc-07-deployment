"""Web-Demo des FocusFlow-Support-Agents (UC7, Branch a: lokal, SQLite).

Seiten:
    /login              Zugangscode eingeben
    /                   Start: "So funktioniert diese Demo" und zwei Wege
    /anliegen           Kundenportal: Kunde wählen + Anliegen schreiben (oder Pause, wenn Budget aufgebraucht)
    /lauf/{id}          Links Kundenportal mit Antwortentwurf, rechts "Hinter den Kulissen" live per SSE
    /freigaben          Support-Konsole: Erstattungsempfehlungen bestätigen oder ablehnen, Übergaben

Start lokal:  uvicorn --factory app.main:create_app --reload   (Einstellungen: app/einstellungen.py, .env.example)
"""

import asyncio
import json
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from uc4_agent.werkzeuge import DATA_DIR, ROOT as UC4_ROOT

from . import budget, darstellung, hinweise, zugang
from .einstellungen import Einstellungen, aus_umgebung
from .lauf import BEOBACHTER, Beobachter, QueryFn, _sdk_query, lauf_ausfuehren
from .speicher import Speicher

log = logging.getLogger("uc7")

MAX_TEXT_ZEICHEN = 1000
SSE_PING_S = 15
POLL_S = 3  # Abfrage-Intervall, wenn ein Lauf auf einer anderen Instanz läuft
RUN_ID_MUSTER = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
OFFENE_PFADE = ("/login", "/health", "/static/")

HIER = Path(__file__).parent
KUNDEN = {k["kunden_id"]: k for k in json.loads((DATA_DIR / "kunden.json").read_text(encoding="utf-8"))["kunden"]}
ZAHLUNGEN = {z["zahlungs_id"]: z for z in json.loads((DATA_DIR / "zahlungen.json").read_text(encoding="utf-8"))["zahlungen"]}
BEISPIELE = [{"id": t["id"], "kunden_id": t["kunde_id"], "text": t["text"], "label": hinweise.BEISPIEL_LABELS[t["id"]]}
             for t in json.loads((UC4_ROOT / "evals" / "aufgaben.json").read_text(encoding="utf-8"))["aufgaben"]
             if t.get("kunde_id") in KUNDEN]
BEISPIEL_TEXTE = {b["id"]: b["text"] for b in BEISPIELE}


def neue_run_id() -> str:
    return f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def create_app(einstellungen: Einstellungen | None = None, query_fn: QueryFn = _sdk_query) -> FastAPI:
    load_dotenv()  # lokal: .env im Projektordner; im Container kommen die Werte direkt als Umgebungsvariablen
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = einstellungen or aus_umgebung()
    speicher = Speicher(cfg.db_pfad, cfg.database_url)
    laufende_tasks: set[asyncio.Task] = set()
    # Höchstens N Agent-Läufe gleichzeitig pro Instanz (Anthropic: 1 GiB/1 CPU je Lauf). Seiten, Konsole und
    # Live-Anzeige laufen daneben weiter, weil Cloud Run mehrere Anfragen je Instanz zulässt (--concurrency).
    agent_platz = asyncio.Semaphore(cfg.agent_laeufe_pro_instanz)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        n = speicher.verwaiste_laeufe_abbrechen(budget.RESERVE_JE_LAUF_USD)
        if n:
            log.warning("%d verwaiste(r) Lauf/Läufe ohne Abschluss, pauschal verbucht", n)
        log.info("Speicher: %s", speicher.art)
        yield
        for t in list(laufende_tasks):
            t.cancel()
        await asyncio.gather(*laufende_tasks, return_exceptions=True)
        speicher.schliessen()

    app = FastAPI(title="UC7 FocusFlow-Support-Agent", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.speicher = speicher
    app.state.einstellungen = cfg
    app.state.laufende_tasks = laufende_tasks
    app.state.agent_platz = agent_platz
    app.mount("/static", StaticFiles(directory=HIER / "static"), name="static")
    templates = Jinja2Templates(directory=HIER / "templates")
    templates.env.filters.update(usd=darstellung.usd, usd_genau=darstellung.usd_genau, datum=darstellung.datum,
                                 abo=darstellung.abo_text)
    templates.env.globals.update(info=hinweise.info)

    def seite(request: Request, name: str, status_code: int = 200, **ctx) -> HTMLResponse:
        ctx.setdefault("budget", budget.stand(speicher, cfg.monatsdeckel_usd))
        ctx.setdefault("offene_freigaben", len(speicher.offene_empfehlungen()))
        ctx.setdefault("aktiv", None)
        return templates.TemplateResponse(request, name, {"kunden": KUNDEN, "zahlungen": ZAHLUNGEN, **ctx},
                                          status_code=status_code)

    def fragment(name: str, **ctx) -> str:
        return templates.get_template(name).render(kunden=KUNDEN, zahlungen=ZAHLUNGEN, **ctx)

    # ---------- Zugang ----------

    @app.middleware("http")
    async def zugangscode_pruefen(request: Request, call_next):
        pfad = request.url.path
        if pfad.startswith(OFFENE_PFADE):
            return await call_next(request)
        if not zugang.cookie_gueltig(request.cookies.get(zugang.COOKIE_NAME), cfg.zugangscode, cfg.session_secret):
            return RedirectResponse("/login", status_code=303)
        return await call_next(request)

    @app.get("/health")
    async def health():
        return JSONResponse({"ok": True})

    @app.get("/diagnose")
    async def diagnose():
        """Smoke-Test ohne API-Kosten: Startet die gebündelte Claude-CLI mit --version (nur mit Zugangscode)."""
        import claude_agent_sdk
        cli = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
        start = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(str(cli), "--version", stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.STDOUT)
            ausgabe, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            ok, text = proc.returncode == 0, ausgabe.decode(errors="replace").strip()[:500]
        except Exception as e:  # noqa: BLE001
            ok, text = False, f"{type(e).__name__}: {e}"[:500]
        return JSONResponse({"cli_ok": ok, "cli_ausgabe": text, "dauer_s": round(time.perf_counter() - start, 2),
                             "speicher": speicher.art, "sdk_version": claude_agent_sdk.__version__},
                            status_code=200 if ok else 500)

    @app.get("/login", response_class=HTMLResponse)
    async def login_seite(request: Request):
        return templates.TemplateResponse(request, "login.html", {"fehler": None})

    @app.post("/login")
    async def login(request: Request, code: str = Form("")):
        if not zugang.code_richtig(code, cfg.zugangscode):
            await asyncio.sleep(1)  # bremst Durchprobieren
            return templates.TemplateResponse(request, "login.html", {"fehler": "Der Zugangscode stimmt nicht."},
                                              status_code=401)
        antwort = RedirectResponse("/", status_code=303)
        antwort.set_cookie(zugang.COOKIE_NAME, zugang.cookie_wert(cfg.zugangscode, cfg.session_secret),
                           max_age=zugang.COOKIE_MAX_ALTER_S, httponly=True, samesite="lax", secure=cfg.cookie_secure)
        return antwort

    @app.post("/abmelden")
    async def abmelden():
        antwort = RedirectResponse("/login", status_code=303)
        antwort.delete_cookie(zugang.COOKIE_NAME)
        return antwort

    # ---------- Start und Anliegen ----------

    @app.get("/", response_class=HTMLResponse)
    async def startseite(request: Request):
        return seite(request, "start.html", aktiv="start")

    def formular(request: Request, fehler: str | None = None, kunden_id: str = "", text: str = "",
                 beispiel: str = "", status_code: int = 200):
        stand = budget.stand(speicher, cfg.monatsdeckel_usd)
        return seite(request, "anliegen.html", status_code=status_code, aktiv="anliegen", budget=stand,
                     fehler=fehler, kunden_id=kunden_id, text=text, beispiel=beispiel, beispiele=BEISPIELE,
                     max_zeichen=MAX_TEXT_ZEICHEN, laeuft_gerade=speicher.laufende_anzahl())

    @app.get("/anliegen", response_class=HTMLResponse)
    async def anliegen(request: Request):
        return formular(request)

    @app.post("/lauf")
    async def lauf_starten(request: Request, kunden_id: str = Form(""), text: str = Form(""), beispiel: str = Form("")):
        text = text.strip()
        if kunden_id not in KUNDEN:
            return formular(request, "Bitte einen Kunden aus der Liste wählen.", kunden_id, text, beispiel, 400)
        if not text:
            return formular(request, "Bitte ein Anliegen eingeben.", kunden_id, text, beispiel, 400)
        if len(text) > MAX_TEXT_ZEICHEN:
            return formular(request, f"Das Anliegen ist zu lang ({len(text)} Zeichen, erlaubt sind {MAX_TEXT_ZEICHEN}).",
                            kunden_id, text[:MAX_TEXT_ZEICHEN], beispiel, 400)
        # Zwischen Prüfung und Anlegen gibt es kein await: Zwei gleichzeitige Anfragen können sich hier nicht überholen.
        if budget.stand(speicher, cfg.monatsdeckel_usd).gesperrt:
            return formular(request, status_code=503)
        if speicher.laufende_anzahl() >= cfg.max_parallele_laeufe:
            return formular(request, "Gerade bearbeitet der Agent schon ein anderes Anliegen. Bitte in einer halben Minute noch einmal versuchen.",
                            kunden_id, text, beispiel, 429)
        run_id = neue_run_id()
        absender = KUNDEN[kunden_id]["email"]  # Absender kommt immer aus dem gewählten Kunden, nie aus dem Formular
        titel = hinweise.titel_fuer(text, beispiel, BEISPIEL_TEXTE)
        # Nur anlegen. Gestartet wird der Lauf von der SSE-Anfrage der Laufseite (lauf_stream): So läuft der
        # Agent garantiert auf der Instanz, mit der der Browser verbunden ist, auch bei mehreren Instanzen.
        speicher.lauf_anlegen(run_id, kunden_id, absender, text, titel)
        return RedirectResponse(f"/lauf/{run_id}", status_code=303)

    # ---------- Lauf ansehen ----------

    def schritt_html(ereignisse: list[dict], i: int) -> str:
        e = ereignisse[i]
        erste_notiz = e["art"] == "text" and not any(x["art"] == "text" for x in ereignisse[:i])
        return fragment("_schritt.html", s=darstellung.schritt(e, erste_notiz))

    def abschluss_html(run_id: str, ergebnis: dict) -> dict[str, str]:
        """Am Ende gibt es drei Teile: Status-Badge (rechts oben), Antwortentwurf (links), Abschluss (rechts)."""
        ctx = {"run_id": run_id, "ergebnis": ergebnis, "empfehlungen": speicher.empfehlungen_zum_lauf(run_id)}
        return {name: fragment(f"_{name}.html", **ctx) for name in ("status", "entwurf", "abschluss")}

    @app.get("/lauf/{run_id}", response_class=HTMLResponse)
    async def lauf_seite(request: Request, run_id: str):
        lauf = speicher.lauf(run_id) if RUN_ID_MUSTER.match(run_id) else None
        if lauf is None:
            return seite(request, "nicht_gefunden.html", status_code=404)
        live = lauf["status"] in ("angelegt", "laeuft")
        teile = abschluss_html(run_id, lauf["ergebnis"]) if not live and lauf["ergebnis"] else None
        return seite(request, "lauf.html", aktiv="anliegen", lauf=lauf, live=live, teile=teile,
                     titel=lauf["titel"] or hinweise.titel_fuer(lauf["text"], None, {}),
                     schritte_html=[] if live else [schritt_html(lauf["ereignisse"], i) for i in range(len(lauf["ereignisse"]))])

    @app.get("/lauf/{run_id}/stream")
    async def lauf_stream(request: Request, run_id: str):
        if not RUN_ID_MUSTER.match(run_id) or speicher.lauf(run_id) is None:
            return JSONResponse({"fehler": "unbekannter Lauf"}, status_code=404)
        # Bei einem Verbindungsabbruch schickt der Browser die letzte ID mit; wir machen dort weiter.
        try:
            ab = int(request.headers.get("last-event-id", "-1")) + 1
        except ValueError:
            ab = 0

        def abschluss_nachrichten(ergebnis: dict, i: int | None):
            for name, html in abschluss_html(run_id, ergebnis).items():
                yield darstellung.sse_nachricht(name, html, i)

        def verfallen(grund: str) -> None:
            ergebnis = {"status": "verfallen", "fehlertext": grund, "subtype": None, "num_turns": None, "schlusstext": None,
                        "entwurf": None, "empfehlungen": [], "uebergaben": [], "kuendigungen": [], "eingriffe": 0,
                        "pflichten_offen": [], "kosten_usd": 0.0, "kosten_pauschal": False, "dauer_s": 0.0}
            speicher.lauf_abschliessen(run_id, status="verfallen", kosten_usd=0.0, kosten_pauschal=False, dauer_s=0.0,
                                       ergebnis=ergebnis, ereignisse=[], empfehlungen=[])

        async def starten() -> Beobachter | None:
            """Wartet auf einen freien Agent-Platz dieser Instanz und übernimmt den Lauf. None, wenn eine andere
            Anfrage schneller war. Der Lauf ist ein eigener Task: Schließt der Besucher den Tab, läuft er zu Ende
            (Cloud Run mit Abrechnung pro Instanz, --no-cpu-throttling)."""
            await asyncio.wait_for(agent_platz.acquire(), cfg.agent_wartezeit_s)
            if not speicher.lauf_uebernehmen(run_id):
                agent_platz.release()
                return None
            lauf = speicher.lauf(run_id)
            b = Beobachter()
            BEOBACHTER[run_id] = b
            task = asyncio.create_task(lauf_ausfuehren(run_id, lauf["absender"], lauf["text"], speicher, cfg.lauf_dir, b, query_fn))
            laufende_tasks.add(task)
            task.add_done_callback(laufende_tasks.discard)
            task.add_done_callback(lambda _t: agent_platz.release())  # Platz erst nach dem Lauf freigeben, nicht beim Tab-Schließen
            return b

        async def ereignisse():
            b = BEOBACHTER.get(run_id)
            if b is None and speicher.lauf(run_id)["status"] == "angelegt":
                if agent_platz.locked():
                    yield darstellung.sse_nachricht("schritt", fragment("_warten.html"))
                try:
                    b = await starten()
                except TimeoutError:
                    verfallen("Gerade ist viel los: Der Agent war die ganze Zeit mit anderen Anliegen beschäftigt. "
                              "Bitte versuch es gleich noch einmal, es wurde nichts berechnet.")
                except asyncio.CancelledError:  # Tab geschlossen, bevor ein Platz frei wurde
                    verfallen("Die Seite wurde geschlossen, bevor der Agent starten konnte. Es wurde nichts berechnet.")
                    raise
            if b is None:
                # Lauf ist fertig, läuft auf einer anderen Instanz oder wurde von einer anderen Anfrage gestartet:
                # im Speicher nachsehen, bis er abgeschlossen ist.
                while (lauf := speicher.lauf(run_id))["status"] in ("angelegt", "laeuft"):
                    if await request.is_disconnected():
                        return
                    yield ": warte\n\n"
                    await asyncio.sleep(POLL_S)
                for i in range(len(lauf["ereignisse"])):
                    yield darstellung.sse_nachricht("schritt", schritt_html(lauf["ereignisse"], i))
                if lauf["ergebnis"]:
                    for n in abschluss_nachrichten(lauf["ergebnis"], None):
                        yield n
                yield darstellung.sse_nachricht("ende", "")
                return
            i = ab
            while True:
                signal = b.signal()  # vor dem Prüfen holen (siehe Beobachter.signal)
                while i < len(b.ereignisse):
                    e = b.ereignisse[i]
                    if e["art"] == "ergebnis":
                        for n in abschluss_nachrichten(e["ergebnis"], i):
                            yield n
                    else:
                        yield darstellung.sse_nachricht("schritt", schritt_html(b.ereignisse, i), i)
                    i += 1
                if b.fertig:
                    yield darstellung.sse_nachricht("ende", "")
                    return
                if await request.is_disconnected():
                    return
                if not await b.warten(SSE_PING_S, signal):
                    yield ": ping\n\n"  # Kommentarzeile hält Proxys und Browser bei langen Pausen wach

        return StreamingResponse(ereignisse(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---------- Freigaben ----------

    @app.get("/freigaben", response_class=HTMLResponse)
    async def freigaben(request: Request, hinweis: str | None = None):
        return seite(request, "konsole.html", aktiv="konsole", offen=speicher.offene_empfehlungen(),
                     entschieden=speicher.entschiedene_empfehlungen(), statistik=speicher.freigabe_statistik(),
                     uebergaben=speicher.uebergaben(), hinweis=hinweis)

    @app.post("/freigaben/{empfehlungs_id}")
    async def entscheiden(empfehlungs_id: str, entscheidung: str = Form(""), kommentar: str = Form("")):
        if entscheidung not in ("bestaetigt", "abgelehnt"):
            return RedirectResponse("/freigaben?hinweis=ungueltig", status_code=303)
        ok = speicher.entscheiden(empfehlungs_id, entscheidung, kommentar[:1000])
        return RedirectResponse(f"/freigaben?hinweis={'gespeichert' if ok else 'schon_entschieden'}", status_code=303)

    return app
