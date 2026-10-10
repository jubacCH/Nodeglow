# Konzept A — „Precision Operations“

Prototyp: [`../prototypes/concept-a/index.html`](../prototypes/concept-a/index.html) (eine Datei,
simulierte Daten aus [`SCENARIO.md`](../prototypes/SCENARIO.md)).

> Kurzform: Nodeglow als ruhiges, präzises Messinstrument. Kein Kontrollraum mit Globus und Glow,
> sondern eine Arbeitsfläche, die in einem Satz sagt, was los ist, und dann auf **einer Zeitachse**
> zeigt, was womit zusammenhängt und wie sicher Nodeglow sich dabei ist.

---

## 1. Identität

### Charakter

Vorbild sind Messgeräte, Fahrpläne und technische Zeichnungen, nicht Sci-Fi. Das Interface soll
sich anfühlen wie ein gut kalibriertes Instrument: Haarlinien statt Karten-Schatten,
monospaced Werte, die exakt untereinander stehen, eine einzige Akzentfarbe, die nur für Auswahl,
Fokus und die „Jetzt“-Linie benutzt wird. Ruhe ist der Normalzustand. Farbe erscheint nur dort,
wo ein Zustand vom Normalen abweicht.

Das Glow-Motiv des Namens bleibt erhalten, aber funktional: Die **Jetzt-Linie** in Zeitachsen
und das Logo (Messpunkt im Fadenkreuz) haben einen dezenten Lichthof. Im Dark Mode leuchtet
genau dieses eine Element, sonst nichts.

### Palette

| Rolle | Light | Dark | Begründung |
|---|---|---|---|
| Papier (Grund) | `#F1F2EF` | `#0F1214` | kühles „Instrumenten-Weiss“ mit leichtem Grün-Grau-Stich bzw. Graphit. Bewusst **kein** Navy (bisheriges Nodeglow) und kein Creme. |
| Fläche | `#FAFAF8` | `#161A1D` | Panels heben sich nur minimal ab, getrennt durch 1px-Linien. |
| Tinte 1/2/3 | `#121518` / `#3C444A` / `#5B646A` | `#E6E9EB` / `#B4BCC1` / `#8C969C` | drei Textstufen, alle ≥ 4.5:1 auf Fläche. |
| Akzent | Ultramarin `#2E36C4` | `#A4AAFF` | Tinte eines technischen Zeichners. Klar getrennt vom bisherigen Sky/Cyan und von allen Statusfarben. Nur für Auswahl, Fokus, „heute/jetzt“. |
| Status | Kritisch/Down `#BC2216`, Warnung `#A64A00`, Degradiert `#7F6200`, Unbekannt `#646C72`, Wartung `#4F5C80`, Gesund `#1C7142` | aufgehellte Varianten | Status ist **nie nur Farbe**: jede Stufe hat eine eigene Form. |

Statusformen (durchgängig in Liste, Matrix, Zeitachse, Legende):

| Zustand | Form |
|---|---|
| Down / kritisch | gefülltes Quadrat ■ |
| Warnung | Dreieck ▲ |
| Degradiert | halb gefüllter Kreis ◐ |
| Unbekannt / veraltet | gestrichelter Kreis ◌, Flächen schraffiert |
| Wartung | Raute ◇ |
| Gesund | kleiner Punkt ● (bewusst leise) |

Unbekannt bekommt Schraffur statt Fläche, damit veraltete Daten **nie** wie gesund aussehen.

### Typografie

- **Instrument Sans** (UI, Fliesstext, Überschriften). Die variable Breitenachse wird für
  Überschriften auf ~90 % gestaucht: kompakt, technisch, ohne „Display-Schrift“-Pose.
- **IBM Plex Mono** (alle Werte, Hostnamen, Zeiten, Regeln, Logzeilen). Hostnamen und Zahlen
  sind die eigentlichen Daten, sie bekommen eine eigene Stimme und stehen tabellarisch.
