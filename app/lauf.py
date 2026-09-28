"""Ein Ticket aus der Web-App = ein Lauf des UC4-Agents.

Ablauf:
1. POST /lauf legt den Lauf im Speicher an und startet `lauf_ausfuehren` als asyncio-Task.
2. Der Task ruft den Agent mit exakt der UC4-Konfiguration auf (agent.baue_optionen, Prompt v3).
3. Jeder Werkzeugaufruf und jeder Text des Agents wird als Ereignis an einen `Beobachter` gemeldet.
4. Die Seite /lauf/{id} hört per SSE zu (main.py) und bekommt die Ereignisse live.
5. Am Ende landen Kosten, Ergebnis und Empfehlungen im Speicher.

Der Lauf hängt nicht an der Browser-Verbindung: Schließt jemand den Tab, läuft er zu Ende und
wird trotzdem protokolliert und verbucht.
"""

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import AsyncIterator, Callable

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, query

from uc4_agent import agent
from uc4_agent.werkzeuge import Werkzeugkasten

from .speicher import Speicher

log = logging.getLogger("uc7.lauf")

PROMPT_VERSION = "v3"


class Beobachter:
    """Sammelt die Ereignisse eines Laufs. Beliebig viele SSE-Verbindungen können mitlesen,
    auch nachträglich (sie bekommen dann alle bisherigen Ereignisse zuerst)."""

    def __init__(self) -> None:
        self.ereignisse: list[dict] = []
        self.fertig = False
        self._signal = asyncio.Event()

    def melden(self, ereignis: dict) -> None:
        self.ereignisse.append(ereignis)
        self._wecken()

    def abschliessen(self) -> None:
        self.fertig = True
        self._wecken()

    def _wecken(self) -> None:
        # Alle Wartenden aufwecken und für die nächste Runde ein frisches Signal bereitlegen.
        self._signal.set()
        self._signal = asyncio.Event()

    def signal(self) -> asyncio.Event:
        """Das Signal VOR dem Prüfen auf neue Ereignisse holen, sonst kann eine Meldung dazwischen verloren gehen."""
        return self._signal

    async def warten(self, timeout: float, signal: asyncio.Event | None = None) -> bool:
        """Wartet auf ein neues Ereignis. False bei Zeitablauf (dann schickt SSE einen Ping)."""
        signal = signal or self._signal
        try:
            await asyncio.wait_for(signal.wait(), timeout)
            return True
        except TimeoutError:
            return False


# run_id -> Beobachter. Nur für laufende und gerade beendete Läufe; alles Dauerhafte steht im Speicher.
BEOBACHTER: dict[str, Beobachter] = {}


class WebKasten(Werkzeugkasten):
    """Der UC4-Werkzeugkasten, unverändert, plus Meldung jedes Aufrufs an den Beobachter."""

    def __init__(self, *args, melden: Callable[[dict], None], **kwargs):
        super().__init__(*args, **kwargs)
        self._melden = melden
        self.empfehlungen: list[dict] = []
        self.uebergaben: list[dict] = []

    def aufrufen(self, werkzeug: str, eingabe: dict) -> tuple[dict, bool]:
        ergebnis, fehler = super().aufrufen(werkzeug, eingabe)
        if not fehler and werkzeug == "erstattung_empfehlen":
            self.empfehlungen.append(ergebnis)
        if not fehler and werkzeug == "an_mensch_uebergeben":
            self.uebergaben.append(ergebnis)
        self._melden({"art": "werkzeug", "werkzeug": werkzeug, "eingabe": eingabe,
                      "ergebnis": ergebnis, "fehler": fehler, "blockiert": False})
        return ergebnis, fehler

    def blockiert_protokollieren(self, werkzeug: str, eingabe: dict, grund: str) -> None:
        super().blockiert_protokollieren(werkzeug, eingabe, grund)
        self._melden({"art": "werkzeug", "werkzeug": werkzeug, "eingabe": eingabe,
                      "ergebnis": {"fehler": grund}, "fehler": True, "blockiert": True})


# Signatur wie claude_agent_sdk.query, zusätzlich der Werkzeugkasten (für Tests ohne LLM).
QueryFn = Callable[[str, object, Werkzeugkasten], AsyncIterator]


def _sdk_query(prompt: str, options, kasten: Werkzeugkasten) -> AsyncIterator:
    return query(prompt=prompt, options=options)


def api_key() -> str:
    """Wie agent.api_key_pruefen, aber ohne sys.exit: In der Web-App wird ein fehlender Key
    zum Lauf-Fehler, statt den Server zu beenden."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY fehlt. Ohne Key würde das Agent SDK über das Claude-Abo abrechnen.")
    return key


async def lauf_ausfuehren(run_id: str, absender: str, text: str, speicher: Speicher, lauf_dir: Path,
                          beobachter: Beobachter, query_fn: QueryFn = _sdk_query) -> None:
    start = time.perf_counter()
    kasten = WebKasten(run_id=run_id, runs_dir=lauf_dir, melden=beobachter.melden)
    eingriffe: list = []
    ergebnis_msg: ResultMessage | None = None
    status, fehlertext = "fehler", None
    try:
        options = agent.baue_optionen(kasten, api_key(), eingriffe, PROMPT_VERSION)
        prompt = agent.ticket_prompt({"absender": absender, "text": text})
        async for msg in query_fn(prompt, options, kasten):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        beobachter.melden({"art": "text", "text": block.text.strip()})
            elif isinstance(msg, ResultMessage):
                ergebnis_msg = msg
        status = "fertig"
    except asyncio.CancelledError:  # Server fährt herunter
        status, fehlertext = "abgebrochen", "Der Lauf wurde abgebrochen, weil der Server beendet wurde."
        raise
    except Exception as e:  # noqa: BLE001 – jeder Fehler soll protokolliert und angezeigt werden
        log.exception("Lauf %s fehlgeschlagen", run_id)
        fehlertext = f"Der Lauf ist mit einem Fehler abgebrochen ({type(e).__name__})."
    finally:
        dauer_s = round(time.perf_counter() - start, 1)
        kosten = ergebnis_msg.total_cost_usd if ergebnis_msg else None
        kosten_pauschal = kosten is None
        if kosten_pauschal:  # keine Kostenangabe: Deckel pro Lauf verbuchen, lieber zu hoch als zu niedrig
            kosten = agent.MAX_BUDGET_USD
        ergebnis = {
            "status": status,
            "fehlertext": fehlertext,
            "subtype": ergebnis_msg.subtype if ergebnis_msg else None,
            "num_turns": ergebnis_msg.num_turns if ergebnis_msg else None,
            "schlusstext": ergebnis_msg.result if ergebnis_msg else None,
            "entwurf": kasten.entwurf["text"] if kasten.entwurf else None,
            "empfehlungen": kasten.empfehlungen,
            "uebergaben": kasten.uebergaben,
            "kuendigungen": list(kasten.kuendigungen.values()),
            "eingriffe": len(eingriffe),
            "pflichten_offen": kasten.fehlende_pflichten(),
            "kosten_usd": round(kosten, 4),
            "kosten_pauschal": kosten_pauschal,
            "dauer_s": dauer_s,
        }
        speicher.lauf_abschliessen(run_id, status=status, kosten_usd=kosten, kosten_pauschal=kosten_pauschal,
                                   dauer_s=dauer_s, ergebnis=ergebnis, ereignisse=list(beobachter.ereignisse),
                                   empfehlungen=kasten.empfehlungen)
        beobachter.melden({"art": "ergebnis", "ergebnis": ergebnis})
        beobachter.abschliessen()
