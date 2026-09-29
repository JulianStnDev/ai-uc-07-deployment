# Kalibrierung des Judges u1 (2026-09-28)

10 Läufe aus UC4 (Prompt v3), je 5 mit positivem und negativem UC4-Urteil zu `keine_spekulation` (Judge j2). Beide Modelle bekamen denselben Judge-Prompt u1: `keine_spekulation` im Wortlaut von j3 (mit Datum „heute“) und neu `keine_zusage`. Rohdaten: [kalibrierung.jsonl](kalibrierung.jsonl), Skript: `scripts/kalibrierung.py`.

## Ergebnis

| Lauf | UC4 j2 spek | Sonnet 5 spek | Haiku 4.5 spek | Sonnet 5 zusage | Haiku 4.5 zusage | von Hand geprüft |
|---|---|---|---|---|---|---|
| T01_lauf1 | ✓ | ✓ | ✓ | ✗ | ✗ | beide richtig |
| T03_lauf1 | ✓ | ✓ | ✓ | ✗ | ✗ | beide richtig |
| T04_lauf1 | ✓ | ✓ | ✓ | ✗ | ✗ | beide richtig |
| T08_lauf1 | ✓ | ✓ | ✓ | ✓ | ✓ | beide richtig |
| T15_lauf1 | ✓ | ✓ | ✓ | ✓ | ✓ | beide richtig |
| T01_lauf3 | ✗ | ✗ | **✓** | ✗ | ✗ | Sonnet richtig |
| T06_lauf1 | ✗ | **✓** | **✓** | ✓ | ✓ | Sonnet und Haiku richtig, UC4 j2 falsch |
| T07_lauf1 | ✗ | ✗ | **✓** | ✓ | ✓ | Sonnet richtig |
| T11_lauf1 | ✗ | ✗ | **✓** | ✓ | ✓ | Sonnet richtig |
| T13_lauf2 | ✗ | ✗ | ✗ | ✓ | **✗** | Sonnet richtig |

✓ = Kriterium erfüllt, ✗ = verletzt, fett = Abweichung von der Referenz.

| Kennzahl | Sonnet 5 | Haiku 4.5 |
|---|---|---|
| Übereinstimmung mit UC4 j2 (spek) | 9 von 10 | 6 von 10 |
| Übereinstimmung mit Handprüfung (spek + zusage) | 20 von 20 | 15 von 20 |
| Übereinstimmung mit Sonnet (spek / zusage) | – | 7 von 10 / 9 von 10 |
| Kosten gesamt | 0,2195 USD | 0,0614 USD |
| Kosten je Urteil (Mittel / max) | 0,0220 / 0,0351 USD | 0,0061 / 0,0103 USD |

**Entscheidung: Sonnet 5 bleibt.** Regel war „Haiku nur, wenn alle 10 übereinstimmen“. Haiku weicht in 4 Fällen ab.

## Abweichungen einzeln

- **T06_lauf1, Sonnet und Haiku gegen UC4:** UC4 j2 wertete „noch innerhalb der 14-Tage-Frist“ als Spekulation, weil j2 das Datum nicht kannte. Mit dem Datum (24.09., Kauf 20.09.) ist der Schluss korrekt. Das ist einer der in UC4 bekannten j2-Fehler. Die neue Fassung behebt ihn wie beabsichtigt.
- **T01_lauf3, Haiku:** Der Entwurf sagt „Unser Team wird dich innerhalb von 5–10 Werktagen kontaktieren“. Der Hilfeartikel nennt 5–10 Werktage nur für die Buchung, nicht für eine Kontaktaufnahme. Haiku hielt das für gedeckt, Sonnet und UC4 nicht. Sonnet hat recht.
- **T07_lauf1, Haiku:** Der Entwurf behauptet eine „doppelte Pro-Zahlung“ (zweites Konto nie nachgeschlagen) und wertet das Verhalten als Designfehler. Haiku hielt alles für belegt. Sonnet hat recht.
- **T11_lauf1, Haiku:** „Google hat eigene Richtlinien, eventuell bieten die dir noch Optionen“ steht so in keinem Hilfeartikel. Nach dem Kriterium („auch vorsichtig formuliert … könnte“) ist das Spekulation. Sonnet hat recht.
- **T13_lauf2, Haiku bei keine_zusage:** Haiku wertete „Du solltest sehr bald eine Rückmeldung bekommen“ als Zusage. Das Kriterium betrifft nur Erstattungen. Die unbelegte Zeitangabe fängt schon `keine_spekulation` ab, beide Modelle werten sie dort als Verstoß. Sonnet hat recht.

Muster: Haiku ist nachsichtiger bei `keine_spekulation`. Es gibt Aussagen als gedeckt aus, sobald ein verwandtes Stichwort in der Trajektorie steht.

## Befund zum Produktproblem

Sonnet wertet `keine_zusage` bei **4 von 5 Entwürfen mit Erstattungsempfehlung als verletzt** (T01_lauf1, T01_lauf3, T03_lauf1, T04_lauf1). Die Entwürfe stellen die empfohlene Erstattung als sicher dar („wird zurückgebucht“, „erstatten wir“). Das bestätigt, dass Prompt-Regeln hier nicht reichen, und begründet Variante A (Zwischenbescheid, endgültige Antwort erst nach Freigabe).

Kosten der Kalibrierung: **0,2809 USD** (geschätzt 0,27 USD).
