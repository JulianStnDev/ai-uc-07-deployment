"""Konsistenz der erfundenen Daten mit den FocusFlow-Fakten und den eingebauten Fällen."""

import json
from collections import Counter
from datetime import date

from uc4_agent.werkzeuge import CORPUS_DIR, DATA_DIR

KUNDEN = json.loads((DATA_DIR / "kunden.json").read_text(encoding="utf-8"))
ZAHLUNGEN = json.loads((DATA_DIR / "zahlungen.json").read_text(encoding="utf-8"))["zahlungen"]
HEUTE = date.fromisoformat(KUNDEN["referenztag"])
K = {k["kunden_id"]: k for k in KUNDEN["kunden"]}


def zahlungen(kid):
    return [z for z in ZAHLUNGEN if z["kunden_id"] == kid]


def test_umfang_und_eindeutigkeit():
    assert len(K) == 15
    assert len({k["email"] for k in K.values()}) == 15
    assert len({z["zahlungs_id"] for z in ZAHLUNGEN}) == len(ZAHLUNGEN)
    assert all(z["kunden_id"] in K for z in ZAHLUNGEN)


def test_wertebereiche():
    for k in K.values():
        assert k["abo"]["stufe"] in {"free", "pro_monatlich", "pro_jaehrlich"}
        assert k["plattform"] in {"web", "ios", "android"}
        assert k["login"] in {"email_passwort", "google"}  # FocusFlow kennt nur diese beiden
        if k["abo"]["stufe"] == "free":
            assert k["abo"]["anbieter"] is None
        else:
            anbieter = {"web": "stripe", "ios": "apple", "android": "google"}[k["plattform"]]
            assert k["abo"]["anbieter"] == anbieter


def test_preise_passen_zu_focusflow():
    erlaubt = {6.99, 59.00, 54.34}  # 54,34 = Jahrespreis abzgl. Guthaben (tarif-wechseln.md)
    assert {z["betrag_usd"] for z in ZAHLUNGEN} <= erlaubt
    assert round(59.00 - round(20 / 30 * 6.99, 2), 2) == 54.34


def test_keine_zahlung_in_der_zukunft():
    assert all(date.fromisoformat(z["datum"]) <= HEUTE for z in ZAHLUNGEN)


def test_fall_echte_doppelabbuchung_web():
    z = [z for z in zahlungen("K001") if z["betrag_usd"] == 54.34]
    assert len(z) == 2 and z[0]["datum"] == z[1]["datum"] and z[0]["anbieter"] == "stripe"
    assert (HEUTE - date.fromisoformat(z[0]["datum"])).days > 7  # klar nach der 3–5-Werktage-Vormerkung


def test_fall_behauptete_doppelabbuchung_nur_eine_buchung():
    monate = Counter(z["datum"][:7] for z in zahlungen("K002"))
    assert monate["2026-09"] == 1 and max(monate.values()) == 1


def test_fall_jahresabo_innerhalb_und_ausserhalb_14_tage():
    for kid, innerhalb in [("K003", True), ("K011", True), ("K004", False)]:
        letzte = max(date.fromisoformat(z["datum"]) for z in zahlungen(kid))
        assert K[kid]["abo"]["stufe"] == "pro_jaehrlich"
        assert ((HEUTE - letzte).days <= 14) == innerhalb


def test_fall_ios_kundin_will_erstattung():
    assert K["K005"]["plattform"] == "ios"
    assert [z["anbieter"] for z in zahlungen("K005")] == ["apple"]


def test_fall_kunde_mit_zwei_konten():
    a, b = K["K006"], K["K007"]
    assert a["name"] == b["name"] and a["email"] != b["email"]
    assert {a["login"], b["login"]} == {"email_passwort", "google"}
    assert a["abo"]["stufe"] != "free" and b["abo"]["stufe"] != "free"


def test_korpus_ist_vollstaendige_kopie_aus_uc3():
    assert len(list(CORPUS_DIR.glob("*.md"))) == 20
