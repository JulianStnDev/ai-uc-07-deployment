"""Zwei Arten von Zugang, mehr Benutzerverwaltung gibt es nicht.

Admin: Wer den Zugangscode kennt, bekommt ein Cookie. Das Cookie enthält keinen Code, sondern eine
HMAC-Signatur über den Code. Ändert sich der Zugangscode oder das SESSION_SECRET, werden alle
bisherigen Cookies ungültig.

Persönlicher Link (?code=...): Besucher-Cookie mit Code und Signatur. Der Code wird bei jeder Anfrage
gegen die Datenbank geprüft, damit Sperren und Ablauf sofort wirken (docs/decisions.md, 2026-09-30).
"""

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timezone

from .speicher import LINK_GUELTIG, LINK_LAEUFE

COOKIE_NAME = "uc7_zugang"
COOKIE_MAX_ALTER_S = 7 * 24 * 3600
LINK_COOKIE_NAME = "uc7_link"
LINK_COOKIE_MAX_ALTER_S = int(LINK_GUELTIG.total_seconds())

# Ohne 0/o und 1/l, damit man einen Link auch abtippen kann. 6 Zeichen aus 32 = rund 1 Milliarde Möglichkeiten.
ZUFALL_ZEICHEN = "abcdefghijkmnpqrstuvwxyz23456789"
CODE_MUSTER = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*-[a-z0-9]{6}$")


def cookie_wert(zugangscode: str, session_secret: str) -> str:
    return hmac.new(session_secret.encode(), f"zugang:{zugangscode}".encode(), hashlib.sha256).hexdigest()


def code_richtig(eingabe: str, zugangscode: str) -> bool:
    # compare_digest vergleicht in konstanter Zeit: Die Antwortzeit verrät nicht, wie viele Zeichen stimmen.
    return hmac.compare_digest(eingabe.strip().encode(), zugangscode.encode())


def cookie_gueltig(wert: str | None, zugangscode: str, session_secret: str) -> bool:
    return bool(wert) and hmac.compare_digest(wert, cookie_wert(zugangscode, session_secret))


# ---------- Persönliche Links ----------

def neuer_code(pseudonym: str) -> str:
    """z. B. "acme" -> "acme-k7m2qx". Das Pseudonym macht den Link in der Liste wiedererkennbar,
    die Zufallszeichen machen ihn unerratbar."""
    teil = re.sub(r"[^a-z0-9]+", "-", pseudonym.lower()).strip("-")[:24].strip("-")
    if not teil:
        raise ValueError("Das Pseudonym braucht mindestens einen Buchstaben oder eine Ziffer (a-z, 0-9).")
    return f"{teil}-{''.join(secrets.choice(ZUFALL_ZEICHEN) for _ in range(6))}"


def link_cookie_wert(code: str, session_secret: str) -> str:
    return f"{code}.{hmac.new(session_secret.encode(), f'link:{code}'.encode(), hashlib.sha256).hexdigest()}"


def link_aus_cookie(wert: str | None, session_secret: str) -> str | None:
    """Code aus einem Besucher-Cookie, None bei fehlender oder falscher Signatur."""
    if not wert or "." not in wert:
        return None
    code = wert.rsplit(".", 1)[0]
    return code if hmac.compare_digest(wert, link_cookie_wert(code, session_secret)) else None


def ablauf(link: dict) -> datetime:
    return datetime.fromisoformat(link["erstellt"]) + LINK_GUELTIG


def link_status(link: dict | None, jetzt: datetime | None = None) -> str:
    """ok | leer (Kontingent verbraucht, eigene Fälle bleiben sichtbar) | abgelaufen | gesperrt | unbekannt"""
    if link is None:
        return "unbekannt"
    if link["gesperrt"]:
        return "gesperrt"
    if (jetzt or datetime.now(timezone.utc)) >= ablauf(link):
        return "abgelaufen"
    return "leer" if link["laeufe"] >= LINK_LAEUFE else "ok"


def uebrig(link: dict) -> int:
    return max(0, LINK_LAEUFE - int(link["laeufe"]))
