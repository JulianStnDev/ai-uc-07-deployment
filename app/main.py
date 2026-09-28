"""Web-Demo des FocusFlow-Support-Agents (UC7, Branch a: lokal, SQLite).

Seiten:
    /login              Zugangscode eingeben
    /                   Start: "So funktioniert diese Demo" und zwei Wege
    /anliegen           Kundenportal: Kunde wählen + Anliegen schreiben (oder Pause, wenn Budget aufgebraucht)
    /lauf/{id}          Links Kundenportal mit Antwortentwurf, rechts "Hinter den Kulissen" live per SSE
    /freigaben          Support-Konsole: Erstattungsempfehlungen bestätigen oder ablehnen, Übergaben
    /betrieb            Übersicht: Läufe, Kosten, Dauer, Fehlerquote, Prüfungen

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

from . import antwort, budget, darstellung, hinweise, pruefung, zugang
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


def create_app(einstellungen: Einstellungen | None = None, query_fn: QueryFn = _sdk_query,
               judge_fn=None, antwort_fn=None) -> FastAPI:
    """judge_fn(ticket, text, ereignisse, entscheidung) -> Urteil und antwort_fn(lauf, empfehlungen) -> {text, kosten_usd,
    modell} sind austauschbar, damit Tests ohne LLM laufen. Standard: echte API-Aufrufe."""
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

    # ---------- LLM-Aufrufe außerhalb des Agents (Judge, endgültige Antwort) ----------
    _client = {}

    def llm():
        if "c" not in _client:
            import anthropic
            _client["c"] = anthropic.Anthropic()  # liest ANTHROPIC_API_KEY
        return _client["c"]

    judge_fn = judge_fn or (lambda ticket, text, ereignisse, entscheidung:
                            pruefung.judge(llm(), ticket, text, ereignisse, entscheidung))
    antwort_fn = antwort_fn or (lambda lauf, empfehlungen: antwort.antwort_schreiben(llm(), lauf, empfehlungen))

    def hintergrund(coro) -> None:
        task = asyncio.create_task(coro)
        laufende_tasks.add(task)
        task.add_done_callback(laufende_tasks.discard)

    def judge_erlaubt() -> str | None:
        """None, wenn geprüft werden darf, sonst der Grund."""
        stand = budget.stand(speicher, cfg.monatsdeckel_usd)
        if stand.gesperrt:
            return "Monatsbudget erreicht"
        if speicher.pruefungskosten_im_monat(stand.monat) >= pruefung.JUDGE_DECKEL_MONAT_USD:
            return "Judge-Deckel erreicht"
        return None

    async def pruefen(run_id: str, art: str = "agent", erzwingen: bool = False) -> None:
        """Regeln immer; Judge für die Stichprobe (oder erzwungen) auf den Text, den der Kunde bekommt."""
        lauf = speicher.lauf(run_id)
        if lauf is None or lauf["status"] in ("angelegt", "laeuft", "verfallen"):
            return
        empfehlungen = speicher.empfehlungen_zum_lauf(run_id)
        entscheidung = None
        if art == "agent":
            regeln = pruefung.regeln_pruefen(lauf["ereignisse"], lauf["kunden_id"])
            entwurf = (lauf["ergebnis"] or {}).get("entwurf")
            # Bei Empfehlungen sieht der Kunde nicht den Agent-Entwurf, sondern später die endgültige Antwort.
            text = entwurf if (erzwingen or not empfehlungen) else None
        else:
            regeln = None
            a = speicher.antwort(run_id)
            text = a["text"] if a else None
            entscheidung = antwort.entscheidungen_als_text(empfehlungen)
        urteil, modell, kosten, fehler = None, None, 0.0, None
        if text and (erzwingen or pruefung.in_stichprobe(run_id)):
            fehler = judge_erlaubt()
            if fehler is None:
                try:
                    urteil = await asyncio.to_thread(judge_fn, lauf["text"], text, lauf["ereignisse"], entscheidung)
                    modell, kosten = urteil.get("modell"), float(urteil.get("kosten_usd", 0))
                except pruefung.JudgeFehler as e:
                    fehler, kosten = str(e), e.kosten_usd
                except Exception as e:  # noqa: BLE001
                    log.exception("Judge für %s fehlgeschlagen", run_id)
                    fehler = f"Judge-Fehler ({type(e).__name__})"
        speicher.pruefung_speichern(run_id, art, regeln, urteil, modell, kosten, fehler)

    async def antwort_erstellen(run_id: str) -> None:
        """Variante A: endgültige Antwort, sobald über alle Empfehlungen eines Laufs entschieden ist."""
        empfehlungen = speicher.empfehlungen_zum_lauf(run_id)
        if not empfehlungen or any(e["entscheidung"] is None for e in empfehlungen) or speicher.antwort(run_id):
            return
        lauf = speicher.lauf(run_id)
        name = KUNDEN[lauf["kunden_id"]]["name"]
        quelle, text, modell, kosten = "vorlage", antwort.vorlage(name, empfehlungen), None, 0.0
        if not budget.stand(speicher, cfg.monatsdeckel_usd).gesperrt:
            try:
                r = await asyncio.to_thread(antwort_fn, lauf, empfehlungen)
                quelle, text, modell, kosten = "llm", r["text"], r["modell"], float(r["kosten_usd"])
            except Exception:  # noqa: BLE001 – Rückfall auf die Vorlage, der Kunde bekommt trotzdem eine Antwort
                log.exception("Endgültige Antwort für %s fehlgeschlagen, Vorlage verwendet", run_id)
        speicher.antwort_speichern(run_id, text, quelle, empfehlungen, modell, kosten)
        if quelle == "llm":
            await pruefen(run_id, "antwort")

    def kundensicht(lauf: dict) -> dict:
        """Was der Kunde im Portal sieht (Variante A)."""
        empfehlungen = speicher.empfehlungen_zum_lauf(lauf["run_id"])
        ergebnis = lauf["ergebnis"] or {}
        if not empfehlungen:
            return {"art": "entwurf", "text": ergebnis.get("entwurf")}
        a = speicher.antwort(lauf["run_id"])
        if a:
            return {"art": "antwort", "text": a["text"], "quelle": a["quelle"]}
        if any(e["entscheidung"] is None for e in empfehlungen):
            return {"art": "zwischenbescheid", "text": antwort.zwischenbescheid(KUNDEN[lauf["kunden_id"]]["name"])}
        return {"art": "wird_geschrieben", "text": None}
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
        """Am Ende gibt es drei Teile: Status-Badge (rechts oben), Kundensicht (links), Abschluss (rechts)."""
        lauf = speicher.lauf(run_id)
        ctx = {"run_id": run_id, "ergebnis": ergebnis, "empfehlungen": speicher.empfehlungen_zum_lauf(run_id),
               "sicht": kundensicht(lauf), "pruefungen": speicher.pruefungen_zum_lauf(run_id)}
        return {name: fragment(f"_{name}.html", **ctx) for name in ("status", "entwurf", "abschluss")}

    @app.get("/lauf/{run_id}/antwort", response_class=HTMLResponse)
    async def lauf_antwort(run_id: str):
        """Wird von der Laufseite abgefragt, solange der Kunde auf die Entscheidung wartet."""
        lauf = speicher.lauf(run_id) if RUN_ID_MUSTER.match(run_id) else None
        if lauf is None or not lauf["ergebnis"]:
            return HTMLResponse("", status_code=404)
        return HTMLResponse(abschluss_html(run_id, lauf["ergebnis"])["entwurf"])

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
            task.add_done_callback(lambda _t: None if _t.cancelled() else hintergrund(pruefen(run_id)))  # Regeln + ggf. Judge
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
        offen = speicher.offene_empfehlungen()
        entwuerfe = {e["run_id"]: ((speicher.lauf(e["run_id"]) or {}).get("ergebnis") or {}).get("entwurf") for e in offen}
        return seite(request, "konsole.html", aktiv="konsole", offen=offen, entwuerfe=entwuerfe,
                     entschieden=speicher.entschiedene_empfehlungen(), statistik=speicher.freigabe_statistik(),
                     uebergaben=speicher.uebergaben(), hinweis=hinweis)

    @app.post("/freigaben/{empfehlungs_id}")
    async def entscheiden(empfehlungs_id: str, entscheidung: str = Form(""), kommentar: str = Form("")):
        if entscheidung not in ("bestaetigt", "abgelehnt"):
            return RedirectResponse("/freigaben?hinweis=ungueltig", status_code=303)
        ok = speicher.entscheiden(empfehlungs_id, entscheidung, kommentar[:1000])
        if ok:
            hintergrund(antwort_erstellen(speicher.run_id_zur_empfehlung(empfehlungs_id)))  # Variante A
        return RedirectResponse(f"/freigaben?hinweis={'gespeichert' if ok else 'schon_entschieden'}", status_code=303)

    # ---------- Betrieb ----------

    @app.get("/betrieb", response_class=HTMLResponse)
    async def betrieb(request: Request, hinweis: str | None = None):
        stand = budget.stand(speicher, cfg.monatsdeckel_usd)
        daten = betriebsdaten(speicher.laeufe_uebersicht(), speicher.pruefungen(), stand,
                              speicher.pruefungskosten_im_monat(stand.monat), speicher.antworten_kosten(stand.monat))
        return seite(request, "betrieb.html", aktiv="betrieb", budget=stand, d=daten, hinweis=hinweis,
                     judge_deckel=pruefung.JUDGE_DECKEL_MONAT_USD, stichprobe=pruefung.STICHPROBE_PROZENT)

    @app.post("/betrieb/pruefen/{run_id}")
    async def betrieb_pruefen(run_id: str):
        """Judge für einen Lauf von Hand auslösen (zählt in den Judge-Deckel)."""
        if not RUN_ID_MUSTER.match(run_id) or speicher.lauf(run_id) is None:
            return RedirectResponse("/betrieb?hinweis=unbekannt", status_code=303)
        grund = judge_erlaubt()
        if grund:
            return RedirectResponse("/betrieb?hinweis=deckel", status_code=303)
        await pruefen(run_id, "antwort" if speicher.antwort(run_id) else "agent", erzwingen=True)
        return RedirectResponse(f"/betrieb?hinweis=geprueft#lauf-{run_id}", status_code=303)

    return app


def _p(werte: list[float], q: float) -> float | None:
    """Perzentil (nächster Rang), None bei leerer Liste."""
    if not werte:
        return None
    import math
    s = sorted(werte)
    return s[max(0, math.ceil(q * len(s)) - 1)]


def betriebsdaten(laeufe: list[dict], pruefungen: list[dict], stand, pruef_kosten: float, antwort_kosten: float,
                  heute: datetime | None = None, tage: int = 14) -> dict:
    """Kennzahlen für /betrieb. Kosten, Dauer und Fehlerquote: aktueller Monat. Qualität: alle Prüfungen."""
    from datetime import timedelta
    heute = heute or datetime.now(timezone.utc)
    im_monat = [l for l in laeufe if l["erstellt"].startswith(stand.monat)]
    fertig = [l for l in im_monat if l["status"] == "fertig"]
    beendet = [l for l in im_monat if l["status"] in ("fertig", "fehler", "abgebrochen")]
    fehler = [l for l in beendet if l["status"] != "fertig"]
    agent_kosten = sum(l["kosten_usd"] or 0 for l in im_monat if l["status"] not in ("angelegt", "laeuft"))
    dauern = [l["dauer_s"] for l in fertig if l["dauer_s"]]
    tageswerte = []
    for i in range(tage - 1, -1, -1):
        tag = (heute - timedelta(days=i)).strftime("%Y-%m-%d")
        n = [l for l in laeufe if l["erstellt"].startswith(tag) and l["status"] != "verfallen"]
        tageswerte.append({"tag": tag, "anzahl": len(n), "fehler": sum(l["status"] in ("fehler", "abgebrochen") for l in n)})
    max_tag = max([t["anzahl"] for t in tageswerte] + [1])
    regel_pruef = [p for p in pruefungen if p["art"] == "agent" and p["regeln"]]
    regel_namen = list(regel_pruef[0]["regeln"]) if regel_pruef else []
    regeln = [{"name": n, "ok": sum(p["regeln"][n] for p in regel_pruef), "gesamt": len(regel_pruef)} for n in regel_namen]
    urteile = [p for p in pruefungen if p["judge"]]
    judge = [{"name": k, "ok": sum(bool(p["judge"].get(k)) for p in urteile), "gesamt": len(urteile)}
             for k in ("keine_spekulation", "keine_zusage")]
    pruef_nach_lauf: dict[str, list] = {}
    for p in pruefungen:
        pruef_nach_lauf.setdefault(p["run_id"], []).append(p)
    return {
        "anzahl": len(im_monat), "fertig": len(fertig), "fehler": len(fehler),
        "abgebrochen": sum(l["status"] == "abgebrochen" for l in im_monat),
        "verfallen": sum(l["status"] == "verfallen" for l in im_monat),
        "fehlerquote": (len(fehler) / len(beendet)) if beendet else None,
        "kosten_agent": agent_kosten, "kosten_pruefung": pruef_kosten, "kosten_antwort": antwort_kosten,
        "kosten_gesamt": agent_kosten + pruef_kosten + antwort_kosten,
        "kosten_je_lauf": (sum(l["kosten_usd"] or 0 for l in fertig) / len(fertig)) if fertig else None,
        "dauer_p50": _p(dauern, 0.5), "dauer_p95": _p(dauern, 0.95),
        "tage": tageswerte, "max_tag": max_tag,
        "regeln": regeln, "judge": judge, "urteile": len(urteile),
        "letzte": [{**l, "pruefungen": pruef_nach_lauf.get(l["run_id"], [])} for l in laeufe[:20]],
    }
