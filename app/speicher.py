"""Protokoll der Läufe, Erstattungsempfehlungen und Freigabe-Entscheidungen in SQLite.

Bewusst ohne ORM: ein paar SQL-Befehle, die man lesen kann. In Branch (b) wird dieselbe
Schnittstelle auf Neon Postgres umgestellt.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS laeufe (
    run_id          TEXT PRIMARY KEY,
    erstellt        TEXT NOT NULL,              -- ISO-Zeit UTC
    kunden_id       TEXT NOT NULL,
    absender        TEXT NOT NULL,
    text            TEXT NOT NULL,
    titel           TEXT,                       -- Chip-Label oder erste Wörter (kein LLM)
    status          TEXT NOT NULL,              -- laeuft | fertig | fehler | abgebrochen
    kosten_usd      REAL,                       -- SDK-Schätzung oder Pauschale
    kosten_pauschal INTEGER NOT NULL DEFAULT 0, -- 1 = keine Kostenangabe, Deckel verbucht
    dauer_s         REAL,
    ergebnis        TEXT,                       -- JSON: Entwurf, Übergaben, Kündigungen, Turns, ...
    ereignisse      TEXT                        -- JSON: alle Live-Ereignisse (Werkzeugaufrufe, Text)
);
CREATE TABLE IF NOT EXISTS empfehlungen (
    empfehlungs_id  TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES laeufe(run_id),
    erstellt        TEXT NOT NULL,
    kunden_id       TEXT NOT NULL,
    zahlungs_id     TEXT NOT NULL,
    betrag_usd      REAL NOT NULL,
    begruendung     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS freigaben (
    empfehlungs_id  TEXT PRIMARY KEY REFERENCES empfehlungen(empfehlungs_id),
    entscheidung    TEXT NOT NULL CHECK (entscheidung IN ('bestaetigt', 'abgelehnt')),
    kommentar       TEXT NOT NULL DEFAULT '',
    entschieden     TEXT NOT NULL
);
"""


