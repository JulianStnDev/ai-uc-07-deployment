"""Werkzeug-Logik des FocusFlow-Support-Agents – ohne LLM, ohne MCP.

Die Grunddaten in data/ und corpus/ werden nur gelesen. Alles, was ein Werkzeug
"tut" (Kündigung, Empfehlung, Entwurf, Übergabe), landet im Ordner des Laufs
(runs/<run_id>/). Jeder Aufruf – auch ein fehlgeschlagener – wird mit Zeitstempel
in runs/<run_id>/trajektorie.jsonl protokolliert. Das ist später die Trajektorie
fürs Eval.

Autonomie-Matrix (siehe CLAUDE.md): Es gibt bewusst kein Werkzeug, das eine
Erstattung auslöst oder eine Antwort versendet.

UC6 (Schutz im Code): Ein Lauf kennt seinen Absender (absender_id, aus der Sitzung, nie aus dem
Ticket-Text). `konto_pruefen` sagt, ob ein Aufruf ein fremdes Konto berührt; der PreToolUse-Hook
in agent.py blockiert ihn dann. `erstattung_pruefen` setzt die Regeln aus corpus/erstattungen.md
deterministisch um; `erstattung_empfehlen` lehnt jede regelwidrige Empfehlung ab.
"""

import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
CORPUS_DIR = ROOT / "corpus"
RUNS_DIR = ROOT / "runs"

STOPPWOERTER = {
    "und", "oder", "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen",
    "ich", "mein", "meine", "mir", "mich", "ist", "sind", "wie", "was", "wird",
    "wurde", "kann", "habe", "hat", "mit", "von", "für", "auf", "bei", "nicht",
    "zu", "im", "in", "es", "an", "ab", "wo", "wann", "warum",
}


# Tarif je Zahlungsbeschreibung, exakt verglichen. Die Beschreibung ist Freitext (also selbst ein Einfallstor),
# deshalb kein Teilstring-Vergleich: Eine unbekannte Beschreibung führt zu keiner Empfehlung.
TARIFE = {
    "FocusFlow Pro monatlich": "monatlich",
    "FocusFlow Pro jährlich": "jaehrlich",
    "FocusFlow Pro jährlich (Verlängerung)": "jaehrlich",
    "FocusFlow Pro jährlich (App Store)": "jaehrlich",
    "FocusFlow Pro jährlich (Google Play)": "jaehrlich",
    "Wechsel auf FocusFlow Pro jährlich (59,00 USD abzgl. 4,66 USD Guthaben)": "jaehrlich",
}
ERSTATTUNGSFRIST_TAGE = 14  # corpus/erstattungen.md; der 14. Tag zählt noch
STORE_HINWEIS = {
    "apple": "Store-Kauf über den App Store: FocusFlow kann ihn technisch nicht erstatten. Der Kunde beantragt die "
             "Erstattung direkt bei Apple (reportaproblem.apple.com), dort gelten Apples Richtlinien.",
    "google": "Store-Kauf über Google Play: FocusFlow kann ihn technisch nicht erstatten. Der Kunde beantragt die "
              "Erstattung direkt bei Google Play, dort gelten die Richtlinien von Google.",
}


