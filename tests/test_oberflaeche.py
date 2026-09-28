"""Oberfläche ohne LLM: Info-Hinweise (Klick/Tastatur/Screenreader), Titelregel, neutrale Zeitleiste,
Übergaben in der Konsole, Migration der Datenbank aus Branch (a)."""

import re
import sqlite3
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from app import darstellung, hinweise
from app.main import BEISPIEL_TEXTE, create_app
from app.speicher import Speicher
from test_app import CODE, cfg, ergebnis_msg, fake_doppelabbuchung, sse_ereignisse, starte, warte_bis_fertig
from uc4_agent.werkzeuge import Werkzeugkasten


class InfoParser(HTMLParser):
    """Sammelt Info-Knöpfe, Info-Texte und prüft, dass kein Knopf in einem <label> steckt."""

    def __init__(self):
        super().__init__()
        self.knoepfe, self.texte, self.ids, self.stapel = [], {}, [], []
        self._text_id = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.append(a["id"])
        klassen = a.get("class", "").split()
        if tag == "button" and "info-knopf" in klassen:
            self.knoepfe.append({**a, "in_label": "label" in self.stapel})
        if tag == "span" and "info-text" in klassen:
            self._text_id = a["id"]
            self.texte[a["id"]] = {**a, "inhalt": ""}
        self.stapel.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stapel:
            while self.stapel and self.stapel.pop() != tag:
                pass
        if tag == "span":
            self._text_id = None

    def handle_data(self, data):
        if self._text_id:
            self.texte[self._text_id]["inhalt"] += data


def pruefe_infos(html: str) -> set[str]:
    """Prüft alle Info-Hinweise einer Seite und gibt die Schlüssel zurück."""
    p = InfoParser()
    p.feed(html)
    assert len(p.ids) == len(set(p.ids)), "IDs auf der Seite sind nicht eindeutig"
    for k in p.knoepfe:
        assert k["type"] == "button"                                # per Tastatur erreichbar, sendet kein Formular
        assert k["aria-expanded"] == "false"                        # Zustand für Screenreader
        assert k["aria-label"].startswith("Erklärung: ")            # verständlicher Name statt nur "i"
        assert not k["in_label"], "Info-Knopf darf nicht im <label> stecken (sonst Teil des Feldnamens)"
        text = p.texte[k["aria-controls"]]                          # Knopf zeigt auf existierenden Text
        assert text["role"] == "note" and "hidden" in text
        assert text["inhalt"].strip() in hinweise.INFO.values()
    return set(re.findall(r'data-info="([a-z]+)"', html))


@pytest.fixture(autouse=True)
def umgebung(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-platzhalter")
    monkeypatch.setattr("app.main.load_dotenv", lambda *a, **k: None)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(cfg(tmp_path), query_fn=fake_doppelabbuchung([]))) as c:
        c.post("/login", data={"code": CODE})
        yield c


# ---------- Info-Hinweise ----------

@pytest.mark.parametrize("schluessel", sorted(hinweise.INFO))
def test_infotexte_kurz_und_einfach(schluessel):
    text = hinweise.INFO[schluessel]
    saetze = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    assert 1 <= len(saetze) <= 2, f"{schluessel}: {len(saetze)} Sätze"
    assert len(text) <= 200
    assert not re.search(r"\b(LLM|MCP|SDK|Token|Hook|API)\b", text), "Fachbegriff im Hinweis"


def test_info_markup_und_escaping():
    html = str(hinweise.info("budget", 'Budget "<b>"'))
    assert 'aria-label="Erklärung: Budget &#34;&lt;b&gt;&#34;"' in html
    assert pruefe_infos(html) == {"budget"}


def test_info_ids_sind_eindeutig():
    ids = {re.search(r'id="([^"]+)"', str(hinweise.info("budget", "B"))).group(1) for _ in range(50)}
    assert len(ids) == 50


def test_info_script_wird_ausgeliefert_und_behandelt_tastatur(client):
    js = client.get("/static/info.js").text
    assert "aria-expanded" in js and "Escape" in js and "focus()" in js
    assert '<script src="/static/info.js">' in client.get("/").text


