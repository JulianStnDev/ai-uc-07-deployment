"""Info-Hinweise ("i") und Beispiel-Anliegen. Texte in einfacher Sprache, je höchstens zwei Sätze.
Englisch wie die ganze Besucher-Oberfläche; nur "fehlerquote" steht auf der Betriebsseite (Admin) und bleibt deutsch.

Die Hinweise erscheinen per Klick oder Tastatur (Knopf mit aria-expanded/aria-controls, siehe
static/info.js). Ein Hinweis wird im Template mit dem Makro `info(schluessel, label)` eingebunden.
"""

import re
import secrets

from markupsafe import Markup, escape

INFO = {
    "budget": "Every request to the AI model costs a few cents. The demo has a fixed monthly budget; once it is used up, it pauses until the next month.",
    "absender": "All customers are made up. The request always comes from the chosen customer's email address, so the agent can check who owns the account.",
    "werkzeug": "The agent works with tools, like a support employee with their software: look up an account, check payments, read help articles. Each item here is one such step.",
    "empfehlung": "The agent cannot pay out money, it can only recommend a refund. Money would only be paid once a human confirms the recommendation.",
    "entwurf": "The agent does not send anything itself. Its reply is a draft that a support employee checks before sending.",
    "uebergabe": "If a case is unclear or beyond its permissions, the agent hands it over to a human instead of guessing.",
    "kuendigung": "The agent may cancel a subscription on its own because that can be undone. Pro stays active until the end of the paid period.",
    "freigabe": "Here a human decides on the refunds the agent recommended. Nothing is booked in the demo, but every decision is recorded.",
    "schatten": "Shadow mode means: the agent recommends, a human decides. This shows how often the agent was right before trusting it with more.",
    "quote": "Share of recommendations a human confirmed. High values suggest letting the agent handle more of these cases on its own later.",
    "kosten": "What this one run cost at the AI provider.",
    "schritte": "How often the agent thought and then acted until the case was done.",
    "notiz": "Along the way, the agent briefly notes what it plans to do. This helps to follow its work; the customer does not see it.",
    "erinnerung": "If the agent forgets a duty, such as the reply draft, the program code reminds it. The number shows how often that was needed.",
    "zwischenbescheid": "A human decides on refunds. Until then, the customer only gets this acknowledgement, so nothing is promised that has not been decided.",
    "agententwurf": "This is what the agent replied. Because it may promise refunds that only a human can decide, this text does not go to the customer as is.",
    "endgueltig": "This reply is written only after the decision in the support console. It tells the customer the outcome and is checked again before sending.",
    "pruefung": "After every run, the program code checks fixed rules. For every fifth run, a second AI model also reviews the reply text.",
    "spekulation": "Does the reply state anything about the case that the agent never looked up? Then the text counts as speculative.",
    "zusage": "Does the text promise a refund that no human has decided on yet? That must not happen.",
    "fehlerquote": "Anteil der Durchläufe, die mit einem Fehler endeten oder abgebrochen wurden. Anliegen, die nie gestartet sind, zählen nicht mit.",
    "blockiert": "The agent may only use its seven support tools. The program code rejects anything else before it runs.",
    "kontobindung": "The agent may only read and act for the account of the person who wrote. The program code blocks any other account before the tool runs.",
    "zusagepruefung": "The reply draft promises money although the agent recommended no refund. The program code holds it back until a human has checked it.",
    "regelgrundlage": "The program code checks every recommendation against the refund policy (14 days for annual plans, double charges always, no store purchases) before it reaches you.",
    "sprache": "FocusFlow is a fictional German app, so customer conversations are in German. Everything around them is in English.",
}


def info(schluessel: str, label: str, rechts: bool = False) -> Markup:
    """HTML für einen Info-Hinweis. Die ID ist zufällig, damit sie auch in nachgeladenen
    Live-Fragmenten eindeutig bleibt."""
    text = INFO[schluessel]
    i = f"info-{schluessel}-{secrets.token_hex(3)}"
    return Markup(
        f'<span class="info{" rechts" if rechts else ""}" data-info="{schluessel}">'
        f'<button type="button" class="info-knopf" aria-expanded="false" aria-controls="{i}" '
        f'aria-label="Explanation: {escape(label)}">i</button>'
        f'<span class="info-text" id="{i}" role="note" hidden>{escape(text)}</span></span>')


# Kurze Labels für die Beispiel-Chips (UC4-Testset). Sie dienen auch als Titel des Anliegens.
BEISPIEL_LABELS = {
    "T01": "Charged twice for annual plan", "T02": "6.99 charged twice", "T03": "Return annual plan",
    "T04": "Forgot to cancel renewal", "T05": "Cancel with prorated refund", "T06": "Refund an iPhone purchase",
    "T07": "Two accounts, paid twice", "T08": "Cancel subscription", "T09": "Cancellation not received?",
    "T10": "Cancel on Android", "T11": "Return Android annual plan", "T12": "How much is Pro?",
    "T13": "Pro not activated", "T14": "Cancel someone else's account", "T15": "Refund and cancel",
}

TITEL_MAX_ZEICHEN = 60


def titel_fuer(text: str, beispiel_id: str | None, beispiele: dict[str, str]) -> str:
    """Titel eines Anliegens ohne LLM: das Chip-Label, wenn der Text noch genau dem Beispiel
    entspricht, sonst die ersten Wörter des Textes."""
    if beispiel_id in BEISPIEL_LABELS and beispiele.get(beispiel_id, "").strip() == text.strip():
        return BEISPIEL_LABELS[beispiel_id]
    titel, gekuerzt = "", False
    for wort in re.sub(r"\s+", " ", text).strip().split(" "):
        if len(titel) + len(wort) + 1 > TITEL_MAX_ZEICHEN:
            gekuerzt = True
            break
        titel = f"{titel} {wort}".strip()
    if not titel:  # ein einziges sehr langes Wort
        titel = text.strip()[:TITEL_MAX_ZEICHEN]
    if gekuerzt:
        titel = titel.rstrip(",.;:!?") + " …"
    return titel[:1].upper() + titel[1:]