def _datum_de(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d.%m.%Y")


def _usd_de(betrag: float) -> str:
    return f"{betrag:.2f}".replace(".", ",") + " USD"


def erstattung_pruefen(zahlung: dict, zahlungen: list[dict], heute: str) -> dict:
    """Regeln aus corpus/erstattungen.md, ohne Modell. Rückgabe {erlaubt, regel, text}; regel ist
    store | doppelbuchung | frist | monatsabo | unbekannt. Gilt für den vollen Betrag der Zahlung."""
    if zahlung["anbieter"] != "stripe":
        return {"erlaubt": False, "regel": "store", "text": STORE_HINWEIS.get(zahlung["anbieter"], STORE_HINWEIS["apple"])}
    gleich = [z for z in zahlungen if z["zahlungs_id"] != zahlung["zahlungs_id"] and z["kunden_id"] == zahlung["kunden_id"]
              and z["datum"] == zahlung["datum"] and z["betrag_usd"] == zahlung["betrag_usd"]
              and z["beschreibung"] == zahlung["beschreibung"]]
    if gleich:
        ids = " und ".join(sorted([zahlung["zahlungs_id"]] + [z["zahlungs_id"] for z in gleich]))
        return {"erlaubt": True, "regel": "doppelbuchung",
                "text": f"Doppelbuchung: {ids} am {_datum_de(zahlung['datum'])}, je {_usd_de(zahlung['betrag_usd'])}. "
                        "Doppelte Belastungen werden immer vollständig erstattet."}
    tarif = TARIFE.get(zahlung["beschreibung"])
    if tarif is None:
        return {"erlaubt": False, "regel": "unbekannt",
                "text": f"Der Tarif der Zahlung ist nicht eindeutig („{zahlung['beschreibung']}“). Keine Empfehlung, "
                        "übergib an einen Menschen."}
    if tarif == "monatlich":
        return {"erlaubt": False, "regel": "monatsabo",
                "text": "Monatsabo: Bereits begonnene Monate werden nicht erstattet, auch nicht anteilig."}
    bezahlt = date.fromisoformat(zahlung["datum"])
    ende = bezahlt + timedelta(days=ERSTATTUNGSFRIST_TAGE)
    if date.fromisoformat(heute) <= ende:
        return {"erlaubt": True, "regel": "frist",
                "text": f"Jahresabo, Zahlung am {bezahlt:%d.%m.%Y}: Die Erstattungsfrist ({ERSTATTUNGSFRIST_TAGE} Tage) "
                        f"läuft bis {ende:%d.%m.%Y}."}
    return {"erlaubt": False, "regel": "frist",
            "text": f"Jahresabo, Zahlung am {bezahlt:%d.%m.%Y}: Die Erstattungsfrist ({ERSTATTUNGSFRIST_TAGE} Tage) "
                    f"endete am {ende:%d.%m.%Y}. Danach gibt es keine vollständige oder anteilige Erstattung."}


class WerkzeugFehler(Exception):
    """Fachlicher Fehler, den das Modell als Werkzeug-Ergebnis zurückbekommt."""


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _artikel_laden(corpus_dir: Path) -> list[dict]:
    artikel = []
    for pfad in sorted(corpus_dir.glob("*.md")):
        text = pfad.read_text(encoding="utf-8")
        titel = text.splitlines()[0].lstrip("# ").strip()
        stand = re.search(r"Zuletzt aktualisiert: (\S+)", text)
        artikel.append({
            "datei": pfad.name,
            "titel": titel,
            "stand": stand.group(1) if stand else None,
            "text": text,
        })
    return artikel


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"\w+", text.lower()) if len(t) >= 3 and t not in STOPPWOERTER]


