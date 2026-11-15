# Konzept F — weiche Formen, E3-Farben

Prototyp: [`../prototypes/concept-f/index.html`](../prototypes/concept-f/index.html) (eine Datei,
simulierte Daten aus [`SCENARIO.md`](../prototypes/SCENARIO.md) plus ein Enterprise-Skalentest mit
480 Hosts). Nur die Übersicht ist gebaut.

> Kurzform: Das **Farbkonzept von E3 bleibt** (Glow Violet, Flächen, Statusfarben, Glow-Regeln aus
> `frontend/design-tokens/tokens.json`). Neu sind **Formen und Aufbau**: runde Karten, Pills, eine
> einklappbare Navigationsleiste mit Text, ein Kopf, der die Lage in einem Satz sagt, und eine
> Übersicht, die **jedes Problem genau einmal** zeigt und von 48 bis 500+ Hosts gleich gross bleibt.
> Eine erste Fassung mit Lime-Akzent wurde verworfen.

## Aufbau der Übersicht

Reihenfolge nach Dringlichkeit, jedes Problem erscheint an genau einer Stelle:

1. **Kopf:** „2 ⚠ incidents need you“, darunter die ehrliche Lage als Balken und Satz
   („28 of 48 hosts healthy · 10 with issues · 9 without data · 1 in maintenance“).
2. **Open incidents** (links oben): worst first, der kritische offen. Jede Beziehung trägt ihr Label
   (Confirmed / Rule-based / Suspected) mit Beleg, dazu ein kleiner Verlauf der betroffenen Metrik,
   „Open incident“ und „Acknowledge“. Ein bestätigter Incident atmet nicht mehr. Darunter eine Zeile
   „Resolved today“.
3. **Needs a look** (rechts oben): alles, was kein offener Incident ist. Hosts ohne Incident, stille
   Probes (zusammengefasst), Kapazität, Zertifikate, Discovery. Bei vielen Einträgen aggregiert
   („12 volumes full within 14 days“), höchstens 6 Zeilen, Rest hinter „Show all“.
4. **Coming up** (rechts): Wartungsfenster und geplante Arbeit.
5. **Trends:** Kacheln „Open incidents“ und „Syslog“ schalten den Chart, „Availability“ und
   „Reporting“ sind reine Kennzahlen.
6. **Hosts by site:** pro Standort ein Anteilsbalken mit Zahlen als Text, keine einzelnen Hosts.
7. **Where it breaks:** Topologie, Problempfade offen, gesunde Äste gefaltet.

Gestrichen gegenüber der ersten Fassung: Gauge „Disk D:“, Prognose-Karte, „Live incidents“ als
eigene Karte, doppelte Listen „Needs you“ / „Needs a look“, KPI-Kachel zur Latenz hinter einem
Switch (der Verlauf steckt jetzt im Incident), Avatar-Chips im Kopf.

## Was sich gegenüber E3 ändert

| Bereich | E3 (heute) | F |
|---|---|---|
| Farben | Tokens aus `tokens.json` | unverändert, vier Vorschläge für neue Tokens (unten) |
| Form | Karten mit Rand, Radius 10–12 | Radius 18–24, Rand als feiner Schatten, Pills |
| Navigation | schmale Rail (60 px) mit Sub-Navigation | Leiste mit Text (216 px), einklappbar auf Icons |
| Kopf der Übersicht | Seitentitel | Satz plus Lagebalken |
| Incidents | Karte mit Liste | aufklappbare Incidents mit Belegen und Metrik-Verlauf |
| Charts | Linien/Flächen | Pill-Balken, Höhe exakt, Rundung wird abgeschnitten statt aufgeschlagen |
| Topologie | Baum | Canvas, Problempfade offen, gesunde Äste als Zähl-Knoten mit Statusring |

## Farbrollen

- **Akzent:** Flächen mit Text immer auf `accent-btn` (Weiss darauf hält AA in beiden Themes).
  `accent` nur für Grafik: Chart-Reihe, gesunde Leitungen und Knoten, Logo.
  Aktive Navigation = `accent-soft` + `accent-text` wie in 04-design-system.
- **Status-Badges und Zähler:** Soft-Hintergrund (`st-*-soft`) mit `st-*-text`, nie Weiss auf
  Statusfarbe. Weiss auf Degraded oder Warning fällt durch (1.7 bis 3.4:1).
