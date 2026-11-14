# Konzept F — „Lime“

Prototyp: [`../prototypes/concept-f/index.html`](../prototypes/concept-f/index.html) (eine Datei,
simulierte Daten aus [`SCENARIO.md`](../prototypes/SCENARIO.md)). Nur die Übersicht ist gebaut.

> Kurzform: weicher, heller Look mit **einem Lime-Akzent** und **Schwarz als zweiter Betonung**.
> Runde Karten ohne harte Ränder, Pill-Buttons, eine schwarze, einklappbare Navigationsleiste und
> eine grosse Begrüssungszeile, die die Lage in einem Satz sagt. Dark Mode gleichwertig.

## Was sich gegenüber E3 ändert

| Bereich | E3 (heute) | F |
|---|---|---|
| Grundton | dunkel zuerst, Violett | hell zuerst, warmes Grau; Dark Mode mit schwarzen Flächen |
| Akzent | Glow Violet | Lime `#D4F04A`, **nur als Fläche**, Text darauf immer dunkel |
| Zweite Betonung | — | Schwarz: aktive KPI-Kachel, Prognose-Karte, Primär-Button |
| Form | Karten mit Rand, Radius 12–16 | Radius 18–28, Schatten statt Rand, Pills überall |
| Navigation | Rail mit Sub-Navigation | schwarze Leiste mit Text, einklappbar auf Icons, Zähler als Badges |
| Kopf der Übersicht | Seitentitel | Satz mit eingebetteten Chips: „2 ⚠ incidents need you, while 29 hosts run fine“ |
| Charts | Linien/Flächen | Pill-Balken mit Punkt-Markern, Segment-Gauges für Füllstände |
| Topologie | Baum | Canvas mit runden Knoten, Leitungen in Statusfarbe |

## Regeln

- **Lime ist Marke, nicht Status.** OK bleibt Grün (`#1E9E6A`), deutlich dunkler und blauer als
  Lime, damit „gesund“ und „Akzent“ nicht verwechselt werden. Warnung Orange, Ausfall Rot,
  Wartung Grau-Blau, keine Daten grau schraffiert.
- **Glow bleibt Nodeglow-Signatur**, aber sparsam: nur kritische Elemente (Incident-Icon,
  Kopf-Switch, betroffene Leitung). Im Dark Mode atmet er langsam, im Light Mode ist es ein
  getönter Schatten.
- **Lime als Text** nur im Dark Mode. Im Light Mode heisst die Textvariante `--lime-text`
  (Oliv `#4F5E00`).
- **Ehrlichkeitsregel** aus `SCENARIO.md` gilt weiter: Beziehungen tragen „Confirmed“,
  „Rule-based“ oder „Suspected“ als Pill.
- In Charts heisst die dunkle Reihe `--chart-ink`: Schwarz im Light Mode, Hellgrau im Dark Mode.

## Interaktion im Prototyp

- KPI-Kacheln schalten den Chart um (Incidents, Syslog, Latenz), die Pill-Tabs den Zeitraum.
- Hover auf einen Balken zeigt den Wert.
- „Needs you“: Klick erledigt den Eintrag, der Zähler sinkt.
- Topologie: Klick auf einen Knoten zeigt Details darunter.
- Leiste einklappbar, Hell/Dunkel umschaltbar (beides in `localStorage`, abgesichert).
- Unter 760 px: Leiste wird zur schwebenden Tab-Bar unten.

## Umsetzung, falls gewählt

Die Token-Pipeline (`design-tokens/tokens.json`) trägt den Grossteil: Farben, Radien, Schatten,
Akzent. Danach `components/ui` (Button, Tabs, Card, BigNumber) und `components/layout`
(Rail, TopBar). Neu wären die Pill-Balken als ECharts-Serie, die Segment-Gauge und die
Begrüssungszeile. Kontraste prüft `npm run check:contrast`; Lime als Textfarbe auf Hell fällt
dort durch und ist deshalb nur als Fläche erlaubt.
