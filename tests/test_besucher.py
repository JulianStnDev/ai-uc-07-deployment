"""Zugang für Portfolio-Besucher (Branch e): Replay ohne Login, persönliche Links mit Kontingent,
Sperren und Ablauf, Isolation zwischen Codes, CLI. Alles ohne LLM und ohne API-Kosten."""

import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import links, zugang
from app.main import AUFZEICHNUNG, create_app
from app.speicher import LINK_LAEUFE, Speicher
from test_app import (CODE, aufrufe, cfg, fake_doppelabbuchung, lege_an, sse_ereignisse, starte,  # noqa: F401
                      umgebung, warte_bis_fertig)
from test_betrieb import warte


def kein_agent(prompt, options, kasten):
    raise AssertionError("Das Replay darf den Agent nicht aufrufen")


OFFEN: list[TestClient] = []


@pytest.fixture(autouse=True)
def browser_schliessen():
    yield
    while OFFEN:
        OFFEN.pop().__exit__(None, None, None)


def browser(app) -> TestClient:
    """Eigener Browser (eigene Cookies) mit eigener Event-Loop, damit Läufe im Hintergrund zu Ende laufen."""
    c = TestClient(app)
    c.__enter__()
    OFFEN.append(c)
    return c


@pytest.fixture
def app(tmp_path, aufrufe):
    return create_app(cfg(tmp_path, replay_takt_s=0), query_fn=fake_doppelabbuchung(aufrufe))


