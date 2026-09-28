"""Die SDK-Hülle: Werkzeugnamen, Schemas, Ergebnisformat – ohne LLM."""

import asyncio
import json

from claude_agent_sdk import SdkMcpTool

from conftest import lese_jsonl
from uc4_agent.mcp_server import baue_server, baue_werkzeuge, erlaubte_werkzeuge
from uc4_agent.werkzeuge import Werkzeugkasten


def test_sieben_werkzeuge_ohne_ausloesen(kasten):
    werkzeuge = baue_werkzeuge(kasten)
    assert all(isinstance(w, SdkMcpTool) for w in werkzeuge)
    namen = {w.name for w in werkzeuge}
    assert namen == set(Werkzeugkasten.NAMEN) and len(namen) == 7
    assert not any("ausloesen" in n or "senden" in n for n in namen)


def test_pflichtfelder_im_schema_passen_zur_funktion(kasten):
    import inspect
    for w in baue_werkzeuge(kasten):
        parameter = inspect.signature(getattr(kasten, w.name)).parameters
        pflicht = {n for n, p in parameter.items() if p.default is inspect.Parameter.empty}
        assert set(w.input_schema["required"]) == pflicht
        assert set(w.input_schema["properties"]) == set(parameter)


def test_erlaubte_werkzeugnamen():
    assert erlaubte_werkzeuge()[0] == "mcp__focusflow__kunde_nachschlagen"
    assert len(erlaubte_werkzeuge()) == 7


def test_server_objekt(kasten):
    server = baue_server(kasten)
    assert server["type"] == "sdk" and server["name"] == "focusflow"


def rufe(kasten, name, args):
    w = next(w for w in baue_werkzeuge(kasten) if w.name == name)
    return asyncio.run(w.handler(args))


def test_handler_liefert_json_text(kasten):
    antwort = rufe(kasten, "zahlungen_ansehen", {"kunden_id": "K002"})
    assert "is_error" not in antwort
    daten = json.loads(antwort["content"][0]["text"])
    assert len(daten["zahlungen"]) == 5


def test_handler_markiert_fehler_und_protokolliert(kasten):
    antwort = rufe(kasten, "erstattung_empfehlen",
                   {"kunden_id": "K005", "zahlungs_id": "Z013", "betrag_usd": 59, "begruendung": "x"})
    assert antwort["is_error"] is True
    (eintrag,) = lese_jsonl(kasten.run_dir / "trajektorie.jsonl")
    assert eintrag["werkzeug"] == "erstattung_empfehlen" and eintrag["fehler"] is True