@pytest.mark.parametrize("pfad, erwartet", [
    ("/", {"budget"}),
    ("/anliegen", {"budget", "absender", "werkzeug"}),
    ("/freigaben", {"budget", "freigabe", "schatten", "quote", "uebergabe"}),
])
def test_seiten_haben_gueltige_hinweise(client, pfad, erwartet):
    assert erwartet <= pruefe_infos(client.get(pfad).text)


def test_laufseite_und_live_fragmente_haben_gueltige_hinweise(client):
    run_id = starte(client, text="Mir wurde doppelt abgebucht.")
    warte_bis_fertig(client, run_id)
    keys = pruefe_infos(client.get(f"/lauf/{run_id}").text)
    assert {"absender", "werkzeug", "notiz", "empfehlung", "entwurf", "kosten", "schritte"} <= keys
    # Jedes Live-Fragment ist für sich gültig (Knopf und Text kommen im selben Fragment an)
    fragmente = sse_ereignisse(client, run_id)
    live_keys = set()
    for f in fragmente:
        live_keys |= pruefe_infos(f.get("data", ""))
    assert {"notiz", "empfehlung", "entwurf", "kosten", "schritte"} <= live_keys
    assert sum('data-info="notiz"' in f.get("data", "") for f in fragmente) == 1  # nur an der ersten Notiz


def test_konsole_zeigt_status_badges_und_quote(client):
    warte_bis_fertig(client, starte(client))
    html = client.get("/freigaben").text
    assert 'class="badge offen">offen<' in html and "0 von 0 bestätigt" in html


# ---------- Titel ----------

def test_titel_ist_chip_label_wenn_text_unveraendert():
    assert hinweise.titel_fuer(BEISPIEL_TEXTE["T01"], "T01", BEISPIEL_TEXTE) == "Doppelt abgebucht beim Jahresabo"


def test_titel_aus_ersten_worten_wenn_text_geaendert():
    t = hinweise.titel_fuer(BEISPIEL_TEXTE["T01"] + " Danke!", "T01", BEISPIEL_TEXTE)
    assert t.startswith("Hallo, beim Wechsel aufs Jahresabo") and t.endswith(" …") and len(t) <= 62


def test_titel_kurz_ohne_beispiel_gross_geschrieben():
    assert hinweise.titel_fuer("  was   kostet pro? ", "", BEISPIEL_TEXTE) == "Was kostet pro?"
    assert hinweise.titel_fuer("x" * 200, "T99", BEISPIEL_TEXTE) == "X" + "x" * 59 + " …"


def test_titel_wird_beim_lauf_gespeichert(client):
    run_id = starte(client, kunden_id="K001", text=BEISPIEL_TEXTE["T01"], beispiel="T01")
    warte_bis_fertig(client, run_id)
    assert client.app.state.speicher.lauf(run_id)["titel"] == "Doppelt abgebucht beim Jahresabo"
    assert "<h1>Doppelt abgebucht beim Jahresabo</h1>" in client.get(f"/lauf/{run_id}").text


def test_chips_fuer_alle_15_beispiele(client):
    html = client.get("/anliegen").text
    assert html.count('class="chip"') == 15 and 'data-beispiel="T12"' in html and "Was kostet Pro?" in html


# ---------- Zeitleiste: nur was der Agent gesehen hat ----------

def test_zahlungen_neutral_ohne_hervorhebung(tmp_path):
    k = Werkzeugkasten(run_id="z", runs_dir=tmp_path)
    erg, fehler = k.aufrufen("zahlungen_ansehen", {"kunden_id": "K001"})
    s = darstellung.schritt({"art": "werkzeug", "werkzeug": "zahlungen_ansehen", "eingabe": {"kunden_id": "K001"},
                             "ergebnis": erg, "fehler": fehler})
    assert s["titel"] == "Sieht die Zahlungen an" and s["text"] == "5 Zahlungen gefunden"
    assert len(s["liste"]) == 5 and s["liste"][3] == "14.09.2026 · 54,34 USD · Wechsel auf FocusFlow Pro jährlich (59,00 USD abzgl. 4,66 USD Guthaben) (Z004)"
    alles = " ".join([s["text"], *s["liste"]]).lower()
    assert not any(w in alles for w in ("doppel", "zweimal", "darunter", "verdächtig"))


