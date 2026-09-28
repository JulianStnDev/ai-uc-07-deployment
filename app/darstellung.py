"""Texte für die Zeitleiste "Hinter den Kulissen" und das SSE-Format.

Grundsatz (docs/decisions.md, 2026-09-28): Die Zeitleiste zeigt nur, was der Agent gesehen und
getan hat, in Alltagssprache. Die Oberfläche deutet nichts und hebt nichts hervor, was der Agent
nicht selbst geschlossen hat. Beispiel: "5 Zahlungen gefunden" plus Liste, nicht "darunter eine
doppelte Abbuchung". Die vollständigen Ein- und Ausgaben bleiben als Rohdaten aufklappbar.
"""

import json
import re

WERKZEUG_TITEL = {
    "kunde_nachschlagen": "Sucht das Kundenkonto",
    "zahlungen_ansehen": "Sieht die Zahlungen an",
    "hilfe_durchsuchen": "Liest die Hilfe-Artikel",
    "an_mensch_uebergeben": "Übergibt an einen Menschen",
    "abo_kuendigen": "Kündigt das Abo",
    "erstattung_empfehlen": "Empfiehlt eine Erstattung",
    "antwort_entwerfen": "Schreibt den Antwortentwurf",
}
WERKZEUG_INFO = {
    "an_mensch_uebergeben": "uebergabe", "abo_kuendigen": "kuendigung",
    "erstattung_empfehlen": "empfehlung", "antwort_entwerfen": "entwurf",
}
ABO_STUFE = {"pro_jaehrlich": "Pro jährlich", "pro_monatlich": "Pro monatlich", "free": "Free"}
ABO_STATUS = {"aktiv": "aktiv", "gekuendigt": "gekündigt"}
ANBIETER = {"stripe": "Web", "apple": "App Store", "google": "Google Play"}


def usd(betrag: float | None) -> str:
    if betrag is None:
        return "–"
    return f"{betrag:,.2f} USD".replace(",", "X").replace(".", ",").replace("X", ".")


def usd_genau(betrag: float | None) -> str:
    return "–" if betrag is None else f"{betrag:.4f} USD".replace(".", ",")


def datum(iso: str | None) -> str:
    if not iso:
        return "–"
    j, m, t = iso[:10].split("-")
    return f"{t}.{m}.{j}" + (f" {iso[11:16]}" if len(iso) > 10 else "")


def abo_text(kunde: dict) -> str:
    """z. B. "Pro jährlich · aktiv · Web" oder "Free"."""
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
        return {"klasse": "notiz", "symbol": "…", "titel": "Notiz des Agents", "info": "notiz" if erste_notiz else None,
                "text": notiz_text(ereignis["text"]), "liste": [], "badge": None, "roh": None, "notiz": True}
    w, ein, erg = ereignis["werkzeug"], ereignis["eingabe"], ereignis["ergebnis"]
    e = {"klasse": "fertig", "symbol": "✓", "titel": WERKZEUG_TITEL.get(w, w), "info": WERKZEUG_INFO.get(w),
         "text": "", "liste": [], "badge": None, "notiz": False}
    if ereignis.get("blockiert"):
        e.update(klasse="fehler", symbol="×", titel="Nicht erlaubtes Werkzeug abgelehnt", info="blockiert",
                 text=f"Der Agent wollte „{w}“ benutzen. Das ist nicht freigegeben.")
    elif ereignis["fehler"]:
        e.update(klasse="fehler", symbol="!", text=f"Hat nicht geklappt: {erg.get('fehler', 'unbekannter Fehler')}")
    elif w == "kunde_nachschlagen":
        treffer = erg.get("treffer", [])
        if len(treffer) == 1:
            k = treffer[0]
            e["text"] = f"Konto gefunden: {k['name']} · {abo_text(k)}"
        else:
            e["text"] = f"{len(treffer)} Konten gefunden" if treffer else f"Kein Konto zu „{ein.get('suche', '')}“ gefunden"
            e["liste"] = [f"{k['name']} ({k['email']}) · {abo_text(k)}" for k in treffer]
    elif w == "zahlungen_ansehen":
        zahlungen = erg.get("zahlungen", [])
        e["text"] = f"{len(zahlungen)} Zahlung{'en' if len(zahlungen) != 1 else ''} gefunden" if zahlungen else "Keine Zahlungen gefunden"
        e["liste"] = [f"{datum(z['datum'])} · {usd(z['betrag_usd'])} · {z['beschreibung']} ({z['zahlungs_id']})" for z in zahlungen]
    elif w == "hilfe_durchsuchen":
        treffer = erg.get("treffer", [])
        e["text"] = f"{len(treffer)} Artikel gefunden" if treffer else "Keinen passenden Artikel gefunden"
        e["liste"] = [a["titel"] for a in treffer]
    elif w == "erstattung_empfehlen":
        e.update(klasse="warnung", symbol="!", text=f"{usd(erg['betrag_usd'])} für Zahlung {erg['zahlungs_id']}",
                 badge=("offen", "wartet auf Freigabe"))
    elif w == "abo_kuendigen":
        e["text"] = f"Gekündigt zum {datum(erg['wirksam_zum'])}"
    elif w == "an_mensch_uebergeben":
        e.update(klasse="warnung", symbol="→", text=erg["grund"])
    elif w == "antwort_entwerfen":
        e["text"] = "Entwurf gespeichert, nicht versendet"
    roh = json.dumps({"eingabe": ein, "ergebnis": erg}, ensure_ascii=False, indent=2)
    e["roh"] = roh if len(roh) <= 4000 else roh[:4000] + "\n… (gekürzt)"
    return e


def sse_nachricht(ereignis_name: str, html: str, ereignis_id: int | None = None) -> str:
    """Server-Sent-Events-Format: optional 'id:', dann 'event:', dann je Zeile 'data:', Leerzeile am Ende."""
    teile = []
    if ereignis_id is not None:
        teile.append(f"id: {ereignis_id}")
    teile.append(f"event: {ereignis_name}")
    teile += [f"data: {zeile}" for zeile in (html.splitlines() or [""])]
    return "\n".join(teile) + "\n\n"