def besucher(app, pseudonym="acme") -> tuple[TestClient, str]:
    """Neuer Link und ein Browser, der ihn geöffnet hat."""
    code = zugang.neuer_code(pseudonym)
    app.state.speicher.link_anlegen(code)
    c = browser(app)
    r = c.get(f"/?code={code}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"  # Code verschwindet aus der Adresse
    return c, code


def admin(app) -> TestClient:
    c = browser(app)
    c.post("/login", data={"code": CODE})
    return c


def replay_ereignisse(c) -> list[dict]:
    ereignisse, aktuell = [], {}
    with c.stream("GET", "/replay/stream") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        for zeile in r.iter_lines():
            if zeile.startswith("event: "):
                aktuell["event"] = zeile[7:]
            elif zeile.startswith("data: "):
                aktuell["data"] = aktuell.get("data", "") + zeile[6:] + "\n"
            elif zeile == "" and aktuell:
                ereignisse.append(aktuell)
                if aktuell["event"] == "ende":
                    break
                aktuell = {}
    return ereignisse


# ---------- Replay ----------

def test_aufzeichnung_ist_der_echte_lauf_ohne_interne_notiz():
    lauf = AUFZEICHNUNG["lauf"]
    assert lauf["run_id"] == "20260928-190527-a9c291" and lauf["status"] == "fertig" and lauf["kunden_id"] == "K001"
    assert [e["entscheidung"] for e in AUFZEICHNUNG["empfehlungen"]] == ["bestaetigt"]
    assert AUFZEICHNUNG["antwort"]["quelle"] == "llm" and AUFZEICHNUNG["antwort"]["text"].startswith("Hallo Anna")
    assert "notiz" not in json.dumps(AUFZEICHNUNG)


def test_startseite_ohne_login_zeigt_replay_ohne_datenbank(tmp_path, monkeypatch):
    app = create_app(cfg(tmp_path, replay_takt_s=0), query_fn=kein_agent)
    abfragen = []
    with TestClient(app) as c:
        echt = app.state.speicher._alle
        monkeypatch.setattr(app.state.speicher, "_alle", lambda *a, **k: abfragen.append(a) or echt(*a, **k))
        html = c.get("/").text
        assert "Recording of a real run from 28 Sep 2026" in html and 'sse-connect="/replay/stream"' in html
        assert "FocusFlow is a fictional German app, so customer conversations are in German." in html
        assert "Ask me for a personal live access link." in html and "/betrieb" not in html
        replay_ereignisse(c)
        assert abfragen == []  # weder Seite noch Stream fragen die Datenbank an


def test_replay_spielt_lauf_freigabe_und_antwort_ab(app, kein_echter_llm_aufruf, aufrufe):
    with TestClient(app) as c:
        ev = replay_ereignisse(c)
    n = len(AUFZEICHNUNG["lauf"]["ereignisse"])
    assert [e["event"] for e in ev] == ["schritt"] * n + ["status", "entwurf", "abschluss",
                                                          "abschluss", "entwurf", "entwurf", "abschluss", "ende"]
    assert "Looks up the customer account" in ev[0]["data"]
    zwischen, konsole, schreibt, final = ev[n + 1]["data"], ev[n + 3]["data"], ev[n + 4]["data"], ev[n + 5]["data"]
    assert "Interim reply" in zwischen and "hx-get" not in zwischen and 'href="/freigaben"' not in zwischen
    assert 'class="badge offen">open<' in ev[n + 2]["data"]
    assert "Support console: a human decided" in konsole and 'class="badge bestaetigt">confirmed<' in konsole
    assert "being written" in schreibt
    assert "Reply after the decision" in final and "Decided by support on 28 Sep 2026, 19:09" in final
    assert AUFZEICHNUNG["antwort"]["text"].split("\n")[0] in final
    assert "No speculation" in ev[n + 6]["data"]  # Judge-Urteil auf der endgültigen Antwort
    assert aufrufe == [] and kein_echter_llm_aufruf == {"judge": [], "antwort": []}


def test_robots_sperrt_crawler(app):
    with TestClient(app) as c:
        r = c.get("/robots.txt")
        assert r.status_code == 200 and "Disallow: /" in r.text
        assert '<meta name="robots" content="noindex">' in c.get("/").text


def test_admin_zugang_unveraendert_und_sieht_replay_und_start(app):
    with TestClient(app):
        c = admin(app)
        assert "How this demo works" in c.get("/").text and "Betrieb" in c.get("/").text
        assert "Recording of a real run" in c.get("/replay").text
        assert c.get("/betrieb").status_code == 200


# ---------- Persönliche Links ----------

def test_code_format_und_zufall():
    code = zugang.neuer_code("Acme GmbH!")
    assert re.fullmatch(r"acme-gmbh-[a-z2-9]{6}", code) and zugang.CODE_MUSTER.match(code)
    assert len({zugang.neuer_code("x") for _ in range(200)}) == 200
    with pytest.raises(ValueError):
        zugang.neuer_code("!!!")


def test_link_gibt_live_zugang_mit_kontingent(app):
    with TestClient(app):
        c, code = besucher(app)
        html = c.get("/").text
        assert "How this demo works" in html and "<strong>5/5</strong> live runs" in html
        assert "valid until" in html and "Sign out" in html and "Betrieb" not in html
        assert "FocusFlow is a fictional German app" in html
        run_id = starte(c)
        assert warte_bis_fertig(c, run_id)["zugang"] == code
        assert app.state.speicher.link(code)["laeufe"] == 1
        assert "<strong>4/5</strong> live runs" in c.get("/").text


def test_kontingent_leer_hinweis_und_zurueck_zum_replay(app):
    with TestClient(app):
        c, code = besucher(app)
        for _ in range(LINK_LAEUFE):
            warte_bis_fertig(c, starte(c))
        assert app.state.speicher.link(code)["laeufe"] == LINK_LAEUFE
        r = c.post("/lauf", data={"kunden_id": "K001", "text": "noch einer"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/?hinweis=leer"
        assert c.get("/anliegen", follow_redirects=False).headers["location"] == "/?hinweis=leer"
        html = c.get("/").text
        assert "used all 5 live runs" in html and "Recording of a real run" in html
        assert html.count('href="/lauf/') == LINK_LAEUFE  # eigene Fälle bleiben erreichbar
        assert c.get("/freigaben").status_code == 200  # Erstattung aus Lauf 5 kann noch entschieden werden
        assert app.state.speicher.link(code)["laeufe"] == LINK_LAEUFE and len(app.state.speicher.laeufe_zum_zugang(code)) == 5


def test_kontingent_atomar_und_nie_gestartete_laeufe_zaehlen_nicht(tmp_path):
    s = Speicher(tmp_path / "db.sqlite")
    s.link_anlegen("acme-aaaaaa")
    assert [s.link_lauf_buchen("acme-aaaaaa") for _ in range(7)] == [True] * 5 + [False] * 2
    s.lauf_anlegen("20260930-000000-aaaaaa", "K001", "a@example.com", "x", zugang="acme-aaaaaa")
    s.lauf_abschliessen("20260930-000000-aaaaaa", status="verfallen", kosten_usd=0, kosten_pauschal=False, dauer_s=0,
                        ergebnis={}, ereignisse=[], empfehlungen=[])
    assert s.link("acme-aaaaaa")["laeufe"] == 4
    # Beim Start einer Instanz: angelegt, aber nie gestartet -> ebenfalls zurück
    alt = datetime.now(timezone.utc) - timedelta(minutes=20)
    s.lauf_anlegen("20260930-000000-bbbbbb", "K001", "a@example.com", "x", jetzt=alt, zugang="acme-aaaaaa")
    s.verwaiste_laeufe_abbrechen(0.5)
    assert s.link("acme-aaaaaa")["laeufe"] == 3


def test_verfallener_lauf_im_browser_gibt_kontingent_zurueck(tmp_path, aufrufe):
    app = create_app(cfg(tmp_path, agent_wartezeit_s=0.2), query_fn=fake_doppelabbuchung(aufrufe))
    with TestClient(app):
        c, code = besucher(app)
        run_id = lege_an(c)
        assert app.state.speicher.link(code)["laeufe"] == 1
        c.portal.call(app.state.agent_platz.acquire)  # Platz belegt, Lauf verfällt ohne Kosten
        sse_ereignisse(c, run_id)
        assert app.state.speicher.lauf(run_id)["status"] == "verfallen" and app.state.speicher.link(code)["laeufe"] == 0


def test_monatsdeckel_gilt_zusaetzlich_und_kostet_kein_kontingent(app):
    with TestClient(app):
        c, code = besucher(app)
        app.state.speicher.lauf_anlegen("20260930-000000-cccccc", "K001", "a@example.com", "x")
        app.state.speicher.db.execute("UPDATE laeufe SET status='fertig', kosten_usd=4.50"); app.state.speicher.db.commit()
        r = c.post("/lauf", data={"kunden_id": "K001", "text": "Hallo"})
        assert r.status_code == 503 and "taking a break" in r.text
        assert app.state.speicher.link(code)["laeufe"] == 0


def test_voller_agent_platz_kostet_kein_kontingent(app):
    with TestClient(app):
        c, code = besucher(app)
        app.state.speicher.lauf_anlegen("20260930-000000-dddddd", "K001", "a@example.com", "x", status="laeuft")
        assert c.post("/lauf", data={"kunden_id": "K001", "text": "Hallo"}).status_code == 429
        assert app.state.speicher.link(code)["laeufe"] == 0


@pytest.mark.parametrize("vorbereiten, hinweis", [
    (lambda s, code: s.link_sperren(code), "gesperrt"),
    (lambda s, code: s.db.execute("UPDATE zugangslinks SET erstellt=? WHERE code=?",
                                  ((datetime.now(timezone.utc) - timedelta(days=61)).isoformat(timespec="seconds"), code)), "abgelaufen"),
])
def test_gesperrter_oder_abgelaufener_link_wirkt_sofort(app, vorbereiten, hinweis):
    with TestClient(app):
        c, code = besucher(app)
        assert c.get("/anliegen").status_code == 200
        vorbereiten(app.state.speicher, code); app.state.speicher.db.commit()
        r = c.get("/anliegen", follow_redirects=False)  # schon mit Cookie: nächste Anfrage greift
        assert r.status_code == 303 and r.headers["location"] == f"/?hinweis={hinweis}"
        assert zugang.LINK_COOKIE_NAME in r.headers["set-cookie"] and "Max-Age=0" in r.headers["set-cookie"]
        html = c.get(r.headers["location"]).text  # Cookie ist weg: Gast sieht Replay mit Hinweis
        assert "Recording of a real run" in html and "live runs</span>" not in html and ("expired" if hinweis == "abgelaufen" else "not valid") in html
        assert c.post("/lauf", data={"kunden_id": "K001", "text": "x"}, follow_redirects=False).headers["location"] == "/"
        # Den Link erneut zu öffnen hilft nicht
        r = c.get(f"/?code={code}", follow_redirects=False)
        assert r.headers["location"] == f"/?hinweis={hinweis}"


def test_unbekannter_code_und_gefaelschtes_cookie(app):
    with TestClient(app) as c:
        assert c.get("/?code=acme-zzzzzz", follow_redirects=False).headers["location"] == "/?hinweis=unbekannt"
        assert c.get("/?code=../../etc", follow_redirects=False).headers["location"] == "/?hinweis=unbekannt"
        app.state.speicher.link_anlegen("acme-aaaaaa")
        c.cookies.set(zugang.LINK_COOKIE_NAME, "acme-aaaaaa." + "0" * 64)  # Code bekannt, Signatur falsch
        assert c.get("/anliegen", follow_redirects=False).headers["location"] == "/"


# ---------- Isolation: Besucher sehen nur Läufe mit ihrem Code ----------

def test_besucher_sehen_nur_eigene_laeufe(app):
    with TestClient(app):
        sp = app.state.speicher
        a = admin(app)
        lauf_admin = starte(a, text="Admin-Fall")
        warte_bis_fertig(a, lauf_admin)
        b1, code1 = besucher(app, "firma-eins")
        lauf_1 = starte(b1, text="Fall von Firma eins")
        warte_bis_fertig(b1, lauf_1)
        b2, code2 = besucher(app, "firma-zwei")
        lauf_2 = starte(b2, text="Fall von Firma zwei")
        warte_bis_fertig(b2, lauf_2)
        eid = {e["run_id"]: e["empfehlungs_id"] for e in sp.offene_empfehlungen()}

        # Konsole: nur eigene Empfehlungen, Zähler in der Navigation ebenso
        konsole = b1.get("/freigaben").text
        assert f"/lauf/{lauf_1}" in konsole and f"/lauf/{lauf_2}" not in konsole and f"/lauf/{lauf_admin}" not in konsole
        assert "Fall von Firma zwei" not in konsole and 'open: </span>1<' in konsole
        # Direkter Aufruf fremder Lauf-IDs: wie unbekannt
        for fremd in (lauf_admin, lauf_2):
            assert b1.get(f"/lauf/{fremd}").status_code == 404
            assert "Fall von" not in b1.get(f"/lauf/{fremd}").text and "Admin-Fall" not in b1.get(f"/lauf/{fremd}").text
            assert b1.get(f"/lauf/{fremd}/stream").status_code == 404
            assert b1.get(f"/lauf/{fremd}/antwort").status_code == 404
        # Fremde Empfehlung entscheiden: abgewiesen, bleibt offen
        r = b1.post(f"/freigaben/{eid[lauf_2]}", data={"entscheidung": "abgelehnt", "notiz": "fremd"})
        assert "does not exist" in r.text and sp.entschiedene_empfehlungen() == []
        # Eigene Entscheidung mit interner Notiz: für Code 2 unsichtbar, für den Admin sichtbar
        b1.post(f"/freigaben/{eid[lauf_1]}", data={"entscheidung": "bestaetigt", "begruendung": "B-1", "notiz": "NOTIZ-1"})
        warte(lambda: sp.antwort(lauf_1) is not None)
        assert "NOTIZ-1" in b1.get("/freigaben").text and "NOTIZ-1" not in b2.get("/freigaben").text
        assert "0 of 0 confirmed" in b2.get("/freigaben").text and "1 of 1 confirmed" in b1.get("/freigaben").text
        assert "NOTIZ-1" in a.get("/freigaben").text and f"/lauf/{lauf_2}" in a.get("/freigaben").text
        assert a.get(f"/lauf/{lauf_1}").status_code == 200 and a.get(f"/lauf/{lauf_2}").status_code == 200
        # Startseite: eigene Fälle
        start = b2.get("/").text
        assert f"/lauf/{lauf_2}" in start and f"/lauf/{lauf_1}" not in start and f"/lauf/{lauf_admin}" not in start
        # Kein Zugriff auf Betrieb, Diagnose, Protokoll
        for pfad in ("/betrieb", "/diagnose"):
            r = b1.get(pfad, follow_redirects=False)
            assert r.status_code == 303 and r.headers["location"] == "/"
        r = b1.post(f"/betrieb/pruefen/{lauf_1}", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"
        assert sp.lauf(lauf_1)["zugang"] == code1 and sp.lauf(lauf_2)["zugang"] == code2 and sp.lauf(lauf_admin)["zugang"] is None


def test_uebergaben_nur_aus_eigenen_laeufen(tmp_path):
    from test_oberflaeche import fake_uebergabe
    app = create_app(cfg(tmp_path), query_fn=fake_uebergabe)
    with TestClient(app):
        a = admin(app)
        warte_bis_fertig(a, starte(a, kunden_id="K002", text="6,99 zweimal"))
        b, _ = besucher(app)
        assert "Im Konto nur eine Zahlung sichtbar." in a.get("/freigaben").text
        assert "Im Konto nur eine Zahlung sichtbar." not in b.get("/freigaben").text


# ---------- Datensparsamkeit und CLI ----------

def test_links_speichern_nur_code_zeitpunkt_und_anzahl(tmp_path):
    s = Speicher(tmp_path / "db.sqlite")
    spalten = {r["name"] for r in s.db.execute("PRAGMA table_info(zugangslinks)")}
    assert spalten == {"code", "erstellt", "laeufe", "gesperrt"}


def test_cli_anlegen_liste_sperren(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(links, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("APP_URL", raising=False)
    monkeypatch.setenv("DATEN_DIR", str(tmp_path))
    assert links.main(["anlegen", "Acme"]) == 0
    ausgabe = capsys.readouterr().out
    url = re.search(r"https://uc7-807149335205\.europe-west3\.run\.app/\?code=(acme-[a-z2-9]{6})", ausgabe)
    assert url and "5 Läufe" in ausgabe
    code = url.group(1)
    assert links.main(["liste"]) == 0
    zeile = [z for z in capsys.readouterr().out.splitlines() if z.startswith(code)][0]
    assert "0/5" in zeile and zeile.endswith("ok")
    assert links.main(["sperren", code]) == 0 and "Gesperrt" in capsys.readouterr().out
    links.main(["liste"])
    assert capsys.readouterr().out.strip().endswith("gesperrt")
    assert links.main(["sperren", "gibts-nicht"]) == 1
    assert links.main(["anlegen", "!!!"]) == 2


def test_schema_ohne_fragezeichen():
    """Für Postgres wird jedes ? zu %s, auch in SQL-Kommentaren; die App würde beim Start abstürzen."""
    from app.speicher import SCHEMA
    assert all("?" not in s for s in SCHEMA)
