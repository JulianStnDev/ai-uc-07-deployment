"""Erzeugt die statischen UX-Entwürfe (nur Entwurf, nicht Teil der App)."""
from pathlib import Path
from itertools import count

HIER = Path(__file__).parent
_n = count(1)

INFO = {
 "budget": "Jede Anfrage an das KI-Modell kostet ein paar Cent. Die Demo hat ein festes Monatsbudget. Ist es aufgebraucht, macht sie bis zum Monatsanfang Pause.",
 "absender": "Alle Kunden sind erfunden. Das Anliegen kommt immer von der E-Mail-Adresse des gewählten Kunden. So kann der Agent prüfen, wem das Konto gehört.",
 "werkzeug": "Der Agent arbeitet mit Werkzeugen, wie ein Mitarbeiter mit seinen Programmen: Konto nachschlagen, Zahlungen ansehen, Hilfe lesen. Jeder Punkt hier ist ein solcher Schritt.",
 "empfehlung": "Der Agent darf kein Geld zurückzahlen. Er kann eine Erstattung nur empfehlen. Ausgezahlt würde erst, wenn ein Mensch die Empfehlung bestätigt.",
 "entwurf": "Der Agent verschickt nichts selbst. Seine Antwort ist ein Entwurf, den ein Mitarbeiter prüft und erst dann abschickt.",
 "uebergabe": "Ist ein Fall unklar oder liegt er außerhalb seiner Befugnisse, gibt der Agent ihn an einen Menschen ab, statt zu raten.",
 "freigabe": "Hier entscheidet ein Mensch über die Erstattungen, die der Agent empfohlen hat. In der Demo wird nichts gebucht, aber jede Entscheidung wird festgehalten.",
 "schatten": "Schattenmodus heißt: Der Agent empfiehlt, ein Mensch entscheidet. So lässt sich messen, wie oft der Agent richtig lag, bevor man ihm mehr zutraut.",
 "quote": "Anteil der Empfehlungen, die ein Mensch bestätigt hat. Hohe Werte sprechen dafür, dem Agent hier später mehr selbst zu überlassen.",
 "kosten": "Was dieser eine Durchlauf beim KI-Anbieter gekostet hat.",
 "schritte": "Wie oft der Agent nachgedacht und dann gehandelt hat, bis der Fall erledigt war.",
 "notiz": "Zwischendurch schreibt der Agent kurz auf, was er vorhat. Das hilft beim Nachvollziehen, der Kunde sieht es nicht.",
}

def info(schluessel, label, rechts=False):
    i = next(_n)
    return (f'<span class="info{" rechts" if rechts else ""}"><button type="button" class="info-knopf" aria-expanded="false" '
            f'aria-controls="info-{i}" aria-label="Erklärung: {label}">i</button>'
            f'<span class="info-text" id="info-{i}" role="note" hidden>{INFO[schluessel]}</span></span>')

def kopf(aktiv, offene=1, budget="0,03", prozent=1):
    def a(href, text, key):
        cur = ' aria-current="page"' if key == aktiv else ""
        return f'<a href="{href}"{cur}>{text}</a>'
    return f"""<header class="kopf"><div class="kopf-innen">
  <a class="logo" href="start.html"><span class="logo-zeichen" aria-hidden="true">F</span>FocusFlow <small>Support-Demo</small></a>
  <nav class="navi" aria-label="Hauptnavigation">
    {a("start.html","Start","start")}{a("ticket-neu.html","Anliegen stellen","ticket")}{a("konsole.html",f'Support-Konsole <span class="zaehler" aria-label="{offene} offen">{offene}</span>',"konsole")}
  </nav>
  <span class="budget">Budget {budget} / 4,50 USD <span class="budget-balken" aria-hidden="true"><span style="width:{prozent}%"></span></span>{info("budget","Budget",True)}</span>
  <button class="abmelden">Abmelden</button>
</div></header>"""

