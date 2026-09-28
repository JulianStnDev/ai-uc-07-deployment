"""Kurzfassungen der Werkzeugaufrufe für die Live-Anzeige und das SSE-Format.

Die vollständigen Ein- und Ausgaben stehen zusätzlich aufklappbar auf der Seite.
"""

import json

WERKZEUG_TITEL = {
    "kunde_nachschlagen": "Kunde nachschlagen",
    "zahlungen_ansehen": "Zahlungen ansehen",
    "hilfe_durchsuchen": "Hilfe durchsuchen",
    "an_mensch_uebergeben": "An Mensch übergeben",
    "abo_kuendigen": "Abo kündigen",
    "erstattung_empfehlen": "Erstattung empfehlen",
    "antwort_entwerfen": "Antwort entwerfen",
}


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


def schritt(ereignis: dict) -> dict:
    """Macht aus einem Werkzeug-Ereignis {titel, eingabe_kurz, zeilen, fehler, blockiert, roh}."""
    w, ein, erg = ereignis["werkzeug"], ereignis["eingabe"], ereignis["ergebnis"]
    titel = WERKZEUG_TITEL.get(w, w)
    zeilen: list[str] = []
    if ereignis["fehler"]:
        zeilen.append(erg.get("fehler", "Fehler"))
    elif w == "kunde_nachschlagen":
        treffer = erg.get("treffer", [])
        zeilen.append(f"{len(treffer)} Treffer" if treffer else "kein Treffer")
        for k in treffer:
            abo = k["abo"]
            zeilen.append(f"{k['name']} ({k['kunden_id']}, {k['email']}): {abo['stufe']}, {abo['status']}, {abo['anbieter']}")
    elif w == "zahlungen_ansehen":
        zahlungen = erg.get("zahlungen", [])
        zeilen.append(f"{len(zahlungen)} Zahlungen" if zahlungen else "keine Zahlungen")
        for z in zahlungen:
            zeilen.append(f"{z['zahlungs_id']} · {datum(z['datum'])} · {usd(z['betrag_usd'])} · {z['anbieter']} · {z['beschreibung']}")
    elif w == "hilfe_durchsuchen":
        treffer = erg.get("treffer", [])
        zeilen.append(f"{len(treffer)} Artikel" if treffer else "keine Artikel")
        zeilen += [f"{a['titel']} (Stand {a['stand']})" for a in treffer]
    elif w == "erstattung_empfehlen":
        zeilen.append(f"{usd(erg['betrag_usd'])} für Zahlung {erg['zahlungs_id']}, wartet auf Freigabe")
    elif w == "abo_kuendigen":
        zeilen.append(f"gekündigt zum {datum(erg['wirksam_zum'])}")
    elif w == "an_mensch_uebergeben":
        zeilen.append(f"Priorität {erg['prioritaet']}: {erg['grund']}")
    elif w == "antwort_entwerfen":
        zeilen.append("Entwurf gespeichert (wird nicht versendet)")
    eingabe_kurz = ", ".join(f"{k}={v!r}" if not isinstance(v, str) or len(v) <= 60 else f"{k}='{v[:57]}…'"
                             for k, v in ein.items())
    roh = json.dumps({"eingabe": ein, "ergebnis": erg}, ensure_ascii=False, indent=2)
    if len(roh) > 4000:
        roh = roh[:4000] + "\n… (gekürzt)"
    return {"werkzeug": w, "titel": titel, "eingabe_kurz": eingabe_kurz, "zeilen": zeilen,
            "fehler": ereignis["fehler"], "blockiert": ereignis.get("blockiert", False), "roh": roh}


def sse_nachricht(ereignis_name: str, html: str, ereignis_id: int | None = None) -> str:
    """Server-Sent-Events-Format: optional 'id:', dann 'event:', dann je Zeile 'data:', Leerzeile am Ende."""
    teile = []
    if ereignis_id is not None:
        teile.append(f"id: {ereignis_id}")
    teile.append(f"event: {ereignis_name}")
    teile += [f"data: {zeile}" for zeile in (html.splitlines() or [""])]
    return "\n".join(teile) + "\n\n"
