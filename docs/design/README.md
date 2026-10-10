# Nodeglow Product Design

Design- und Umsetzungsdokumentation für die Neugestaltung von Nodeglow (2026).
Konzept E3 wurde gewählt und ist umgesetzt. Übersicht über die gesamte
Dokumentation: [../README.md](../README.md).

| Dokument | Inhalt | Status |
|---|---|---|
| [04-design-system.md](04-design-system.md) | Phase D: Tokens, Tailwind-Klassen, Komponentenkatalog, Glow- und Zustandsregeln, App-Shell, Migrations-Checkliste | **aktuell** — verbindlich für UI-Arbeit |
| [05-dashboard-api.md](05-dashboard-api.md) | Backend für die neue Oberfläche: einheitlicher Host-Zustand, `/api/v2/dashboard`, `/api/v2/summary`, Änderungen seit dem letzten Besuch, was ableitbar ist | **aktuell** — umgesetzt; Überblick über die ganze API in [../API.md](../API.md) |
| [02-information-architecture.md](02-information-architecture.md) | Phase B: Navigation, Objektmodell, Workflows, UX-Muster, Dashboard-Hierarchie, Backend-Lücken | Grundlage; Navigation und Routen gelten so, wie sie in `frontend/src/lib/navigation.ts` stehen |
| [01-ux-audit.md](01-ux-audit.md) | Phase A: Seiteninventar, Funktionsabdeckung, Scorecard, 58 Befunde, technische Rahmenbedingungen | historisch — beschreibt die Oberfläche vor der Neugestaltung |
| [03-design-directions.md](03-design-directions.md) | Phase C: Vergleich der Richtungen, Bewertung, Entscheid | historisch |
| [directions/](directions/) | Die Richtungsdokumente A–E3 im Detail | historisch (E3 umgesetzt) |
| [prototypes/](prototypes/) | Szenario und die Browser-Prototypen | historisch, simulierte Daten |

## Prototypen ansehen

Die Prototypen sind eigenständige HTML-Dateien ohne Abhängigkeiten ausser Google Fonts. Sie
enthalten keinen `<!doctype>`-Rahmen, weil sie zusätzlich als Seiten auf claude.ai
veröffentlicht werden. Lokal am einfachsten über einen kleinen Webserver öffnen:

```
python -m http.server 8080 --directory docs/design/prototypes
```

Dann `http://localhost:8080/concept-a/` usw. aufrufen. Alle Daten darin sind simuliert und
werden von der Anwendung nicht verwendet. (Die Anwendung selbst lädt keine Google Fonts; sie
liefert ihre Schriften selbst aus.)