class Werkzeugkasten:
    """Die 7 Werkzeuge für genau einen Lauf (run_id)."""

    NAMEN = (
        "kunde_nachschlagen", "zahlungen_ansehen", "hilfe_durchsuchen",
        "an_mensch_uebergeben", "abo_kuendigen", "erstattung_empfehlen",
        "antwort_entwerfen",
    )

    def __init__(self, run_id: str | None = None, runs_dir: Path = RUNS_DIR,
                 data_dir: Path = DATA_DIR, corpus_dir: Path = CORPUS_DIR, absender_id: str | None = None):
        self.run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_dir = Path(runs_dir) / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        kunden = json.loads((data_dir / "kunden.json").read_text(encoding="utf-8"))
        self.referenztag = kunden["referenztag"]
        self.kunden = {k["kunden_id"]: k for k in kunden["kunden"]}
        self.zahlungen = json.loads((data_dir / "zahlungen.json").read_text(encoding="utf-8"))["zahlungen"]
        self.artikel = _artikel_laden(corpus_dir)
        # Konto des Absenders aus der Sitzung (UC7: lauf.kunden_id, UC4-Kommandozeile: kunde_id des Tickets)
        self.absender_id = str(absender_id).strip().upper() if absender_id else None
        if self.absender_id and self.absender_id not in self.kunden:
            raise ValueError(f"Absender {absender_id!r} ist kein bekannter Kunde.")
        self.kuendigungen: dict[str, dict] = {}  # kunden_id -> Kündigung in diesem Lauf
        self.entwurf: dict | None = None          # gültiger Entwurf (ein neuer ersetzt den alten)
        self.erfolgreich: Counter = Counter()     # Werkzeug -> Anzahl erfolgreicher Aufrufe
        self._seq = 0

    # ---------- Einstieg: Aufruf mit Protokoll ----------

    def aufrufen(self, werkzeug: str, eingabe: dict) -> tuple[dict, bool]:
        """Führt ein Werkzeug aus und protokolliert den Aufruf. Gibt (ergebnis, ist_fehler) zurück."""
        start = _jetzt()
        try:
            if werkzeug not in self.NAMEN:
                raise WerkzeugFehler(f"Unbekanntes Werkzeug: {werkzeug}")
            ergebnis, fehler = getattr(self, werkzeug)(**eingabe), False
        except WerkzeugFehler as e:
            ergebnis, fehler = {"fehler": str(e)}, True
        except TypeError as e:  # falsche/fehlende Parameter
            ergebnis, fehler = {"fehler": f"Ungültige Eingabe: {e}"}, True
        if not fehler:
            self.erfolgreich[werkzeug] += 1
        self._seq += 1
        self._anhaengen("trajektorie.jsonl", {
            "run_id": self.run_id, "seq": self._seq, "zeit_start": start, "zeit_ende": _jetzt(),
            "werkzeug": werkzeug, "eingabe": eingabe, "ergebnis": ergebnis, "fehler": fehler,
        })
        return ergebnis, fehler

    def fehlende_pflichten(self) -> list[str]:
        """Pflichten, die in jedem Lauf gelten und im Code erzwungen werden (Stop-Hook im Agent)."""
        fehlt = []
        if not self.erfolgreich["kunde_nachschlagen"]:
            fehlt.append("kunde_nachschlagen")
        if self.entwurf is None:
            fehlt.append("antwort_entwerfen")
        return fehlt

    def blockiert_protokollieren(self, werkzeug: str, eingabe: dict, grund: str, art: str = "werkzeug") -> None:
        """Für Aufrufe, die schon vor dem Server abgelehnt wurden (PreToolUse-Hook im Agent).
        art: werkzeug (nicht freigegebenes Werkzeug) | fremdes_konto (Konto-Bindung)."""
        self._seq += 1
        jetzt = _jetzt()
        self._anhaengen("trajektorie.jsonl", {
            "run_id": self.run_id, "seq": self._seq, "zeit_start": jetzt, "zeit_ende": jetzt,
            "werkzeug": werkzeug, "eingabe": eingabe, "ergebnis": {"fehler": grund},
            "fehler": True, "blockiert": True, "blockiert_art": art,
        })

    def hinweis_protokollieren(self, werkzeug: str, eingabe: dict, text: str, art: str) -> None:
        """Für Aufrufe, die der PreToolUse-Hook mit einem Hinweis anhält: ein Werkzeugfehler, keine Blockade."""
        self._seq += 1
        jetzt = _jetzt()
        self._anhaengen("trajektorie.jsonl", {
            "run_id": self.run_id, "seq": self._seq, "zeit_start": jetzt, "zeit_ende": jetzt,
            "werkzeug": werkzeug, "eingabe": eingabe, "ergebnis": {"fehler": text},
            "fehler": True, "blockiert": False, "hinweis": art,
        })

    def konto_hinweis(self, werkzeug: str, eingabe: dict) -> str | None:
        """Steht in kunden_id keine Kunden-ID, sondern genau die E-Mail des Absenders (ohne Rücksicht auf Groß-/
        Kleinschreibung und Leerzeichen am Rand), bekommt der Agent einen Hinweis statt einer Blockade. Im Goldset
        kam das in v3 viermal und nach dem Umbau einmal vor (UC6). Alles andere ohne Kunden-ID blockiert konto_pruefen."""
        wert = str(eingabe.get("kunden_id") or "").strip()
        if wert and wert.upper() not in self.kunden and wert.lower() == self.kunden[self.absender_id]["email"].lower():
            return f"Verwende die Kunden-ID {self.absender_id}."
        return None

    def konto_pruefen(self, werkzeug: str, eingabe: dict) -> str | None:
        """Konto-Bindung: None, wenn der Aufruf nur das Konto des Absenders berührt, sonst die Meldung an den Agent.
        Prüft jede kunden_id und Zahlungs-ID, auch beim Lesen. Fehlende oder leere IDs lässt sie durch, die
        meldet das Werkzeug selbst als Fehler."""
        a = self.absender_id
        weg = (f"Du darfst nur für das Konto des Absenders ({a}) lesen und handeln. Betrifft die Anfrage ein anderes "
               "Konto, übergib an einen Menschen (an_mensch_uebergeben ohne fremde kunden_id) und nenne das andere "
               "Konto im Grund.")
        if werkzeug == "kunde_nachschlagen":
            fremd = [k for k in self._treffer(str(eingabe.get("suche") or "")) if k["kunden_id"] != a]
            if fremd:
                konten = ", ".join(k["kunden_id"] for k in fremd)
                return (f"Zugriff verweigert: Die Suche trifft auch fremde Konten ({konten}). Schlage den Absender über "
                        f"seine E-Mail-Adresse ({self.kunden[a]['email']}) oder seine Kunden-ID nach. " + weg)
            return None
        kid = str(eingabe.get("kunden_id") or "").strip().upper()
        if kid and kid != a:
            return f"Zugriff verweigert: {kid} gehört nicht zum Absender dieses Tickets ({a}). " + weg
        zid = str(eingabe.get("zahlungs_id") or "").strip().upper()
        zahlung = next((z for z in self.zahlungen if z["zahlungs_id"] == zid), None)
        if zahlung and zahlung["kunden_id"] != a:
            return f"Zugriff verweigert: Zahlung {zid} gehört nicht zum Absender dieses Tickets ({a}). " + weg
        return None

    def _anhaengen(self, datei: str, eintrag: dict) -> None:
        with open(self.run_dir / datei, "a", encoding="utf-8") as f:
            f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")

    def _kunde(self, kunden_id: str) -> dict:
        kunde = self.kunden.get(str(kunden_id or "").strip().upper())
        if kunde is None:
            raise WerkzeugFehler(f"Kunde {kunden_id!r} nicht gefunden.")
        return kunde

    def _mit_laufstand(self, kunde: dict) -> dict:
        """Kundendaten inkl. Änderungen aus diesem Lauf (z. B. Kündigung)."""
        k = json.loads(json.dumps(kunde))
        if k["kunden_id"] in self.kuendigungen:
            k["abo"]["status"] = "gekuendigt"
        return k

    @staticmethod
    def _pflichttext(wert: str, feld: str) -> str:
        if not isinstance(wert, str) or not wert.strip():
            raise WerkzeugFehler(f"'{feld}' darf nicht leer sein.")
        return wert.strip()

    # ---------- Lesen ----------

    def _treffer(self, suche: str) -> list[dict]:
        s = suche.strip().lower()
        if not s:
            return []
        return [k for k in self.kunden.values()
                if s == k["kunden_id"].lower() or s == k["email"].lower() or s in k["name"].lower()]

    def kunde_nachschlagen(self, suche: str) -> dict:
        """Sucht per Kunden-ID, E-Mail (exakt) oder Namensteil. Kann mehrere Treffer liefern."""
        return {"treffer": [self._mit_laufstand(k) for k in self._treffer(self._pflichttext(suche, "suche"))]}

    def zahlungen_ansehen(self, kunden_id: str) -> dict:
        kunde = self._kunde(kunden_id)
        eigene = [z for z in self.zahlungen if z["kunden_id"] == kunde["kunden_id"]]
        return {"kunden_id": kunde["kunden_id"], "zahlungen": sorted(eigene, key=lambda z: (z["datum"], z["zahlungs_id"]))}

    def hilfe_durchsuchen(self, anfrage: str, max_treffer: int = 3) -> dict:
        """Einfache Volltextsuche: Teilstring-Treffer je Suchwort, Titeltreffer zählen dreifach."""
        woerter = _tokens(self._pflichttext(anfrage, "anfrage"))
        if not woerter:
            raise WerkzeugFehler("Anfrage enthält keine verwertbaren Suchwörter.")
        try:
            max_treffer = max(1, min(int(max_treffer), 5))
        except (TypeError, ValueError):
            raise WerkzeugFehler("'max_treffer' muss eine Zahl sein.")
        bewertet = []
        for a in self.artikel:
            titel, text = a["titel"].lower(), a["text"].lower()
            punkte = sum(text.count(w) + 3 * titel.count(w) for w in woerter)
            if punkte:
                bewertet.append((punkte, a))
        bewertet.sort(key=lambda x: (-x[0], x[1]["datei"]))
        return {"treffer": [
            {"datei": a["datei"], "titel": a["titel"], "stand": a["stand"], "punkte": p, "text": a["text"]}
            for p, a in bewertet[:max_treffer]
        ]}

    # ---------- Handeln ----------

    def an_mensch_uebergeben(self, grund: str, kunden_id: str | None = None, prioritaet: str = "normal") -> dict:
        grund = self._pflichttext(grund, "grund")
        if prioritaet not in ("normal", "hoch"):
            raise WerkzeugFehler("'prioritaet' muss 'normal' oder 'hoch' sein.")
        if kunden_id:
            kunden_id = self._kunde(kunden_id)["kunden_id"]
        eintrag = {"uebergabe_id": f"U-{self.run_id}-{self._seq + 1}", "zeit": _jetzt(),
                   "kunden_id": kunden_id, "grund": grund, "prioritaet": prioritaet, "status": "offen"}
        self._anhaengen("uebergaben.jsonl", eintrag)
        return eintrag

    def abo_kuendigen(self, kunden_id: str) -> dict:
        """Darf der Agent allein: Kündigung zum Periodenende, nur für Web-Abos (Stripe)."""
        kunde = self._mit_laufstand(self._kunde(kunden_id))
        abo = kunde["abo"]
        if abo["stufe"] == "free":
            raise WerkzeugFehler("Kunde hat kein Pro-Abo, es gibt nichts zu kündigen.")
        if abo["status"] == "gekuendigt":
            raise WerkzeugFehler(f"Abo ist bereits gekündigt und läuft am {abo['periode_ende']} aus.")
        if abo["anbieter"] != "stripe":
            store = {"apple": "im App Store (iPhone-Einstellungen > Abonnements)",
                     "google": "in Google Play (Zahlungen und Abos > Abos)"}[abo["anbieter"]]
            raise WerkzeugFehler(f"Store-Abo: FocusFlow kann es nicht kündigen. Der Kunde muss selbst {store} kündigen.")
        eintrag = {"kuendigungs_id": f"C-{self.run_id}-{self._seq + 1}", "zeit": _jetzt(),
                   "kunden_id": kunde["kunden_id"], "wirksam_zum": abo["periode_ende"],
                   "hinweis": "Pro läuft bis zum Periodenende weiter, keine weitere Abbuchung, keine Erstattung."}
        self.kuendigungen[kunde["kunden_id"]] = eintrag
        self._anhaengen("kuendigungen.jsonl", eintrag)
        return eintrag

    def erstattung_empfehlen(self, kunden_id: str, zahlungs_id: str, betrag_usd: float, begruendung: str) -> dict:
        """Legt NUR eine Empfehlung ab (Schattenmodus). Gebucht wird erst nach menschlicher Freigabe."""
        kunde = self._kunde(kunden_id)
        begruendung = self._pflichttext(begruendung, "begruendung")
        zahlung = next((z for z in self.zahlungen if z["zahlungs_id"] == str(zahlungs_id or "").strip().upper()), None)
        if zahlung is None or zahlung["kunden_id"] != kunde["kunden_id"]:
            raise WerkzeugFehler(f"Zahlung {zahlungs_id!r} gehört nicht zu Kunde {kunde['kunden_id']}.")
        try:
            betrag = round(float(betrag_usd), 2)
        except (TypeError, ValueError):
            raise WerkzeugFehler("'betrag_usd' muss eine Zahl sein.")
        if not 0 < betrag <= zahlung["betrag_usd"]:
            raise WerkzeugFehler(f"Betrag muss größer 0 und höchstens {zahlung['betrag_usd']:.2f} USD sein.")
        regel = erstattung_pruefen(zahlung, self.zahlungen, self.referenztag)
        if not regel["erlaubt"]:
            raise WerkzeugFehler(f"Keine Empfehlung möglich. {regel['text']}")
        if betrag != zahlung["betrag_usd"]:
            raise WerkzeugFehler(f"Empfohlen werden kann nur der volle Betrag der Zahlung ({_usd_de(zahlung['betrag_usd'])}), "
                                 "keine Teilbeträge.")
        eintrag = {"empfehlungs_id": f"E-{self.run_id}-{self._seq + 1}", "zeit": _jetzt(),
                   "kunden_id": kunde["kunden_id"], "zahlungs_id": zahlung["zahlungs_id"],
                   "betrag_usd": betrag, "begruendung": begruendung, "regel": regel["text"],
                   "status": "wartet_auf_freigabe"}
        self._anhaengen("erstattungsempfehlungen.jsonl", eintrag)
        return eintrag

    def antwort_entwerfen(self, text: str, kunden_id: str | None = None) -> dict:
        """Speichert nur einen Entwurf. Versendet wird nichts. Ein neuer Entwurf ersetzt den bisherigen,
        es gibt pro Lauf also immer höchstens einen gültigen Entwurf."""
        text = self._pflichttext(text, "text")
        if kunden_id:
            kunden_id = self._kunde(kunden_id)["kunden_id"]
        eintrag = {"entwurfs_id": f"A-{self.run_id}-{self._seq + 1}", "zeit": _jetzt(),
                   "kunden_id": kunden_id, "text": text, "status": "entwurf",
                   "ersetzt": self.entwurf["entwurfs_id"] if self.entwurf else None}
        self.entwurf = eintrag
        self._anhaengen("antwortentwuerfe.jsonl", eintrag)
        return eintrag