def seite(titel, aktiv, inhalt, schmal=False, **kw):
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titel} · FocusFlow Support-Demo</title><link rel="stylesheet" href="portal.css"></head><body>
{kopf(aktiv, **kw)}
<main class="seite{' schmal' if schmal else ''}">{inhalt}</main>
<footer class="fuss">Demo mit erfundenen Kundendaten und einem echten KI-Modell (Claude Haiku 4.5). Es wird nichts versendet und nichts gebucht.</footer>
<script src="info.js"></script></body></html>"""

START = f"""
<section class="held">
  <p class="ueberzeile">So funktioniert diese Demo</p>
  <h1>Ein KI-Agent im Kundensupport, zum Ausprobieren</h1>
  <p>FocusFlow ist eine erfundene Habit-Tracker-App. Ihr Support wird von einem KI-Agent unterstützt, der Konten nachschlägt, Regeln liest und Antworten vorbereitet. Was er darf und was nicht, ist fest geregelt.</p>
</section>
<ol class="schritte3" aria-label="Drei Schritte">
  <li class="karte schritt3" style="list-style:none"><div class="nr" aria-hidden="true">1</div><h3>Du schreibst als Kunde</h3><p>Wähle einen erfundenen Kunden und beschreibe ein Anliegen, etwa eine doppelte Abbuchung oder eine Kündigung.</p></li>
  <li class="karte schritt3" style="list-style:none"><div class="nr" aria-hidden="true">2</div><h3>Der Agent arbeitet</h3><p>Du siehst live, welche Schritte er macht: Konto prüfen, Zahlungen ansehen, Hilfe lesen. Nach etwa 30 Sekunden steht ein Antwortentwurf.</p></li>
  <li class="karte schritt3" style="list-style:none"><div class="nr" aria-hidden="true">3</div><h3>Ein Mensch entscheidet</h3><p>Geld zurückzahlen darf der Agent nicht. Er empfiehlt, und in der Support-Konsole bestätigt oder lehnt ein Mensch ab.</p></li>
</ol>
<div class="wege">
  <a class="karte weg" href="ticket-neu.html"><span class="symbol" aria-hidden="true">✉️</span><h2>Als Kunde ein Anliegen stellen</h2><p>Schreib dem Support und schau dem Agent dabei über die Schulter.</p><span class="los">Anliegen stellen →</span></a>
  <a class="karte weg" href="konsole.html"><span class="symbol" aria-hidden="true">🧑‍💼</span><h2>Support-Konsole (Freigaben)</h2><p>Sieh dir als Mitarbeiter an, welche Erstattungen der Agent empfohlen hat, und entscheide selbst.</p><span class="los">Zur Konsole · 1 offen →</span></a>
</div>
"""

BEISPIELE = ["Doppelt abgebucht beim Jahresabo", "6,99 zweimal abgebucht", "Abo kündigen", "Login mit Google klappt nicht",
             "Erstattung nach 20 Tagen?", "Konto für meine Frau kündigen", "Streak verloren"]

def kunde_select():
    kunden = [("K001","Anna Berger","anna.berger@example.com","Pro jährlich · Web"),("K002","Ben Hoffmann","ben.hoffmann@example.com","Pro monatlich · Web"),
              ("K003","Clara Neumann","clara.neumann@gmail.com","Pro monatlich · iPhone")]
    opts = "".join(f'<option value="{k}"{" selected" if k=="K001" else ""}>{n} · {e} · {t}</option>' for k,n,e,t in kunden)
    return f'<select id="kunde" name="kunden_id">{opts}<option>… 12 weitere</option></select>'

def ticket_neu():
    chips = "".join(f'<button type="button" class="chip">{b}</button>' for b in BEISPIELE)
    return f"""