def test_hilfe_und_kunde_in_alltagssprache(tmp_path):
    k = Werkzeugkasten(run_id="h", runs_dir=tmp_path)
    erg, _ = k.aufrufen("kunde_nachschlagen", {"suche": "Felix"})
    s = darstellung.schritt({"art": "werkzeug", "werkzeug": "kunde_nachschlagen", "eingabe": {"suche": "Felix"}, "ergebnis": erg, "fehler": False})
    assert s["text"] == "2 Konten gefunden" and len(s["liste"]) == 2
    erg, _ = k.aufrufen("kunde_nachschlagen", {"suche": "K008"})
    s = darstellung.schritt({"art": "werkzeug", "werkzeug": "kunde_nachschlagen", "eingabe": {}, "ergebnis": erg, "fehler": False})
    assert s["text"] == "Konto gefunden: Greta Koch · Free"


def test_blockierter_aufruf_und_fehler_verstaendlich():
    s = darstellung.schritt({"art": "werkzeug", "werkzeug": "Bash", "eingabe": {}, "ergebnis": {"fehler": "x"}, "fehler": True, "blockiert": True})
    assert s["klasse"] == "fehler" and s["info"] == "blockiert" and "nicht freigegeben" in s["text"]
    s = darstellung.schritt({"art": "werkzeug", "werkzeug": "zahlungen_ansehen", "eingabe": {}, "ergebnis": {"fehler": "Kunde 'K999' nicht gefunden."}, "fehler": True})
    assert s["text"] == "Hat nicht geklappt: Kunde 'K999' nicht gefunden."


def test_notiz_ohne_markdown():
    s = darstellung.schritt({"art": "text", "text": "✅ **Fertig.** Alles erledigt."}, erste_notiz=True)
    assert s["text"] == "✅ Fertig. Alles erledigt." and s["info"] == "notiz"
    assert darstellung.schritt({"art": "text", "text": "x"})["info"] is None


# ---------- Übergaben in der Konsole ----------

def fake_uebergabe(prompt, options, kasten):
    async def q():
        kasten.aufrufen("kunde_nachschlagen", {"suche": "ben.hoffmann@example.com"})
        kasten.aufrufen("an_mensch_uebergeben", {"grund": "Im Konto nur eine Zahlung sichtbar.", "kunden_id": "K002"})
        kasten.aufrufen("antwort_entwerfen", {"text": "Hallo Ben, wir prüfen das."})
        yield ergebnis_msg(kosten=0.02)
    return q()


def test_uebergabe_erscheint_in_konsole_und_lauf(tmp_path):
    with TestClient(create_app(cfg(tmp_path), query_fn=fake_uebergabe)) as c:
        c.post("/login", data={"code": CODE})
        run_id = starte(c, kunden_id="K002", text="6,99 zweimal abgebucht")
        warte_bis_fertig(c, run_id)
        konsole = c.get("/freigaben").text
        assert "Im Konto nur eine Zahlung sichtbar." in konsole and f'/lauf/{run_id}' in konsole
        assert "Keine offenen Empfehlungen" in konsole
        lauf = c.get(f"/lauf/{run_id}").text
        assert "Übergibt an einen Menschen" in lauf and "An einen Menschen übergeben" in lauf


# ---------- Migration ----------

def test_datenbank_aus_branch_a_bekommt_titel_spalte(tmp_path):
    pfad = tmp_path / "alt.sqlite"
    db = sqlite3.connect(pfad)
    db.execute("CREATE TABLE laeufe (run_id TEXT PRIMARY KEY, erstellt TEXT NOT NULL, kunden_id TEXT NOT NULL, absender TEXT NOT NULL, "
               "text TEXT NOT NULL, status TEXT NOT NULL, kosten_usd REAL, kosten_pauschal INTEGER NOT NULL DEFAULT 0, "
               "dauer_s REAL, ergebnis TEXT, ereignisse TEXT)")
    db.execute("INSERT INTO laeufe (run_id, erstellt, kunden_id, absender, text, status) VALUES ('a', '2026-09-28', 'K001', 'x', 'alt', 'fertig')")
    db.commit(); db.close()
    s = Speicher(pfad)
    assert s.lauf("a")["titel"] is None
    s.lauf_anlegen("b", "K001", "x", "neu", "Titel")
    assert s.lauf("b")["titel"] == "Titel"
