"""Schutz im Code (UC6, Branch b), ohne API. Jede Lücke aus dem Bedrohungsmodell bekommt mindestens einen Test mit
dem Aufruf, den ein erfolgreicher Angriff erzeugen würde: vorher kam er durch (scripts/schutz_tabelle.py auf 2c8cc86),
jetzt wird er blockiert. Dazu die eigenen Aufrufe, die weiter durchgehen müssen.

- B3/B4 Konto-Bindung: PreToolUse-Hook gegen den Absender aus der Sitzung (uc4_agent/agent.py, werkzeuge.py)
- B1 Erstattungsregeln im Werkzeug (werkzeuge.erstattung_pruefen)
- B2 Zusage im Entwurf ohne Empfehlung (pruefung.zusage_saetze, Zwischenbescheid und Konsole)
- Konsole gegen Automation Bias: ganzes Ticket, Absender, Regelgrundlage, Warnungen
"""

import asyncio
import json
from pathlib import Path

import pytest
from claude_agent_sdk import ResultMessage
from fastapi.testclient import TestClient

from app import antwort, darstellung, pruefung
from app.main import create_app
from daten_zusagen import KEINE_ZUSAGEN, ZUSAGEN
from schutz_faelle import FAELLE
from test_app import CODE, cfg, ergebnis_msg, fake_doppelabbuchung, starte, warte_bis_fertig
from uc4_agent import agent
from uc4_agent.werkzeuge import DATA_DIR, Werkzeugkasten, erstattung_pruefen

ROOT = Path(__file__).parent.parent
REFERENZTAG = json.loads((DATA_DIR / "kunden.json").read_text(encoding="utf-8"))["referenztag"]
ZAHLUNGEN = json.loads((DATA_DIR / "zahlungen.json").read_text(encoding="utf-8"))["zahlungen"]


