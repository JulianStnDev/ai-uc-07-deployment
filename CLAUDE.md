# Projekt-Kontext

## Problem
[Wird hier näher beschrieben, sobald das Use-Case-Repo konkret wird]

## Erwartete Artefakte
- README.md nach Schema (Problem, PM-Entscheidung, Architektur, Eval, Kosten/Latenz, Learnings)
- README.md auf Englisch, README_DE.md auf Deutsch, inhaltlich identisch (gleiche Zahlen, Tabellen, Fachbegriffe). Oben jeweils Sprachlink (🇩🇪 Deutsche Version / 🇬🇧 English version). Änderungen immer in beiden Dateien nachziehen.
- meta.json gepflegt (status ausschließlich: planned | active | done)
- meta.json auf Englisch (speist die Portfolio-Seite): title, summary = ein Satz „what it shows“, metrics = 1–2 Kennzahlen wörtlich aus dem README; optional demo {url, note} und screenshot (Pfad im Repo)
- evals/ mit Datensatz + Ergebnissen
- docs/decisions.md mit datierten Entscheidungen

## Erlaubte Libraries
- Direkt gegen das SDK, kein LangChain/LlamaIndex
- [ggf. weitere Einschränkungen pro Use Case]

## Stil
- Python, einfache Skripte statt Frameworks
- Drei Zahlen im README Pflicht: Kosten/1000 Requests, p95-Latenz, Qualitätsmetrik
