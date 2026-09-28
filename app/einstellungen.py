"""Alle Einstellungen der App kommen aus Umgebungsvariablen (lokal aus .env).

Pflicht:  ZUGANGSCODE, ANTHROPIC_API_KEY (ohne Key startet kein Lauf, die App selbst läuft aber)
Optional: DATABASE_URL (Neon; ohne: SQLite in DATEN_DIR), AGENT_LAEUFE_PRO_INSTANZ, AGENT_WARTEZEIT_S, SESSION_SECRET, DATEN_DIR, MONATSDECKEL_USD, MAX_PARALLELE_LAEUFE, COOKIE_SECURE
"""

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Einstellungen:
    zugangscode: str
    session_secret: str
    daten_dir: Path
    monatsdeckel_usd: float
    max_parallele_laeufe: int
    cookie_secure: bool
    database_url: str | None = None
    agent_laeufe_pro_instanz: int = 1
    agent_wartezeit_s: float = 90.0

    @property
    def db_pfad(self) -> Path:
        return self.daten_dir / "uc7.sqlite"

    @property
    def lauf_dir(self) -> Path:
        return self.daten_dir / "laeufe"


def aus_umgebung() -> Einstellungen:
    code = os.environ.get("ZUGANGSCODE", "").strip()
    if len(code) < 8:
        raise RuntimeError("ZUGANGSCODE fehlt oder ist kürzer als 8 Zeichen. Ohne Zugangscode startet die App nicht.")
    return Einstellungen(
        zugangscode=code,
        # Ohne SESSION_SECRET gilt ein Zufallswert: Nach einem Neustart muss man sich neu anmelden.
        session_secret=os.environ.get("SESSION_SECRET") or secrets.token_hex(32),
        daten_dir=Path(os.environ.get("DATEN_DIR", "daten")),
        monatsdeckel_usd=float(os.environ.get("MONATSDECKEL_USD", "4.50")),
        max_parallele_laeufe=int(os.environ.get("MAX_PARALLELE_LAEUFE", "1")),
        cookie_secure=os.environ.get("COOKIE_SECURE", "0") == "1",
        database_url=os.environ.get("DATABASE_URL") or None,
        agent_laeufe_pro_instanz=int(os.environ.get("AGENT_LAEUFE_PRO_INSTANZ", "1")),
        agent_wartezeit_s=float(os.environ.get("AGENT_WARTEZEIT_S", "90")),
    )
