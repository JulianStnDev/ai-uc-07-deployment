"""Info-Hinweise ("i") und Beispiel-Anliegen. Texte in einfacher Sprache, je höchstens zwei Sätze.

Die Hinweise erscheinen per Klick oder Tastatur (Knopf mit aria-expanded/aria-controls, siehe
static/info.js). Ein Hinweis wird im Template mit dem Makro `info(schluessel, label)` eingebunden.
"""

import re
import secrets

from markupsafe import Markup, escape

INFO = {
    "budget": "Jede Anfrage an das KI-Modell kostet ein paar Cent. Die Demo hat ein festes Monatsbudget, ist es aufgebraucht, macht sie bis zum Monatsanfang Pause.",
    "absender": "Alle Kunden sind erfunden. Das Anliegen kommt immer von der E-Mail-Adresse des gewählten Kunden, so kann der Agent prüfen, wem das Konto gehört.",
    "werkzeug": "Der Agent arbeitet mit Werkzeugen, wie ein Mitarbeiter mit seinen Programmen: Konto nachschlagen, Zahlungen ansehen, Hilfe lesen. Jeder Punkt hier ist ein solcher Schritt.",
    "empfehlung": "Der Agent darf kein Geld zurückzahlen, er kann eine Erstattung nur empfehlen. Ausgezahlt würde erst, wenn ein Mensch die Empfehlung bestätigt.",
    "entwurf": "Der Agent verschickt nichts selbst. Seine Antwort ist ein Entwurf, den ein Mitarbeiter prüft und erst dann abschickt.",
    "uebergabe": "Ist ein Fall unklar oder liegt er außerhalb seiner Befugnisse, gibt der Agent ihn an einen Menschen ab, statt zu raten.",
    "kuendigung": "Ein Abo kündigen darf der Agent allein, weil das umkehrbar ist. Pro läuft bis zum Ende der bezahlten Zeit weiter.",
    "freigabe": "Hier entscheidet ein Mensch über die Erstattungen, die der Agent empfohlen hat. In der Demo wird nichts gebucht, aber jede Entscheidung wird festgehalten.",
    "schatten": "Schattenmodus heißt: Der Agent empfiehlt, ein Mensch entscheidet. So lässt sich messen, wie oft der Agent richtig lag, bevor man ihm mehr zutraut.",
    "quote": "Anteil der Empfehlungen, die ein Mensch bestätigt hat. Hohe Werte sprechen dafür, dem Agent hier später mehr selbst zu überlassen.",
    "kosten": "Was dieser eine Durchlauf beim KI-Anbieter gekostet hat.",
    "schritte": "Wie oft der Agent nachgedacht und dann gehandelt hat, bis der Fall erledigt war.",
    "notiz": "Zwischendurch schreibt der Agent kurz auf, was er vorhat. Das hilft beim Nachvollziehen, der Kunde sieht es nicht.",
    "erinnerung": "Vergisst der Agent eine Pflicht, etwa den Antwortentwurf, erinnert ihn der Programmcode daran. Die Zahl zeigt, wie oft das nötig war.",
    "blockiert": "Der Agent darf nur seine sieben Support-Werkzeuge benutzen. Alles andere lehnt der Programmcode ab, bevor es ausgeführt wird.",
}


def info(schluessel: str, label: str, rechts: bool = False) -> Markup:
    """HTML für einen Info-Hinweis. Die ID ist zufällig, damit sie auch in nachgeladenen
    Live-Fragmenten eindeutig bleibt."""
    text = INFO[schluessel]
    i = f"info-{schluessel}-{secrets.token_hex(3)}"
    return Markup(
        f'<span class="info{" rechts" if rechts else ""}" data-info="{schluessel}">'
        f'<button type="button" class="info-knopf" aria-expanded="false" aria-controls="{i}" '
        f'aria-label="Erklärung: {escape(label)}">i</button>'
        f'<span class="info-text" id="{i}" role="note" hidden>{escape(text)}</span></span>')


# Kurze Labels für die Beispiel-Chips (UC4-Testset). Sie dienen auch als Titel des Anliegens.
BEISPIEL_LABELS = {
    "T01": "Doppelt abgebucht beim Jahresabo", "T02": "6,99 zweimal abgebucht", "T03": "Jahresabo zurückgeben",
    "T04": "Verlängerung vergessen zu kündigen", "T05": "Kündigen und anteilig erstatten", "T06": "iPhone-Kauf zurückgeben",
    "T07": "Zwei Konten, doppelt bezahlt", "T08": "Abo kündigen", "T09": "Kündigung nicht angekommen?",
    "T10": "Kündigen auf Android", "T11": "Android-Jahresabo zurückgeben", "T12": "Was kostet Pro?",
    "T13": "Pro nicht aktiviert", "T14": "Anderes Konto kündigen", "T15": "Geld zurück und kündigen",
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
