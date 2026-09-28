"""Branch (c) ohne LLM: Variante A (endgültige Antwort nach Freigabe), Judge-Stichprobe und Deckel,
Regelprüfungen, Budget inkl. Prüf- und Antwortkosten, Betriebsseite."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import budget, pruefung
from app.main import betriebsdaten, create_app
from test_app import CODE, cfg, fake_doppelabbuchung, sse_ereignisse, starte, warte_bis_fertig
from test_oberflaeche import fake_uebergabe, pruefe_infos


@pytest.fixture(autouse=True)
def umgebung(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-platzhalter")
    monkeypatch.setattr("app.main.load_dotenv", lambda *a, **k: None)


def app_client(tmp_path, query_fn=None, **kw):
    c = TestClient(create_app(cfg(tmp_path, **kw), query_fn=query_fn or fake_doppelabbuchung([])))
    c.__enter__()
    c.post("/login", data={"code": CODE})
    return c


def warte(bedingung, timeout=5):
    import time
    ende = time.time() + timeout
    while time.time() < ende:
        if bedingung():
            return True
        time.sleep(0.02)
    raise AssertionError("Bedingung nicht erfüllt")


# ---------- Variante A ----------

def test_zwischenbescheid_dann_antwort_nach_freigabe(tmp_path, kein_echter_llm_aufruf):
    c = app_client(tmp_path)
    run_id = starte(c)
    seite = c.get(f"/lauf/{run_id}").text
    assert "Zwischenbescheid" in seite and "Erstattung in Prüfung" in seite and 'hx-trigger="every 4s"' in seite
    assert "Entwurf des Agents (intern)" in seite                  # Agent-Entwurf nur intern
    assert "Entwurf des Agents (intern)" in c.get("/freigaben").text
    eid = c.app.state.speicher.offene_empfehlungen()[0]["empfehlungs_id"]
    c.post(f"/freigaben/{eid}", data={"entscheidung": "bestaetigt", "kommentar": "passt"})
    warte(lambda: c.app.state.speicher.antwort(run_id) is not None)
    a = c.app.state.speicher.antwort(run_id)
    assert a["quelle"] == "llm" and a["text"] == "Hallo, deine Erstattung ist freigegeben." and a["kosten_usd"] == 0.01
    assert kein_echter_llm_aufruf["antwort"][0]["entscheidungen"] == ["bestaetigt"]
    frag = c.get(f"/lauf/{run_id}/antwort").text
    assert "Antwort nach Freigabe" in frag and "freigegeben" in frag and "hx-trigger" not in frag  # Abfragen endet
    monat = datetime.now(timezone.utc).strftime("%Y-%m")
    assert c.app.state.speicher.kosten_im_monat(monat) == pytest.approx(0.031 + 0.01 + sum(
        p["kosten_usd"] for p in c.app.state.speicher.pruefungen()))
    c.__exit__(None, None, None)


def test_ablehnung_und_nur_eine_antwort(tmp_path, kein_echter_llm_aufruf):
    c = app_client(tmp_path)
    run_id = starte(c)
    eid = c.app.state.speicher.offene_empfehlungen()[0]["empfehlungs_id"]
    c.post(f"/freigaben/{eid}", data={"entscheidung": "abgelehnt", "kommentar": "Vormerkung"})
    warte(lambda: c.app.state.speicher.antwort(run_id) is not None)
    c.post(f"/freigaben/{eid}", data={"entscheidung": "bestaetigt"})  # schon entschieden: keine zweite Antwort
    assert len(kein_echter_llm_aufruf["antwort"]) == 1
    assert "abgelehnt" in c.app.state.speicher.antwort(run_id)["text"]
    c.__exit__(None, None, None)


def test_antwort_faellt_auf_vorlage_zurueck(tmp_path, monkeypatch):
    def kaputt(*a, **k):
        raise ConnectionError("API weg")
    monkeypatch.setattr("app.antwort.antwort_schreiben", kaputt)
    c = app_client(tmp_path)
    run_id = starte(c)
    eid = c.app.state.speicher.offene_empfehlungen()[0]["empfehlungs_id"]
    c.post(f"/freigaben/{eid}", data={"entscheidung": "bestaetigt"})
    warte(lambda: c.app.state.speicher.antwort(run_id) is not None)
    a = c.app.state.speicher.antwort(run_id)
    assert a["quelle"] == "vorlage" and a["kosten_usd"] == 0
    assert "Die Erstattung über 54,34 USD für deine Zahlung Z005 ist freigegeben." in a["text"]
    assert "Aus einer festen Vorlage erstellt" in c.get(f"/lauf/{run_id}").text
    c.__exit__(None, None, None)


def test_bei_erschoepftem_budget_vorlage_ohne_llm(tmp_path, kein_echter_llm_aufruf):
    c = app_client(tmp_path)
    run_id = starte(c)
    sp = c.app.state.speicher
    sp.lauf_anlegen("20260928-000000-ffffff", "K001", "a@example.com", "x", status="laeuft")
    sp.db.execute("UPDATE laeufe SET status='fertig', kosten_usd=4.50 WHERE run_id='20260928-000000-ffffff'"); sp.db.commit()
    eid = sp.offene_empfehlungen()[0]["empfehlungs_id"]
    c.post(f"/freigaben/{eid}", data={"entscheidung": "bestaetigt"})
    warte(lambda: sp.antwort(run_id) is not None)
    assert sp.antwort(run_id)["quelle"] == "vorlage" and kein_echter_llm_aufruf["antwort"] == []
    c.__exit__(None, None, None)


def test_zwischenbescheid_ohne_zusage_und_frist():
    from app.antwort import zwischenbescheid
    t = zwischenbescheid("Anna Berger").lower()
    assert t.startswith("hallo anna,") and "erstatt" in t
    for verboten in ("wird erstattet", "erstatten wir", "werktag", "tagen", "zurückgebucht"):
        assert verboten not in t


# ---------- Judge: Stichprobe, Deckel, was geprüft wird ----------

def test_stichprobe_reproduzierbar_und_rund_20_prozent():
    ids = [f"20260928-120000-{i:06x}" for i in range(2000)]
    anteil = sum(pruefung.in_stichprobe(i) for i in ids) / len(ids)
    assert 0.17 < anteil < 0.23
    assert all(pruefung.in_stichprobe(i) == pruefung.in_stichprobe(i) for i in ids[:50])


def test_judge_nur_in_stichprobe_regeln_immer(tmp_path, monkeypatch, kein_echter_llm_aufruf):
    monkeypatch.setattr("app.pruefung.in_stichprobe", lambda run_id, prozent=20: False)
    c = app_client(tmp_path, query_fn=fake_uebergabe)
    run_id = starte(c, kunden_id="K002", text="6,99 zweimal")
    warte(lambda: c.app.state.speicher.pruefungen_zum_lauf(run_id))
    p = c.app.state.speicher.pruefungen_zum_lauf(run_id)[0]
    assert p["regeln"]["kunde_nachgeschlagen"] and p["judge"] is None and kein_echter_llm_aufruf["judge"] == []
    assert "Nicht in der Stichprobe" in c.get(f"/lauf/{run_id}").text
    c.__exit__(None, None, None)


def test_judge_prueft_entwurf_ohne_empfehlung(tmp_path, monkeypatch, kein_echter_llm_aufruf):
    monkeypatch.setattr("app.pruefung.in_stichprobe", lambda run_id, prozent=20: True)
    c = app_client(tmp_path, query_fn=fake_uebergabe)
    run_id = starte(c, kunden_id="K002", text="6,99 zweimal")
    warte(lambda: (c.app.state.speicher.pruefungen_zum_lauf(run_id) or [{}])[0].get("judge"))
    assert kein_echter_llm_aufruf["judge"][0]["text"] == "Hallo Ben, wir prüfen das."
    seite = c.get(f"/lauf/{run_id}").text
    assert "Keine Spekulation" in seite and "Keine Zusage vor Freigabe" in seite
    c.__exit__(None, None, None)


def test_judge_prueft_bei_empfehlung_die_endgueltige_antwort(tmp_path, monkeypatch, kein_echter_llm_aufruf):
    monkeypatch.setattr("app.pruefung.in_stichprobe", lambda run_id, prozent=20: True)
    c = app_client(tmp_path)
    run_id = starte(c)
    warte(lambda: c.app.state.speicher.pruefungen_zum_lauf(run_id))
    assert kein_echter_llm_aufruf["judge"] == []  # Agent-Entwurf sieht der Kunde nicht -> nicht automatisch geprüft
    eid = c.app.state.speicher.offene_empfehlungen()[0]["empfehlungs_id"]
    c.post(f"/freigaben/{eid}", data={"entscheidung": "bestaetigt"})
    warte(lambda: len(c.app.state.speicher.pruefungen_zum_lauf(run_id)) == 2)
    j = kein_echter_llm_aufruf["judge"][0]
    assert j["text"] == "Hallo, deine Erstattung ist freigegeben." and "bestätigt" in j["entscheidung"]
    c.__exit__(None, None, None)


def test_judge_deckel(tmp_path, monkeypatch, kein_echter_llm_aufruf):
    monkeypatch.setattr("app.pruefung.in_stichprobe", lambda run_id, prozent=20: True)
    c = app_client(tmp_path, query_fn=fake_uebergabe)
    sp = c.app.state.speicher
    sp.lauf_anlegen("20260928-000000-aaaaab", "K001", "a@example.com", "x", status="fertig")
    sp.pruefung_speichern("20260928-000000-aaaaab", "agent", {}, None, None, pruefung.JUDGE_DECKEL_MONAT_USD)
    run_id = starte(c, kunden_id="K002", text="6,99 zweimal")
    warte(lambda: c.app.state.speicher.pruefungen_zum_lauf(run_id))
    p = sp.pruefungen_zum_lauf(run_id)[0]
    assert p["judge"] is None and p["fehler"] == "Judge-Deckel erreicht" and kein_echter_llm_aufruf["judge"] == []
    r = c.post(f"/betrieb/pruefen/{run_id}")
    assert "Judge-Deckel oder Monatsbudget erreicht" in r.text
    c.__exit__(None, None, None)


def test_judge_von_hand_auf_betriebsseite(tmp_path, monkeypatch, kein_echter_llm_aufruf):
    monkeypatch.setattr("app.pruefung.in_stichprobe", lambda run_id, prozent=20: False)
    c = app_client(tmp_path)
    run_id = starte(c)
    warte(lambda: c.app.state.speicher.pruefungen_zum_lauf(run_id))
    assert f'action="/betrieb/pruefen/{run_id}"' in c.get("/betrieb").text
    r = c.post(f"/betrieb/pruefen/{run_id}")
    assert "Prüfung durchgeführt" in r.text and len(kein_echter_llm_aufruf["judge"]) == 1
    assert f'action="/betrieb/pruefen/{run_id}"' not in r.text  # schon geprüft: kein Knopf mehr
    c.__exit__(None, None, None)


# ---------- Regeln ----------

def aufruf(w, ein, erg=None, fehler=False, blockiert=False):
    return {"art": "werkzeug", "werkzeug": w, "eingabe": ein, "ergebnis": erg or {}, "fehler": fehler, "blockiert": blockiert}


def test_regeln_erkennen_verstoesse():
    gut = [aufruf("kunde_nachschlagen", {"suche": "x"}), aufruf("zahlungen_ansehen", {"kunden_id": "K001"}),
           aufruf("erstattung_empfehlen", {"kunden_id": "K001"}), aufruf("antwort_entwerfen", {"text": "x"})]
    assert all(pruefung.regeln_pruefen(gut, "K001").values())
    schlecht = [aufruf("Bash", {}, fehler=True, blockiert=True), aufruf("erstattung_empfehlen", {"kunden_id": "K007"}),
                aufruf("abo_kuendigen", {"kunden_id": "K007"})]
    r = pruefung.regeln_pruefen(schlecht, "K006")
    assert r == {"kunde_nachgeschlagen": False, "entwurf_abgelegt": False, "keine_blockierten_werkzeuge": False,
                 "nur_eigenes_konto": False, "zahlungen_vor_empfehlung": False}


def test_fehlgeschlagene_aufrufe_zaehlen_nicht_als_handlung():
    r = pruefung.regeln_pruefen([aufruf("abo_kuendigen", {"kunden_id": "K007"}, fehler=True),
                                 aufruf("kunde_nachschlagen", {"suche": "x"}, fehler=True)], "K006")
    assert r["nur_eigenes_konto"] and not r["kunde_nachgeschlagen"]


# ---------- Betriebsseite ----------

def test_betriebsseite_kennzahlen_und_hinweise(tmp_path):
    c = app_client(tmp_path)
    starte(c)
    html = c.get("/betrieb").text
    assert "Läufe im Monat" in html and "Fehlerquote" in html and html.count('class="saeule"') == 14
    assert "Als Tabelle" in html and 'tabindex="0"' in html
    assert {"budget", "kosten", "fehlerquote", "pruefung", "spekulation", "zusage"} <= pruefe_infos(html)
    assert '<a href="/betrieb" aria-current="page">Betrieb</a>' in html
    c.__exit__(None, None, None)


def test_betriebsdaten_rechnet_richtig():
    stand = budget.BudgetStand("2026-09", 0, 0, 4.5)
    def l(i, status, kosten, dauer, tag="2026-09-28"):
        return {"run_id": f"r{i}", "erstellt": f"{tag}T10:00:00+00:00", "kunden_id": "K001", "titel": "t",
                "status": status, "kosten_usd": kosten, "kosten_pauschal": 0, "dauer_s": dauer}
    laeufe = [l(1, "fertig", 0.03, 30), l(2, "fertig", 0.05, 40), l(3, "fehler", 0.5, 5), l(4, "verfallen", 0, 0),
              l(5, "fertig", 0.04, 35, tag="2026-08-31")]
    d = betriebsdaten(laeufe, [], stand, 0.02, 0.01, heute=datetime(2026, 9, 28, 12, tzinfo=timezone.utc))
    assert d["anzahl"] == 4 and d["fertig"] == 2 and d["fehlerquote"] == pytest.approx(1 / 3) and d["verfallen"] == 1
    assert d["kosten_agent"] == pytest.approx(0.58) and d["kosten_gesamt"] == pytest.approx(0.61)
    assert d["kosten_je_lauf"] == pytest.approx(0.04) and d["dauer_p50"] == 30 and d["dauer_p95"] == 40
    assert d["tage"][-1] == {"tag": "2026-09-28", "anzahl": 3, "fehler": 1} and len(d["tage"]) == 14