def jetzt_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Speicher:
    def __init__(self, pfad: Path):
        Path(pfad).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(pfad, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        spalten = {r["name"] for r in self.db.execute("PRAGMA table_info(laeufe)")}
        if "titel" not in spalten:  # Datenbank aus Branch (a)
            self.db.execute("ALTER TABLE laeufe ADD COLUMN titel TEXT")

    # ---------- Läufe ----------

    def lauf_anlegen(self, run_id: str, kunden_id: str, absender: str, text: str, titel: str | None = None) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO laeufe (run_id, erstellt, kunden_id, absender, text, titel, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'laeuft')",
                (run_id, jetzt_iso(), kunden_id, absender, text, titel))

    def lauf_abschliessen(self, run_id: str, *, status: str, kosten_usd: float, kosten_pauschal: bool,
                          dauer_s: float, ergebnis: dict, ereignisse: list, empfehlungen: list[dict]) -> None:
        with self.db:  # eine Transaktion: Lauf und Empfehlungen zusammen oder gar nicht
            self.db.execute(
                "UPDATE laeufe SET status=?, kosten_usd=?, kosten_pauschal=?, dauer_s=?, ergebnis=?, ereignisse=? "
                "WHERE run_id=?",
                (status, kosten_usd, int(kosten_pauschal), dauer_s,
                 json.dumps(ergebnis, ensure_ascii=False), json.dumps(ereignisse, ensure_ascii=False), run_id))
            for e in empfehlungen:
                self.db.execute(
                    "INSERT INTO empfehlungen (empfehlungs_id, run_id, erstellt, kunden_id, zahlungs_id, betrag_usd, begruendung) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (e["empfehlungs_id"], run_id, e["zeit"], e["kunden_id"], e["zahlungs_id"], e["betrag_usd"], e["begruendung"]))

    def lauf(self, run_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM laeufe WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["ergebnis"] = json.loads(d["ergebnis"]) if d["ergebnis"] else None
        d["ereignisse"] = json.loads(d["ereignisse"]) if d["ereignisse"] else []
        return d

    def laufende_anzahl(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM laeufe WHERE status='laeuft'").fetchone()[0]

    def verwaiste_laeufe_abbrechen(self, pauschale_usd: float) -> int:
        """Beim Start der App: Läufe, die beim letzten Beenden noch liefen, haben keine Kostenangabe.
        Sie werden mit der Pauschale verbucht (lieber zu hoch als zu niedrig)."""
        with self.db:
            cur = self.db.execute(
                "UPDATE laeufe SET status='abgebrochen', kosten_usd=?, kosten_pauschal=1 WHERE status='laeuft'",
                (pauschale_usd,))
        return cur.rowcount

    # ---------- Budget ----------

    def kosten_im_monat(self, monat: str) -> float:
        """monat im Format 'YYYY-MM' (UTC). Summe aller abgeschlossenen Läufe."""
        return self.db.execute(
            "SELECT COALESCE(SUM(kosten_usd), 0) FROM laeufe WHERE substr(erstellt, 1, 7)=? AND status != 'laeuft'",
            (monat,)).fetchone()[0]

    def laufende_im_monat(self, monat: str) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM laeufe WHERE substr(erstellt, 1, 7)=? AND status='laeuft'", (monat,)).fetchone()[0]

    # ---------- Empfehlungen und Freigaben ----------

    def offene_empfehlungen(self) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT e.*, l.text AS ticket_text, l.titel FROM empfehlungen e JOIN laeufe l USING (run_id) "
            "LEFT JOIN freigaben f USING (empfehlungs_id) WHERE f.empfehlungs_id IS NULL ORDER BY e.erstellt")]

    def entschiedene_empfehlungen(self, limit: int = 20) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT e.*, f.entscheidung, f.kommentar, f.entschieden, l.titel FROM empfehlungen e JOIN laeufe l USING (run_id) "
            "JOIN freigaben f USING (empfehlungs_id) ORDER BY f.entschieden DESC LIMIT ?", (limit,))]

    def empfehlungen_zum_lauf(self, run_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT e.*, f.entscheidung, f.kommentar FROM empfehlungen e "
            "LEFT JOIN freigaben f USING (empfehlungs_id) WHERE e.run_id=? ORDER BY e.erstellt", (run_id,))]

    def entscheiden(self, empfehlungs_id: str, entscheidung: str, kommentar: str) -> bool:
        """Speichert genau eine Entscheidung je Empfehlung. False, wenn unbekannt oder schon entschieden."""
        if entscheidung not in ("bestaetigt", "abgelehnt"):
            raise ValueError(f"Unbekannte Entscheidung: {entscheidung!r}")
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO freigaben (empfehlungs_id, entscheidung, kommentar, entschieden) VALUES (?, ?, ?, ?)",
                    (empfehlungs_id, entscheidung, kommentar.strip(), jetzt_iso()))
        except sqlite3.IntegrityError:  # schon entschieden oder Empfehlung gibt es nicht
            return False
        return True

    def uebergaben(self, limit: int = 20) -> list[dict]:
        """Fälle, die der Agent an einen Menschen übergeben hat (aus dem Ergebnis der Läufe)."""
        faelle = []
        for r in self.db.execute(
                "SELECT run_id, erstellt, kunden_id, titel, text, ergebnis FROM laeufe "
                "WHERE ergebnis LIKE '%uebergabe_id%' ORDER BY erstellt DESC LIMIT ?", (limit,)):
            for u in json.loads(r["ergebnis"]).get("uebergaben", []):
                faelle.append({"run_id": r["run_id"], "erstellt": r["erstellt"], "kunden_id": r["kunden_id"],
                               "titel": r["titel"], "grund": u["grund"], "prioritaet": u["prioritaet"]})
        return faelle

    def freigabe_statistik(self) -> dict:
        r = self.db.execute(
            "SELECT COUNT(*) AS gesamt, COALESCE(SUM(entscheidung='bestaetigt'), 0) AS bestaetigt FROM freigaben").fetchone()
        return {"gesamt": r["gesamt"], "bestaetigt": r["bestaetigt"]}
