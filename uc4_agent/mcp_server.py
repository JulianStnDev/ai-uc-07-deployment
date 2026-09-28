"""In-Process-MCP-Server "focusflow" für das Claude Agent SDK.

Dünne Hülle um werkzeuge.Werkzeugkasten: Jedes Werkzeug gibt das Ergebnis als
JSON-Text zurück, fachliche Fehler mit is_error=True. Im Agent heißen die
Werkzeuge mcp__focusflow__<name>.

    kasten = Werkzeugkasten(run_id="...")
    options = ClaudeAgentOptions(mcp_servers={"focusflow": baue_server(kasten)}, ...)
"""

import json

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server, tool

from .werkzeuge import Werkzeugkasten

SERVER_NAME = "focusflow"

SCHEMAS = {
    "kunde_nachschlagen": {
        "type": "object",
        "properties": {"suche": {"type": "string", "description": "Kunden-ID (z. B. K001), exakte E-Mail oder Namensteil"}},
        "required": ["suche"],
    },
    "zahlungen_ansehen": {
        "type": "object",
        "properties": {"kunden_id": {"type": "string"}},
        "required": ["kunden_id"],
    },
    "hilfe_durchsuchen": {
        "type": "object",
        "properties": {
            "anfrage": {"type": "string", "description": "Suchbegriffe, z. B. 'Erstattung Jahresabo'"},
            "max_treffer": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
        },
        "required": ["anfrage"],
    },
    "an_mensch_uebergeben": {
        "type": "object",
        "properties": {
            "grund": {"type": "string"},
            "kunden_id": {"type": "string"},
            "prioritaet": {"type": "string", "enum": ["normal", "hoch"], "default": "normal"},
        },
        "required": ["grund"],
    },
    "abo_kuendigen": {
        "type": "object",
        "properties": {"kunden_id": {"type": "string"}},
        "required": ["kunden_id"],
    },
    "erstattung_empfehlen": {
        "type": "object",
        "properties": {
            "kunden_id": {"type": "string"},
            "zahlungs_id": {"type": "string"},
            "betrag_usd": {"type": "number"},
            "begruendung": {"type": "string"},
        },
        "required": ["kunden_id", "zahlungs_id", "betrag_usd", "begruendung"],
    },
    "antwort_entwerfen": {
        "type": "object",
        "properties": {"text": {"type": "string"}, "kunden_id": {"type": "string"}},
        "required": ["text"],
    },
}

BESCHREIBUNGEN = {
    "kunde_nachschlagen": "Kundendaten (Abo-Stufe, Plattform, Login-Methode, Abo-Status) per Kunden-ID, E-Mail oder Name suchen. Kann mehrere Treffer liefern.",
    "zahlungen_ansehen": "Alle Zahlungen eines Kunden mit Datum, Betrag und Anbieter (stripe = Web, apple, google).",
    "hilfe_durchsuchen": "Volltextsuche in den FocusFlow-Hilfeartikeln. Liefert die passendsten Artikel mit Stand-Datum und vollem Text.",
    "an_mensch_uebergeben": "Fall an einen menschlichen Support-Mitarbeiter übergeben, mit Grund.",
    "abo_kuendigen": "Web-Abo (Stripe) zum Ende der bezahlten Periode kündigen. Store-Abos kann FocusFlow nicht kündigen.",
    "erstattung_empfehlen": "Erstattung EMPFEHLEN (Betrag + Begründung). Bucht nichts; ein Mensch muss freigeben.",
    "antwort_entwerfen": "Antwort an den Kunden als Entwurf speichern. Versendet nichts. Ein neuer Entwurf ersetzt den bisherigen.",
}


def _werkzeug(kasten: Werkzeugkasten, name: str) -> SdkMcpTool:
    @tool(name, BESCHREIBUNGEN[name], SCHEMAS[name])
    async def handler(args: dict) -> dict:
        ergebnis, fehler = kasten.aufrufen(name, dict(args))
        antwort = {"content": [{"type": "text", "text": json.dumps(ergebnis, ensure_ascii=False)}]}
        if fehler:
            antwort["is_error"] = True
        return antwort

    return handler


def baue_werkzeuge(kasten: Werkzeugkasten) -> list[SdkMcpTool]:
    return [_werkzeug(kasten, name) for name in Werkzeugkasten.NAMEN]


def baue_server(kasten: Werkzeugkasten):
    return create_sdk_mcp_server(name=SERVER_NAME, version="0.1.0", tools=baue_werkzeuge(kasten))


def erlaubte_werkzeuge() -> list[str]:
    return [f"mcp__{SERVER_NAME}__{name}" for name in Werkzeugkasten.NAMEN]