- Fallbacks: Segoe UI Variable / system-ui bzw. Cascadia Mono / Consolas.
- Schweizer Details: Tausendertrennzeichen `11’622`, Datum `10.10.2026`, 24h-Zeit, „CEST“.

Typ-Skala (px): 10.5 · 11 · 12 · **13 (Basis)** · 15 · 19 · 25. Uppercase nur für Mikro-Labels
(Gruppentitel, Relationslabel) mit +0.05em Laufweite, nicht als Allzweck-Stil.

### Raster, Radius, Elevation

- 4px-Basis (4/8/12/16/24/32). Dichte-Umschalter verkleinert Zeilenhöhe (30 → 24px) und Panel-Padding.
- Radius 2–3px. Bewusst hart; keine `rounded-lg`-Kacheln.
- Elevation: keine. Ebenen werden durch Linien getrennt. Schatten gibt es nur für Dinge, die
  wirklich über der Fläche liegen (Drawer, Command-Menü, Tooltip).

---

## 2. Informationshierarchie und Layout

Gelesen wird von oben nach unten, vom Urteil zur Evidenz:

1. **Lagesatz** (links) + **Bestandsleiste** (rechts): ein formulierter Satz statt vier KPI-Kacheln.
   Die Leiste zeigt alle 48 Hosts als je eine Zelle, nach Schwere sortiert, plus drei Fakten
   (Datenaktualität 39/48, Verfügbarkeit 30 d, Syslog 24 h).
2. **Needs attention** (breit) + **Since your last visit** (schmal): die Arbeitsliste und das
   Änderungsprotokoll seit 09:12.
3. **Causality strip** (volle Breite): das Signature-Element, siehe §5.
4. **Estate by site and parent** + **Risks & trends**: Bestand nach Standort und
   Topologie-Parent; Trends mit expliziten Zeitfenstern.

Shell: feste Index-Leiste links (224px), schmale Topbar mit Breadcrumb, Command-Trigger
(Ctrl K, `/`), Zeitraum, „Since 09:12“, Dichte, Theme und dem Marker **Simulated data**.

### Navigation (IA)

Gruppiert nach Tätigkeit statt nach Datenquelle:

- **Operate** — Situation, Incidents, Hosts, Logs
- **Analyse** — Topology, Trends & capacity, Changes
- **Configure** — Rules & alerting, Checks & SNMP, Integrations, Agents, Discovery
- **Admin** — Users & access, System

Heute hat Nodeglow 20 gleichrangige Menüpunkte (SNMP, SSL, Credentials, Bandwidth, Scanner …).
Diese werden zu Unterseiten der Gruppen. Der Tenant-Indikator unten links ist sichtbar als
**Planned** markiert und nicht bedienbar.

---

## 3. Dashboard-Konzept: die 8 Fragen

