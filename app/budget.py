"""Monatsdeckel für die API-Kosten (siehe docs/decisions.md, 2026-09-28).

Gerechnet wird: Kosten aller abgeschlossenen Läufe im Kalendermonat (UTC)
               + Reserve je laufendem Lauf (= Deckel pro Lauf, 0,50 USD).
Ein neuer Lauf startet nur, wenn das unter dem Monatsdeckel (4,50 USD) liegt. Weil ein Lauf
höchstens 0,50 USD kostet, bleibt der Monat damit unter 5,00 USD.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from uc4_agent.agent import MAX_BUDGET_USD

from .speicher import Speicher

RESERVE_JE_LAUF_USD = MAX_BUDGET_USD


@dataclass(frozen=True)
class BudgetStand:
    monat: str
    verbraucht_usd: float
    reserviert_usd: float
    deckel_usd: float

    @property
    def belegt_usd(self) -> float:
        return self.verbraucht_usd + self.reserviert_usd

    @property
    def gesperrt(self) -> bool:
        return self.belegt_usd >= self.deckel_usd

    @property
    def naechster_monat(self) -> str:
        jahr, monat = map(int, self.monat.split("-"))
        return f"01.{monat % 12 + 1:02d}.{jahr + (monat == 12)}"


def aktueller_monat(jetzt: datetime | None = None) -> str:
    return (jetzt or datetime.now(timezone.utc)).strftime("%Y-%m")


def stand(speicher: Speicher, deckel_usd: float, jetzt: datetime | None = None) -> BudgetStand:
    monat = aktueller_monat(jetzt)
    return BudgetStand(
        monat=monat,
        verbraucht_usd=speicher.kosten_im_monat(monat),
        reserviert_usd=speicher.laufende_im_monat(monat) * RESERVE_JE_LAUF_USD,
        deckel_usd=deckel_usd,
    )
