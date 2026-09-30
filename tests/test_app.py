"""Web-App ohne LLM: Zugang, Eingabeprüfung, Live-Ereignisse, Freigaben, Budgetdeckel.

Statt des echten SDK läuft eine Fake-`query`, die echte UC4-Werkzeuge aufruft und eine echte
ResultMessage des SDK liefert. So wird alles außer dem Modell selbst geprüft.
"""

import time
from datetime import datetime, timezone

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
from fastapi.testclient import TestClient

from app import budget, darstellung
from app.einstellungen import Einstellungen, aus_umgebung
from app.lauf import PROMPT_VERSION
from app.main import create_app
from app.speicher import Speicher
from uc4_agent import agent

CODE = "test-zugang-123"


def ergebnis_msg(kosten=0.031, **kw):
    return ResultMessage(subtype="success", duration_ms=1000, duration_api_ms=900, is_error=False,
                         num_turns=6, session_id="s", total_cost_usd=kosten, result="Erledigt.", **kw)


def fake_doppelabbuchung(aufrufe: list):
    """Verhält sich wie ein guter Lauf für T01: nachschlagen, Zahlungen, Empfehlung, Entwurf."""
    async def q(prompt, options, kasten):
        aufrufe.append({"prompt": prompt, "options": options})
        yield AssistantMessage(content=[TextBlock("Ich schaue mir das Konto an.")], model=agent.MODELL)
        kasten.aufrufen("kunde_nachschlagen", {"suche": "anna.berger@example.com"})
        kasten.aufrufen("zahlungen_ansehen", {"kunden_id": "K001"})
        kasten.aufrufen("erstattung_empfehlen", {"kunden_id": "K001", "zahlungs_id": "Z005", "betrag_usd": 54.34,
                                                 "begruendung": "Echte Doppelabbuchung am 14.09."})
        kasten.aufrufen("antwort_entwerfen", {"text": "Hallo Anna, die doppelte Zahlung ist zur Erstattung weitergeleitet."})
        yield ergebnis_msg()
    return q


def fake_absturz(prompt, options, kasten):
    async def q():
        kasten.aufrufen("kunde_nachschlagen", {"suche": "K001"})
        raise ConnectionError("Netz weg")
        yield  # macht q zum Generator
    return q()


def cfg(tmp_path, **kw):
    werte = dict(zugangscode=CODE, session_secret="geheim", daten_dir=tmp_path, monatsdeckel_usd=4.50,
                 max_parallele_laeufe=1, cookie_secure=False)
    werte.update(kw)
    return Einstellungen(**werte)


