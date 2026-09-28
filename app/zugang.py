"""Zugangscode: Wer den Code kennt, bekommt ein Cookie. Mehr Benutzerverwaltung gibt es nicht.

Das Cookie enthält keinen Code, sondern eine HMAC-Signatur über den Code. Ändert sich der
Zugangscode oder das SESSION_SECRET, werden alle bisherigen Cookies ungültig.
"""

import hashlib
import hmac

COOKIE_NAME = "uc7_zugang"
COOKIE_MAX_ALTER_S = 7 * 24 * 3600


def cookie_wert(zugangscode: str, session_secret: str) -> str:
    return hmac.new(session_secret.encode(), f"zugang:{zugangscode}".encode(), hashlib.sha256).hexdigest()


def code_richtig(eingabe: str, zugangscode: str) -> bool:
    # compare_digest vergleicht in konstanter Zeit: Die Antwortzeit verrät nicht, wie viele Zeichen stimmen.
    return hmac.compare_digest(eingabe.strip().encode(), zugangscode.encode())


def cookie_gueltig(wert: str | None, zugangscode: str, session_secret: str) -> bool:
    return bool(wert) and hmac.compare_digest(wert, cookie_wert(zugangscode, session_secret))
