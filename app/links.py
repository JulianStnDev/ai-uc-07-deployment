"""Persönliche Links für Besucher verwalten (Kommandozeile).

    .venv/bin/python -m app.links anlegen <pseudonym>   # gibt den fertigen Link aus
    .venv/bin/python -m app.links liste                 # alle Links mit Nutzung, Ablauf, Status
    .venv/bin/python -m app.links sperren <code>        # sofort ungültig

Datenbank wie die App: DATABASE_URL aus .env (Neon), sonst SQLite in DATEN_DIR. Die Basis-URL des Links
kommt aus APP_URL (Standard: der Cloud-Run-Dienst). Als Pseudonym z. B. Firma oder Anlass, keinen
Personennamen: Gespeichert werden nur Code, Zeitpunkt und Anzahl Läufe.
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from . import zugang
from .speicher import LINK_LAEUFE, Speicher

APP_URL = "https://uc7-807149335205.europe-west3.run.app"


def speicher_aus_umgebung() -> Speicher:
    return Speicher(Path(os.environ.get("DATEN_DIR", "daten")) / "uc7.sqlite", os.environ.get("DATABASE_URL") or None)


def link_url(code: str) -> str:
    return f"{os.environ.get('APP_URL', APP_URL).rstrip('/')}/?code={code}"


def anlegen(s: Speicher, pseudonym: str) -> str:
    code = zugang.neuer_code(pseudonym)
    s.link_anlegen(code)
    link = s.link(code)
    return (f"Link angelegt ({LINK_LAEUFE} Läufe, gültig bis {zugang.ablauf(link):%d.%m.%Y}):\n{link_url(code)}")


def liste(s: Speicher) -> str:
    links = s.links()
    if not links:
        return "Noch keine Links."
    zeilen = [f"{'Code':<34} {'Läufe':>6}  {'angelegt':<10}  {'gültig bis':<10}  Status"]
    for l in links:
        zeilen.append(f"{l['code']:<34} {l['laeufe']:>3}/{LINK_LAEUFE}  {l['erstellt'][:10]:<10}  "
                      f"{zugang.ablauf(l):%Y-%m-%d}  {zugang.link_status(l)}")
    return "\n".join(zeilen)


def sperren(s: Speicher, code: str) -> str:
    return f"Gesperrt: {code}" if s.link_sperren(code) else f"Unbekannter Code: {code}"


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    p = argparse.ArgumentParser(prog="python -m app.links", description="Persönliche Links für Besucher verwalten.")
    befehle = p.add_subparsers(dest="befehl", required=True)
    befehle.add_parser("anlegen", help="neuen Link anlegen").add_argument("pseudonym", help="z. B. Firma oder Anlass, kein Personenname")
    befehle.add_parser("liste", help="alle Links mit Nutzung")
    befehle.add_parser("sperren", help="Link sofort sperren").add_argument("code")
    a = p.parse_args(argv)
    s = speicher_aus_umgebung()
    try:
        if a.befehl == "anlegen":
            print(anlegen(s, a.pseudonym))
        elif a.befehl == "liste":
            print(liste(s))
        else:
            ausgabe = sperren(s, a.code)
            print(ausgabe)
            return 0 if ausgabe.startswith("Gesperrt") else 1
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    finally:
        s.schliessen()
    return 0


if __name__ == "__main__":
    sys.exit(main())