@pytest.fixture(autouse=True)
def umgebung(monkeypatch):
    """Platzhalter-Key (die Fake-query ruft nie die API) und keine echte .env in Tests."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-platzhalter")
    monkeypatch.setattr("app.main.load_dotenv", lambda *a, **k: None)


@pytest.fixture
def aufrufe():
    return []


@pytest.fixture
def client(tmp_path, aufrufe):
    app = create_app(cfg(tmp_path), query_fn=fake_doppelabbuchung(aufrufe))
    with TestClient(app) as c:
        c.post("/login", data={"code": CODE})
        yield c


def warte_bis_fertig(client, run_id, timeout=5):
    speicher = client.app.state.speicher
    ende = time.time() + timeout
    while time.time() < ende:
        if speicher.lauf(run_id)["status"] != "laeuft":
            return speicher.lauf(run_id)
        time.sleep(0.02)
    raise AssertionError("Lauf wurde nicht fertig")


def lege_an(client, kunden_id="K001", text="Mir wurde doppelt abgebucht.", **extra):
    """Nur POST: Der Lauf ist danach angelegt, aber noch nicht gestartet."""
    r = client.post("/lauf", data={"kunden_id": kunden_id, "text": text, **extra}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return r.headers["location"].rsplit("/", 1)[1]


def starte(client, kunden_id="K001", text="Mir wurde doppelt abgebucht.", **extra):
    """Wie im Browser: POST, dann öffnet die Laufseite den Stream, und der startet den Lauf."""
    run_id = lege_an(client, kunden_id, text, **extra)
    sse_ereignisse(client, run_id)
    return run_id


def sse_ereignisse(client, run_id, headers=None):
    ereignisse = []
    with client.stream("GET", f"/lauf/{run_id}/stream", headers=headers or {}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        aktuell = {}
        for zeile in r.iter_lines():
            if zeile.startswith("event: "):
                aktuell["event"] = zeile[7:]
            elif zeile.startswith("id: "):
                aktuell["id"] = int(zeile[4:])
            elif zeile.startswith("data: "):
                aktuell["data"] = aktuell.get("data", "") + zeile[6:] + "\n"
            elif zeile == "" and aktuell:
                ereignisse.append(aktuell)
                if aktuell.get("event") == "ende":
                    break
                aktuell = {}
    return ereignisse


# ---------- Zugang ----------

def test_ohne_code_nur_replay_login_health_static(tmp_path):
    with TestClient(create_app(cfg(tmp_path))) as c:
        for pfad in ["/anliegen", "/freigaben", "/lauf/20260928-120000-abcdef", "/betrieb", "/diagnose"]:
            r = c.get(pfad, follow_redirects=False)
            assert r.status_code == 303 and r.headers["location"] == "/"   # zurück zum Replay
        r = c.post("/lauf", data={"kunden_id": "K001", "text": "x"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"
        assert c.app.state.speicher.db.execute("SELECT COUNT(*) FROM laeufe").fetchone()[0] == 0
        assert "Recording of a real run" in c.get("/", follow_redirects=False).text
        assert c.get("/login").status_code == 200
        assert c.get("/health").json() == {"ok": True}
        assert c.get("/static/htmx.min.js").status_code == 200


def test_falscher_code_abgelehnt_richtiger_setzt_cookie(tmp_path):
    with TestClient(create_app(cfg(tmp_path))) as c:
        r = c.post("/login", data={"code": "falsch"}, follow_redirects=False)
        assert r.status_code == 401 and "not correct" in r.text
        r = c.post("/login", data={"code": CODE}, follow_redirects=False)
        assert r.status_code == 303
        assert CODE not in r.headers["set-cookie"]  # im Cookie steht nur eine Signatur
        assert "httponly" in r.headers["set-cookie"].lower()
        assert "How this demo works" in c.get("/", follow_redirects=False).text


def test_gefaelschtes_cookie_hilft_nicht(tmp_path):
    with TestClient(create_app(cfg(tmp_path))) as c:
        c.cookies.set("uc7_zugang", "a" * 64)
        assert c.get("/freigaben", follow_redirects=False).status_code == 303
        assert "Recording of a real run" in c.get("/").text


def test_app_startet_nicht_ohne_zugangscode(monkeypatch):
    monkeypatch.setenv("ZUGANGSCODE", "kurz")
    with pytest.raises(RuntimeError):
        aus_umgebung()
    monkeypatch.delenv("ZUGANGSCODE")
    with pytest.raises(RuntimeError):
        aus_umgebung()


# ---------- Ticket-Eingabe ----------

@pytest.mark.parametrize("kunden_id, text, stichwort", [
    ("K999", "Hallo", "choose a customer"),
    ("", "Hallo", "choose a customer"),
    ("K001", "   ", "write a request"),
    ("K001", "x" * 1001, "too long"),
])
def test_ungueltige_eingaben_starten_keinen_lauf(client, aufrufe, kunden_id, text, stichwort):
    r = client.post("/lauf", data={"kunden_id": kunden_id, "text": text})
    assert r.status_code == 400 and stichwort in r.text
    assert aufrufe == []
    assert client.app.state.speicher.db.execute("SELECT COUNT(*) FROM laeufe").fetchone()[0] == 0


def test_1000_zeichen_sind_erlaubt(client):
    run_id = starte(client, text="x" * 1000)
    assert warte_bis_fertig(client, run_id)["status"] == "fertig"


def test_absender_kommt_immer_aus_dem_kunden(client, aufrufe):
    run_id = starte(client, kunden_id="K003", text="Hallo", absender="boese@example.com")
    lauf = warte_bis_fertig(client, run_id)
    assert lauf["absender"] == "clara.neumann@gmail.com"
    assert aufrufe[0]["prompt"].startswith("Neues Ticket\nVon: clara.neumann@gmail.com\n\n")
    assert "boese" not in aufrufe[0]["prompt"]


def test_web_app_nutzt_uc4_konfiguration_unveraendert(client, aufrufe):
    warte_bis_fertig(client, starte(client))
    o = aufrufe[0]["options"]
    assert PROMPT_VERSION == "v3" and o.system_prompt == agent.SYSTEM_PROMPT_V3
    assert o.model == "claude-haiku-4-5"
    assert o.max_budget_usd == 0.50 and o.max_turns == 25
    assert o.tools == [] and o.setting_sources == [] and o.strict_mcp_config
    assert set(o.allowed_tools) == {f"mcp__focusflow__{n}" for n in agent.Werkzeugkasten.NAMEN}
    assert set(o.hooks) == {"PreToolUse", "Stop"}  # Werkzeug-Filter und Pflicht-Stop-Hook


# ---------- Lauf: Live-Anzeige und Protokoll ----------

def test_lauf_wird_protokolliert(client):
    lauf = warte_bis_fertig(client, starte(client))
    assert lauf["status"] == "fertig" and lauf["kosten_usd"] == pytest.approx(0.031) and not lauf["kosten_pauschal"]
    e = lauf["ergebnis"]
    assert e["entwurf"].startswith("Hallo Anna") and e["num_turns"] == 6 and e["pflichten_offen"] == []
    assert [x["werkzeug"] for x in lauf["ereignisse"] if x["art"] == "werkzeug"] == [
        "kunde_nachschlagen", "zahlungen_ansehen", "erstattung_empfehlen", "antwort_entwerfen"]
    offen = client.app.state.speicher.offene_empfehlungen()
    assert len(offen) == 1 and offen[0]["zahlungs_id"] == "Z005" and offen[0]["betrag_usd"] == 54.34


def test_sse_liefert_schritte_ergebnis_und_ende(client):
    run_id = starte(client)
    warte_bis_fertig(client, run_id)
    ev = sse_ereignisse(client, run_id)
    assert [e["event"] for e in ev] == ["schritt"] * 5 + ["status", "entwurf", "abschluss", "ende"]
    assert "Ich schaue mir das Konto an." in ev[0]["data"] and "Agent&#39;s note" in ev[0]["data"]
    assert "Looks up the customer account" in ev[1]["data"] and "Account found: Anna Berger" in ev[1]["data"]
    assert "54.34 USD" in ev[3]["data"] and "awaiting approval" in ev[3]["data"]
    assert "done" in ev[5]["data"]
    assert "Refund under review" in ev[6]["data"] and "Hallo Anna" in ev[6]["data"]  # Variante A: Zwischenbescheid
    assert "zur Erstattung weitergeleitet" not in ev[6]["data"]                                  # Agent-Entwurf nicht beim Kunden
    assert "Done" in ev[7]["data"] and "0.03 USD" in ev[7]["data"] and "to the support console" in ev[7]["data"]
    # Alle drei Abschluss-Teile tragen die ID des Ergebnis-Ereignisses (5)
    assert [e.get("id") for e in ev[:8]] == [0, 1, 2, 3, 4, 5, 5, 5]


def test_sse_setzt_nach_verbindungsabbruch_fort(client):
    run_id = starte(client)
    warte_bis_fertig(client, run_id)
    ev = sse_ereignisse(client, run_id, headers={"Last-Event-ID": "3"})
    assert [e.get("id") for e in ev[:-1]] == [4, 5, 5, 5]


def test_lauf_seite_nach_ende_ohne_sse_und_escaped(client):
    run_id = starte(client, text="<script>alert(1)</script> doppelt abgebucht")
    warte_bis_fertig(client, run_id)
    r = client.get(f"/lauf/{run_id}")
    assert r.status_code == 200
    assert "sse-connect" not in r.text and "Hallo Anna" in r.text
    assert "<script>alert(1)</script>" not in r.text and "&lt;script&gt;" in r.text


def test_unbekannter_lauf(client):
    assert client.get("/lauf/20260101-000000-000000").status_code == 404
    assert client.get("/lauf/../../etc").status_code == 404
    assert client.get("/lauf/20260101-000000-000000/stream").status_code == 404


def test_fehler_im_lauf_wird_pauschal_verbucht(tmp_path):
    with TestClient(create_app(cfg(tmp_path), query_fn=fake_absturz)) as c:
        c.post("/login", data={"code": CODE})
        lauf = warte_bis_fertig(c, starte(c))
        assert lauf["status"] == "fehler"
        assert lauf["kosten_usd"] == agent.MAX_BUDGET_USD and lauf["kosten_pauschal"] == 1
        assert "ConnectionError" in lauf["ergebnis"]["fehlertext"]
        assert lauf["ergebnis"]["pflichten_offen"] == ["antwort_entwerfen"]
        assert "flat rate" in c.get(f"/lauf/{lauf['run_id']}").text


def test_ohne_api_key_wird_lauf_zum_fehler(tmp_path, monkeypatch):
    from app.lauf import _sdk_query
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with TestClient(create_app(cfg(tmp_path), query_fn=_sdk_query)) as c:
        c.post("/login", data={"code": CODE})
        lauf = warte_bis_fertig(c, starte(c))
        assert lauf["status"] == "fehler" and "RuntimeError" in lauf["ergebnis"]["fehlertext"]


def test_nur_ein_lauf_gleichzeitig(client):
    client.app.state.speicher.lauf_anlegen("20260928-000000-aaaaaa", "K001", "a@example.com", "läuft noch", status="laeuft")
    r = client.post("/lauf", data={"kunden_id": "K002", "text": "Hallo"})
    assert r.status_code == 429 and "another request" in r.text


def test_verwaiste_laeufe_beim_start_pauschal_verbucht(tmp_path):
    from datetime import timedelta
    s = Speicher(tmp_path / "uc7.sqlite")
    alt = datetime.now(timezone.utc) - timedelta(minutes=20)
    s.lauf_anlegen("20260928-000000-bbbbbb", "K001", "a@example.com", "alt", jetzt=alt, status="laeuft")
    s.lauf_anlegen("20260928-000000-dddddd", "K001", "a@example.com", "nie gestartet", jetzt=alt)
    s.lauf_anlegen("20260928-000000-cccccc", "K001", "a@example.com", "läuft gerade woanders", status="laeuft")
    with TestClient(create_app(cfg(tmp_path))) as c:
        sp = c.app.state.speicher
        lauf = sp.lauf("20260928-000000-bbbbbb")
        assert lauf["status"] == "abgebrochen" and lauf["kosten_usd"] == 0.50
        assert sp.lauf("20260928-000000-dddddd")["status"] == "verfallen" and sp.lauf("20260928-000000-dddddd")["kosten_usd"] == 0
        # Jüngere Läufe können auf einer anderen Cloud-Run-Instanz laufen und bleiben unberührt
        assert sp.lauf("20260928-000000-cccccc")["status"] == "laeuft"


# ---------- Freigaben ----------

def test_freigabe_bestaetigen_nur_einmal(client):
    warte_bis_fertig(client, starte(client))
    eid = client.app.state.speicher.offene_empfehlungen()[0]["empfehlungs_id"]
    r = client.get("/freigaben")
    assert "Echte Doppelabbuchung" in r.text and "Z005" in r.text and 'class="badge offen">open<' in r.text
    r = client.post(f"/freigaben/{eid}", data={"entscheidung": "bestaetigt", "begruendung": " passt "})
    assert "Decision saved" in r.text and "No open recommendations" in r.text
    assert 'class="badge bestaetigt">confirmed<' in r.text and "100 %" in r.text
    r = client.post(f"/freigaben/{eid}", data={"entscheidung": "abgelehnt"})
    assert "already been decided" in r.text
    s = client.app.state.speicher
    assert s.entschiedene_empfehlungen()[0]["entscheidung"] == "bestaetigt"
    assert s.entschiedene_empfehlungen()[0]["kommentar"] == "passt"
    assert s.freigabe_statistik() == {"gesamt": 1, "bestaetigt": 1}


def test_freigabe_ungueltig_und_unbekannt(client):
    warte_bis_fertig(client, starte(client))
    eid = client.app.state.speicher.offene_empfehlungen()[0]["empfehlungs_id"]
    assert "Please choose “Confirm” or “Reject”" in client.post(f"/freigaben/{eid}", data={"entscheidung": "vielleicht"}).text
    assert "does not exist" in client.post("/freigaben/E-gibtsnicht", data={"entscheidung": "abgelehnt"}).text
    assert len(client.app.state.speicher.offene_empfehlungen()) == 1


# ---------- Budget ----------

def lauf_mit_kosten(speicher, run_id, kosten, erstellt=None, status="fertig"):
    speicher.lauf_anlegen(run_id, "K001", "a@example.com", "x")
    speicher.db.execute("UPDATE laeufe SET status=?, kosten_usd=?, erstellt=COALESCE(?, erstellt) WHERE run_id=?",
                        (status, kosten, erstellt, run_id))
    speicher.db.commit()


def test_budget_gesperrt_zeigt_abschaltmeldung(client, aufrufe):
    lauf_mit_kosten(client.app.state.speicher, "20260928-000000-000001", 4.50)
    r = client.get("/anliegen")
    assert "taking a break" in r.text and "1 " in r.text and "<textarea" not in r.text
    assert "demo budget for this month is used up" in client.get("/").text
    r = client.post("/lauf", data={"kunden_id": "K001", "text": "Hallo"})
    assert r.status_code == 503 and "taking a break" in r.text
    assert aufrufe == []


def test_laufende_laeufe_werden_reserviert(tmp_path):
    s = Speicher(tmp_path / "db.sqlite")
    lauf_mit_kosten(s, "r1", 4.00)
    assert not budget.stand(s, 4.50).gesperrt
    lauf_mit_kosten(s, "r2", None, status="laeuft")  # Reserve 0,50
    st = budget.stand(s, 4.50)
    assert st.reserviert_usd == 0.50 and st.gesperrt


def test_budget_zaehlt_nur_den_aktuellen_monat(tmp_path):
    s = Speicher(tmp_path / "db.sqlite")
    lauf_mit_kosten(s, "alt", 4.49, erstellt="2026-08-31T23:59:59+00:00")
    lauf_mit_kosten(s, "neu", 0.10, erstellt="2026-09-01T00:00:00+00:00")
    st = budget.stand(s, 4.50, jetzt=datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert st.verbraucht_usd == pytest.approx(0.10) and not st.gesperrt


def test_deckel_plus_reserve_bleibt_unter_5_usd():
    assert 4.50 + budget.RESERVE_JE_LAUF_USD <= 5.00


@pytest.mark.parametrize("monat, erwartet", [("2026-09", "1 Oct 2026"), ("2026-12", "1 Jan 2027")])
def test_naechster_monat(monat, erwartet):
    assert budget.BudgetStand(monat, 0, 0, 4.5).naechster_monat == erwartet


# ---------- Darstellung ----------

def test_sse_format_mehrzeilig():
    assert darstellung.sse_nachricht("schritt", "<li>\na\n</li>", 7) == "id: 7\nevent: schritt\ndata: <li>\ndata: a\ndata: </li>\n\n"
    assert darstellung.sse_nachricht("ende", "") == "event: ende\ndata: \n\n"


def test_usd_und_datum_englisch():
    assert darstellung.usd(1234.5) == "1,234.50 USD"
    assert darstellung.usd_genau(0.0312) == "0.0312 USD"
    assert darstellung.datum("2026-09-28T19:09:01+00:00") == "28 Sep 2026, 19:09"
    assert darstellung.datum("2026-01-05") == "5 Jan 2026"


def test_diagnose_startet_cli_ohne_api_und_nur_mit_zugang(client, tmp_path):
    r = client.get("/diagnose")
    assert r.status_code == 200 and r.json()["cli_ok"] and "Claude Code" in r.json()["cli_ausgabe"]
    assert r.json()["speicher"] == "sqlite"
    with TestClient(create_app(cfg(tmp_path / "x"))) as fremd:
        assert fremd.get("/diagnose", follow_redirects=False).status_code == 303


# ---------- Start in der SSE-Anfrage, ein Agent-Platz pro Instanz ----------

def test_post_legt_nur_an_stream_startet(client, aufrufe):
    run_id = lege_an(client)
    sp = client.app.state.speicher
    assert sp.lauf(run_id)["status"] == "angelegt" and aufrufe == []
    assert budget.stand(sp, 4.50).reserviert_usd == 0.50            # angelegte Läufe sind schon reserviert
    assert "sse-connect" in client.get(f"/lauf/{run_id}").text      # Seite hört zu und startet damit den Lauf
    ev = sse_ereignisse(client, run_id)
    assert ev[-1]["event"] == "ende" and len(aufrufe) == 1
    assert sp.lauf(run_id)["status"] == "fertig"
    # Zweite Verbindung (Reload, anderer Tab) startet nicht noch einmal
    sse_ereignisse(client, run_id)
    assert len(aufrufe) == 1


def test_belegter_platz_wartet_und_verfaellt_ohne_kosten(tmp_path, aufrufe):
    app = create_app(cfg(tmp_path, agent_wartezeit_s=0.3), query_fn=fake_doppelabbuchung(aufrufe))
    with TestClient(app) as c:
        c.post("/login", data={"code": CODE})
        run_id = lege_an(c)
        c.portal.call(app.state.agent_platz.acquire)  # anderer Lauf belegt den Platz dieser Instanz
        ev = sse_ereignisse(c, run_id)
        assert "Waiting for a free slot" in ev[0]["data"]
        assert "busy right now" in "".join(e.get("data", "") for e in ev) and "not started" in "".join(e.get("data", "") for e in ev)
        lauf = c.app.state.speicher.lauf(run_id)
        assert lauf["status"] == "verfallen" and lauf["kosten_usd"] == 0 and aufrufe == []


def test_wartet_bis_platz_frei_und_startet_dann(tmp_path, aufrufe):
    import threading
    app = create_app(cfg(tmp_path, agent_wartezeit_s=5), query_fn=fake_doppelabbuchung(aufrufe))
    with TestClient(app) as c:
        c.post("/login", data={"code": CODE})
        run_id = lege_an(c)
        c.portal.call(app.state.agent_platz.acquire)
        threading.Timer(0.3, lambda: c.portal.call(app.state.agent_platz.release)).start()
        ev = sse_ereignisse(c, run_id)
        assert "Waiting for a free slot" in ev[0]["data"] and ev[-1]["event"] == "ende"
        assert c.app.state.speicher.lauf(run_id)["status"] == "fertig" and len(aufrufe) == 1
        assert not app.state.agent_platz.locked()  # nach dem Lauf wieder frei


def test_lauf_auf_anderer_instanz_wird_aus_dem_speicher_nachgereicht(client, monkeypatch):
    import threading
    monkeypatch.setattr("app.main.POLL_S", 0.05)
    sp = client.app.state.speicher
    sp.lauf_anlegen("20260928-000000-eeeeee", "K001", "anna.berger@example.com", "x", status="laeuft")
    def andere_instanz_fertig():
        sp.lauf_abschliessen("20260928-000000-eeeeee", status="fertig", kosten_usd=0.03, kosten_pauschal=False, dauer_s=30,
                             ergebnis={"status": "fertig", "entwurf": "Hallo von Instanz B", "empfehlungen": [], "uebergaben": [],
                                       "kuendigungen": [], "eingriffe": 0, "pflichten_offen": [], "kosten_usd": 0.03,
                                       "kosten_pauschal": False, "dauer_s": 30, "num_turns": 4, "fehlertext": None},
                             ereignisse=[{"art": "text", "text": "Notiz von B"}], empfehlungen=[])
    threading.Timer(0.3, andere_instanz_fertig).start()
    ev = sse_ereignisse(client, "20260928-000000-eeeeee")
    daten = "".join(e.get("data", "") for e in ev)
    assert "Notiz von B" in daten and "Hallo von Instanz B" in daten and ev[-1]["event"] == "ende"


def test_lauf_laeuft_weiter_wenn_stream_abbricht(tmp_path, aufrufe):
    """Tab zu: Der Lauf ist ein eigener Task und wird trotzdem fertig und verbucht."""
    import asyncio as aio
    langsam_aufrufe = []
    async def langsam(prompt, options, kasten):
        await aio.sleep(0.3)
        async for m in fake_doppelabbuchung(langsam_aufrufe)(prompt, options, kasten):
            yield m
    with TestClient(create_app(cfg(tmp_path), query_fn=langsam)) as c:
        c.post("/login", data={"code": CODE})
        run_id = lege_an(c)
        with c.stream("GET", f"/lauf/{run_id}/stream") as r:
            next(r.iter_lines())  # erste Zeile gelesen, dann Verbindung zu
        lauf = warte_bis_fertig(c, run_id)
        assert lauf["status"] == "fertig" and lauf["kosten_usd"] == pytest.approx(0.031)


def test_beobachter_verliert_keine_meldung_zwischen_pruefen_und_warten():
    import asyncio as aio
    from app.lauf import Beobachter
    async def ablauf():
        b = Beobachter()
        signal = b.signal()          # Stream holt das Signal ...
        b.melden({"art": "text"})    # ... dann meldet der Lauf, bevor der Stream wartet
        return await b.warten(5, signal)
    t0 = __import__("time").perf_counter()
    assert aio.run(ablauf()) is True and __import__("time").perf_counter() - t0 < 1
