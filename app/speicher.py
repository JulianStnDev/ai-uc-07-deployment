"""Protokoll der Läufe, Erstattungsempfehlungen und Freigabe-Entscheidungen.

Zwei Backends hinter derselben Schnittstelle, bewusst ohne ORM:
- Postgres (Neon), wenn DATABASE_URL gesetzt ist: Produktion und alles, was dauerhaft sein soll.
- SQLite-Datei sonst: lokale Entwicklung und Tests.
Die SQL-Befehle sind für beide gleich geschrieben (Platzhalter `?`, wird für Postgres zu `%s`).
"""

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

# DOUBLE PRECISION statt REAL: In Postgres ist REAL nur 4 Byte genau (0,03128755 USD würde gerundet).
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS laeufe (
        run_id          TEXT PRIMARY KEY,
        erstellt        TEXT NOT NULL,              -- ISO-Zeit UTC
        kunden_id       TEXT NOT NULL,
        absender        TEXT NOT NULL,
        text            TEXT NOT NULL,
        titel           TEXT,                       -- Chip-Label oder erste Wörter (kein LLM)
        status          TEXT NOT NULL,              -- angelegt | laeuft | fertig | fehler | abgebrochen | verfallen
        kosten_usd      DOUBLE PRECISION,           -- SDK-Schätzung oder Pauschale
        kosten_pauschal INTEGER NOT NULL DEFAULT 0, -- 1 = keine Kostenangabe, Deckel verbucht
        dauer_s         DOUBLE PRECISION,
        ergebnis        TEXT,                       -- JSON: Entwurf, Übergaben, Kündigungen, Turns, ...
        ereignisse      TEXT,                       -- JSON: alle Live-Ereignisse (Werkzeugaufrufe, Text)
        zugang          TEXT                        -- Code des persönlichen Links; NULL = Admin
    )""",
    """CREATE TABLE IF NOT EXISTS empfehlungen (
        empfehlungs_id  TEXT PRIMARY KEY,
        run_id          TEXT NOT NULL REFERENCES laeufe(run_id),
        erstellt        TEXT NOT NULL,
        kunden_id       TEXT NOT NULL,
        zahlungs_id     TEXT NOT NULL,
        betrag_usd      DOUBLE PRECISION NOT NULL,
        begruendung     TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS pruefungen (
        pruefungs_id    TEXT PRIMARY KEY,           -- run_id + ':' + art
        run_id          TEXT NOT NULL REFERENCES laeufe(run_id),
        art             TEXT NOT NULL,              -- agent (nach dem Lauf) | antwort (endgültige Antwort)
        erstellt        TEXT NOT NULL,
        regeln          TEXT,                       -- JSON: Regelprüfungen ohne LLM
        judge           TEXT,                       -- JSON: Urteil des LLM-Judges (nur Stichprobe)
        modell          TEXT,
        kosten_usd      DOUBLE PRECISION NOT NULL DEFAULT 0,
        fehler          TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS antworten (
        run_id          TEXT PRIMARY KEY REFERENCES laeufe(run_id),
        erstellt        TEXT NOT NULL,
        text            TEXT NOT NULL,
        quelle          TEXT NOT NULL,              -- llm | vorlage
        entscheidungen  TEXT,                       -- JSON: Stand der Freigaben beim Schreiben
        modell          TEXT,
        kosten_usd      DOUBLE PRECISION NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS freigaben (
        empfehlungs_id  TEXT PRIMARY KEY REFERENCES empfehlungen(empfehlungs_id),
        entscheidung    TEXT NOT NULL CHECK (entscheidung IN ('bestaetigt', 'abgelehnt')),
        kommentar       TEXT NOT NULL DEFAULT '',   -- Begründung für den Kunden: geht in die endgültige Antwort
        entschieden     TEXT NOT NULL,
        notiz           TEXT NOT NULL DEFAULT ''    -- interne Notiz: nur Protokoll und Konsole, nie an ein Modell
    )""",
    # Persönliche Links für Besucher. Bewusst nur Code, Zeitpunkt und Zähler, keine Angaben zur Person.
    """CREATE TABLE IF NOT EXISTS zugangslinks (
        code            TEXT PRIMARY KEY,           -- Pseudonym + Zufallszeichen, steht im Link (Parameter code); kein Fragezeichen im SQL (Platzhalter)
        erstellt        TEXT NOT NULL,              -- gültig bis erstellt + LINK_GUELTIG
        laeufe          INTEGER NOT NULL DEFAULT 0, -- gestartete Läufe (nie gestartete werden zurückgebucht)
        gesperrt        INTEGER NOT NULL DEFAULT 0
    )""",
]
TABELLEN = {re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", s).group(1) for s in SCHEMA}
# Spalten, die per ALTER TABLE nachgerüstet werden (ältere Datenbanken)
NACHGERUESTET = {("freigaben", "notiz"), ("laeufe", "zugang")}

# Kontingent eines persönlichen Links (docs/decisions.md, 2026-09-30)
LINK_LAEUFE = 5
LINK_GUELTIG = timedelta(days=60)

# Ein Lauf dauert typisch 30–45 s. Was nach 15 min noch "läuft", hat keinen Prozess mehr.
VERWAIST_NACH = timedelta(minutes=15)
# "angelegt" = POST ist durch, der Lauf startet erst, wenn die Seite per SSE zuhört (siehe main.py).
OFFEN = ("angelegt", "laeuft")


def jetzt_iso(jetzt: datetime | None = None) -> str:
    return (jetzt or datetime.now(timezone.utc)).isoformat(timespec="seconds")


class _Sqlite:
    fehler_integritaet = (sqlite3.IntegrityError,)

    def __init__(self, pfad: Path):
        Path(pfad).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(pfad, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")

    @contextmanager
    def verbindung(self):
        with self.db:  # Transaktion: commit am Ende, rollback bei Fehler
            yield lambda sql, p=(): self.db.execute(sql, p)

    def einrichten(self):
        with self.verbindung() as x:
            for s in SCHEMA:
                x(s)
            spalten = {r["name"] for r in x("PRAGMA table_info(laeufe)")}
            if "titel" not in spalten:  # Datenbank aus Branch (a)
                x("ALTER TABLE laeufe ADD COLUMN titel TEXT")
            if "notiz" not in {r["name"] for r in x("PRAGMA table_info(freigaben)")}:  # Datenbank vor der Notiz
                x("ALTER TABLE freigaben ADD COLUMN notiz TEXT NOT NULL DEFAULT ''")
            if "zugang" not in {r["name"] for r in x("PRAGMA table_info(laeufe)")}:  # Datenbank vor den Links
                x("ALTER TABLE laeufe ADD COLUMN zugang TEXT")


class _Postgres:
    def __init__(self, url: str):
        import psycopg
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        self.fehler_integritaet = (psycopg.errors.IntegrityError,)
        # Neon legt die Datenbank nach 5 min ohne Nutzung schlafen und trennt dabei Verbindungen.
        # check_connection prüft jede Verbindung vor der Ausgabe und ersetzt tote.
        self.pool = ConnectionPool(url, min_size=1, max_size=4, open=True, timeout=20,
                                   check=ConnectionPool.check_connection,
                                   kwargs={"row_factory": dict_row, "connect_timeout": 15})

    @contextmanager
    def verbindung(self):
        with self.pool.connection() as conn:  # Transaktion: commit am Ende, rollback bei Fehler
            yield lambda sql, p=(): conn.execute(sql.replace("?", "%s"), p)

    def einrichten(self):
        with self.verbindung() as x:
            # Die App-Rolle uc7_app darf nur Zeilen lesen und schreiben, kein DDL (docs/decisions.md, 2026-09-30).
            # Ist das Schema vollständig, bleibt es beim Start unberührt. Sonst einmal mit dem Owner starten.
            vorhanden = {(r["table_name"], r["column_name"]) for r in x(
                "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = current_schema()")}
            if {t for t, _ in vorhanden} >= TABELLEN and vorhanden >= NACHGERUESTET:
                return
            for s in SCHEMA:
                x(s)
            x("ALTER TABLE freigaben ADD COLUMN IF NOT EXISTS notiz TEXT NOT NULL DEFAULT ''")  # Datenbank vor der Notiz
            x("ALTER TABLE laeufe ADD COLUMN IF NOT EXISTS zugang TEXT")  # Datenbank vor den Links

    def schliessen(self):
        self.pool.close()


class Speicher:
    def __init__(self, pfad: Path | None = None, database_url: str | None = None):
        self.backend = _Postgres(database_url) if database_url else _Sqlite(pfad)
        self.art = "postgres" if database_url else "sqlite"
        self.backend.einrichten()

    @property
    def db(self):
        """Direkter Zugriff auf die SQLite-Verbindung (nur für Tests)."""
        return self.backend.db

    def _alle(self, sql: str, p=()) -> list[dict]:
        with self.backend.verbindung() as x:
            return [dict(r) for r in x(sql, p).fetchall()]

    def _eins(self, sql: str, p=()) -> dict | None:
        zeilen = self._alle(sql, p)
        return zeilen[0] if zeilen else None

    def schliessen(self):
        if hasattr(self.backend, "schliessen"):
            self.backend.schliessen()

    # ---------- Läufe ----------

    def lauf_anlegen(self, run_id: str, kunden_id: str, absender: str, text: str, titel: str | None = None,
                     jetzt: datetime | None = None, status: str = "angelegt", zugang: str | None = None) -> None:
        with self.backend.verbindung() as x:
            x("INSERT INTO laeufe (run_id, erstellt, kunden_id, absender, text, titel, status, zugang) "
              "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (run_id, jetzt_iso(jetzt), kunden_id, absender, text, titel, status, zugang))

    def lauf_uebernehmen(self, run_id: str) -> bool:
        """Genau eine Anfrage darf einen angelegten Lauf starten, auch bei mehreren Instanzen.
        Das UPDATE mit Bedingung ist atomar: Die zweite Anfrage findet keine Zeile mehr."""
        with self.backend.verbindung() as x:
            return x("UPDATE laeufe SET status='laeuft' WHERE run_id=? AND status='angelegt'", (run_id,)).rowcount == 1

    def lauf_abschliessen(self, run_id: str, *, status: str, kosten_usd: float, kosten_pauschal: bool,
                          dauer_s: float, ergebnis: dict, ereignisse: list, empfehlungen: list[dict]) -> None:
        with self.backend.verbindung() as x:  # eine Transaktion: Lauf und Empfehlungen zusammen oder gar nicht
            if status == "verfallen":  # nie gestartet: Der Besucher bekommt seinen Lauf zurück
                x("UPDATE zugangslinks SET laeufe = laeufe - 1 WHERE laeufe > 0 AND code = "
                  "(SELECT zugang FROM laeufe WHERE run_id=? AND status='angelegt')", (run_id,))
            x("UPDATE laeufe SET status=?, kosten_usd=?, kosten_pauschal=?, dauer_s=?, ergebnis=?, ereignisse=? WHERE run_id=?",
              (status, kosten_usd, int(kosten_pauschal), dauer_s,
               json.dumps(ergebnis, ensure_ascii=False), json.dumps(ereignisse, ensure_ascii=False), run_id))
            for e in empfehlungen:
                x("INSERT INTO empfehlungen (empfehlungs_id, run_id, erstellt, kunden_id, zahlungs_id, betrag_usd, begruendung) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (e["empfehlungs_id"], run_id, e["zeit"], e["kunden_id"], e["zahlungs_id"], e["betrag_usd"], e["begruendung"]))

    def lauf(self, run_id: str) -> dict | None:
        d = self._eins("SELECT * FROM laeufe WHERE run_id=?", (run_id,))
        if d is None:
            return None
        d["ergebnis"] = json.loads(d["ergebnis"]) if d["ergebnis"] else None
        d["ereignisse"] = json.loads(d["ereignisse"]) if d["ereignisse"] else []
        return d

    def laufende_anzahl(self) -> int:
        """Angelegte und laufende Läufe (beide belegen einen Platz)."""
        return self._eins("SELECT COUNT(*) AS n FROM laeufe WHERE status IN (?, ?)", OFFEN)["n"]

    def verwaiste_laeufe_abbrechen(self, pauschale_usd: float, jetzt: datetime | None = None) -> int:
        """Aufräumen beim Start einer Instanz, nur für Läufe älter als VERWAIST_NACH (jüngere können gerade
        auf einer anderen Instanz laufen):
        - "läuft" ohne Abschluss (Neustart, Absturz): Pauschale verbuchen, lieber zu hoch als zu niedrig.
        - "angelegt", aber nie gestartet (Seite geschlossen, bevor sie zuhörte): verfallen, kostet nichts."""
        grenze = jetzt_iso((jetzt or datetime.now(timezone.utc)) - VERWAIST_NACH)
        with self.backend.verbindung() as x:
            for r in x("SELECT zugang FROM laeufe WHERE status='angelegt' AND erstellt < ? AND zugang IS NOT NULL",
                       (grenze,)).fetchall():  # nie gestartet: Lauf zurück aufs Kontingent
                x("UPDATE zugangslinks SET laeufe = laeufe - 1 WHERE code=? AND laeufe > 0", (r["zugang"],))
            n = x("UPDATE laeufe SET status='abgebrochen', kosten_usd=?, kosten_pauschal=1 WHERE status='laeuft' AND erstellt < ?",
                  (pauschale_usd, grenze)).rowcount
            n += x("UPDATE laeufe SET status='verfallen', kosten_usd=0 WHERE status='angelegt' AND erstellt < ?", (grenze,)).rowcount
            return n

    # ---------- Budget ----------

    def kosten_im_monat(self, monat: str) -> float:
        """monat im Format 'YYYY-MM' (UTC). Alle API-Kosten: abgeschlossene Läufe, Prüfungen, endgültige Antworten."""
        laeufe = self._eins(
            "SELECT COALESCE(SUM(kosten_usd), 0) AS s FROM laeufe WHERE substr(erstellt, 1, 7)=? AND status NOT IN (?, ?)",
            (monat, *OFFEN))["s"]
        return float(laeufe) + self.pruefungskosten_im_monat(monat) + float(self._eins(
            "SELECT COALESCE(SUM(kosten_usd), 0) AS s FROM antworten WHERE substr(erstellt, 1, 7)=?", (monat,))["s"])

    def pruefungskosten_im_monat(self, monat: str) -> float:
        return float(self._eins("SELECT COALESCE(SUM(kosten_usd), 0) AS s FROM pruefungen WHERE substr(erstellt, 1, 7)=?",
                                (monat,))["s"])

    def laufende_im_monat(self, monat: str) -> int:
        return self._eins("SELECT COUNT(*) AS n FROM laeufe WHERE substr(erstellt, 1, 7)=? AND status IN (?, ?)",
                          (monat, *OFFEN))["n"]

    # ---------- Empfehlungen und Freigaben ----------

    # Besucher mit persönlichem Link sehen nur Läufe mit ihrem Code. nur_zugang=None heißt: alle (Admin).
    @staticmethod
    def _filter(nur_zugang: str | None, verbinder: str = "AND") -> tuple[str, tuple]:
        return (f" {verbinder} l.zugang = ?", (nur_zugang,)) if nur_zugang is not None else ("", ())

    def offene_empfehlungen(self, nur_zugang: str | None = None) -> list[dict]:
        f, p = self._filter(nur_zugang)
        return self._alle(
            "SELECT e.*, l.text AS ticket_text, l.titel FROM empfehlungen e JOIN laeufe l USING (run_id) "
            f"LEFT JOIN freigaben f USING (empfehlungs_id) WHERE f.empfehlungs_id IS NULL{f} ORDER BY e.erstellt", p)

    def entschiedene_empfehlungen(self, limit: int = 20, nur_zugang: str | None = None) -> list[dict]:
        f, p = self._filter(nur_zugang, "WHERE")
        return self._alle(
            "SELECT e.*, f.entscheidung, f.kommentar, f.notiz, f.entschieden, l.titel FROM empfehlungen e JOIN laeufe l USING (run_id) "
            f"JOIN freigaben f USING (empfehlungs_id){f} ORDER BY f.entschieden DESC LIMIT ?", (*p, limit))

    def empfehlungen_zum_lauf(self, run_id: str) -> list[dict]:
        """Grundlage der endgültigen Antwort. Die interne Notiz fehlt hier absichtlich."""
        return self._alle(
            "SELECT e.*, f.entscheidung, f.kommentar, f.entschieden FROM empfehlungen e "
            "LEFT JOIN freigaben f USING (empfehlungs_id) WHERE e.run_id=? ORDER BY e.erstellt", (run_id,))

    def entscheiden(self, empfehlungs_id: str, entscheidung: str, kommentar: str, notiz: str = "") -> bool:
        """Speichert genau eine Entscheidung je Empfehlung. False, wenn unbekannt oder schon entschieden."""
        if entscheidung not in ("bestaetigt", "abgelehnt"):
            raise ValueError(f"Unbekannte Entscheidung: {entscheidung!r}")
        try:
            with self.backend.verbindung() as x:
                x("INSERT INTO freigaben (empfehlungs_id, entscheidung, kommentar, notiz, entschieden) VALUES (?, ?, ?, ?, ?)",
                  (empfehlungs_id, entscheidung, kommentar.strip(), notiz.strip(), jetzt_iso()))
        except self.backend.fehler_integritaet:  # schon entschieden oder Empfehlung gibt es nicht
            return False
        return True

    def offene_entwurfspruefungen(self, nur_zugang: str | None = None, limit: int = 50) -> list[dict]:
        """Fertige Läufe ohne Empfehlung, deren Entwurf etwas zusagt (ergebnis.zusage_saetze) und über die noch
        niemand entschieden hat (keine Antwort). UC6, B2."""
        f, p = self._filter(nur_zugang)
        faelle = []
        for r in self._alle("SELECT l.run_id, l.erstellt, l.kunden_id, l.absender, l.text, l.titel, l.ergebnis FROM laeufe l "
                            "LEFT JOIN antworten a USING (run_id) WHERE a.run_id IS NULL AND l.status = 'fertig' "
                            f"AND l.ergebnis LIKE ?{f} ORDER BY l.erstellt LIMIT ?", ('%"zusage_saetze": ["%', *p, limit)):
            ergebnis = json.loads(r["ergebnis"])
            if ergebnis.get("zusage_saetze") and not ergebnis.get("empfehlungen"):
                faelle.append({**r, "ergebnis": ergebnis})
        return faelle

    def uebergaben(self, limit: int = 20, nur_zugang: str | None = None) -> list[dict]:
        """Fälle, die der Agent an einen Menschen übergeben hat (aus dem Ergebnis der Läufe)."""
        faelle = []
        f, p = self._filter(nur_zugang)
        # Muster als Parameter: Ein %-Zeichen direkt im SQL müsste man für Postgres verdoppeln.
        for r in self._alle("SELECT run_id, erstellt, kunden_id, titel, ergebnis FROM laeufe l "
                            f"WHERE ergebnis LIKE ?{f} ORDER BY erstellt DESC LIMIT ?", ("%uebergabe_id%", *p, limit)):
            for u in json.loads(r["ergebnis"]).get("uebergaben", []):
                faelle.append({"run_id": r["run_id"], "erstellt": r["erstellt"], "kunden_id": r["kunden_id"],
                               "titel": r["titel"], "grund": u["grund"], "prioritaet": u["prioritaet"]})
        return faelle

    # ---------- Prüfungen und endgültige Antworten ----------

    def pruefung_speichern(self, run_id: str, art: str, regeln: dict | None, judge: dict | None, modell: str | None,
                           kosten_usd: float, fehler: str | None = None) -> None:
        pid = f"{run_id}:{art}"
        with self.backend.verbindung() as x:
            x("DELETE FROM pruefungen WHERE pruefungs_id=?", (pid,))
            x("INSERT INTO pruefungen (pruefungs_id, run_id, art, erstellt, regeln, judge, modell, kosten_usd, fehler) "
              "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
              (pid, run_id, art, jetzt_iso(), json.dumps(regeln, ensure_ascii=False) if regeln is not None else None,
               json.dumps(judge, ensure_ascii=False) if judge is not None else None, modell, kosten_usd, fehler))

    def pruefungen_zum_lauf(self, run_id: str) -> list[dict]:
        return [self._pruefung(r) for r in self._alle("SELECT * FROM pruefungen WHERE run_id=? ORDER BY erstellt", (run_id,))]

    def pruefungen(self, limit: int = 500) -> list[dict]:
        return [self._pruefung(r) for r in self._alle("SELECT * FROM pruefungen ORDER BY erstellt DESC LIMIT ?", (limit,))]

    @staticmethod
    def _pruefung(r: dict) -> dict:
        r["regeln"] = json.loads(r["regeln"]) if r["regeln"] else None
        r["judge"] = json.loads(r["judge"]) if r["judge"] else None
        return r

    def antwort_speichern(self, run_id: str, text: str, quelle: str, entscheidungen: list[dict], modell: str | None,
                          kosten_usd: float) -> bool:
        try:
            with self.backend.verbindung() as x:
                x("INSERT INTO antworten (run_id, erstellt, text, quelle, entscheidungen, modell, kosten_usd) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (run_id, jetzt_iso(), text, quelle, json.dumps(entscheidungen, ensure_ascii=False), modell, kosten_usd))
        except self.backend.fehler_integritaet:  # gibt es schon
            return False
        return True

    def antwort(self, run_id: str) -> dict | None:
        return self._eins("SELECT * FROM antworten WHERE run_id=?", (run_id,))

    def run_id_zur_empfehlung(self, empfehlungs_id: str) -> str | None:
        r = self._eins("SELECT run_id FROM empfehlungen WHERE empfehlungs_id=?", (empfehlungs_id,))
        return r["run_id"] if r else None

    def laeufe_uebersicht(self, limit: int = 1000) -> list[dict]:
        """Für die Betriebsseite: die letzten Läufe ohne die großen JSON-Spalten."""
        return self._alle("SELECT run_id, erstellt, kunden_id, titel, status, kosten_usd, kosten_pauschal, dauer_s "
                          "FROM laeufe ORDER BY erstellt DESC LIMIT ?", (limit,))

    def laeufe_zum_zugang(self, code: str, limit: int = 20) -> list[dict]:
        """Die eigenen Läufe eines Besuchers mit persönlichem Link (Startseite)."""
        return self._alle("SELECT run_id, erstellt, kunden_id, titel, status FROM laeufe WHERE zugang=? "
                          "ORDER BY erstellt DESC LIMIT ?", (code, limit))

    def antworten_kosten(self, monat: str) -> float:
        return float(self._eins("SELECT COALESCE(SUM(kosten_usd), 0) AS s FROM antworten WHERE substr(erstellt, 1, 7)=?",
                                (monat,))["s"])

    def freigabe_statistik(self, nur_zugang: str | None = None) -> dict:
        f, p = self._filter(nur_zugang, "WHERE")
        r = self._eins("SELECT COUNT(*) AS gesamt, "
                       "COALESCE(SUM(CASE WHEN entscheidung='bestaetigt' THEN 1 ELSE 0 END), 0) AS bestaetigt FROM freigaben "
                       f"JOIN empfehlungen USING (empfehlungs_id) JOIN laeufe l USING (run_id){f}", p)
        return {"gesamt": int(r["gesamt"]), "bestaetigt": int(r["bestaetigt"])}

    # ---------- Persönliche Links ----------

    def link_anlegen(self, code: str, jetzt: datetime | None = None) -> None:
        with self.backend.verbindung() as x:
            x("INSERT INTO zugangslinks (code, erstellt) VALUES (?, ?)", (code, jetzt_iso(jetzt)))

    def link(self, code: str) -> dict | None:
        return self._eins("SELECT * FROM zugangslinks WHERE code=?", (code,))

    def links(self) -> list[dict]:
        return self._alle("SELECT * FROM zugangslinks ORDER BY erstellt DESC")

    def link_sperren(self, code: str) -> bool:
        with self.backend.verbindung() as x:
            return x("UPDATE zugangslinks SET gesperrt=1 WHERE code=?", (code,)).rowcount == 1

    def link_lauf_buchen(self, code: str, jetzt: datetime | None = None) -> bool:
        """Zählt einen Lauf aufs Kontingent. Atomar: Das UPDATE greift nur, solange ein Lauf frei ist, der Link
        noch gilt und nicht gesperrt ist. Gleichzeitige Anfragen (auch auf zwei Instanzen) überziehen nicht."""
        frueheste = jetzt_iso((jetzt or datetime.now(timezone.utc)) - LINK_GUELTIG)
        with self.backend.verbindung() as x:
            return x("UPDATE zugangslinks SET laeufe = laeufe + 1 "
                     "WHERE code=? AND laeufe < ? AND gesperrt=0 AND erstellt > ?",
                     (code, LINK_LAEUFE, frueheste)).rowcount == 1