<div class="kopfzeile-seite"><div><p class="ueberzeile">Anliegen stellen</p><h1>Wie können wir helfen?</h1></div></div>
<div class="geteilt">
  <section class="karte portal" aria-labelledby="portal-titel">
    <div class="spalte-kopf"><span class="ueberzeile" id="portal-titel">Kundenportal</span><span class="sehr-leise">so sieht es der Kunde</span></div>
    <div class="feld"><label for="kunde">Du schreibst als {info("absender","Absender")}</label>{kunde_select()}</div>
    <div class="feld"><span class="feld-titel">Beispiele zum Ausprobieren</span><div class="chips">{chips}</div></div>
    <div class="feld"><label for="text">Dein Anliegen</label>
      <textarea id="text" placeholder="Beschreibe kurz, worum es geht …">Hallo, beim Wechsel aufs Jahresabo am 14.09. wurden mir 54,34 $ ZWEI MAL abgebucht. Beide Buchungen sind nach über einer Woche immer noch auf meinem Konto, also keine Vormerkung. Bitte erstattet mir das doppelte Geld.</textarea>
      <div class="feld-hinweis"><span>Höchstens 1000 Zeichen</span><span>217 / 1000</span></div></div>
    <button class="knopf breit">Anliegen senden</button>
    <p class="sehr-leise" style="margin-top:12px;text-align:center">Dauert etwa 30 Sekunden und kostet rund 3 Cent.</p>
  </section>
  <section class="karte kulisse" aria-labelledby="kulisse-titel">
    <div class="spalte-kopf"><span class="ueberzeile" id="kulisse-titel">Hinter den Kulissen</span>{info("werkzeug","Schritte des Agents",True)}</div>
    <div class="leer-zustand"><div class="gross" aria-hidden="true">🔍</div><p><strong>Hier siehst du gleich, was der Agent tut.</strong></p>
      <p style="margin-top:6px">Jeder Schritt erscheint live: welches Konto er prüft, welche Regeln er liest und was er am Ende vorschlägt.</p></div>
  </section>
</div>"""

def ereignis(klasse, symbol, titel, text, roh=None, extra=""):
    roh_html = f'<details class="roh"><summary>Rohdaten</summary><pre>{roh}</pre></details>' if roh else ""
    return f"""<li class="ereignis {klasse}"><span class="punkt" aria-hidden="true">{symbol}</span>
  <div class="ereignis-titel">{titel}{extra}</div><div class="ereignis-text">{text}</div>{roh_html}</li>"""

ENTWURF = """Hallo Anna,

danke, dass du dich so schnell bei uns gemeldet hast! Du hast völlig recht – beim Wechsel auf das Jahresabo ist dir am 14.09. derselbe Betrag zweimal abgebucht worden. Das ist leider ein bekanntes Problem, das wir gerade beheben.

Ich habe deine doppelte Belastung überprüft und den Fall zur Erstattung weitergeleitet. Der Betrag von 54,34 USD wird dir vollständig auf dein Zahlungsmittel zurückerstattet. Dein Pro-Abo bleibt aktiv und wird nicht unterbrochen.

Die Erstattung ist in der Regel innerhalb von 5–10 Werktagen auf deinem Konto sichtbar. Sollte bis dahin nichts ankommen, schreib uns gerne erneut.

Vielen Dank für deine Geduld!

