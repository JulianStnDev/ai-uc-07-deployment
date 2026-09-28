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
