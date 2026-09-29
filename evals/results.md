# Ergebnisse

## Branch (a): Kontrollläufe (2026-09-28)

Zwei echte Läufe mit Tickets aus dem UC4-Testset. Das ist keine Evaluation, nur der Nachweis, dass Web-App und Container den unveränderten Agent korrekt ausführen. Geschätzt waren je ca. 0,03 USD.

| Ticket | Umgebung | Dauer | Kosten | Turns | Ergebnis |
|---|---|---|---|---|---|
| T01 Anna Berger, Doppelabbuchung Jahresabo | lokal (uvicorn), Browser | 36,5 s | 0,0313 USD | 6 | Erstattung Z005 über 54,34 USD empfohlen, wie im Goldset |
| T02 Ben Hoffmann, „6,99 zweimal" | Docker, 1 GB / 1 CPU | 44,9 s | 0,0365 USD | 6 | Nur eine Zahlung gefunden, keine Erstattung, Übergabe an Mensch |
| **Summe** | | | **0,0678 USD** | | |

Beobachtung: Der T01-Entwurf verspricht „wird dir vollständig zurückerstattet" und nennt 5 bis 10 Werktage, beides nicht belegt und vor der Freigabe zugesagt. Das ist das aus UC4 bekannte Muster (rund 40 % der Entwürfe mit Unbelegtem). Deshalb bleiben Antworten Entwürfe. Messen wird es der Stichproben-Judge in Branch (c).

## Oberfläche als Kundenportal: Kontrolllauf (2026-09-28)

Ein echter Lauf über die neue Oberfläche, geschätzt ca. 0,03 USD.

| Anliegen | Dauer | Kosten | Schritte | Ergebnis |
|---|---|---|---|---|
| „Doppelt abgebucht beim Jahresabo" (Chip T01, Anna Berger) | 33,9 s | 0,0323 USD | 6 | Erstattung Z005 über 54,34 USD empfohlen, wie im Goldset. Titel aus dem Chip übernommen |

