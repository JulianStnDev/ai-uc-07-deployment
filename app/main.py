"""Web-Demo des FocusFlow-Support-Agents (UC7, Branch a: lokal, SQLite).

Seiten:
    /login              Zugangscode eingeben
    /                   Kunde wählen + Anliegen schreiben (oder Abschaltmeldung, wenn Budget aufgebraucht)
    /lauf/{id}          Live-Anzeige der Werkzeugaufrufe (SSE über /lauf/{id}/stream), am Ende Ergebnis
    /freigaben          Offene Erstattungsempfehlungen bestätigen oder ablehnen

Start lokal:  uvicorn --factory app.main:create_app --reload   (Einstellungen: app/einstellungen.py, .env.example)
"""

import asyncio
import json
import logging
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from uc4_agent.werkzeuge import DATA_DIR, ROOT as UC4_ROOT

from . import budget, darstellung, zugang
from .einstellungen import Einstellungen, aus_umgebung
from .lauf import BEOBACHTER, Beobachter, QueryFn, _sdk_query, lauf_ausfuehren
from .speicher import Speicher

log = logging.getLogger("uc7")

MAX_TEXT_ZEICHEN = 1000
SSE_PING_S = 15
RUN_ID_MUSTER = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
OFFENE_PFADE = ("/login", "/health", "/static/")

HIER = Path(__file__).parent
KUNDEN = {k["kunden_id"]: k for k in json.loads((DATA_DIR / "kunden.json").read_text(encoding="utf-8"))["kunden"]}
ZAHLUNGEN = {z["zahlungs_id"]: z for z in json.loads((DATA_DIR / "zahlungen.json").read_text(encoding="utf-8"))["zahlungen"]}
BEISPIELE = [{"id": t["id"], "kunden_id": t["kunde_id"], "text": t["text"]}
             for t in json.loads((UC4_ROOT / "evals" / "aufgaben.json").read_text(encoding="utf-8"))["aufgaben"]
             if t.get("kunde_id") in KUNDEN]


