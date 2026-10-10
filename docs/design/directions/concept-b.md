# Konzept B: Operational Intelligence

Prototyp: [`../prototypes/concept-b/index.html`](../prototypes/concept-b/index.html) (eine Datei,
simulierte Daten aus [`SCENARIO.md`](../prototypes/SCENARIO.md)).

Konzept B behandelt Nodeglow als **Lagebild**. Im Mittelpunkt stehen nicht Hosts oder Metriken,
sondern Probleme, Veränderungen und deren Zusammenhänge. Die Startseite beantwortet zuerst "Was ist
los, seit wann, woran hängt es und was tue ich als Nächstes?" und erst danach "Wie sieht die
Infrastruktur aus?".

---

## 1. Identität

### Charakter

Ein Einsatzleitstand, kein Cockpit. Nüchtern, dicht und erklärend. Die Oberfläche argumentiert:
Jede Aussage zeigt, woher sie kommt. Die visuelle Sprache besteht aus **Linien zwischen Dingen**.
Wie sicher ein Zusammenhang ist, codiert durchgehend der Linienstil: durchgezogen, gestrichelt,
gepunktet oder grau. Das gilt im Kontextfaden, in der Beweiskette, in der Impact-Map und in jedem
Label-Chip.

Bewusst nicht: Globus, Glow, Glas, Neon, vier KPI-Kacheln oben, großflächige Gradients.

### Farbe

| Token | Hell | Dunkel | Rolle |
|---|---|---|---|
| `--ground` | `#E5E8EC` | `#0E1014` | Arbeitsfläche, kühles Mineralgrau |
| `--surface` / `-2` / `-3` | `#FAFBFC` / `#F0F2F5` / `#E3E7EC` | `#161920` / `#1C2028` / `#262B35` | Panels, Zonen, Hover |
| `--ink` / `-2` / `-3` | `#14171C` / `#434A55` / `#5B626E` | `#E7E9EE` / `#B4BAC5` / `#9097A3` | Text, drei Stufen, alle ≥ 4.5:1 |
| `--accent` | `#2B3AB5` Ultramarin | `#9CA7FF` | **Beziehungen und Veränderung**: Relationslinien, "seit letztem Besuch", Fokus, primäre Aktion |
| `--crit` / `--warn` / `--ok` | Rot / Ocker / Grün | aufgehellt | nur Status, nie Akzent |
| `--unk` | Schiefergrau mit Punktraster | | unbekannt / nicht beobachtet |
| `--maint` | Violett mit Schraffur | | Wartung |

Begründung: Ultramarin ist "Tinte". Es markiert, was der Mensch lesen soll (Zusammenhänge,
Neues), und kollidiert mit keiner Statusfarbe. Die Neutralen sind leicht blaustichig statt
neutralgrau, damit Ocker und Rot darauf nicht schmutzig wirken. Die Firmenfarben des Owners
(Petrol/Cyan) werden bewusst nicht verwendet.

**Status nie nur über Farbe.** Jede Statusform hat eine eigene Glyphe: Kreis = gesund,
Halbkreis = degradiert, Quadrat = down/kritisch, Dreieck = Warnung, gestrichelter Kreis =
unbekannt, schraffierter Kreis = Wartung, Ring = gelöst. In der Host-Leiste kommen Muster dazu
(Punktraster = unbekannt, Schraffur = Wartung, halbe Füllung = degradiert). Unbekannte Hosts
sehen also nie grün aus.

### Typografie

| Rolle | Schrift | Einsatz |
|---|---|---|
| Display | **Archivo** (variable Breite 62–125) | Überschriften leicht gedehnt (106–112 %), Labels schmal (78–88 %). Eine Familie, zwei Gesten. |
| Text | **Atkinson Hyperlegible** | Fließtext und Erklärungen. Gute Unterscheidbarkeit von Il1/O0 bei Hostnamen. |
| Daten | **IBM Plex Mono** | Hostnamen, Zeiten, Zahlen (tabular-nums) |

Typskala (px): 12 / 13 / 14 (Basis) / 16 / 20 / 25. Labels sind in Satzschreibung gesetzt und
schmal laufend, keine versalen Micro-Labels wie im aktuellen UI.

### Raum, Radius, Elevation

- Raster 4 px (`--s1`..`--s6` = 4/8/12/16/24/32).
- Radius 2 px für Chips, 4 px für Panels. Keine Pillen, kein `rounded-lg`.
- Elevation hat genau eine Bedeutung: **Das wichtigste offene Problem schwebt.** Nur der
  Incident-Cluster #1071 hat einen Schatten. Popover und Palette haben ebenfalls einen, alles
  andere liegt flach mit Haarlinie.
