"""Agent-Konfiguration und PreToolUse-Hook – ohne LLM-Aufruf."""

import asyncio
import json

import pytest

from uc4_agent import agent
from uc4_agent.werkzeuge import Werkzeugkasten


def test_hook_erlaubt_nur_focusflow(tmp_path):
    k = Werkzeugkasten(run_id="h", runs_dir=tmp_path)
    hook = agent.nur_focusflow_hook(k)
    ok = asyncio.run(hook({"tool_name": "mcp__focusflow__kunde_nachschlagen", "tool_input": {}}, "id1", None))
    assert ok["hookSpecificOutput"]["permissionDecision"] == "allow"
    for name in ["Bash", "Read", "mcp__andere__x", "mcp__focusflowfake__x"]:
        nein = asyncio.run(hook({"tool_name": name, "tool_input": {"a": 1}}, "id2", None))
        assert nein["hookSpecificOutput"]["permissionDecision"] == "deny"
    log = [json.loads(z) for z in (k.run_dir / "trajektorie.jsonl").read_text().splitlines()]
    assert [e["werkzeug"] for e in log] == ["Bash", "Read", "mcp__andere__x", "mcp__focusflowfake__x"]
    assert all(e["blockiert"] and e["fehler"] for e in log)


def test_prompt_enthaelt_keine_falldetails():
    for t, system in [(t, p) for t in agent.lade_aufgaben() for p in agent.SYSTEM_PROMPTS.values()]:
        prompt = system + agent.ticket_prompt(t)
        for verboten in ["DATA_NOTES", t["kernaussage_entwurf"], t["kunde_id"], "Schattenmodus"]:
            assert verboten not in prompt


def test_modell_und_budget_fest():
    assert agent.MODELL == "claude-haiku-4-5"
    assert 0 < agent.MAX_BUDGET_USD <= 1


def test_abbruch_ohne_api_key(monkeypatch):
    monkeypatch.setattr(agent, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        agent.api_key_pruefen()


def test_v2_unterscheidet_sich_nur_in_den_regeln():
    v1, v2 = agent.SYSTEM_PROMPT_V1, agent.SYSTEM_PROMPT_V2
    assert v1.split("## Vorgehen")[0] == v2.split("## Vorgehen")[0]  # Rolle + Matrix identisch
    assert "Vermute niemals" in v2 and "Vermute niemals" not in v1
    assert "anderes Konto" in v2 and "weder Kundendaten noch Hilfe" in v2


def test_v3_ist_v1_plus_zwei_regeln():
    v1, v3 = agent.SYSTEM_PROMPT_V1, agent.SYSTEM_PROMPT_V3
    zeilen = lambda p: [z for z in p.splitlines() if z.strip()]
    neu = [z for z in zeilen(v3) if z not in zeilen(v1)]
    assert len(neu) == 3  # zwei neue Regeln + umnummerierte Entwurfsregel (5. -> 7.)
    assert any("anderes Konto" in z for z in neu) and any("Vermute niemals" in z for z in neu)
    assert "weder Kundendaten noch Hilfe" not in v3  # T02-Regel aus v2 entfällt


def test_stop_hook_blockiert_bis_pflichten_erfuellt(tmp_path):
    k = Werkzeugkasten(run_id="s", runs_dir=tmp_path)
    eingriffe = []
    hook = agent.pflicht_stop_hook(k, eingriffe)
    r = asyncio.run(hook({"hook_event_name": "Stop", "stop_hook_active": False}, None, None))
    assert r["decision"] == "block" and "kunde_nachschlagen" in r["reason"] and "antwort_entwerfen" in r["reason"]
    k.aufrufen("an_mensch_uebergeben", {"grund": "fremdes Konto"})
    k.aufrufen("kunde_nachschlagen", {"suche": "K006"})
    r = asyncio.run(hook({"hook_event_name": "Stop", "stop_hook_active": True}, None, None))
    assert r["decision"] == "block" and "kunde_nachschlagen" not in r["reason"]
    k.aufrufen("antwort_entwerfen", {"text": "Hallo Felix"})
    assert asyncio.run(hook({"hook_event_name": "Stop", "stop_hook_active": True}, None, None)) == {}
    assert [e["fehlt"] for e in eingriffe] == [["kunde_nachschlagen", "antwort_entwerfen"], ["antwort_entwerfen"]]
