"""Jedes Werkzeug mit festen Eingaben – ohne LLM."""

import pytest

from conftest import lese_jsonl


# ---------- kunde_nachschlagen ----------

def test_kunde_per_id(kasten):
    erg, fehler = kasten.aufrufen("kunde_nachschlagen", {"suche": "k001"})
    assert not fehler
    assert [k["email"] for k in erg["treffer"]] == ["anna.berger@example.com"]


def test_kunde_per_email(kasten):
    erg, _ = kasten.aufrufen("kunde_nachschlagen", {"suche": "Emma.Wagner@example.com"})
    assert erg["treffer"][0]["kunden_id"] == "K005"


def test_kunde_per_name_findet_beide_konten(kasten):
    erg, _ = kasten.aufrufen("kunde_nachschlagen", {"suche": "Felix Braun"})
    assert sorted(k["kunden_id"] for k in erg["treffer"]) == ["K006", "K007"]


def test_kunde_unbekannt_ist_leer_nicht_fehler(kasten):
    erg, fehler = kasten.aufrufen("kunde_nachschlagen", {"suche": "niemand@example.com"})
    assert not fehler and erg == {"treffer": []}


def test_kunde_leere_suche_ist_fehler(kasten):
    _, fehler = kasten.aufrufen("kunde_nachschlagen", {"suche": "  "})
    assert fehler


# ---------- zahlungen_ansehen ----------

def test_zahlungen_doppelabbuchung_sichtbar(kasten):
    erg, _ = kasten.aufrufen("zahlungen_ansehen", {"kunden_id": "K001"})
    betraege = [z["betrag_usd"] for z in erg["zahlungen"]]
    assert betraege == [6.99, 6.99, 6.99, 54.34, 54.34]


def test_zahlungen_free_kunde_leer(kasten):
    erg, fehler = kasten.aufrufen("zahlungen_ansehen", {"kunden_id": "K008"})
    assert not fehler and erg["zahlungen"] == []


def test_zahlungen_unbekannter_kunde(kasten):
    erg, fehler = kasten.aufrufen("zahlungen_ansehen", {"kunden_id": "K999"})
    assert fehler and "nicht gefunden" in erg["fehler"]


# ---------- hilfe_durchsuchen ----------

@pytest.mark.parametrize("anfrage, erwartet", [
    ("doppelte Abbuchung", "doppelte-abbuchung.md"),
    ("Erstattung Jahresabo", "erstattungen.md"),
    ("Abo kündigen", "abo-kuendigen.md"),
    ("zwei Konten zusammenführen", "konten-zusammenfuehren.md"),
    ("Passwort zurücksetzen", "passwort-zuruecksetzen.md"),
])
def test_hilfe_findet_passenden_artikel(kasten, anfrage, erwartet):
    erg, fehler = kasten.aufrufen("hilfe_durchsuchen", {"anfrage": anfrage})
    assert not fehler
    assert erwartet in [t["datei"] for t in erg["treffer"]]


def test_hilfe_liefert_text_und_stand(kasten):
    erg, _ = kasten.aufrufen("hilfe_durchsuchen", {"anfrage": "Erstattung", "max_treffer": 1})
    (t,) = erg["treffer"]
    assert t["stand"] and "14 Tagen" in t["text"]


def test_hilfe_max_treffer_begrenzt(kasten):
    erg, _ = kasten.aufrufen("hilfe_durchsuchen", {"anfrage": "Pro Abo Konto", "max_treffer": 99})
    assert len(erg["treffer"]) == 5


def test_hilfe_ohne_treffer(kasten):
    erg, fehler = kasten.aufrufen("hilfe_durchsuchen", {"anfrage": "Quantencomputer"})
    assert not fehler and erg["treffer"] == []


def test_hilfe_nur_stoppwoerter_ist_fehler(kasten):
    _, fehler = kasten.aufrufen("hilfe_durchsuchen", {"anfrage": "und oder die"})
    assert fehler


# ---------- an_mensch_uebergeben ----------