| Frage | Antwort im Prototyp |
|---|---|
| Ist alles gesund? | Lagesatz + Zustandspille „Impaired“; Bestandsleiste mit 48 Zellen und Legende mit Formen und Zahlen. |
| Gibt es kritische Probleme? | Erster Eintrag in *Needs attention* (#1071, kritisch, Quadrat-Symbol), Lagesatz nennt ihn zuerst. |
| Welche Systeme sind betroffen? | Estate-Gruppe „downstream of SW-ZH-CORE-02“ ist rot hinterlegt und mit #1071 markiert; Drawer listet die 7 Hosts mit Latenz und Zeitpunkt. |
| Was hat sich seit dem letzten Besuch geändert? | *Since your last visit* (09:12 → 14:32) mit Zeitstempeln und Summenzeile; „Since 09:12“-Schalter markiert neue Einträge mit NEW. |
| Welche Incidents brauchen Aufmerksamkeit? | *Needs attention*, sortiert nach Schwere und Nähe der Auswirkung; Incidents, Datenlücke, fehlgeschlagener Check, Kapazität, Zertifikat, Wartung (bewusst stummgeschaltet). |
| Welche Probleme hängen zusammen? | Causality strip gruppiert Lanes nach Relation zum gewählten Incident, mit Label *Confirmed / Rule-based / Suspected* und passendem Linienstil; Drawer erklärt jede Relation mit Quelle. |
| Gibt es negative Trends? | *Risks & trends*: D: 91 % mit Linear-Projektion bis 15.10., Syslog-Spike, Incidents/Tag, Verfügbarkeit vs. Ziel, Zertifikat 9 Tage. |
| Was ist die nächste Aktion? | Jeder Eintrag in *Needs attention* hat eine Zeile **Next step**; Drawer wiederholt sie oben. Acknowledge und Feedback (Real problem / Noise) funktionieren (simuliert); Assign und Runbook sind als *Planned* markiert. |

---

## 4. Operational Intelligence: Darstellung

- **Ehrlichkeitsregel als Gestaltungsmittel.** Jede Relation trägt Label *und* Linienstil:
  durchgezogen = Confirmed (Topologie-Parent), Strich-Punkt = Rule-based (Korrelationsregel),
  gepunktet = Suspected (nur zeitliche Überlappung/Baseline), dünn grau = Not available yet.
  Der gleiche Code erscheint in Legende, Strip, Drawer. So lernt man ihn einmal.
- **Unsicherheit wird gezeigt, nicht versteckt.** Die 9 Bern-Hosts stehen schraffiert in einer
  eigenen Gruppe „no current data“; die Probe-Lane zeigt den Übergang heartbeat → Toleranz (3 min)
  → stale. In der Bestandsleiste sind sie nicht grün.
- **Was nicht zusammenhängt, wird auch gesagt.** Die Gruppe „No relation established“ im Strip
  (Veeam, Probe, intranet bei #1071) verhindert, dass zeitgleiche Ereignisse als Ursache gelesen
  werden. Bei #1072 dreht sich die Gruppierung um.
- **Kein KI-Theater.** Root-Cause-Ranking über Metriken hinweg ist explizit *Not available yet*.
  Der „Next step“ ist ein redaktioneller Text pro Regel-Typ, keine erfundene Diagnose.

---

## 5. Signature-Element: der Causality Strip

**Was es ist.** Ein Swimlane-Diagramm mit **einer gemeinsamen Zeitachse** (1-Minuten-Auflösung,
1 h oder 2 h), in dem jede Evidenzquelle eine Lane bekommt: Syslog-Rate des Switches,
Latenzband der 7 Hosts (log-Skala 0.1–100 ms, Min-Max-Band + Median), Erreichbarkeit der
2 Down-Hosts, Filterlog-Verhältnis zur Baseline, Agent-Reports, Probe-Heartbeat, HTTP-Check.
Die Lanes sind **nach ihrer Relation zum gewählten Incident gruppiert und sortiert**
(Confirmed → Rule-based → Suspected → keine Relation). Markierungen auf der Achse zeigen
Schlüsselmomente; eine leuchtende Jetzt-Linie schliesst rechts ab.

**Interaktion.** Ein Fadenkreuz (Maus oder Pfeiltasten, Shift = 10 min, Home/End) zeigt für die
gewählte Minute alle Lanes gleichzeitig im Tooltip und als `aria-live`-Text. Die Liste der
Schlüsselmomente unter dem Strip setzt beim Hover/Fokus das Fadenkreuz. Ein Klick auf einen
Incident in *Needs attention* gruppiert den Strip neu.

**Operativer Vorteil.**
1. *Reihenfolge* wird sichtbar: 14:05 erster Link-Flap, 14:07 Hosts down, 14:10 Firewall.
   Wer zuerst kommt, ist ein Ursachen-Kandidat; was nachher kommt, eher Folge. Das ist die
   Frage, die heute 7 separate Alerts nicht beantworten.
2. *Gewissheit* wird sichtbar: man sieht sofort, dass die Firewall-Anomalie nur „suspected“
   ist und nicht auf denselben Fix wartet.
3. *Abgrenzung* wird sichtbar: Probe Bern (14:21) und Veeam (14:22) beginnen später und haben
   keine Relation. Sie brauchen eigene Massnahmen.
4. Er ersetzt das Springen zwischen Host-Detail, Syslog und Incident-Seite für die erste
   Triage-Minute.

---

## 6. UX-Strategie

- **Urteil vor Evidenz:** Satz → Liste → Zeitachse → Bestand. Jede Ebene ist für sich lesbar.
- **Eine Aktion pro Zeile:** „Context“ öffnet den Drawer, „Acknowledge“ quittiert; Hosts
  springen in die Estate-Matrix mit Readout (Zustand, letzte Daten, Parent, Incident-Link).
- **Ein Drill-down:** Incident-Drawer mit Next step, Relationen (mit Quelle), betroffenen Hosts,
  Log-Auszug und Timeline. Fokus springt hinein, Tab bleibt gefangen, Esc schliesst und gibt
  den Fokus zurück.
- **Command-Menü** (Ctrl K oder `/`): Hosts, Incidents, Aktionen; geplante Aktionen
  (Tenant-Wechsel) erscheinen deaktiviert mit *Planned*.
- **Dichte:** „Compact“ reduziert die Estate-Matrix auf reine Formzellen (Hostnamen im
  Tooltip/Readout) und verkleinert Zeilen. Gedacht für MSP-Bildschirme mit vielen Hosts.
- **Ruhe durch Weglassen:** gesunde Hosts sind gedämpft, Wartung ist ausdrücklich „muted“.

---

## 7. Skalierung

| Hosts | Lagesatz/Leiste | Estate | Strip | Needs attention |
|---|---|---|---|---|
| 10 | Leiste mit 10 breiten Zellen | Namen-Chips, eine Gruppe | wie Prototyp | kurz |
| 100 | 1 Zelle pro Host funktioniert noch (≥ 6px) | Gruppen nach Site/Parent, Compact-Modus | Lanes = Evidenz, nicht Hosts: Grösse unverändert | gruppiert nach Incident, nicht nach Host |
| 1’000 | Leiste wird zu proportionaler Stapelleiste mit Zählern; Zellen nur noch beim Hover auf ein Segment | Gruppen standardmässig eingeklappt, nur Gruppen mit Abweichungen offen; Filter „nur Probleme“ als Default | Host-Lanes werden zu Band-Lanes (Min/Median/Max, wie heute beim Latenzband) | Top-N mit „weitere 37 in 4 Gruppen“ |
| 10’000 | Tenant-/Site-Ebene als erste Stufe (Multi-Tenancy nötig) | Matrix pro Site als Heatmap-Kacheln (eine Zelle = Parent-Gruppe) | unverändert, weil pro Incident | serverseitig sortiert, paginiert, gespeicherte Sichten |

Wichtig: Der Strip skaliert mit der **Anzahl Evidenzquellen eines Incidents**, nicht mit dem
Bestand. Das ist der Grund, warum er das Signature-Element ist.

---

## 8. Barrierefreiheit

- Status immer Form + Text + Farbe; Schraffur für „unbekannt“ und „Wartung“.
- Kontraste: Text ≥ 4.5:1 in beiden Themes, Statusfarben im Light Mode dunkel genug für Text.
- Sichtbarer Fokus (2px Akzent-Outline); alle Steuerelemente sind Buttons mit `aria-pressed`
  bzw. Labels; Drawer und Command-Menü sind `role="dialog"` mit Fokus-Trap und Esc.
- Strip: per Tastatur bedienbar (Pfeile/Home/End), Werte als `aria-live`-Text, Schlüsselmomente
  als Button-Liste (Alternative zum Diagramm); SVGs mit `role="img"` + Beschreibung.
- `prefers-reduced-motion` schaltet Transitions ab; kein Inhalt hängt an Animation.
- Bei 400px: Navigation als Off-Canvas, Panels gestapelt, Strip und Tabellen in eigenem
  horizontalen Scroll-Container.

---

## 9. Technische Machbarkeit (Next.js 15 / Tailwind 3 / ECharts 6)

- **Tokens:** CSS-Variablen in `globals.css`, in `tailwind.config` als Farben gemappt
  (`bg-paper`, `text-ink-2`, `text-status-crit` …). Light/Dark über `data-theme` + Media-Query
  wie im Prototyp. Fonts via `next/font/google` (Instrument Sans, IBM Plex Mono).
- **Statusformen:** eine `<StatusGlyph state>`-Komponente (Inline-SVG), ersetzt die heutigen
  Farbpunkte.
- **Causality strip:** machbar mit ECharts (mehrere `grid`s mit geteilter `xAxis` und
  `axisPointer: {link: [{xAxisIndex: 'all'}]}`, `custom`-Series für Zustandsbalken und
  Schraffur via `decal`). Alternative: eigene SVG-Komponente (der Prototyp braucht ~150 Zeilen);
  für Tastaturbedienung und präzise Labels ist die eigene SVG-Variante einfacher.
- **Estate-Matrix, Bestandsleiste:** reines HTML/CSS-Grid, keine Chart-Library nötig.
- **Command-Menü:** z. B. `cmdk` oder eigene Komponente; bestehende Suche in der Sidebar wird ersetzt.
- **Drawer:** Route-Intercepting (`@modal`-Slot) in Next.js, damit `/incidents/1071` direkt
  verlinkbar bleibt.
- Der 3D-Globus und `three`-Abhängigkeiten entfallen auf dem Dashboard.

---

## 10. Benötigte Backend-Daten, die es noch nicht gibt

| Bedarf | Status heute | Was fehlt |
|---|---|---|
| Relation-Label pro Zuordnung (Confirmed / Rule-based / Suspected) | Incidents haben `rule` und `IncidentEvent`s, aber keine typisierte Relation | Tabelle `incident_relation` (incident_id, target_type, target_id, kind, source, evidence) |
| Verdächtige Relationen (Baseline-Abweichung im Zeitfenster) | Baselines existieren, werden nicht an Incidents gehängt | Job, der Abweichungen im Incident-Fenster als `suspected` anhängt |
| „Seit letztem Besuch“ | kein `last_seen` pro Benutzer | `user.last_dashboard_visit` + Endpunkt „changes since t“ (Incidents, Discovery, Agent-Updates, Wartung) |
| Zeitreihen pro Incident-Fenster in 1-min-Auflösung, gebündelt | einzelne Endpunkte pro Host/Quelle | `GET /incidents/{id}/evidence?from&to&step=60` mit allen Lanes in einer Antwort |
| Aggregierte Latenzbänder (min/median/max über Hostgruppe) | nur pro Host | serverseitige Aggregation (ClickHouse geeignet) |
| Probe-Heartbeat als Zustand mit Schwelle, „stale“ für abhängige Hosts | teils vorhanden; Hosts hinter stiller Probe dürfen nicht „up“ liefern | expliziter Zustand `unknown` + `stale_since` pro Host in der API |
| Linear-Projektion Disk mit Datum | Prognose nicht als API-Feld | `forecast_full_at` + Fit-Güte pro Volume |
| Next step pro Incident | nicht vorhanden | Textbausteine pro Regel/Check-Typ (statisch, versioniert) |
| Assign, Runbooks, Tenant-Wechsel, Root-Cause-Ranking | nicht vorhanden | im Prototyp als *Planned* markiert |

### Anmerkungen zum Szenario

- Das Szenario nennt „4 Incidents opened (#1069–#1072)“, beschreibt aber #1070 nicht. Der
  Prototyp zeigt die Summenzeile wie angegeben und listet #1070 nicht einzeln, statt Inhalte
  zu erfinden. #1068 (11:02–11:09) liegt ebenfalls nach 09:12; er erscheint im Protokoll als resolved.
- Die Inventur listet `SW-ZH-CORE-02` unter den 29 gesunden Hosts (er ist erreichbar). Der
  Prototyp zeigt ihn daher als „healthy, Quelle des Syslog-Bursts“, nicht als Warnung.
- 11’622 Syslog-Meldungen in 24 h passen rechnerisch nicht ganz zu einer Baseline von
  8–14/min; das Syslog-Diagramm zeigt die Baseline, die Kennzahl den Szenario-Wert.