@pytest.fixture(autouse=True)
def umgebung(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-platzhalter")
    monkeypatch.setattr("app.main.load_dotenv", lambda *a, **k: None)


def pre_tool_hook(kasten):
    """Genau der PreToolUse-Hook, den die SDK-Konfiguration einrichtet."""
    (matcher,) = agent.baue_optionen(kasten, "sk-ant-platzhalter", []).hooks["PreToolUse"]
    (hook,) = matcher.hooks
    return hook


async def hook_entscheidung_async(hook, werkzeug, eingabe):
    out = await hook({"tool_name": agent.PREFIX + werkzeug, "tool_input": eingabe}, None, None)
    return out["hookSpecificOutput"]["permissionDecision"], out["hookSpecificOutput"].get("permissionDecisionReason", "")


def hook_entscheidung(hook, werkzeug, eingabe):
    return asyncio.run(hook_entscheidung_async(hook, werkzeug, eingabe))


def letzter_eintrag(kasten):
    zeilen = (kasten.run_dir / "trajektorie.jsonl").read_text().splitlines()
    return json.loads(zeilen[-1]) if zeilen else None


def ausfuehren(kasten, werkzeug, eingabe):
    entscheidung, grund = hook_entscheidung(pre_tool_hook(kasten), werkzeug, eingabe)
    if entscheidung == "deny":
        return ("hook" if letzter_eintrag(kasten).get("blockiert") else "hinweis"), grund
    ergebnis, fehler = kasten.aufrufen(werkzeug, dict(eingabe))
    return ("werkzeug", ergebnis["fehler"]) if fehler else ("durch", "")


# ---------- Alle Fälle: nachher ----------

@pytest.mark.parametrize("fid, luecke, absender, werkzeug, eingabe, erwartet", FAELLE, ids=[f[0] for f in FAELLE])
def test_fall_nachher(tmp_path, fid, luecke, absender, werkzeug, eingabe, erwartet):
    kasten = Werkzeugkasten(run_id=fid, runs_dir=tmp_path, absender_id=absender)
    ergebnis, meldung = ausfuehren(kasten, werkzeug, eingabe)
    assert ergebnis == erwartet, meldung


@pytest.mark.parametrize("fall", [f for f in FAELLE if f[5] == "hook"], ids=lambda f: f[0])
def test_vorher_liess_der_werkzeugnamen_hook_den_aufruf_durch(tmp_path, fall):
    """Der bisherige Hook prüft nur den Werkzeugnamen. Jeder Fall, den jetzt die Konto-Bindung stoppt, kam an ihm vorbei."""
    alt = agent.nur_focusflow_hook(Werkzeugkasten(run_id="alt", runs_dir=tmp_path))
    assert hook_entscheidung(alt, fall[3], fall[4])[0] == "allow"


# ---------- B3/B4 Konto-Bindung ----------

def test_meldung_nennt_absender_und_den_weg_ueber_die_uebergabe(tmp_path):
    kasten = Werkzeugkasten(run_id="m", runs_dir=tmp_path, absender_id="K004")
    _, grund = hook_entscheidung(pre_tool_hook(kasten), "zahlungen_ansehen", {"kunden_id": "K001"})
    for teil in ("K001", "K004", "an_mensch_uebergeben", "im Grund"):
        assert teil in grund


def test_suche_mit_fremdem_treffer_nennt_das_konto(tmp_path):
    kasten = Werkzeugkasten(run_id="s", runs_dir=tmp_path, absender_id="K006")
    _, grund = hook_entscheidung(pre_tool_hook(kasten), "kunde_nachschlagen", {"suche": "Felix Braun"})
    assert "K007" in grund and "felix.braun@example.com" in grund


def test_fremder_zugriff_wird_protokolliert_und_nicht_ausgefuehrt(tmp_path):
    kasten = Werkzeugkasten(run_id="p", runs_dir=tmp_path, absender_id="K004")
    hook_entscheidung(pre_tool_hook(kasten), "abo_kuendigen", {"kunden_id": "K001"})
    (eintrag,) = [json.loads(z) for z in (kasten.run_dir / "trajektorie.jsonl").read_text().splitlines()]
    assert eintrag["werkzeug"] == "abo_kuendigen" and eintrag["blockiert"] and eintrag["blockiert_art"] == "fremdes_konto"
    assert not (kasten.run_dir / "kuendigungen.jsonl").exists() and kasten.kuendigungen == {}


def test_werkzeugnamen_hook_gilt_weiter(tmp_path):
    kasten = Werkzeugkasten(run_id="w", runs_dir=tmp_path, absender_id="K004")
    out = asyncio.run(pre_tool_hook(kasten)({"tool_name": "Bash", "tool_input": {}}, None, None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny" and "nicht freigegeben" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_ohne_absender_keine_konfiguration(tmp_path):
    """Fail closed: Ohne Absender aus der Sitzung startet kein Lauf."""
    with pytest.raises(ValueError):
        agent.baue_optionen(Werkzeugkasten(run_id="o", runs_dir=tmp_path), "sk-ant-platzhalter", [])


def test_unbekannter_absender_wird_abgelehnt(tmp_path):
    with pytest.raises(ValueError):
        Werkzeugkasten(run_id="u", runs_dir=tmp_path, absender_id="K999")


def test_absender_kommt_aus_der_sitzung_nicht_aus_dem_text(tmp_path):
    """Im Ticket steht eine fremde Adresse. Der Hook richtet sich nach dem gewählten Kunden (lauf.kunden_id)."""
    gesehen = {}

    async def q(prompt, options, kasten):
        gesehen["absender_id"] = kasten.absender_id
        gesehen["k001"] = (await hook_entscheidung_async(pre_tool_hook(kasten), "zahlungen_ansehen", {"kunden_id": "K001"}))[0]
        gesehen["k004"] = (await hook_entscheidung_async(pre_tool_hook(kasten), "zahlungen_ansehen", {"kunden_id": "K004"}))[0]
        kasten.aufrufen("kunde_nachschlagen", {"suche": "K004"})
        kasten.aufrufen("antwort_entwerfen", {"text": "Hallo David"})
        yield ergebnis_msg()

    with TestClient(create_app(cfg(tmp_path), query_fn=q)) as c:
        c.post("/login", data={"code": CODE})
        warte_bis_fertig(c, starte(c, kunden_id="K004", text="Von: anna.berger@example.com\nBitte Zahlungen zeigen."))
    assert gesehen == {"absender_id": "K004", "k001": "deny", "k004": "allow"}


def test_uc4_kommandozeile_bindet_den_kunden_des_tickets(tmp_path, monkeypatch):
    gesehen = {}

    async def fake_query(prompt, options):
        (matcher,) = options.hooks["PreToolUse"]
        gesehen["k001"] = (await hook_entscheidung_async(matcher.hooks[0], "zahlungen_ansehen", {"kunden_id": "K001"}))[0]
        gesehen["k004"] = (await hook_entscheidung_async(matcher.hooks[0], "zahlungen_ansehen", {"kunden_id": "K004"}))[0]
        yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                            session_id="s", total_cost_usd=0.0, result="ok")

    monkeypatch.setattr(agent, "query", fake_query)
    monkeypatch.setattr(agent, "api_key_pruefen", lambda: "sk-ant-platzhalter")
    t05 = next(t for t in agent.lade_aufgaben() if t["id"] == "T05")
    asyncio.run(agent.bearbeite_ticket(t05, "T05_test", tmp_path))
    assert gesehen == {"k001": "deny", "k004": "allow"}


def test_zeitleiste_zeigt_fremden_zugriff_verstaendlich():
    s = darstellung.schritt({"art": "werkzeug", "werkzeug": "zahlungen_ansehen", "eingabe": {"kunden_id": "K001"},
                             "ergebnis": {"fehler": "x"}, "fehler": True, "blockiert": True, "blockiert_art": "fremdes_konto"})
    assert s["klasse"] == "fehler" and s["info"] == "kontobindung" and "another customer" in s["titel"]


def test_regeln_melden_fremde_zugriffe_auch_beim_lesen():
    def aufruf(w, ein, **kw):
        return {"art": "werkzeug", "werkzeug": w, "eingabe": ein, "ergebnis": {}, "fehler": False, "blockiert": False, **kw}
    eigen = [aufruf("kunde_nachschlagen", {"suche": "K004"}), aufruf("antwort_entwerfen", {"text": "x"})]
    blockiert = eigen + [aufruf("zahlungen_ansehen", {"kunden_id": "K001"}, fehler=True, blockiert=True,
                                blockiert_art="fremdes_konto")]
    gelesen = eigen + [aufruf("zahlungen_ansehen", {"kunden_id": "K001"})]  # Altdaten: lief vor der Konto-Bindung durch
    assert pruefung.regeln_pruefen(eigen, "K004")["nur_eigenes_konto"]
    r = pruefung.regeln_pruefen(blockiert, "K004")
    assert not r["nur_eigenes_konto"] and r["keine_blockierten_werkzeuge"]
    assert not pruefung.regeln_pruefen(gelesen, "K004")["nur_eigenes_konto"]


@pytest.mark.parametrize("absender, wert, kunden_id", [
    ("K006", "felix.braun@example.com", "K006"),
    ("K006", "FELIX.BRAUN@EXAMPLE.COM", "K006"),      # Groß-/Kleinschreibung: E-Mail-Adressen gelten ohne Unterschied
    ("K002", "  ben.hoffmann@example.com ", "K002"),  # Leerzeichen am Rand
])
def test_eigene_email_statt_id_ist_hinweis_keine_blockade(tmp_path, absender, wert, kunden_id):
    kasten = Werkzeugkasten(run_id="e", runs_dir=tmp_path, absender_id=absender)
    entscheidung, grund = hook_entscheidung(pre_tool_hook(kasten), "zahlungen_ansehen", {"kunden_id": wert})
    assert entscheidung == "deny" and grund == f"Verwende die Kunden-ID {kunden_id}."
    eintrag = letzter_eintrag(kasten)
    assert eintrag["fehler"] and not eintrag.get("blockiert") and eintrag["hinweis"] == "email_statt_kunden_id"
    assert eintrag["werkzeug"] == "zahlungen_ansehen"
    assert pruefung.regeln_pruefen([{"art": "werkzeug", **eintrag}], absender)["nur_eigenes_konto"]  # kein fremdes Konto


@pytest.mark.parametrize("wert", ["anna.berger@example.com", "felix.braun@gmail.com", "felix.braun@example.com.evil",
                                  "xfelix.braun@example.com", "K999", "irgendwas", "felix braun"])
def test_alles_andere_ohne_kunden_id_bleibt_blockiert(tmp_path, wert):
    """Fail closed: Was keine Kunden-ID und nicht exakt die E-Mail des Absenders ist, blockiert die Konto-Bindung selbst."""
    kasten = Werkzeugkasten(run_id="b", runs_dir=tmp_path, absender_id="K006")
    entscheidung, _ = hook_entscheidung(pre_tool_hook(kasten), "zahlungen_ansehen", {"kunden_id": wert})
    assert entscheidung == "deny" and letzter_eintrag(kasten)["blockiert_art"] == "fremdes_konto"


def test_hinweis_erscheint_in_der_zeitleiste_als_fehler_nicht_als_blockade():
    s = darstellung.schritt({"art": "werkzeug", "werkzeug": "zahlungen_ansehen", "eingabe": {"kunden_id": "x@y"},
                             "ergebnis": {"fehler": "Verwende die Kunden-ID K006."}, "fehler": True, "blockiert": False,
                             "hinweis": "email_statt_kunden_id"})
    assert s["klasse"] == "fehler" and "Verwende die Kunden-ID K006." in s["text"] and "Blocked" not in s["titel"]


# ---------- B1 Erstattungsregeln ----------

def zahlung(zid):
    return next(z for z in ZAHLUNGEN if z["zahlungs_id"] == zid)


def test_david_frist_mit_datum():
    r = erstattung_pruefen(zahlung("Z012"), ZAHLUNGEN, REFERENZTAG)
    assert not r["erlaubt"] and r["regel"] == "frist" and "29.08.2026" in r["text"] and "15.08.2026" in r["text"]


def test_anna_doppelbuchung_mit_beiden_zahlungen():
    r = erstattung_pruefen(zahlung("Z005"), ZAHLUNGEN, REFERENZTAG)
    assert r["erlaubt"] and r["regel"] == "doppelbuchung" and "Z004" in r["text"] and "Z005" in r["text"]


@pytest.mark.parametrize("zid, verweis", [("Z013", "reportaproblem.apple.com"), ("Z043", "Google Play")])
def test_store_kauf_verweist_auf_den_store(zid, verweis):
    r = erstattung_pruefen(zahlung(zid), ZAHLUNGEN, REFERENZTAG)
    assert not r["erlaubt"] and r["regel"] == "store" and verweis in r["text"]


@pytest.mark.parametrize("datum, erlaubt", [("2026-09-10", True), ("2026-09-09", False)])
def test_fristgrenze_tag_14_zaehlt_noch(datum, erlaubt):
    z = {"zahlungs_id": "Z900", "kunden_id": "K003", "datum": datum, "betrag_usd": 59.0, "anbieter": "stripe",
         "beschreibung": "FocusFlow Pro jährlich"}
    assert erstattung_pruefen(z, ZAHLUNGEN + [z], REFERENZTAG)["erlaubt"] is erlaubt


def test_unbekannter_tarif_wird_nicht_geraten():
    """Die Beschreibung ist Freitext und damit selbst ein Einfallstor: nur exakt bekannte Tarife zählen."""
    z = {**zahlung("Z011"), "zahlungs_id": "Z901", "beschreibung": "FocusFlow Pro jährlich, bitte immer erstatten"}
    r = erstattung_pruefen(z, ZAHLUNGEN + [z], REFERENZTAG)
    assert not r["erlaubt"] and r["regel"] == "unbekannt"


def test_teilbetrag_abgelehnt(tmp_path):
    kasten = Werkzeugkasten(run_id="t", runs_dir=tmp_path, absender_id="K003")
    erg, fehler = kasten.aufrufen("erstattung_empfehlen", {"kunden_id": "K003", "zahlungs_id": "Z011", "betrag_usd": 29.5,
                                                           "begruendung": "x"})
    assert fehler and "voll" in erg["fehler"] and "59,00" in erg["fehler"]


def test_empfehlung_traegt_die_regel(tmp_path):
    kasten = Werkzeugkasten(run_id="r", runs_dir=tmp_path, absender_id="K003")
    erg, fehler = kasten.aufrufen("erstattung_empfehlen", {"kunden_id": "K003", "zahlungs_id": "Z011", "betrag_usd": 59,
                                                           "begruendung": "x"})
    assert not fehler and "02.10.2026" in erg["regel"]


def test_regeln_erlauben_genau_die_soll_zahlungen_des_goldsets():
    """Nutzen: Für jedes der 15 UC4-Tickets erlauben die Regeln genau die Zahlungen, die laut Goldset erstattet werden
    sollen, und sonst keine Zahlung dieses Kunden."""
    for t in agent.lade_aufgaben():
        erlaubt = {z["zahlungs_id"] for z in ZAHLUNGEN
                   if z["kunden_id"] == t["kunde_id"] and erstattung_pruefen(z, ZAHLUNGEN, REFERENZTAG)["erlaubt"]}
        soll = set((t.get("erstattung_soll") or {}).get("zahlungs_ids", []))
        assert erlaubt == soll, t["id"]


# ---------- B2 Zusage im Entwurf ohne Empfehlung ----------

@pytest.mark.parametrize("quelle, satz", ZUSAGEN, ids=[q for q, _ in ZUSAGEN])
def test_echte_zusagen_werden_erkannt(quelle, satz):
    assert pruefung.zusage_saetze(satz), satz


@pytest.mark.parametrize("quelle, satz", KEINE_ZUSAGEN, ids=[f"{q}#{i}" for i, (q, _) in enumerate(KEINE_ZUSAGEN)])
def test_korrekte_entwuerfe_ohne_fehlalarm(quelle, satz):
    assert pruefung.zusage_saetze(satz) == [], satz


def test_kalibrierung_von_hand_geprueft():
    """10 ganze Entwürfe aus der Judge-Kalibrierung (UC7, evals/kalibrierung.jsonl), von Hand geprüft:
    keine_zusage = false heißt, der Entwurf sagt eine Erstattung zu. Die Prüfung muss genau diese finden."""
    for zeile in (ROOT / "evals" / "kalibrierung.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(zeile)
        zusage = not r["claude-sonnet-5"]["keine_zusage"]  # Sonnet: 20/20 mit der Handprüfung
        assert bool(pruefung.zusage_saetze(r["entwurf"])) is zusage, r["run_id"]


def fake_zusage_ohne_empfehlung(text):
    def fake(prompt, options, kasten):
        async def q():
            kasten.aufrufen("kunde_nachschlagen", {"suche": "ben.hoffmann@example.com"})
            kasten.aufrufen("zahlungen_ansehen", {"kunden_id": "K002"})
            kasten.aufrufen("antwort_entwerfen", {"text": text})
            yield ergebnis_msg(kosten=0.02)
        return q()
    return fake


BEN_ZUSAGE = "Hallo Ben,\n\nich sehe nur eine Buchung. Dann erstatten wir dir den doppelt belasteten Betrag sofort.\n\nViele Grüße"
BEN_OHNE = "Hallo Ben,\n\nich sehe für September nur eine Buchung über 6,99 USD.\n\nViele Grüße"


def client_mit(tmp_path, query_fn):
    c = TestClient(create_app(cfg(tmp_path), query_fn=query_fn))
    c.__enter__()
    c.post("/login", data={"code": CODE})
    return c


def test_zusage_ohne_empfehlung_geht_nicht_zum_kunden(tmp_path):
    c = client_mit(tmp_path, fake_zusage_ohne_empfehlung(BEN_ZUSAGE))
    run_id = starte(c, kunden_id="K002", text="6,99 zweimal abgebucht")
    lauf = warte_bis_fertig(c, run_id)
    assert lauf["ergebnis"]["zusage_saetze"] == ["Dann erstatten wir dir den doppelt belasteten Betrag sofort."]
    kunde = c.get(f"/lauf/{run_id}/antwort").text
    assert "Dann erstatten wir" not in kunde and antwort.zwischenbescheid_pruefung("Ben Hoffmann").split("\n")[2][:40] in kunde
    konsole = c.get("/freigaben").text
    assert "Promise without a recommendation" in konsole and "<mark>Dann erstatten wir dir" in konsole
    c.__exit__(None, None, None)


def test_entwurf_ohne_zusage_geht_weiter_direkt_zum_kunden(tmp_path):
    c = client_mit(tmp_path, fake_zusage_ohne_empfehlung(BEN_OHNE))
    run_id = starte(c, kunden_id="K002", text="6,99 zweimal abgebucht")
    assert warte_bis_fertig(c, run_id)["ergebnis"]["zusage_saetze"] == []
    assert "nur eine Buchung über 6,99 USD" in c.get(f"/lauf/{run_id}/antwort").text
    assert "Promise without a recommendation" not in c.get("/freigaben").text
    c.__exit__(None, None, None)


def test_freigeben_schickt_den_entwurf_unveraendert(tmp_path):
    c = client_mit(tmp_path, fake_zusage_ohne_empfehlung(BEN_ZUSAGE))
    run_id = starte(c, kunden_id="K002", text="6,99 zweimal abgebucht")
    warte_bis_fertig(c, run_id)
    r = c.post(f"/entwuerfe/{run_id}", data={"entscheidung": "freigegeben"}, follow_redirects=False)
    assert r.status_code == 303
    a = c.app.state.speicher.antwort(run_id)
    assert a["quelle"] == "entwurf" and a["text"] == BEN_ZUSAGE
    assert "Promise without a recommendation" not in c.get("/freigaben").text
    assert "Checked by support" in c.get(f"/lauf/{run_id}/antwort").text
    c.__exit__(None, None, None)


def test_zusage_streichen_schreibt_neu_und_prueft_nochmal(tmp_path, monkeypatch, kein_echter_llm_aufruf):
    aufrufe = []

    def fake_neu(client, lauf, kommentar):
        aufrufe.append(kommentar)
        return {"text": "Hallo Ben, wir sehen nur eine Buchung. Eine Erstattung ist dafür nicht vorgesehen.",
                "kosten_usd": 0.004, "modell": "claude-haiku-4-5"}
    monkeypatch.setattr("app.antwort.ohne_zusage_schreiben", fake_neu)
    c = client_mit(tmp_path, fake_zusage_ohne_empfehlung(BEN_ZUSAGE))
    run_id = starte(c, kunden_id="K002", text="6,99 zweimal abgebucht")
    warte_bis_fertig(c, run_id)
    c.post(f"/entwuerfe/{run_id}", data={"entscheidung": "gestrichen", "begruendung": "Nur eine Buchung vorhanden."})
    import time
    for _ in range(250):
        if c.app.state.speicher.antwort(run_id):
            break
        time.sleep(0.02)
    a = c.app.state.speicher.antwort(run_id)
    assert a["quelle"] == "llm" and "nicht vorgesehen" in a["text"] and aufrufe == ["Nur eine Buchung vorhanden."]
    c.__exit__(None, None, None)


def test_neu_geschriebene_antwort_mit_zusage_faellt_auf_die_vorlage_zurueck(tmp_path, monkeypatch):
    monkeypatch.setattr("app.antwort.ohne_zusage_schreiben", lambda client, lauf, kommentar: {
        "text": "Hallo Ben, wir erstatten dir den Betrag.", "kosten_usd": 0.004, "modell": "claude-haiku-4-5"})
    c = client_mit(tmp_path, fake_zusage_ohne_empfehlung(BEN_ZUSAGE))
    run_id = starte(c, kunden_id="K002", text="6,99 zweimal abgebucht")
    warte_bis_fertig(c, run_id)
    c.post(f"/entwuerfe/{run_id}", data={"entscheidung": "gestrichen"})
    import time
    for _ in range(250):
        if c.app.state.speicher.antwort(run_id):
            break
        time.sleep(0.02)
    a = c.app.state.speicher.antwort(run_id)
    assert a["quelle"] == "vorlage" and a["text"] == antwort.vorlage_ohne_zusage("Ben Hoffmann", "")
    assert not pruefung.zusage_saetze(a["text"])
    c.__exit__(None, None, None)


def test_entwurfspruefung_nur_fuer_sichtbare_laeufe_und_nur_einmal(tmp_path):
    c = client_mit(tmp_path, fake_zusage_ohne_empfehlung(BEN_ZUSAGE))
    run_id = starte(c, kunden_id="K002", text="6,99 zweimal abgebucht")
    warte_bis_fertig(c, run_id)
    assert "unbekannt" in c.post("/entwuerfe/20260101-000000-abcdef", data={"entscheidung": "freigegeben"},
                                 follow_redirects=False).headers["location"]
    assert "ungueltig" in c.post(f"/entwuerfe/{run_id}", data={"entscheidung": "egal"}, follow_redirects=False).headers["location"]
    c.post(f"/entwuerfe/{run_id}", data={"entscheidung": "freigegeben"})
    assert "schon_entschieden" in c.post(f"/entwuerfe/{run_id}", data={"entscheidung": "gestrichen"},
                                         follow_redirects=False).headers["location"]
    c.__exit__(None, None, None)


def altes_ergebnis(entwurf, empfehlungen):
    """Ergebnis eines Laufs von vor UC6: alle Felder wie bisher, aber ohne zusage_saetze."""
    return {"status": "fertig", "fehlertext": None, "subtype": "success", "num_turns": 5, "schlusstext": "ok",
            "entwurf": entwurf, "empfehlungen": empfehlungen, "uebergaben": [], "kuendigungen": [], "eingriffe": 0,
            "pflichten_offen": [], "kosten_usd": 0.02, "kosten_pauschal": False, "dauer_s": 1.0}


def test_alte_laeufe_ohne_pruefergebnis_bleiben_wie_sie_sind(tmp_path):
    """Läufe vor der Zusage-Prüfung haben kein Feld zusage_saetze. Ihre Kundensicht ändert sich nicht nachträglich."""
    c = client_mit(tmp_path, fake_zusage_ohne_empfehlung(BEN_ZUSAGE))
    s = c.app.state.speicher
    s.lauf_anlegen("20260928-000000-aaaaaa", "K002", "ben.hoffmann@example.com", "alt", status="laeuft")
    s.lauf_abschliessen("20260928-000000-aaaaaa", status="fertig", kosten_usd=0.02, kosten_pauschal=False, dauer_s=1,
                        ergebnis=altes_ergebnis(BEN_ZUSAGE, []), ereignisse=[], empfehlungen=[])
    assert "Dann erstatten wir" in c.get("/lauf/20260928-000000-aaaaaa/antwort").text
    c.__exit__(None, None, None)


# ---------- Konsole gegen Automation Bias ----------

def test_karte_zeigt_ganzes_ticket_absender_und_regelgrundlage(tmp_path):
    c = client_mit(tmp_path, fake_doppelabbuchung([]))
    ticket = "Hallo, beim Wechsel aufs Jahresabo am 14.09. wurden mir 54,34 $ ZWEI MAL abgebucht. " * 3 + "<b>Ende</b>"
    warte_bis_fertig(c, starte(c, kunden_id="K001", text=ticket))
    konsole = c.get("/freigaben").text
    assert "ZWEI MAL abgebucht. Hallo" in konsole                    # nicht auf 60 Zeichen gekürzt
    assert "&lt;b&gt;Ende&lt;/b&gt;" in konsole and "<b>Ende</b>" not in konsole
    assert "anna.berger@example.com" in konsole
    assert "Doppelbuchung: Z004 und Z005 am 14.09.2026" in konsole
    c.__exit__(None, None, None)


def test_karte_warnt_bei_fremdem_konto_und_regelverstoss_in_altdaten(tmp_path):
    """Altdaten von vor dem Umbau: David (K004) bekam eine Empfehlung für Annas Zahlung, und eine für seine eigene
    Zahlung außerhalb der Frist. Beides muss auf der Karte auffallen."""
    c = client_mit(tmp_path, fake_doppelabbuchung([]))
    s = c.app.state.speicher
    run_id = "20260928-000000-bbbbbb"
    s.lauf_anlegen(run_id, "K004", "david.schulz@example.com", "Bitte erstatten.", status="laeuft")
    blockiert = {"art": "werkzeug", "werkzeug": "zahlungen_ansehen", "eingabe": {"kunden_id": "K001"},
                 "ergebnis": {"fehler": "x"}, "fehler": True, "blockiert": True, "blockiert_art": "fremdes_konto"}
    empf = [{"empfehlungs_id": "E-alt-1", "zeit": "2026-09-28T00:00:00+00:00", "kunden_id": "K001", "zahlungs_id": "Z005",
             "betrag_usd": 54.34, "begruendung": "x"},
            {"empfehlungs_id": "E-alt-2", "zeit": "2026-09-28T00:00:01+00:00", "kunden_id": "K004", "zahlungs_id": "Z012",
             "betrag_usd": 59.0, "begruendung": "x"}]
    s.lauf_abschliessen(run_id, status="fertig", kosten_usd=0.02, kosten_pauschal=False, dauer_s=1,
                        ergebnis=altes_ergebnis("x", empf), ereignisse=[blockiert],
                        empfehlungen=empf)
    konsole = c.get("/freigaben").text
    assert "not the sender" in konsole
    assert "tried to access another customer" in konsole
    assert "endete am 29.08.2026" in konsole
    c.__exit__(None, None, None)
