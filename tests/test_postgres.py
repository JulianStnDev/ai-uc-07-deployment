"""Speicher gegen echtes Postgres (Neon). Läuft nur mit UC7_PG_TEST=1 und DATABASE_URL.

Jeder Testlauf arbeitet in einem eigenen, frisch angelegten Schema und löscht es danach.
Die Produktionstabellen im Schema public werden nicht angefasst. Kosten: keine (Neon Free).
"""

import os
import secrets
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("UC7_PG_TEST") != "1" or not os.environ.get("DATABASE_URL"),
                                reason="nur mit UC7_PG_TEST=1 und DATABASE_URL")


@pytest.fixture
def speicher():
    import psycopg
    from app.speicher import Speicher
    # Direkter Endpunkt statt Pooler: Der Neon-Pooler nimmt den Startparameter "options" (search_path) nicht an.
    url = os.environ["DATABASE_URL"].replace("-pooler.", ".")
    schema = f"uc7_test_{secrets.token_hex(4)}"
    trenner = "&" if "?" in url else "?"
    s = None
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(f'CREATE SCHEMA "{schema}"')
    try:
        s = Speicher(database_url=f"{url}{trenner}options=-csearch_path%3D{schema}")
        yield s
    finally:
        if s:
            s.schliessen()
        with psycopg.connect(url, autocommit=True) as c:
            c.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_ablauf_komplett_auf_postgres(speicher):
    assert speicher.art == "postgres"
    speicher.lauf_anlegen("r1", "K001", "anna.berger@example.com", "doppelt", "Titel")
    assert speicher.lauf_uebernehmen("r1") is True and speicher.lauf_uebernehmen("r1") is False
    assert speicher.laufende_anzahl() == 1
    monat = datetime.now(timezone.utc).strftime("%Y-%m")
    assert speicher.laufende_im_monat(monat) == 1 and speicher.kosten_im_monat(monat) == 0
    speicher.lauf_abschliessen("r1", status="fertig", kosten_usd=0.03128755, kosten_pauschal=False, dauer_s=33.9,
                               ergebnis={"uebergaben": [{"uebergabe_id": "U1", "grund": "unklar", "prioritaet": "hoch"}]},
                               ereignisse=[{"art": "text", "text": "ä ö ü „Zitat“"}],
                               empfehlungen=[{"empfehlungs_id": "E1", "zeit": "2026-09-28T12:00:00+00:00", "kunden_id": "K001",
                                              "zahlungs_id": "Z005", "betrag_usd": 54.34, "begruendung": "doppelt"}])
    lauf = speicher.lauf("r1")
    assert lauf["status"] == "fertig" and lauf["kosten_usd"] == 0.03128755  # DOUBLE PRECISION, nicht gerundet
    assert lauf["ereignisse"][0]["text"] == "ä ö ü „Zitat“"
    assert speicher.kosten_im_monat(monat) == pytest.approx(0.03128755)
    assert [e["zahlungs_id"] for e in speicher.offene_empfehlungen()] == ["Z005"]
    assert speicher.uebergaben()[0]["grund"] == "unklar"
    assert speicher.entscheiden("E1", "bestaetigt", " ok ") is True
    assert speicher.entscheiden("E1", "abgelehnt", "") is False          # nur einmal
    assert speicher.entscheiden("E-gibtsnicht", "abgelehnt", "") is False  # Fremdschlüssel
    assert speicher.freigabe_statistik() == {"gesamt": 1, "bestaetigt": 1}
    assert speicher.entschiedene_empfehlungen()[0]["kommentar"] == "ok" and speicher.offene_empfehlungen() == []


def test_verwaist_nur_alte_laeufe(speicher):
    alt = datetime.now(timezone.utc) - timedelta(minutes=20)
    speicher.lauf_anlegen("alt", "K001", "a@example.com", "x", jetzt=alt, status="laeuft")
    speicher.lauf_anlegen("neu", "K001", "a@example.com", "x", status="laeuft")
    assert speicher.verwaiste_laeufe_abbrechen(0.5) == 1
    assert speicher.lauf("alt")["status"] == "abgebrochen" and speicher.lauf("neu")["status"] == "laeuft"


def test_link_kontingent_atomar_bei_gleichzeitigen_anfragen(speicher):
    """20 gleichzeitige Buchungen über den Verbindungspool (wie zwei Instanzen mit je mehreren Anfragen): genau 5 greifen."""
    from concurrent.futures import ThreadPoolExecutor
    from app.speicher import LINK_LAEUFE
    speicher.link_anlegen("acme-aaaaaa")
    with ThreadPoolExecutor(max_workers=8) as pool:
        ergebnisse = list(pool.map(lambda _: speicher.link_lauf_buchen("acme-aaaaaa"), range(20)))
    assert sum(ergebnisse) == LINK_LAEUFE and speicher.link("acme-aaaaaa")["laeufe"] == LINK_LAEUFE
    assert speicher.link_sperren("acme-aaaaaa") and speicher.link("acme-aaaaaa")["gesperrt"] == 1


def test_filter_nach_code_auf_postgres(speicher):
    for run_id, code in (("r-admin", None), ("r-eins", "eins-aaaaaa"), ("r-zwei", "zwei-aaaaaa")):
        speicher.lauf_anlegen(run_id, "K001", "a@example.com", "x", zugang=code)
        speicher.lauf_abschliessen(run_id, status="fertig", kosten_usd=0.03, kosten_pauschal=False, dauer_s=30,
                                   ergebnis={"uebergaben": [{"uebergabe_id": "U", "grund": run_id, "prioritaet": "normal"}]},
                                   ereignisse=[], empfehlungen=[{"empfehlungs_id": f"E-{run_id}", "zeit": "2026-09-30T12:00:00+00:00",
                                                                 "kunden_id": "K001", "zahlungs_id": "Z005", "betrag_usd": 54.34,
                                                                 "begruendung": "doppelt"}])
    assert [e["run_id"] for e in speicher.offene_empfehlungen("eins-aaaaaa")] == ["r-eins"]
    assert len(speicher.offene_empfehlungen()) == 3
    assert speicher.entscheiden("E-r-zwei", "bestaetigt", "") is True
    assert speicher.entschiedene_empfehlungen(nur_zugang="eins-aaaaaa") == []
    assert speicher.freigabe_statistik("zwei-aaaaaa") == {"gesamt": 1, "bestaetigt": 1}
    assert speicher.freigabe_statistik("eins-aaaaaa") == {"gesamt": 0, "bestaetigt": 0}
    assert [u["grund"] for u in speicher.uebergaben(nur_zugang="zwei-aaaaaa")] == ["r-zwei"]
    assert [l["run_id"] for l in speicher.laeufe_zum_zugang("eins-aaaaaa")] == ["r-eins"]
