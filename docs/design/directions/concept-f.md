# Konzept F — weiche Formen, E3-Farben

Prototyp: [`../prototypes/concept-f/index.html`](../prototypes/concept-f/index.html) (eine Datei,
simulierte Daten aus [`SCENARIO.md`](../prototypes/SCENARIO.md)). Nur die Übersicht ist gebaut.

> Kurzform: Das **Farbkonzept von E3 bleibt** (Glow Violet, Flächen, Statusfarben, Glow-Regeln,
> Werte 1:1 aus `frontend/design-tokens/tokens.json`). Neu sind **Formen und Aufbau**: runde
> Karten, Pill-Buttons und Pill-Tabs, eine einklappbare Navigationsleiste mit Text, eine
> Begrüssungszeile, die die Lage in einem Satz sagt, Pill-Balken und ein Topologie-Canvas.
> Eine erste Fassung mit Lime-Akzent wurde verworfen.

## Was sich gegenüber E3 ändert

| Bereich | E3 (heute) | F |
|---|---|---|
| Farben | Tokens aus `tokens.json` | unverändert |
| Form | Karten mit Rand, Radius 10–12 | Radius 18–28, Rand als feiner Schatten, Pills überall |
| Navigation | schmale Rail (60 px) mit Sub-Navigation | Leiste mit Text (216 px), einklappbar auf Icons, Zähler als Badges |
| Kopf der Übersicht | Seitentitel | Satz mit eingebetteten Chips: „2 ⚠ incidents need you, while 29 hosts run fine“ |
| KPI | einzelne Karten | Kachelreihe in der Chart-Karte; die aktive Kachel bestimmt den Chart |
| Charts | Linien/Flächen | Pill-Balken mit Punkt-Markern, Segment-Gauges für Füllstände |
| Topologie | Baum | Canvas mit runden Knoten, Problempfade offen, gesunde Äste als Zähl-Knoten mit Statusring |
| Hosts | eigene Seite | Host-Karte auf der Übersicht: pro Standort ein Anteilsbalken, darunter nur, was Aufmerksamkeit braucht |

## Farbrollen

- **Akzent** (`accent`): aktive Navigation, Hauptreihe in Charts, gesunde Leitungen und Knoten in
  der Topologie, Gauge-Karte „Disk D:“, Logo. Primär-Button = `accent-btn` mit `accent-glow`.
- **Dunkle Betonung** = E3-Tooltip-Fläche `tip-bg` (immer dunkel): aktive KPI-Kachel (mit
  Akzent-Rand), Prognose-Karte, sekundäre Aktions-Buttons, Pill „Confirmed“.
- **Zweite Chart-Reihe** = Textfarbe (`text`), damit Charts zweifarbig bleiben, ohne eine
  Statusfarbe zu belegen.
- **Status** und **Glow** exakt nach E3: nur Kritisches leuchtet, im Light Mode als getönter
  Schatten, unbekannt bleibt grau schraffiert.
- **Ehrlichkeitsregel** aus `SCENARIO.md` gilt weiter: Beziehungen tragen „Confirmed“,
  „Rule-based“ oder „Suspected“ als Pill.

## Interaktion im Prototyp

- KPI-Kacheln schalten den Chart um (Incidents, Syslog, Latenz), die Pill-Tabs den Zeitraum.
- Hover auf einen Balken zeigt den Wert.
- „Needs you“: Klick erledigt den Eintrag, der Zähler sinkt.
- Topologie, gebaut für 48 bis 500+ Hosts (gleicher Umschalter wie die Host-Karte, beide synchron):
  - Der Pfad zu einem Problem bleibt offen bis zum Verursacher (z. B. SW-DC-AGG-03, #2214).
  - Gesunde Äste falten sich zu einem Zähl-Knoten („98 hosts“) mit Statusring zusammen.
  - Mehr als 2 Hosts im selben Zustand unter einem Parent werden ebenfalls zu einem Zähl-Knoten.
  - Ein Parent zeigt höchstens die 3 dringendsten Kinder offen, der Rest steckt in „+N sites“. Ein Klick klappt auf.
  - Das Layout wird automatisch berechnet: Spalten nach Tiefe, Zeilen nach Blättern, Parents mittig. Parents tragen das
    Label unter dem Knoten, Blätter rechts daneben. Die Höhe wächst mit der Anzahl Blätter, nicht mit der Anzahl Hosts.
  - Klick auf einen Knoten zeigt Details und Zählung darunter.
- Host-Karte, gebaut für 48 bis 500+ Hosts (Umschalter „48 hosts / 480 hosts“, beides simuliert):
  - Pro Standort eine Zeile mit Anteilsbalken und „x / y healthy · n issues“, keine einzelnen Hosts.
    Sortiert nach Schwere (stille Probe und Down zuerst), höchstens 6 Standorte, Rest hinter „Show all“.
  - „Needs a look“ fasst Hosts mit gemeinsamer Ursache zu einer Zeile zusammen
    („21 hosts behind SW-DC-AGG-03 · 3 down, 18 degraded · #2214“), aufklappbar auf die Host-Namen.
    Einzelne Probleme danach, höchstens 6 Einträge, Rest hinter „Show all“.
  - Gesunde Hosts erscheinen nie einzeln. Eine Zeile pro Host gehört auf die Hosts-Seite, nicht auf die Übersicht.
- Leiste einklappbar, Hell/Dunkel umschaltbar (beides in `localStorage`, abgesichert).
- Unter 760 px: Leiste wird zur schwebenden Tab-Bar unten.

## Umsetzung, falls gewählt

Die Farb-Tokens bleiben. Anzupassen sind die Radien in `tokens.json` (`radius*`), danach
`components/ui` (Button, Tabs, Card, BigNumber als Kachel) und `components/layout`
(Rail mit Text und Einklappen, TopBar). Neu wären die Pill-Balken als ECharts-Serie, die
Segment-Gauge, die Begrüssungszeile und der Topologie-Canvas.