def test_uebergabe_wird_gespeichert(kasten):
    erg, fehler = kasten.aufrufen("an_mensch_uebergeben",
                                  {"grund": "Konten zusammenführen", "kunden_id": "K006", "prioritaet": "hoch"})
    assert not fehler and erg["status"] == "offen"
    (gespeichert,) = lese_jsonl(kasten.run_dir / "uebergaben.jsonl")
    assert gespeichert["kunden_id"] == "K006" and gespeichert["prioritaet"] == "hoch"


def test_uebergabe_ohne_kunde_erlaubt(kasten):
    _, fehler = kasten.aufrufen("an_mensch_uebergeben", {"grund": "Kunde nicht identifizierbar"})
    assert not fehler


@pytest.mark.parametrize("eingabe", [
    {"grund": ""},
    {"grund": "x", "prioritaet": "dringend"},
    {"grund": "x", "kunden_id": "K999"},
])
def test_uebergabe_ungueltig(kasten, eingabe):
    _, fehler = kasten.aufrufen("an_mensch_uebergeben", eingabe)
    assert fehler
    assert not (kasten.run_dir / "uebergaben.jsonl").exists()


# ---------- abo_kuendigen ----------

def test_kuendigen_web_abo_zum_periodenende(kasten):
    erg, fehler = kasten.aufrufen("abo_kuendigen", {"kunden_id": "K012"})
    assert not fehler and erg["wirksam_zum"] == "2026-10-08"
    # Laufstand: Nachschlagen zeigt jetzt "gekuendigt", Grunddaten bleiben unverändert
    nach, _ = kasten.aufrufen("kunde_nachschlagen", {"suche": "K012"})
    assert nach["treffer"][0]["abo"]["status"] == "gekuendigt"
    assert kasten.kunden["K012"]["abo"]["status"] == "aktiv"


def test_kuendigen_zweimal_ist_fehler(kasten):
    kasten.aufrufen("abo_kuendigen", {"kunden_id": "K012"})
    _, fehler = kasten.aufrufen("abo_kuendigen", {"kunden_id": "K012"})
    assert fehler
    assert len(lese_jsonl(kasten.run_dir / "kuendigungen.jsonl")) == 1


@pytest.mark.parametrize("kid, stichwort", [
    ("K013", "bereits gekündigt"),
    ("K008", "kein Pro-Abo"),
    ("K005", "App Store"),
    ("K010", "Google Play"),
])
def test_kuendigen_nicht_moeglich(kasten, kid, stichwort):
    erg, fehler = kasten.aufrufen("abo_kuendigen", {"kunden_id": kid})
    assert fehler and stichwort in erg["fehler"]


# ---------- erstattung_empfehlen ----------

def test_empfehlung_doppelabbuchung(kasten):
    erg, fehler = kasten.aufrufen("erstattung_empfehlen", {
        "kunden_id": "K001", "zahlungs_id": "Z005", "betrag_usd": 54.34,
        "begruendung": "Doppelte Belastung beim Tarifwechsel, immer voll erstattbar."})
    assert not fehler and erg["status"] == "wartet_auf_freigabe"
    (gespeichert,) = lese_jsonl(kasten.run_dir / "erstattungsempfehlungen.jsonl")
    assert gespeichert["betrag_usd"] == 54.34 and gespeichert["zahlungs_id"] == "Z005"


def test_empfehlung_bucht_nichts(kasten):
    _, fehler = kasten.aufrufen("erstattung_empfehlen", {
        "kunden_id": "K003", "zahlungs_id": "Z011", "betrag_usd": 59, "begruendung": "Jahresabo innerhalb 14 Tagen."})
    assert not fehler
    assert len(kasten.zahlungen) == 43  # keine Gegenbuchung
    assert kasten.kunden["K003"]["abo"]["status"] == "aktiv"


