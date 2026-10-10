# Nodeglow Product Design

Design- und Umsetzungsdokumentation für die Neugestaltung von Nodeglow.

| Dokument | Inhalt |
|---|---|
| [01-ux-audit.md](01-ux-audit.md) | Phase A: Seiteninventar, Funktionsabdeckung, Scorecard, 58 Befunde, technische Rahmenbedingungen |
| [02-information-architecture.md](02-information-architecture.md) | Phase B: Navigation, Objektmodell, Workflows, UX-Muster, Dashboard-Hierarchie, Backend-Lücken |
| [03-design-directions.md](03-design-directions.md) | Phase C: Vergleich der drei Richtungen, Bewertung, Empfehlung |
| [05-dashboard-api.md](05-dashboard-api.md) | Backend für die neue Oberfläche: einheitlicher Host-Zustand, `/api/v2/dashboard`, `/api/v2/summary`, Änderungen seit dem letzten Besuch, was ableitbar ist |
| [directions/](directions/) | Die drei Richtungsdokumente im Detail |
| [prototypes/](prototypes/) | Szenario und die drei Browser-Prototypen |

## Prototypen ansehen

Die Prototypen sind eigenständige HTML-Dateien ohne Abhängigkeiten ausser Google Fonts. Sie
enthalten keinen `<!doctype>`-Rahmen, weil sie zusätzlich als Seiten auf claude.ai
veröffentlicht werden. Lokal am einfachsten über einen kleinen Webserver öffnen:

```
python -m http.server 8080 --directory docs/design/prototypes
```

Dann `http://localhost:8080/concept-a/` usw. aufrufen. Alle Daten darin sind simuliert und
werden von der Anwendung nicht verwendet.