- **Dunkle Betonung** = `tip-bg` / `tip-text` (immer dunkel): aktive Kachel, Pill „Confirmed“,
  sekundäre Aktions-Buttons, Tooltips, Toast.
- **Zweite Chart-Reihe** = `text`, damit Charts zweifarbig bleiben, ohne eine Statusfarbe zu belegen.
- **Fokus:** `focus` mit einem Ring in `surface`-Farbe dahinter, damit er auch auf dunklen Flächen sichtbar ist.

**Vorgeschlagene neue Tokens** (im Prototyp als „proposal“ markiert): `tip-text-2` (Sekundärtext
auf `tip-bg`), `tip-accent` und `tip-down` (Akzent bzw. Down als Text auf `tip-bg`, in beiden Themes
die Dark-Werte), `card-shadow` (Kartenrand als 1-px-Schatten).

## Datenehrlichkeit

- Abweichung von `SCENARIO.md`: **SW-ZH-CORE-02 zählt als degraded** (Uplink flappt, Switch antwortet),
  also 28 statt 29 gesunde Hosts. Sonst wäre der Verursacher des kritischen Incidents „healthy“.
- Labels: Die Beziehung der Hosts zum Incident ist *Confirmed* (gemeinsamer Parent), der
  Syslog-Burst am Switch ist *Rule-based*, die zeitliche Überlappung der Firewall ist *Suspected*.
- Der Umschalter „48 / 480 hosts“ ist als „Simulated data“ markiert und schaltet **alle** Zahlen der
  Seite (Kopf, Tenant, Kacheln, Incidents, Listen, Topologie). Nur die Tagesverläufe sind skaliert.
- Der Tenant-Switcher trägt sichtbar „Planned“ und ist nicht bedienbar.

## Skalierung (48 bis 500+ Hosts)

- **Hosts by site:** eine Zeile pro Standort, sortiert nach Schwere, höchstens 6, Rest hinter
  „Show all“. Die Zahlen stehen als Text in der Zeile und im Screenreader-Label.
- **Needs a look:** Hosts mit gemeinsamer Ursache werden zusammengefasst, gleichartige Funde
  aggregiert, höchstens 6 Zeilen.
- **Topologie:**
  - Der Pfad zu einem Problem bleibt offen bis zum Verursacher.
  - Gesunde Äste werden zu Zähl-Knoten mit Statusring.
  - Mehr als 2 Hosts im selben Zustand unter einem Parent werden zu einem Zähl-Knoten.
  - Ein Parent zeigt höchstens 3 Kinder offen, der Rest steckt in „+N“. Das lässt sich auf- und wieder zuklappen.
  - Die Höhe wächst mit der Zahl der Blätter, nicht mit der Zahl der Hosts.
  - Auf schmalen Bildschirmen scrollt die Fläche zum gewählten Knoten.

## Barrierefreiheit

- Chartbalken sind fokussierbar und tragen ihren Wert als Label. Der Tooltip erscheint bei Hover,
  Fokus und Tap.
- Umschalter sind Buttons mit `aria-pressed` (keine Tab-Rollen ohne Panels). Incidents sind
  `aria-expanded`-Buttons.
- Nach jedem Neuzeichnen bleibt der Tastaturfokus auf dem gleichen Element (`data-fk`).
- Eingeklappte Leiste und mobiler „Add host“ behalten ihren Namen (visuell versteckt, nicht entfernt).
- Theme wird vor dem ersten Paint gesetzt. Gespeichert wird nur eine bewusste Wahl, sonst folgt
  die Seite dem Betriebssystem.
- `breathe` nur im Dark Mode und nur ohne `prefers-reduced-motion`.

## Umsetzung, falls gewählt

Die Farb-Tokens bleiben. Dazu kommen die vier vorgeschlagenen Tokens und angepasste Radien
(`radius*`) in `tokens.json`. Danach folgen `components/ui` (Button, Segmented, Card, Kachel, Badge)
und `components/layout` (Rail mit Text und Einklappen, TopBar). Neu wären die Pill-Balken als
ECharts-Serie, der Incident-Block mit Belegen, die Standort-Zeilen und der Topologie-Canvas mit
Faltlogik.