@pytest.mark.parametrize("eingabe, stichwort", [
    ({"kunden_id": "K001", "zahlungs_id": "Z005", "betrag_usd": 60, "begruendung": "x"}, "höchstens"),
    ({"kunden_id": "K001", "zahlungs_id": "Z005", "betrag_usd": 0, "begruendung": "x"}, "größer 0"),
    ({"kunden_id": "K001", "zahlungs_id": "Z005", "betrag_usd": "viel", "begruendung": "x"}, "Zahl"),
    ({"kunden_id": "K001", "zahlungs_id": "Z007", "betrag_usd": 59, "begruendung": "x"}, "gehört nicht"),
    ({"kunden_id": "K005", "zahlungs_id": "Z013", "betrag_usd": 59, "begruendung": "x"}, "Store"),
    ({"kunden_id": "K001", "zahlungs_id": "Z005", "betrag_usd": 54.34, "begruendung": " "}, "leer"),
])
def test_empfehlung_ungueltig(kasten, eingabe, stichwort):
    erg, fehler = kasten.aufrufen("erstattung_empfehlen", eingabe)
    assert fehler and stichwort in erg["fehler"]
    assert not (kasten.run_dir / "erstattungsempfehlungen.jsonl").exists()


# ---------- antwort_entwerfen ----------

def test_entwurf_wird_gespeichert(kasten):
    erg, fehler = kasten.aufrufen("antwort_entwerfen", {"kunden_id": "K002", "text": "Hallo Ben, ..."})
    assert not fehler and erg["status"] == "entwurf"
    (gespeichert,) = lese_jsonl(kasten.run_dir / "antwortentwuerfe.jsonl")
    assert gespeichert["text"] == "Hallo Ben, ..."


def test_entwurf_leer_ist_fehler(kasten):
    _, fehler = kasten.aufrufen("antwort_entwerfen", {"text": ""})
    assert fehler


# ---------- Protokoll / Trajektorie ----------

def test_jeder_aufruf_wird_protokolliert(kasten):
    kasten.aufrufen("kunde_nachschlagen", {"suche": "K004"})
    kasten.aufrufen("zahlungen_ansehen", {"kunden_id": "K999"})       # fachlicher Fehler
    kasten.aufrufen("abo_kuendigen", {"falscher_parameter": "K004"})  # falsche Eingabe
    kasten.aufrufen("erstattung_ausloesen", {"kunden_id": "K004"})    # gibt es absichtlich nicht
    log = lese_jsonl(kasten.run_dir / "trajektorie.jsonl")
    assert [e["seq"] for e in log] == [1, 2, 3, 4]
    assert [e["fehler"] for e in log] == [False, True, True, True]
    assert log[3]["werkzeug"] == "erstattung_ausloesen" and "Unbekanntes Werkzeug" in log[3]["ergebnis"]["fehler"]
    for e in log:
        assert e["run_id"] == "test" and e["zeit_start"] <= e["zeit_ende"]
        assert e["zeit_start"].endswith("+00:00")


def test_grunddaten_bleiben_unveraendert(kasten):
    from uc4_agent.werkzeuge import DATA_DIR
    vorher = {p.name: p.read_bytes() for p in DATA_DIR.iterdir()}
    _, f1 = kasten.aufrufen("abo_kuendigen", {"kunden_id": "K012"})
    _, f2 = kasten.aufrufen("erstattung_empfehlen", {"kunden_id": "K003", "zahlungs_id": "Z011", "betrag_usd": 59, "begruendung": "x"})
    assert not f1 and not f2
    assert {p.name: p.read_bytes() for p in DATA_DIR.iterdir()} == vorher


def test_neuer_entwurf_ersetzt_alten_und_pflichten(kasten):
    assert kasten.fehlende_pflichten() == ["kunde_nachschlagen", "antwort_entwerfen"]
    kasten.aufrufen("kunde_nachschlagen", {"suche": ""})            # Fehler zählt nicht
    assert "kunde_nachschlagen" in kasten.fehlende_pflichten()
    kasten.aufrufen("kunde_nachschlagen", {"suche": "K001"})
    a, _ = kasten.aufrufen("antwort_entwerfen", {"text": "eins"})
    b, _ = kasten.aufrufen("antwort_entwerfen", {"text": "zwei"})
    assert a["ersetzt"] is None and b["ersetzt"] == a["entwurfs_id"]
    assert kasten.entwurf["text"] == "zwei" and kasten.fehlende_pflichten() == []
