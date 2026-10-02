"""Texte für die Zeitleiste "Hinter den Kulissen" und das SSE-Format.

Texte auf Englisch (Oberfläche für Besucher, docs/decisions.md, 2026-09-30). Die Daten selbst (Kunden,
Zahlungen, Hilfe-Artikel) bleiben deutsch wie das Kundengespräch.

Grundsatz (docs/decisions.md, 2026-09-28): Die Zeitleiste zeigt nur, was der Agent gesehen und
getan hat, in Alltagssprache. Die Oberfläche deutet nichts und hebt nichts hervor, was der Agent
nicht selbst geschlossen hat. Beispiel: "5 payments found" plus Liste, nicht "including a
double charge". Die vollständigen Ein- und Ausgaben bleiben als Rohdaten aufklappbar.
"""

import json
import re

WERKZEUG_TITEL = {
    "kunde_nachschlagen": "Looks up the customer account",
    "zahlungen_ansehen": "Checks the payments",
    "hilfe_durchsuchen": "Reads the help articles",
    "an_mensch_uebergeben": "Hands over to a human",
    "abo_kuendigen": "Cancels the subscription",
    "erstattung_empfehlen": "Recommends a refund",
    "antwort_entwerfen": "Writes the reply draft",
}
WERKZEUG_INFO = {
    "an_mensch_uebergeben": "uebergabe", "abo_kuendigen": "kuendigung",
    "erstattung_empfehlen": "empfehlung", "antwort_entwerfen": "entwurf",
}
ABO_STUFE = {"pro_jaehrlich": "Pro annual", "pro_monatlich": "Pro monthly", "free": "Free"}
ABO_STATUS = {"aktiv": "active", "gekuendigt": "cancelled"}
MONATE = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
ANBIETER = {"stripe": "Web", "apple": "App Store", "google": "Google Play"}


def usd(betrag: float | None) -> str:
    if betrag is None:
        return "–"
    return f"{betrag:,.2f} USD"


def usd_de(betrag: float | None) -> str:
    """Deutsches Format für Kundengespräch, Modell-Eingaben (unverändert seit UC7 Branch d) und Betriebsseite."""
    if betrag is None:
        return "–"
    return f"{betrag:,.2f} USD".replace(",", "X").replace(".", ",").replace("X", ".")


def usd_genau(betrag: float | None) -> str:
    return "–" if betrag is None else f"{betrag:.4f} USD"


def datum(iso: str | None) -> str:
    """z. B. "28 Sep 2026" oder "28 Sep 2026, 19:09"."""
    if not iso:
        return "–"
    j, m, t = iso[:10].split("-")
    return f"{int(t)} {MONATE[int(m) - 1]} {j}" + (f", {iso[11:16]}" if len(iso) > 10 else "")


def datum_de(iso: str | None) -> str:
    """Deutsches Datum für die Betriebsseite (nur Admin, bleibt deutsch), z. B. "28.09.2026 19:09"."""
    if not iso:
        return "–"
    j, m, t = iso[:10].split("-")
    return f"{t}.{m}.{j}" + (f" {iso[11:16]}" if len(iso) > 10 else "")


def abo_text(kunde: dict) -> str:
    """z. B. "Pro annual · active · Web" oder "Free"."""
    abo = kunde["abo"]
    if abo["stufe"] == "free":
        return "Free"
    return " · ".join([ABO_STUFE.get(abo["stufe"], abo["stufe"]), ABO_STATUS.get(abo["status"], abo["status"] or "–"),
                       ANBIETER.get(abo["anbieter"], abo["anbieter"] or "–")])


def notiz_text(text: str) -> str:
    """Zwischentext des Agents ohne Markdown-Sternchen."""
    return re.sub(r"\*\*|__", "", text).strip()