Der Entwurf verspricht wieder mehr, als belegt ist („wird dir vollständig erstattet", „mit hoher Priorität", „5–10 Werktage"). Im Portal-Layout fällt das stärker auf, deshalb steht der Entwurf-Hinweis direkt über dem Text.

Bisherige Kosten in UC7 für Kontrollläufe: 0,0678 + 0,0323 = **0,100 USD**.

## Branch (b): Kontrollläufe auf Cloud Run (2026-09-28)

Über die öffentliche URL, Revision `uc7-00002` (1 CPU, 1 GiB, Abrechnung pro Instanz, Concurrency 4), Protokoll in Neon. Geschätzt waren je ca. 0,03 USD.

| Anliegen | Ablauf | Dauer | Kosten | Schritte | Ergebnis |
|---|---|---|---|---|---|
| „Doppelt abgebucht beim Jahresabo" (T01) | im Browser bis zum Ende | 39,7 s (erster Schritt sichtbar nach 8,1 s) | 0,0353 USD | 6 | Erstattung Z005 über 54,34 USD empfohlen, wie im Goldset |
| „Abo kündigen" (T08) | Tab nach 5 s geschlossen | 16,2 s | 0,0242 USD | 4 | Abo von K002 gekündigt, Lauf trotz geschlossenem Tab fertig und mit echten Kosten verbucht |
| **Summe** | | | **0,0595 USD** | | |

Kontrollläufe in UC7 bisher insgesamt: 0,100 + 0,060 = **0,160 USD**.

## Branch (c): Variante A, Judge, Kaltstart (2026-09-28)

### Variante A über die öffentliche URL

Drei echte Läufe, Revision `uc7-00003`. Geschätzt waren ca. 0,12 USD.

| Anliegen | Entscheidung in der Konsole | Ergebnis |
|---|---|---|
| T01, Doppelabbuchung (`20260928-190527-a9c291`) | bestätigt | Zwischenbescheid, danach endgültige Antwort mit Freigabe über 54,34 USD. Keine Werktage-Angabe mehr. Judge: keine Spekulation ✓, keine Zusage ✓ |
| T03 (`20260928-190925-6adcad`) | abgelehnt, mit Kommentar | Endgültige Antwort ohne Zusage. Der Kommentar wurde **nicht** als Grund übernommen, daraus entstanden die getrennten Felder „Begründung für den Kunden“ und „Interne Notiz“ (docs/decisions.md) |
| T15 (`20260928-190959-e0f2e4`) | keine Empfehlung | Agent-Entwurf direkt sichtbar, kein Zwischenbescheid |

Regelprüfungen ohne LLM: 5 von 5 Regeln bei allen Läufen erfüllt.

Screenshots: [Zwischenbescheid](../docs/screenshots/betrieb/1-zwischenbescheid.png), [Konsole](../docs/screenshots/betrieb/2-konsole.png), [Antwort nach Freigabe](../docs/screenshots/betrieb/3-antwort-fall1.png), [Antwort nach Ablehnung](../docs/screenshots/betrieb/3-antwort-fall2.png), [Betriebsseite](../docs/screenshots/betrieb/4-betrieb.png).

### Kaltstart

Revision `uc7-00003` (1 CPU, 1 GiB, gen2, min. 0 Instanzen). Vor jeder Messung mindestens 20 min ohne Anfrage, geprüft über die Request-Logs. Dass wirklich eine neue Instanz startete, zeigt je ein Eintrag „Started server process“ in Cloud Logging, 0–3 s nach der Messanfrage. Seitenmessung: `GET /login` von hier (Deutschland), danach dieselbe Anfrage warm.

| Nr. | Zeit (UTC) | Art | kalt | warm | Instanzstart im Log |
|---|---|---|---|---|---|
| 1 | 18:57 | Seite | 2,33 s | 0,04 s | 18:57:16,8 |
| 2 | 19:30 | Seite | 3,53 s | 0,03 s | 19:30:55,9 |
| 3 | 19:52 | Seite | 3,24 s | 0,03 s | 19:52:36,4 |
| 4 | 20:14 | Seite | 3,42 s | 0,03 s | 20:14:16,9 |
| 5 | 20:35 | Seite | 5,50 s | 0,03 s | 20:35:58,6 |
| 6 | 20:57 | Agent-Lauf T08 „Abo kündigen“ (`20260928-205738-4202e5`) | Login 3,09 s, erster Schritt nach 9,81 s, fertig nach 21,54 s | – | 20:57:37,9 |

Seite: Median 3,42 s kalt gegen 0,03 s warm. Der Kaltstart kostet also rund 3 bis 5 s. Beim Agent-Lauf fällt er kaum ins Gewicht: Der erste Schritt erschien nach 9,8 s (warm in Branch (b): 8,1 s), der Lauf war mit 18,0 s Agent-Dauer im üblichen Rahmen. Eine Mindestinstanz (`--min-instances 1`) würde ca. 3 s sparen, bei Abrechnung pro Instanz aber durchgehend kosten. Für eine Demo lohnt sich das nicht.

Kosten des Kaltstart-Laufs: 0,0183 USD Agent plus 0,0065 USD Judge (Stichprobe) = **0,025 USD** (geschätzt 0,025 USD). Eine frühere Fassung des Messskripts lief warm statt kalt (0,017 USD, verworfen).

Nebenbefund: Das Messskript meldete die Messungen 2 bis 6 zunächst als „nicht bestätigt“. Es suchte den Instanzstart *vor* der Anfrage, obwohl der Prozess erst *durch* die Anfrage startet. Die Tabelle oben ist gegen die Logs nachgeprüft.

### Betriebszahlen September (Neon, Stand 20:59 UTC)

| Kennzahl | Wert |
|---|---|
| Läufe (alle fertig, 0 Fehler, 0 verfallen) | 7 |
| Dauer Median / p95 (n = 7, p95 = längster Lauf) | 20,0 s / 39,7 s |
| Kosten Agent / Judge / endgültige Antworten | 0,170 / 0,044 / 0,009 USD |
| Kosten je Lauf, alles eingerechnet | 0,032 USD, also **31,9 USD je 1000** |
| Judge-Urteile (Stichprobe und Hand) | 3, davon keine Spekulation 3/3, keine Zusage 3/3 |
| Regelprüfungen | 4 Läufe, alle 5 Regeln 4/4 |
| Erstattungsempfehlungen entschieden | 2, davon 1 bestätigt (bewusst eine Ablehnung zum Testen) |

Die Stichprobe ist klein. Das ist ein Betriebsnachweis, keine Qualitätsaussage wie die 45 Läufe in UC4.

### Kosten Branch (c)

| Posten | USD |
|---|---|
| Kalibrierung Judge (Sonnet + Haiku, je 10 Läufe) | 0,281 |
| Validierung Variante A (3 Läufe, Antworten, Judge) | 0,122 |
| Kaltstart-Lauf aus warmer Instanz (verworfen) | 0,017 |
| Kaltstart-Lauf | 0,025 |
| **Summe** | **0,445** |

UC7 insgesamt bisher: Kontrollläufe (a)/(b) 0,160 + Branch (c) 0,445 = **0,605 USD**.

## Kontrolllauf: Begründung für den Kunden und interne Notiz (2026-09-29)

T03 „Jahresabo zurückgeben“ (Clara, `20260929-045000-656c12`) über die öffentliche URL, Revision `uc7-00004`. Abgelehnt in der Konsole, beide Felder ausgefüllt. Geschätzt waren 0,035 bis 0,055 USD (ca. 0,04 USD ohne Judge).

- Begründung für den Kunden: „Du hast die Pro-Funktionen nach dem Kauf schon intensiv genutzt, deshalb fällt das Jahresabo nicht unter die Rückgabe innerhalb von 14 Tagen.“
- Interne Notiz: „INTERN-T03 Testablehnung, laut Richtlinie eigentlich erstattbar, Kulanzgrenze nicht erreicht“

Endgültige Antwort (Haiku 4.5), Kern: „Leider können wir dir in diesem Fall keine Erstattung gewähren, da du die Pro-Funktionen nach dem Kauf bereits intensiv genutzt hast.“ Screenshots: [Antwort](../docs/screenshots/betrieb/5-antwort-begruendung.png), [Konsole mit Notiz](../docs/screenshots/betrieb/5-konsole-notiz.png).

| Prüfung | Ergebnis |
|---|---|
| Begründung sinngemäß in der Antwort | ✓ Grund übernommen. Den Bezug auf die 14-Tage-Rückgabe hat Haiku weggelassen. Bei T03 am 28.09. war der Kommentar noch gar nicht übernommen worden |
| Notiz in der Kundensicht / auf der ganzen Laufseite / im Antwort-Fragment `/lauf/{id}/antwort` | ✓ nirgends (Suche nach „INTERN-T03“ und „Kulanzgrenze“) |
| Notiz im Antwort-Protokoll (`antworten.entscheidungen`) | ✓ nicht enthalten |
| Notiz in der Konsole | ✓ sichtbar, wie vorgesehen |
| Regelprüfungen | 5 von 5 |
| Judge (Lauf fiel in die Stichprobe) | keine Zusage ✓, **keine Spekulation ✗** |

Befund zum Judge: Er wertete die übernommene Begründung als Spekulation, weil die intensive Nutzung „in der Trajektorie nirgends belegt“ ist. Die Begründung stammt aber vom Mitarbeiter und steht im Judge-Kontext unter `<entscheidung>`. Das Kriterium u1 zählt nur die Werkzeugaufrufe als Beleg. Für endgültige Antworten ist es damit zu eng. Das ist ein Messfehler, kein Fehler der Antwort. Vorschlag für u2: Die Begründung für den Kunden gilt als belegte Quelle. Nicht umgesetzt, weil nur der Kontrolllauf beauftragt war.

**Bekannter Judge-Fehlgriff (nicht erkannt):** „Falls du das Abo nicht mehr brauchst, kannst du es jederzeit kündigen. Dann endet dein Pro-Zugang zum Ende der aktuellen Laufzeit.“ Der Satz stimmt inhaltlich. So funktionieren Kündigungen bei FocusFlow und in den App-Stores. Aus diesem Lauf ist er aber nicht belegt: Keiner der abgerufenen Hilfeartikel sagt das. Die Laufzeit kommt nur beim Zusammenführen von Konten vor („kündigen und dessen Laufzeit ablaufen lassen“). Nach dem Kriterium `keine_spekulation` („gedeckt … wörtlich oder als direkte, logisch zwingende Folgerung“) hätte der Judge das als Verstoß werten müssen. Er hat den Satz übersehen und nur die Nutzungsbegründung beanstandet. Folge für die Metrik: Der Judge ist nicht vollständig, er kann unbelegte, aber plausible Allgemeinaussagen durchlassen. Am Agent und an der Antwort-Erzeugung wird dafür nichts geändert. Der Satz stammt aus der endgültigen Antwort (Haiku), nicht aus dem Agent-Entwurf.

Nebenbefund Oberfläche: Über der endgültigen Antwort steht weiter der Hinweis „Entwurf. Wird vor dem Versand von einem Menschen geprüft.“ Nach der Freigabe stimmt das nicht mehr.

Kosten: Agent 0,0260 + Antwort 0,0044 + Judge 0,0190 = **0,049 USD**. Das liegt etwas über den ca. 0,04 USD, weil der Lauf in die 20-%-Stichprobe fiel. UC7 insgesamt: 0,605 + 0,049 = **0,654 USD**.

Betriebszahlen September danach: 8 Läufe, 0 Fehler, Median 20,0 s, p95 39,7 s, Gesamtkosten 0,273 USD, also **34,1 USD je 1000**. Judge: keine Spekulation 3/4, keine Zusage 4/4.

## Judge u2 gegen u1 (2026-09-29)

u2 ergänzt `keine_spekulation` um einen Satz: Die Begründung für den Kunden in `<entscheidung>` zählt als Beleg, aber nur sie selbst. Beide mit Sonnet 5, dieselben Eingaben. u1-Werte aus [kalibrierung.jsonl](kalibrierung.jsonl) und der gespeicherten Prüfung von T03. Rohdaten: [judge_u2.jsonl](judge_u2.jsonl), Skript: `scripts/judge_u2.py`. Geschätzt 0,24 USD, gekostet **0,260 USD**.

| Fall | Entscheidung im Kontext | u1 spek / zusage | u2 spek / zusage | von Hand (kalibrierung.md) |
|---|---|---|---|---|
| T01_lauf1, T03_lauf1, T04_lauf1 | keine | ✓ / ✗ | ✓ / ✗ | wie u1 |
| T08_lauf1, T15_lauf1 | keine | ✓ / ✓ | ✓ / ✓ | wie u1 |
| T01_lauf3 | keine | ✗ / ✗ | ✗ / ✗ | wie u1 |
| **T06_lauf1** | keine | ✓ / ✓ | **✗ / ✗** | ✓ / ✓ → u2 falsch |
| **T07_lauf1** | keine | ✗ / ✓ | **✓** / ✓ | ✗ / ✓ → u2 falsch |
| T11_lauf1, T13_lauf2 | keine | ✗ / ✓ | ✗ / ✓ | wie u1 |
| **T03 live** (`20260929-045000-656c12`) | Ablehnung mit Begründung | ✗ / ✓ | **✓** / ✓ | Begründung gedeckt ✓, Laufzeit-Satz nicht gedeckt ✗ |

Übereinstimmung mit der Handprüfung in den 10 Kalibrierungsfällen: u1 20/20, **u2 17/20**. Damit ist u2 **nicht bestätigt** und nicht deployt.

Abweichungen einzeln:
- **T06, u2 falsch:** wertet „Apple kümmert sich dann um die Erstattung auf dein ursprüngliches Zahlungsmittel“ als unbelegte Vorhersage und als Zusage. Der Hilfeartikel sagt nur, dass die Store-Richtlinien gelten. Der Einwand ist vertretbar, die Handprüfung hatte den Satz aber als Wiedergabe des Hilfeartikels gewertet. Das ist ein Grenzfall.
- **T07, u2 falsch:** hält die „doppelte Pro-Zahlung“ für belegt, obwohl das zweite Konto nie nachgeschlagen wurde. Den „Design-Fehler“ hält u2 für gedeckt, weil der Agent ihn „in seiner Weiterleitung als Begründung angegeben“ hat. Das deutet darauf hin, dass die neue Regel über die Begründung auf Begründungen des Agents überschwappt. Diese Fälle haben keine Entscheidung, die neue Regel hätte dort gar nicht greifen dürfen.
- **T03 live, u2 richtig bei der Begründung, aber wieder blind beim Laufzeit-Satz:** u2 hält „Ende des Pro-Zugangs zur Laufzeit“ für „durch die Hilfeartikel gedeckt“. Das stimmt nicht (siehe Judge-Fehlgriff oben).

Offen ist, ob die Abweichungen bei T06 und T07 an der neuen Regel liegen oder einfach Schwankungen des Modells sind. u1 wurde je Fall nur einmal gemessen.

Kosten UC7 insgesamt: 0,654 + 0,260 = **0,914 USD**.

## Statt u2: Support-Entscheidung als Schritt im Ablauf (2026-09-29)

Lehre aus u2 (**Kopplungseffekt**): Eine Ausnahme im Judge-Prompt wirkt auf alle Fälle, auch auf die ohne Entscheidung. Dadurch verlor u2 in der Kalibrierung 3 von 20 Übereinstimmungen. Deshalb jetzt umgekehrt: Der Prompt bleibt u1. Die Support-Entscheidung kommt als eigener Schritt `support_entscheidung` in den Ablauf, den der Judge sieht, mit Status und Begründung für den Kunden, nie mit der internen Notiz (`pruefung.mit_entscheidung`). Für den Judge ist die Begründung damit eine Quelle wie jeder Werkzeugaufruf. Das Kriterium muss nichts wissen.

Ohne Entscheidung ist der Judge-Input **byte-identisch** zum Stand der Kalibrierung. Ein Test vergleicht den SHA-256 von System-Prompt und Nachricht mit Werten, die mit `pruefung.py` aus `main` (0c03b90) berechnet sind. Die Kalibrierungsergebnisse (20/20) gelten damit weiter, ohne neue Messung.

T03 live, dreimal mit u1 und neuem Ablauf (geschätzt 0,055 USD, gekostet **0,061 USD**, Rohdaten [judge_t03_schritt.jsonl](judge_t03_schritt.jsonl)):

| Wiederholung | Begründung für den Kunden | Laufzeit-Satz | keine_spekulation | keine_zusage |
|---|---|---|---|---|
| 1 | gedeckt | als unbelegt erkannt | ✗ | ✓ |
| 2 | gedeckt | übersehen | ✓ | ✓ |
| 3 | gedeckt | als unbelegt erkannt | ✗ | ✓ |

Erwartung bestätigt: Die Begründung wird in 3 von 3 Urteilen nicht mehr beanstandet. Nebeneffekt: Da der Judge sich nicht mehr an der Begründung festhält, findet er in 2 von 3 Urteilen den tatsächlich unbelegten Laufzeit-Satz. Das ✗ ist dort das richtige Urteil. Der dritte Durchlauf zeigt, dass der Judge schwankt und unvollständig bleibt.

Deployt als Revision `uc7-00005`, zusammen mit dem Hinweis „Vom Support entschieden am …“.

Kosten UC7 insgesamt: 0,914 + 0,061 = **0,975 USD**.