Liebe Grüße
FocusFlow Support"""

def ticket_ergebnis():
    zeit = "".join([
      ereignis("notiz","…","Plant das Vorgehen",'<span class="gedanke">„Ich schlage den Kunden nach, prüfe die Zahlungen und lese die passenden Hilferegeln."</span>', extra=info("notiz","Notiz des Agents")),
      ereignis("fertig","✓","Sucht das Kundenkonto","Konto gefunden: <strong>Anna Berger</strong> · Pro jährlich · aktiv · bezahlt im Web",
               '{"suche": "anna.berger@example.com"}\n→ {"treffer": [{"kunden_id": "K001", …}]}'),
      ereignis("fertig","✓","Liest die Hilfe-Artikel","3 passende Artikel: Erstattungsrichtlinie, Doppelte Abbuchung, Zwischen monatlich und jährlich wechseln",'{"anfrage": "Jahresabo doppelte Zahlung Erstattung"}'),
      ereignis("fertig","✓","Prüft die Zahlungen","5 Zahlungen gefunden, darunter <strong>zweimal 54,34 USD am 14.09.2026</strong> (Z004, Z005)",'{"kunden_id": "K001"}\n→ {"zahlungen": [ … 5 Einträge … ]}'),
      ereignis("warnung","!","Empfiehlt eine Erstattung",f'54,34 USD für Zahlung Z005 · <span class="badge offen">wartet auf Freigabe</span>','{"zahlungs_id": "Z005", "betrag_usd": 54.34, …}', extra=info("empfehlung","Empfehlung statt Erstattung")),
      ereignis("fertig","✓","Schreibt den Antwortentwurf","Entwurf gespeichert, nicht versendet",None, extra=info("entwurf","Antwortentwurf")),
      ereignis("fertig","✓","Fertig",f'<dl class="kennzahlen"><div class="kennzahl"><dt>Dauer</dt><dd>36,5 s</dd></div><div class="kennzahl"><dt>Kosten {info("kosten","Kosten")}</dt><dd>0,03 USD</dd></div><div class="kennzahl"><dt>Schritte {info("schritte","Schritte",True)}</dt><dd>6</dd></div></dl>'),
    ])
    return f"""
<div class="kopfzeile-seite"><div><p class="ueberzeile">Anliegen · 28.09.2026, 13:10</p><h1>Doppelte Abbuchung beim Jahresabo</h1></div>
  <a class="knopf zweit" href="ticket-neu.html">Neues Anliegen</a></div>
<div class="geteilt">
  <section class="karte portal" aria-labelledby="portal-titel">
    <div class="spalte-kopf"><span class="ueberzeile" id="portal-titel">Kundenportal</span><span class="sehr-leise">so sieht es der Kunde</span></div>
    <a class="sprung nur-handy" href="#kulisse-titel">↓ Hinter den Kulissen: 6 Schritte des Agents</a>
    <div class="kunde-zeile"><span class="avatar" aria-hidden="true">AB</span><div><strong>Anna Berger</strong><div class="sehr-leise">anna.berger@example.com · Pro jährlich</div></div>{info("absender","Absender",True)}</div>
    <div class="nachricht-kopf" style="justify-content:flex-end">Du · 13:10</div>
    <div class="nachricht kunde">Hallo, beim Wechsel aufs Jahresabo am 14.09. wurden mir 54,34 $ ZWEI MAL abgebucht. Beide Buchungen sind nach über einer Woche immer noch auf meinem Konto, also keine Vormerkung. Bitte erstattet mir das doppelte Geld.</div>
    <div class="nachricht-kopf"><span class="avatar" style="width:22px;height:22px;font-size:.65rem" aria-hidden="true">F</span>FocusFlow Support · Antwortentwurf {info("entwurf","Antwortentwurf")}</div>
    <div class="entwurf-hinweis" role="note"><span aria-hidden="true">✎</span><span><strong>Entwurf.</strong> Wird vor dem Versand von einem Menschen geprüft.</span></div>
    <div class="nachricht support">{ENTWURF}</div>
  </section>
  <section class="karte kulisse" aria-labelledby="kulisse-titel">
    <div class="spalte-kopf"><span class="ueberzeile" id="kulisse-titel">Hinter den Kulissen</span><span class="badge bestaetigt">fertig</span>{info("werkzeug","Schritte des Agents",True)}</div>
    <ol class="zeitleiste">{zeit}</ol>
    <p class="sehr-leise"><a href="konsole.html">Die Empfehlung liegt jetzt in der Support-Konsole →</a></p>
  </section>
</div>"""

def konsole():
    offen = f"""