- Schweregrad am Problem: 4 px Streifen links, gefüllt (kritisch/Warnung), gestrichelt (unbekannt),
  schraffiert (Wartung), grau (Trend/gelöst).

---

## 2. Informationshierarchie

1. **Lagesatz.** Ein Satz, der die Lage benennt ("Ein kritisches Problem in Zürich: 7 Hosts hinter
   SW-ZH-CORE-02. Bern seit 11 Minuten nicht beobachtet."), dazu das Urteil "Not healthy".
2. **Host-Leiste.** Alle 48 Hosts als Zellen, schlimmste zuerst, mit Muster und Legende. Unterstrich
   = Teil eines offenen Incidents.
3. **Kontextfaden** (Signatur, s. u.). Zeitliche Ordnung und Zusammenhänge.
4. **Needs attention.** Nach Ursache gruppiert: *Open now*, *Getting worse*, *Quiet on purpose*.
5. **Next steps** und **Since your last visit** daneben.
6. **Impact-Map** und **Trends** als Kontext darunter.

Grundsatz: Erst die Zusammenfassung, dann die Begründung, dann die Rohdaten. Kein Element zeigt
eine Zahl ohne Einordnung.

## 3. Layout

- Desktop (1440): Rail 212 px links, Topbar 56 px. Inhalt maximal 1680 px breit. Zwei Spalten
  `1.65fr / minmax(320px, 1fr)`: links Probleme und Impact-Map, rechts Handlung und Veränderung.
- 1920: gleiche Struktur, Lagesatz und Host-Leiste nebeneinander, mehr Luft im Kontextfaden.
- 1280: Lagesatz und Leiste bleiben nebeneinander, unter 1180 untereinander.
- ≤ 860 px: Rail wird zur horizontal scrollbaren Leiste, Topbar bricht um, alles einspaltig. Der
  Kontextfaden und die Impact-Map scrollen innerhalb ihres Containers, die Seite selbst nie.

## 4. Navigation

Die Rail ist nach **Absicht** gruppiert, nicht nach Datenquelle:

- **Now**: Situation, Problems, Changes (Zähler = neu seit letztem Besuch), Next steps
- **Context**: Impact map, Hosts, Evidence (Logs, Metriken), Data freshness
- **Configure**: Correlation rules, Maintenance, Agents, Integrations

Heute hat Nodeglow 20+ gleichrangige Menüpunkte (Syslog, SNMP, SSL, Scanner, ...). In Konzept B
werden sie zu Evidenzquellen unter *Context* bzw. zu Konfiguration. Man navigiert vom Problem zur
Quelle, nicht umgekehrt.

Global: Befehlspalette (`Ctrl K` oder `/`) für Hosts, Incidents, Ansichten und Aktionen;
Zeitbereich (*Since 09:12* / *2 h* / *24 h*) steuert den Kontextfaden; Mandantenanzeige ist
sichtbar als **planned** markiert und deaktiviert. Natürlichsprachliche Fragen in der Palette
ebenfalls **planned**.

## 5. Dashboard und die 8 Fragen

| Frage | Antwort im Prototyp |
|---|---|
| Ist alles gesund? | Urteil "Not healthy" plus Lagesatz plus Host-Leiste (19 von 48 nicht gesund, davon 9 unbekannt, 1 Wartung). Unbekannt wird explizit gezählt, nicht weggelassen. |
| Gibt es kritische Probleme? | Cluster #1071 oben in *Needs attention*, erhöht, roter Streifen, Quadrat-Glyphe. |
| Welche Systeme sind betroffen? | Host-Chips im Cluster (2 down, 5 degradiert mit Latenz), Impact-Map mit Parent SW-ZH-CORE-02, Hover hebt alle Vorkommen hervor. |
| Was hat sich seit dem letzten Besuch geändert? | *Since your last visit* (Feed ab 09:12, Zusammenfassung 4 eröffnet / 2 gelöst / 2 neue Hosts / 1 Agent-Update) und die getönte Fläche im Kontextfaden ab der Markierung "last visit". "Mark all as seen" verschiebt die Marke. |
| Welche Incidents brauchen Aufmerksamkeit? | *Open now*: #1071, Blind Spot probe-bern-01, #1072, #1070, web-01. Sortiert nach Schweregrad und Anzahl betroffener Hosts. |
| Hängen Probleme zusammen? | Kontextfaden (Linien), Beweiskette mit Labels, "Linked by"-Zeile pro Problem. Ebenso explizit: *kein* Zusammenhang (web-01, #1072, Agent-Update SRV-RDS-01). |
| Gibt es negative Trends? | *Getting worse*: D: auf SRV-FILE-01 (91 %, voll ≈ 15.10.), Zertifikat portal (9 Tage). Im Kontextfaden rechts in der Spalte "Ahead". Trends-Panel mit Projektion. |
| Was ist der nächste Schritt? | *Next steps*: 7 Schritte, jeder mit Chips "Based on", die auf genau die Daten springen, aus denen er abgeleitet ist. |

Scenario-Hinweis: SCENARIO.md nennt "4 incidents opened (#1069–#1072)", beschreibt #1070 aber
nicht. Der Prototyp belegt #1070 mit dem einzigen dafür passenden Signal, dem `log_anomaly` auf
fw-zh-01 (Filterlog +420 %). Die Beziehung zu #1071 bleibt wie gefordert *Suspected*.

## 6. Darstellung der Operational Intelligence

### Label-System

| Label | Linie | Bedeutung | Quelle in Nodeglow |
|---|---|---|---|
| **Confirmed** | durchgezogen, kräftig | Strukturelle Verbindung | `build_topology()` → `parent_id` (manuell, Proxmox-Node, UniFi `sw_mac`), gleiche Probe, gleiche Integration; Topologie-Kaskade (`filter_upstream_failures`) |
| **Rule-based** | gestrichelt | Eine Korrelationsregel hat gefeuert | `services/correlation.py`: `multi_host_down`, `host_down_syslog`, `log_anomaly`, `agent_service`, ... (jeweils nach `min_cycles` = 2 Durchläufen) |
| **Suspected** | gepunktet, Chip mit gestricheltem Rand | Nur zeitliche Überlappung oder Baseline-Abweichung | Zeitfenster, Baselines |
| **Not available yet** | grau, mit "planned" | Geplante Analyse | z. B. Root-Cause-Ranking über Metriken, Change-Korrelation |

Regeln:
- Jede gezeigte Relation trägt ein Label. Ohne Label keine Linie.
- "Kein Zusammenhang gefunden" ist eine eigene, sichtbare Aussage (web-01, Agent-Update).
- Suspected wird nie in einen Cluster gemischt. #1070 steht als eigenes Problem da, mit Verweis.
- Die Filter im Kontextfaden blenden einzelne Label-Stufen aus ("nur Confirmed zeigen").

### Beweiskette

Ein Klick auf *Show evidence chain* öffnet die Kette als zeitlich geordnete Liste (die
Reihenfolge ist hier Information): 14:04 Syslog-Burst (Rule-based) → 14:05 Latenz auf 5 Hosts
(Confirmed, gleicher Parent) → 14:07 zwei Hosts down (Rule-based + Confirmed) → 14:07 sieben
Folgealarme unterdrückt (Confirmed, Kaskade) → 14:10 Filterlog fw-zh-01 (Suspected) →
Root-Cause-Ranking (Not available yet). Der Verbindungsstrich zwischen den Schritten übernimmt den
Linienstil des Labels. Jeder Schritt hat einen "Based on"-Link zur Datenquelle.

### Next steps

Regelbasiert, ohne Sprachmodell, und so gekennzeichnet. Jeder Schritt nennt seine Quelle(n) mit
Label. Wo Nodeglow selbst nicht handeln kann (Dienst starten), steht das dabei ("planned").

## 7. Signatur-Element: der Kontextfaden

Eine Zeitachse mit sechs Spuren: **Problems**, **Reachability & checks**, **Logs**, **Agents &
services**, **Data freshness**, **Changes & maintenance**. Balken laufen, solange ein Zustand
anhält; Rauten sind einmalige Änderungen. Zwischen den Ereignissen verlaufen die Relationslinien
im Linienstil ihres Labels.

Was er besser kann als Liste oder Graph:

1. **Reihenfolge sichtbar.** Der Port flappt um 14:04, die Latenz steigt um 14:05, die Hosts fallen
   um 14:07. Die Ursache liegt links von der Wirkung, ohne dass man Zeitstempel vergleicht.
2. **Quellen nebeneinander.** Logs, Ping, Agent, Probe und Wartung liegen auf einer Achse.
   Heute verteilen sie sich auf Syslog-, Hosts-, Agents- und Maintenance-Seiten.
3. **Gewissheit lesbar.** Die durchgezogene Linie (Topologie) und die gepunktete (fw-zh-01) sieht
   man sofort als verschieden an.
4. **Datenlücken als Ereignis.** probe-bern-01 erscheint als Balken mit Punktraster. Fehlende
   Daten sind ein Zustand und keine leere Stelle.
5. **Seit letztem Besuch.** Die Marke "last visit 09:12" und die getönte Fläche zeigen, was neu ist.
6. **Ahead.** Rechts von *now*: Wartung bis 15:00 (schraffiert, geplant), am Rand die Prognosen
   (D: voll ≈ 15.10., Zertifikat 19.10.).

**Stückweise lineare Zeitachse.** Ruhige Stunden werden gestaucht, die letzte Stunde gedehnt. Die
Bruchstelle ist gezeichnet und beschriftet ("time compressed before 13:30" / "last hour
stretched"). So bleiben fünf Stunden Kontext sichtbar, ohne dass die entscheidenden Minuten
zusammenfallen. Im Modus *2 h* ist ab 14:00 gedehnt, im Modus *24 h* liegt der gesamte Vortag
im ersten Segment.

Interaktion: Ein Ereignis auswählen (Klick, Enter, Leertaste) stellt alles Unverbundene zurück.
Der Inspector darunter listet jede Relation mit Label, Richtung ("leads to" / "comes from") und
Begründung, oder die explizite Aussage, dass kein Zusammenhang besteht. Esc hebt die Auswahl auf.

## 8. UX-Strategie

- **Erklären statt anzeigen.** Jede Zahl hat einen Vergleich (0.3 ms → 38 ms, 91 % → voll in 5 d,
  Baseline 8–14/min → 46/min).
- **Nach Ursache gruppieren.** 7 Host-Alarme erscheinen als ein Problem. Ruhige Dinge (Wartung,
  gelöst) bekommen eine eigene Gruppe *Quiet on purpose*, damit sie nicht als vergessen gelten.
- **Hover-Tracing.** Fährt man über einen Hostnamen, egal wo, werden alle Vorkommen, sein Parent
  bzw. seine Kinder und die zugehörigen Fadenereignisse hervorgehoben. Ein Tooltip nennt Zustand,
  Parent mit Quelle (UniFi client table / manual / Proxmox) und Incident. Funktioniert auch per
  Tastaturfokus.
- **Ein Drill-down: Incident-Workspace #1071.** Beweiskette, Latenzchart der 7 Hosts (log-Skala,
  Band der 5 degradierten plus Median, die 2 ausgefallenen enden mit ×), Burst des Log-Templates pro
  Minute (38 in 10 min), betroffene Hosts mit Parent-Quelle, Next steps für diesen Incident und
  ein Abschnitt "What Nodeglow cannot tell you yet".
- **Ehrlichkeit.** "Simulated data" in Topbar, Footer und Workspace. Alles Geplante ist
  gestrichelt umrandet und mit "planned" markiert. Unbekannt ist nie grün.

## 9. Skalierung

| Hosts | Was sich ändert |
|---|---|
| **10** | Host-Leiste mit großen Zellen, Kontextfaden meist leer bis auf Changes. Lagesatz wird "All 10 hosts healthy, nothing new since 09:12". Impact-Map kann die ganze Topologie zeigen. |
| **100** | Wie im Prototyp (48). Leiste 2–5 Reihen. Faden bleibt bei 6 Spuren, Ereignisse sind bereits Aggregate ("5 hosts 3–40 ms"). |
| **1'000** | Host-Leiste wird zu Gruppenstreifen (pro Site / Gruppe ein proportional geteilter Balken, Klick öffnet die Gruppe). Faden: pro Spur max. N Ereignisse, Rest als "+12 more" bündeln; Aggregation pro Parent ist Pflicht. Impact-Map zeigt nur Teilbäume offener Probleme (heute schon so gedacht). |
| **10'000** | Site-/Mandanten-Ebene als erste Hierarchie (Mandanten sind planned). Lagesatz pro Site. Faden zeigt Probleme und Changes; Rohevidenz nur nach Auswahl. Suche/Palette wird primäre Navigation. Server-seitige Aggregation nötig (s. Abschnitt 12). |

Die Grundidee skaliert, weil sie auf **Problemen** statt auf Hosts aufbaut. Die Zahl offener
Probleme wächst viel langsamer als die Zahl der Hosts, besonders wenn die Topologie-Kaskade
greift.

## 10. Barrierefreiheit

- Status immer als Form + Farbe + Text. Labels als Linienstil + Text.
- Kontraste: Text ≥ 4.5:1 in beiden Themes; Statustexte nutzen dunklere bzw. hellere Varianten
  (`--warn` #7F5000 hell) als die Füllfarben.
- Tastatur: Skip-Link, alle Fadenereignisse fokussierbar (`role="button"`, `aria-pressed`),
  Enter/Leertaste wählt, Esc hebt auf; Zeitbereich als Radiogroup mit Pfeiltasten; Palette mit
  `combobox`/`listbox`, Pfeile/Enter/Esc, Fokus kehrt zurück.
- Hover-Tracing ist auch per Fokus verfügbar; Tooltip `role="tooltip"`.
- Charts sind SVG mit `role="img"` und einer `aria-label`-Zusammenfassung der Kernaussage.
- `aria-live` am Inspector, `aria-expanded` an der Beweiskette.
- `prefers-reduced-motion` schaltet Animationen und Smooth-Scroll ab.
- Sichtbarer Fokusring im Akzent.

## 11. Machbarkeit auf dem aktuellen Stack

| Teil | Umsetzung |
|---|---|
| Shell, Rail, Palette | Next.js App Router Layout; Palette als Client-Komponente. Tailwind-Tokens über die vorhandene `build-tokens.mjs`-Pipeline. |
| Fonts | `next/font/google` für Archivo (variable `wdth`), Atkinson Hyperlegible, IBM Plex Mono. |
| Host-Leiste | Reines CSS-Grid, keine Chart-Library. |
| Kontextfaden | Eigene SVG-Komponente (React). ECharts *custom series* wäre möglich, aber stückweise Achse, Spurlayout, Relationspfade und Tastaturfokus sind mit eigenem SVG einfacher und zugänglicher. Rund 400 Zeilen. |
| Beweiskette, Problems, Next steps | Normale Komponenten, Daten aus Incidents + Events. |
| Impact-Map | Bestehende Topology-Daten; Layout als Teilbaum (dagre/elkjs oder einfaches Spaltenlayout). |
| Trends / Latenz / Burst | ECharts (bereits im Projekt): Bars, Line mit `markArea`/`markLine`, log-Achse. Die Prototyp-Charts sind bewusst einfach genug dafür. |
| Theme | Vorhandenes Light/Dark über CSS-Variablen. |

Aufwand grob: Shell + Situation 1 Woche, Kontextfaden 1–1.5 Wochen, Workspace 1 Woche,
Backend-Ergänzungen (s. u.) 1–2 Wochen.

## 12. Backend-Daten, die es noch nicht gibt

1. **Relationen als Daten.** Heute steckt der Zusammenhang implizit in `Incident.rule`,
   `host_ids` und im Topologie-Cache. Benötigt: eine Tabelle/API `incident_relations`
   (`from_event`, `to_event`, `label` ∈ confirmed/rule/suspected, `reason`, `source`), die die
   Korrelationsregeln beim Feuern schreiben.
2. **Herkunft der Parent-Links.** `build_topology()` liefert nur `{host_id: parent_id}`. Für das
   Label und den Tooltip braucht es die Quelle (`manual` / `proxmox` / `unifi_client`).
3. **Unterdrückte Folgealarme zählen** (Kaskade): Anzahl und Liste pro Incident.
4. **Suspected-Kandidaten.** Ein Job, der zeitlich überlappende Baseline-Abweichungen anderer Hosts
   zu einem offenen Incident sucht und als *suspected* ablegt, ohne zu mergen.
5. **"Seit letztem Besuch".** Pro Benutzer `last_seen_at` plus ein vereinheitlichter
   Event-Stream (Incident opened/resolved, Wartung, Agent-Update, Scanner-Fund, Probe-Ausfall).
   Teile existieren verstreut (Audit Log, Incidents, Scanner).
6. **Probe-Freshness als Zustand** auf Host-Ebene (`unknown` mit Grund und Zeitpunkt), damit
   die UI nie "up" aus alten Daten ableitet.
7. **Timeline-Endpoint**, der für ein Zeitfenster bereits aggregierte Ereignisse pro Spur liefert
   (wichtig ab 1'000 Hosts).
8. **Next-Step-Regeln** als kleine, testbare Regeltabelle (Bedingung → Text → Quellen), damit
   jeder Vorschlag nachvollziehbar bleibt.
9. **Geplant und so markiert:** Root-Cause-Ranking über Metriken, Change-Korrelation
   (Config-Backup-Diffs), Dienste aus Nodeglow starten, Mandanten.