def schritt(ereignis: dict, erste_notiz: bool = False) -> dict:
    """Macht aus einem Ereignis einen Eintrag der Zeitleiste:
    {klasse, symbol, titel, info, text, liste, badge, roh}."""
    if ereignis["art"] == "text":
        return {"klasse": "notiz", "symbol": "…", "titel": "Agent's note", "info": "notiz" if erste_notiz else None,
                "text": notiz_text(ereignis["text"]), "liste": [], "badge": None, "roh": None, "notiz": True}
    w, ein, erg = ereignis["werkzeug"], ereignis["eingabe"], ereignis["ergebnis"]
    e = {"klasse": "fertig", "symbol": "✓", "titel": WERKZEUG_TITEL.get(w, w), "info": WERKZEUG_INFO.get(w),
         "text": "", "liste": [], "badge": None, "notiz": False}
    if ereignis.get("blockiert") and ereignis.get("blockiert_art") == "fremdes_konto":
        ziel = ein.get("kunden_id") or ein.get("zahlungs_id") or ein.get("suche") or "?"
        e.update(klasse="fehler", symbol="×", titel="Blocked: another customer's account", info="kontobindung",
                 text=f"The agent tried “{w}” for “{ziel}”. It may only use the account of the person who wrote.")
    elif ereignis.get("blockiert"):
        e.update(klasse="fehler", symbol="×", titel="Blocked a tool that is not allowed", info="blockiert",
                 text=f"The agent tried to use “{w}”. That tool is not permitted.")
    elif ereignis["fehler"]:
        e.update(klasse="fehler", symbol="!", text=f"Didn't work: {erg.get('fehler', 'unknown error')}")
    elif w == "kunde_nachschlagen":
        treffer = erg.get("treffer", [])
        if len(treffer) == 1:
            k = treffer[0]
            e["text"] = f"Account found: {k['name']} · {abo_text(k)}"
        else:
            e["text"] = f"{len(treffer)} accounts found" if treffer else f"No account found for “{ein.get('suche', '')}”"
            e["liste"] = [f"{k['name']} ({k['email']}) · {abo_text(k)}" for k in treffer]
    elif w == "zahlungen_ansehen":
        zahlungen = erg.get("zahlungen", [])
        e["text"] = f"{len(zahlungen)} payment{'s' if len(zahlungen) != 1 else ''} found" if zahlungen else "No payments found"
        e["liste"] = [f"{datum(z['datum'])} · {usd(z['betrag_usd'])} · {z['beschreibung']} ({z['zahlungs_id']})" for z in zahlungen]
    elif w == "hilfe_durchsuchen":
        treffer = erg.get("treffer", [])
        e["text"] = f"{len(treffer)} article{'s' if len(treffer) != 1 else ''} found" if treffer else "No matching article found"
        e["liste"] = [a["titel"] for a in treffer]
    elif w == "erstattung_empfehlen":
        e.update(klasse="warnung", symbol="!", text=f"{usd(erg['betrag_usd'])} for payment {erg['zahlungs_id']}",
                 badge=("offen", "awaiting approval"))
    elif w == "abo_kuendigen":
        e["text"] = f"Cancelled effective {datum(erg['wirksam_zum'])}"
    elif w == "an_mensch_uebergeben":
        e.update(klasse="warnung", symbol="→", text=erg["grund"])
    elif w == "antwort_entwerfen":
        e["text"] = "Draft saved, not sent"
    roh = json.dumps({"eingabe": ein, "ergebnis": erg}, ensure_ascii=False, indent=2)
    e["roh"] = roh if len(roh) <= 4000 else roh[:4000] + "\n… (truncated)"
    return e


def sse_nachricht(ereignis_name: str, html: str, ereignis_id: int | None = None) -> str:
    """Server-Sent-Events-Format: optional 'id:', dann 'event:', dann je Zeile 'data:', Leerzeile am Ende."""
    teile = []
    if ereignis_id is not None:
        teile.append(f"id: {ereignis_id}")
    teile.append(f"event: {ereignis_name}")
    teile += [f"data: {zeile}" for zeile in (html.splitlines() or [""])]
    return "\n".join(teile) + "\n\n"