<article class="karte fall">
  <div>
    <div class="fall-kopf"><span class="betrag">54,34 USD</span><span class="badge offen">offen</span><span class="sehr-leise">empfohlen 28.09.2026, 13:11</span></div>
    <dl class="fall-fakten"><dt>Kunde</dt><dd>Anna Berger (K001) · Pro jährlich · Web</dd><dt>Zahlung</dt><dd>Z005 · 14.09.2026 · 54,34 USD · Wechsel auf Pro jährlich</dd><dt>Anliegen</dt><dd>„… wurden mir 54,34 $ ZWEI MAL abgebucht …" · <a href="ticket-ergebnis.html">ganzen Fall ansehen</a></dd></dl>
    <div class="begruendung"><strong>Begründung des Agents:</strong> Doppelte Belastung beim Wechsel auf Jahresabo am 14.09.2026. Beide Zahlungen (Z004 und Z005) sind identisch und nach über einer Woche echte Abbuchungen. Volle Erstattung der zweiten Zahlung gemäß Regel für doppelte Belastungen.</div>
  </div>
  <form class="entscheidung" aria-label="Entscheidung zu 54,34 USD für Anna Berger">
    <label for="k1" class="sehr-leise" style="font-weight:500">Kommentar (optional)</label><input id="k1" placeholder="z. B. Grund der Ablehnung">
    <button class="knopf gruen" type="button">✓ Bestätigen</button><button class="knopf rot" type="button">Ablehnen</button>
  </form>
</article>"""
    zeilen = [("27.09., 18:02","Ben Hoffmann","6,99 USD","abgelehnt","Nur Vormerkung, keine zweite Abbuchung"),
              ("27.09., 17:40","Hannah Fischer","59,00 USD","bestaetigt","Innerhalb 14 Tage, passt"),
              ("27.09., 16:15","Max Wolf","6,99 USD","bestaetigt","")]
    tab = "".join(f'<tr><td>{z}</td><td>{k}</td><td><strong>{b}</strong></td><td><span class="badge {s}">{"bestätigt" if s=="bestaetigt" else "abgelehnt"}</span></td><td class="leise">{c or "–"}</td><td><a href="ticket-ergebnis.html">Fall</a></td></tr>' for z,k,b,s,c in zeilen)
    return f"""
<div class="kopfzeile-seite"><div><p class="ueberzeile">Support-Konsole</p><h1>Freigaben {info("freigabe","Freigaben")}</h1>
  <p class="leise" style="margin-top:6px">Der Agent empfiehlt Erstattungen, du entscheidest. Schattenmodus {info("schatten","Schattenmodus")}</p></div></div>
<div class="statistik">
  <div class="karte stat"><div class="titel">Offen</div><div class="wert">1</div></div>
  <div class="karte stat"><div class="titel">Entschieden</div><div class="wert">3</div></div>
  <div class="karte stat"><div class="titel">Bestätigungsquote {info("quote","Bestätigungsquote",True)}</div><div class="wert">67 %</div><div class="sehr-leise">2 von 3 bestätigt</div></div>
</div>
<h2 class="abschnitt-titel">Offene Empfehlungen <span class="badge offen">1</span></h2>
{offen}
<h2 class="abschnitt-titel">Zuletzt entschieden</h2>
<div class="karte" style="padding:16px 12px"><table class="tabelle"><thead><tr><th>Zeit</th><th>Kunde</th><th>Betrag</th><th>Status</th><th>Kommentar</th><th></th></tr></thead><tbody>{tab}</tbody></table></div>
<h2 class="abschnitt-titel">Übergaben an Menschen {info("uebergabe","Übergabe an Mensch")}</h2>
<div class="karte"><p class="leise">1 Fall wurde übergeben: <strong>Ben Hoffmann</strong> · „Kunde meldet doppelte Abbuchung, im Konto nur eine Zahlung sichtbar." <a href="ticket-ergebnis.html">Fall ansehen</a></p></div>"""

(HIER / "start.html").write_text(seite("Start", "start", START))
(HIER / "ticket-neu.html").write_text(seite("Anliegen stellen", "ticket", ticket_neu(), budget="0,00", prozent=0))
(HIER / "ticket-ergebnis.html").write_text(seite("Anliegen", "ticket", ticket_ergebnis()))
(HIER / "konsole.html").write_text(seite("Support-Konsole", "konsole", konsole()))
print("ok")