def neue_run_id() -> str:
    return f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def create_app(einstellungen: Einstellungen | None = None, query_fn: QueryFn = _sdk_query) -> FastAPI:
    load_dotenv()  # lokal: .env im Projektordner; im Container kommen die Werte direkt als Umgebungsvariablen
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = einstellungen or aus_umgebung()
    speicher = Speicher(cfg.db_pfad)
    laufende_tasks: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        n = speicher.verwaiste_laeufe_abbrechen(budget.RESERVE_JE_LAUF_USD)
        if n:
            log.warning("%d Lauf/Läufe vom letzten Start ohne Abschluss, pauschal verbucht", n)
        yield
        for t in list(laufende_tasks):
            t.cancel()
        await asyncio.gather(*laufende_tasks, return_exceptions=True)

    app = FastAPI(title="UC7 FocusFlow-Support-Agent", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.speicher = speicher
    app.state.einstellungen = cfg
    app.state.laufende_tasks = laufende_tasks
    app.mount("/static", StaticFiles(directory=HIER / "static"), name="static")
    templates = Jinja2Templates(directory=HIER / "templates")
    templates.env.filters.update(usd=darstellung.usd, usd_genau=darstellung.usd_genau, datum=darstellung.datum)

    def seite(request: Request, name: str, status_code: int = 200, **ctx) -> HTMLResponse:
        ctx.setdefault("budget", budget.stand(speicher, cfg.monatsdeckel_usd))
        ctx.setdefault("offene_freigaben", len(speicher.offene_empfehlungen()))
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

    # ---------- Ticket anlegen ----------

    def formular(request: Request, fehler: str | None = None, kunden_id: str = "", text: str = "", status_code: int = 200):
        stand = budget.stand(speicher, cfg.monatsdeckel_usd)
        if stand.gesperrt:
            return seite(request, "gesperrt.html", status_code=503 if status_code != 200 else 200, budget=stand)
        return seite(request, "index.html", status_code=status_code, budget=stand, fehler=fehler,
                     kunden_id=kunden_id, text=text, beispiele=BEISPIELE, max_zeichen=MAX_TEXT_ZEICHEN,
                     laeuft_gerade=speicher.laufende_anzahl())

    @app.get("/", response_class=HTMLResponse)
    async def startseite(request: Request):
        return formular(request)

    @app.post("/lauf")
    async def lauf_starten(request: Request, kunden_id: str = Form(""), text: str = Form("")):
        text = text.strip()
        if kunden_id not in KUNDEN:
            return formular(request, "Bitte einen Kunden aus der Liste wählen.", kunden_id, text, 400)
        if not text:
            return formular(request, "Bitte ein Anliegen eingeben.", kunden_id, text, 400)
        if len(text) > MAX_TEXT_ZEICHEN:
            return formular(request, f"Das Anliegen ist zu lang ({len(text)} Zeichen, erlaubt sind {MAX_TEXT_ZEICHEN}).",
                            kunden_id, text[:MAX_TEXT_ZEICHEN], 400)
        # Zwischen Prüfung und Anlegen gibt es kein await: Zwei gleichzeitige Anfragen können sich hier nicht überholen.
        if budget.stand(speicher, cfg.monatsdeckel_usd).gesperrt:
            return formular(request, status_code=503)
        if speicher.laufende_anzahl() >= cfg.max_parallele_laeufe:
            return formular(request, "Gerade bearbeitet der Agent schon ein anderes Ticket. Bitte in einer halben Minute noch einmal versuchen.",
                            kunden_id, text, 429)
        run_id = neue_run_id()
        absender = KUNDEN[kunden_id]["email"]  # Absender kommt immer aus dem gewählten Kunden, nie aus dem Formular
        speicher.lauf_anlegen(run_id, kunden_id, absender, text)
        beobachter = Beobachter()
        BEOBACHTER[run_id] = beobachter
        task = asyncio.create_task(lauf_ausfuehren(run_id, absender, text, speicher, cfg.lauf_dir, beobachter, query_fn))
        laufende_tasks.add(task)
        task.add_done_callback(laufende_tasks.discard)
        return RedirectResponse(f"/lauf/{run_id}", status_code=303)

    # ---------- Lauf ansehen ----------

    def schritt_html(e: dict) -> str:
        return fragment("_schritt.html", e=e, s=darstellung.schritt(e) if e["art"] == "werkzeug" else None)

    def ergebnis_html(run_id: str, ergebnis: dict) -> str:
        return fragment("_ergebnis.html", run_id=run_id, ergebnis=ergebnis,
                        empfehlungen=speicher.empfehlungen_zum_lauf(run_id))

    @app.get("/lauf/{run_id}", response_class=HTMLResponse)
    async def lauf_seite(request: Request, run_id: str):
        lauf = speicher.lauf(run_id) if RUN_ID_MUSTER.match(run_id) else None
        if lauf is None:
            return seite(request, "nicht_gefunden.html", status_code=404)
        live = lauf["status"] == "laeuft"
        return seite(request, "lauf.html", lauf=lauf, live=live,
                     schritte_html=[] if live else [schritt_html(e) for e in lauf["ereignisse"]],
                     ergebnis_html=None if live or not lauf["ergebnis"] else ergebnis_html(run_id, lauf["ergebnis"]))

    @app.get("/lauf/{run_id}/stream")
    async def lauf_stream(request: Request, run_id: str):
        if not RUN_ID_MUSTER.match(run_id) or speicher.lauf(run_id) is None:
            return JSONResponse({"fehler": "unbekannter Lauf"}, status_code=404)
        # Bei einem Verbindungsabbruch schickt der Browser die letzte ID mit; wir machen dort weiter.
        try:
            ab = int(request.headers.get("last-event-id", "-1")) + 1
        except ValueError:
            ab = 0

        async def ereignisse():
            b = BEOBACHTER.get(run_id)
            if b is None:  # Lauf ist nicht (mehr) in diesem Prozess: Stand aus dem Speicher zeigen
                lauf = speicher.lauf(run_id)
                if lauf["ergebnis"]:
                    yield darstellung.sse_nachricht("ergebnis", ergebnis_html(run_id, lauf["ergebnis"]))
                yield darstellung.sse_nachricht("ende", "")
                return
            i = ab
            while True:
                while i < len(b.ereignisse):
                    e = b.ereignisse[i]
                    if e["art"] == "ergebnis":
                        yield darstellung.sse_nachricht("ergebnis", ergebnis_html(run_id, e["ergebnis"]), i)
                    else:
                        yield darstellung.sse_nachricht("schritt", schritt_html(e), i)
                    i += 1
                if b.fertig:
                    yield darstellung.sse_nachricht("ende", "")
                    return
                if await request.is_disconnected():
                    return
                if not await b.warten(SSE_PING_S):
                    yield ": ping\n\n"  # Kommentarzeile hält Proxys und Browser bei langen Pausen wach

        return StreamingResponse(ereignisse(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---------- Freigaben ----------

    @app.get("/freigaben", response_class=HTMLResponse)
    async def freigaben(request: Request, hinweis: str | None = None):
        return seite(request, "freigaben.html", offen=speicher.offene_empfehlungen(),
                     entschieden=speicher.entschiedene_empfehlungen(), statistik=speicher.freigabe_statistik(),
                     hinweis=hinweis)

    @app.post("/freigaben/{empfehlungs_id}")
    async def entscheiden(empfehlungs_id: str, entscheidung: str = Form(""), kommentar: str = Form("")):
        if entscheidung not in ("bestaetigt", "abgelehnt"):
            return RedirectResponse("/freigaben?hinweis=ungueltig", status_code=303)
        ok = speicher.entscheiden(empfehlungs_id, entscheidung, kommentar[:1000])
        return RedirectResponse(f"/freigaben?hinweis={'gespeichert' if ok else 'schon_entschieden'}", status_code=303)

    return app
