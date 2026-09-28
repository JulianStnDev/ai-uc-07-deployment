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


def starte(client, kunden_id="K001", text="Mir wurde doppelt abgebucht.", **extra):
    r = client.post("/lauf", data={"kunden_id": kunden_id, "text": text, **extra}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return r.headers["location"].rsplit("/", 1)[1]


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

def test_ohne_code_alles_gesperrt_ausser_login_health_static(tmp_path):
    with TestClient(create_app(cfg(tmp_path))) as c:
        for pfad in ["/", "/freigaben", "/lauf/20260928-120000-abcdef"]:
            r = c.get(pfad, follow_redirects=False)
            assert r.status_code == 303 and r.headers["location"] == "/login"
        assert c.post("/lauf", data={"kunden_id": "K001", "text": "x"}, follow_redirects=False).status_code == 303
        assert c.get("/login").status_code == 200
        assert c.get("/health").json() == {"ok": True}
        assert c.get("/static/htmx.min.js").status_code == 200


def test_falscher_code_abgelehnt_richtiger_setzt_cookie(tmp_path):
    with TestClient(create_app(cfg(tmp_path))) as c:
        r = c.post("/login", data={"code": "falsch"}, follow_redirects=False)
        assert r.status_code == 401 and "stimmt nicht" in r.text
        r = c.post("/login", data={"code": CODE}, follow_redirects=False)
        assert r.status_code == 303
        assert CODE not in r.headers["set-cookie"]  # im Cookie steht nur eine Signatur
        assert "httponly" in r.headers["set-cookie"].lower()
        assert c.get("/", follow_redirects=False).status_code == 200


def test_gefaelschtes_cookie_hilft_nicht(tmp_path):
    with TestClient(create_app(cfg(tmp_path))) as c:
        c.cookies.set("uc7_zugang", "a" * 64)
        assert c.get("/", follow_redirects=False).status_code == 303


def test_app_startet_nicht_ohne_zugangscode(monkeypatch):
    monkeypatch.setenv("ZUGANGSCODE", "kurz")
    with pytest.raises(RuntimeError):
        aus_umgebung()
    monkeypatch.delenv("ZUGANGSCODE")
    with pytest.raises(RuntimeError):
        aus_umgebung()


# ---------- Ticket-Eingabe ----------

@pytest.mark.parametrize("kunden_id, text, stichwort", [
    ("K999", "Hallo", "Kunden"),
    ("", "Hallo", "Kunden"),
    ("K001", "   ", "Anliegen"),
    ("K001", "x" * 1001, "zu lang"),
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
    assert [e["event"] for e in ev] == ["schritt"] * 5 + ["ergebnis", "ende"]
    assert "Ich schaue mir das Konto an." in ev[0]["data"]
    assert "Kunde nachschlagen" in ev[1]["data"] and "Anna Berger" in ev[1]["data"]
    assert "54,34 USD" in ev[3]["data"]
    assert "Antwortentwurf" in ev[5]["data"] and "wartet auf Freigabe" in ev[5]["data"]
    assert [e.get("id") for e in ev[:6]] == list(range(6))


def test_sse_setzt_nach_verbindungsabbruch_fort(client):
    run_id = starte(client)
    warte_bis_fertig(client, run_id)
    ev = sse_ereignisse(client, run_id, headers={"Last-Event-ID": "3"})
    assert [e.get("id") for e in ev[:-1]] == [4, 5]


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
        assert "Pauschale" in c.get(f"/lauf/{lauf['run_id']}").text


def test_ohne_api_key_wird_lauf_zum_fehler(tmp_path, monkeypatch):
    from app.lauf import _sdk_query
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with TestClient(create_app(cfg(tmp_path), query_fn=_sdk_query)) as c:
        c.post("/login", data={"code": CODE})
        lauf = warte_bis_fertig(c, starte(c))
        assert lauf["status"] == "fehler" and "RuntimeError" in lauf["ergebnis"]["fehlertext"]


def test_nur_ein_lauf_gleichzeitig(client):
    client.app.state.speicher.lauf_anlegen("20260928-000000-aaaaaa", "K001", "a@example.com", "läuft noch")
    r = client.post("/lauf", data={"kunden_id": "K002", "text": "Hallo"})
    assert r.status_code == 429 and "anderes Ticket" in r.text


def test_verwaiste_laeufe_beim_start_pauschal_verbucht(tmp_path):
    Speicher(tmp_path / "uc7.sqlite").lauf_anlegen("20260928-000000-bbbbbb", "K001", "a@example.com", "x")
    with TestClient(create_app(cfg(tmp_path))) as c:
        lauf = c.app.state.speicher.lauf("20260928-000000-bbbbbb")
        assert lauf["status"] == "abgebrochen" and lauf["kosten_usd"] == 0.50


# ---------- Freigaben ----------

def test_freigabe_bestaetigen_nur_einmal(client):
    warte_bis_fertig(client, starte(client))
    eid = client.app.state.speicher.offene_empfehlungen()[0]["empfehlungs_id"]
    r = client.get("/freigaben")
    assert "Offen (1)" in r.text and "Echte Doppelabbuchung" in r.text and "Z005" in r.text
    r = client.post(f"/freigaben/{eid}", data={"entscheidung": "bestaetigt", "kommentar": " passt "})
    assert "Entscheidung gespeichert" in r.text and "Offen (0)" in r.text
    r = client.post(f"/freigaben/{eid}", data={"entscheidung": "abgelehnt"})
    assert "schon entschieden" in r.text
    s = client.app.state.speicher
    assert s.entschiedene_empfehlungen()[0]["entscheidung"] == "bestaetigt"
    assert s.entschiedene_empfehlungen()[0]["kommentar"] == "passt"
    assert s.freigabe_statistik() == {"gesamt": 1, "bestaetigt": 1}


def test_freigabe_ungueltig_und_unbekannt(client):
    warte_bis_fertig(client, starte(client))
    eid = client.app.state.speicher.offene_empfehlungen()[0]["empfehlungs_id"]
    assert "Bestätigen" in client.post(f"/freigaben/{eid}", data={"entscheidung": "vielleicht"}).text
    assert "schon entschieden" in client.post("/freigaben/E-gibtsnicht", data={"entscheidung": "abgelehnt"}).text
    assert len(client.app.state.speicher.offene_empfehlungen()) == 1


# ---------- Budget ----------

def lauf_mit_kosten(speicher, run_id, kosten, erstellt=None, status="fertig"):
    speicher.lauf_anlegen(run_id, "K001", "a@example.com", "x")
    speicher.db.execute("UPDATE laeufe SET status=?, kosten_usd=?, erstellt=COALESCE(?, erstellt) WHERE run_id=?",
                        (status, kosten, erstellt, run_id))
    speicher.db.commit()


def test_budget_gesperrt_zeigt_abschaltmeldung(client, aufrufe):
    lauf_mit_kosten(client.app.state.speicher, "20260928-000000-000001", 4.50)
    r = client.get("/")
    assert "macht Pause" in r.text and "01." in r.text and "<textarea" not in r.text
    r = client.post("/lauf", data={"kunden_id": "K001", "text": "Hallo"})
    assert r.status_code == 503 and "macht Pause" in r.text
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


@pytest.mark.parametrize("monat, erwartet", [("2026-09", "01.10.2026"), ("2026-12", "01.01.2027")])
def test_naechster_monat(monat, erwartet):
    assert budget.BudgetStand(monat, 0, 0, 4.5).naechster_monat == erwartet


# ---------- Darstellung ----------

def test_sse_format_mehrzeilig():
    assert darstellung.sse_nachricht("schritt", "<li>\na\n</li>", 7) == "id: 7\nevent: schritt\ndata: <li>\ndata: a\ndata: </li>\n\n"
    assert darstellung.sse_nachricht("ende", "") == "event: ende\ndata: \n\n"


def test_usd_deutsch():
    assert darstellung.usd(1234.5) == "1.234,50 USD"
    assert darstellung.usd_genau(0.0312) == "0,0312 USD"
