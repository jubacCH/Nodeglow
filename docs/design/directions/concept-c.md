# Konzept C: Strata

**Next-Generation Infrastructure Experience.** Prototyp: [`../prototypes/concept-c/index.html`](../prototypes/concept-c/index.html)
Szenario: [`../prototypes/SCENARIO.md`](../prototypes/SCENARIO.md) (alle Werte simuliert, im Prototyp sichtbar markiert).

Konzept C ersetzt den dekorativen 3D-Globus durch eine Visualisierung, die eine operative Frage
beantwortet: **Wo im Aufbau sitzt das Problem, und was hängt darunter?** Die Infrastruktur wird als
geologischer Schnitt gezeigt. Unten liegen Tenant und Standorte als Grundgestein, darüber Firewalls,
Core-Switches, Hypervisoren, und an der Oberfläche die Endgeräte. Die Schichten kommen direkt aus
den Topologie-Parents, die Nodeglow heute schon ableitet.

---

## 1. Identität

**Charakter:** Vermessungsinstrument und Kartenschnitt. Präzise, technisch, mit einer einzigen
mutigen Fläche (der Strata-Ansicht), alles andere ruhig. Abgrenzung:
- A („calm precise workspace“) ist ruhig und listenorientiert. C ist **räumlich/strukturell**.
- B („problem/context-centred“) erzählt über Zeitachsen und Kontext. C argumentiert über **Lage im Aufbau**.

**Palette** (Tokens im `:root`-Block des Prototyps, Hell und Dunkel vollständig):

| Rolle | Hell | Dunkel | Verwendung |
|---|---|---|---|
| Ground | `#EDF0F3` | `#0E131A` | Seitengrund, kühles Zeichenfilm-Grau |
| Surface | `#FFFFFF` | `#151C25` | Panels |
| Ink | `#131A23` | `#E2E8EF` | Text |
| Stratum | `#D6DCE3` | `#242E3B` | Aggregat-Schichten (Tenant, Standort) |
| Accent „Survey Cobalt“ | `#2A39B0` | `#9CA9FF` | nur Auswahl, Fokus, Navigation, Zoom-Pfad |

Status ist vom Akzent getrennt und hat je drei Tokens (Text / Zellfüllung / Glyphe auf Füllung):
Down Karmin, Warning Orange, Degraded Ocker, Unknown Schiefergrau **mit Schraffur**, Maintenance
Pflaume **mit Punktraster**, Healthy ein stilles Grün mit niedriger Sättigung, damit Probleme
hervortreten. Keine Firmenfarben (kein Petrol/Cyan), kein Neon, keine Verläufe.

**Status nie nur über Farbe:** jede Zustandsklasse hat eine Form (Healthy Punkt, Degraded Raute,
Warning Dreieck, Down Quadrat mit X, Unknown gestrichelter Ring, Maintenance Pausenbalken) plus
Muster (Schraffur/Punkte) und Textlabel. Unknown sieht nie gesund aus: eigene Schraffur, gestrichelter
Rand, eigenes Label „No data“.

**Typografie:**
- *Archivo* mit Breitenachse. Expanded (118–125 %) für Verdict-Headline und Wortmarke, normal für
  UI, **condensed (78 %) für Labels in der Strata**. Die Condensed-Breite ist funktional: mehr
  Hostnamen passen in schmale Zellen.
- *IBM Plex Mono* für Zahlen, Zeiten, IPs (tabular-nums).
- Skala: 11 / 12 / 13 / 14 (Basis) / 16 / 20 / 28 px.

**Raster, Radius, Elevation:** 16 px Gutter und Gap, 8/4 px intern. Radius 2 px (Instrument, nicht
„rounded-lg“). Keine Schatten auf Panels, nur 1-px-Linien. Schatten nur auf dem Drawer und der
Kommandopalette (echte Overlays).

## 2. Informationshierarchie

1. **Verdict** (eine Satz-Antwort auf „Ist alles gesund?“) und **Host-Ledger** (48 Ticks, ein Tick pro
   Host, nach Schwere sortiert). Kein Raster aus 4 gleichen KPI-Kacheln.
2. **Strata**: Lage und Blast Radius der Probleme.
3. **Needs attention**: nach Wirkung sortierte Queue, jede Karte mit Next Step und Korrelationslabels.
4. **Since your last visit** und **Drift** (negative Trends mit Projektion).

## 3. Layout

Desktop 1440: zweistufige Top-Bar (Navigation, darunter Kontext: Tenant, Suche, Zeitfenster,
„Simulated data“), Verdict-Band, dann drei Spalten `[Outline 232 | Strata flexibel | Queue 344]`,
darunter `Changes | Drift`. Unter 1360 px wandert die Outline unter die Strata, unter 900 px wird
alles einspaltig und die Queue rückt nach oben. Unter 560 px Breite schaltet die Strata automatisch
auf gruppierte Blätter, und die Outline bleibt als vollwertige Listenalternative.

## 4. Navigation (IA)

Horizontale Hauptnavigation statt Sidebar, nach Arbeitsmodus statt nach Datenquelle:
**Landscape** (Dashboard) · **Incidents** · **Changes** · **Trends** · **Inventory** · **Logs** ·
**Configure** (Rules, Integrations, Agents, Credentials, Users). Die heutigen 20 Sidebar-Einträge
(SNMP, SSL, Bandwidth, Scanner …) werden Filter bzw. Tabs in Inventory, Trends oder Configure.
Globale Suche und Kommandos über `Ctrl K` oder `/`. Der Tenant-Switch steht schon im Kontext-Bar,
ist aber als **planned** markiert.

## 5. Dashboard: die 8 Fragen

| Frage | Antwort im Prototyp |
|---|---|
| Ist alles gesund? | Verdict-Satz („No. A core uplink in Zürich is failing and Bern is unobserved.“) + Ledger 29/5/2/2/9/1 + 30-Tage-Verfügbarkeit 99.94 % (Ziel 99.90 %) |
| Kritische Probleme? | Strata: roter Blast-Radius-Rahmen um SW-ZH-CORE-02, Label „#1071 · blast radius 7 hosts · Confirmed“; Queue-Karte #1071 oben |
| Betroffene Systeme? | Die 7 Zellen über CORE-02 (2 Down, 5 Degraded), Inspector und Drawer listen sie; Bern-Probe mit gestricheltem „No data · 9 hosts unknown“-Rahmen |
| Was hat sich seit dem letzten Besuch geändert? | Liste „Since your last visit 09:12 → 14:32“ + Toggle „Changed since 09:12“, das geänderte Zellen in der Strata mit einer Ecke markiert |
| Welche Incidents brauchen Aufmerksamkeit? | Queue: #1071 kritisch, Bern Blind Spot, #1072 Warning, web-01 Check; Acknowledge direkt in der Karte |
| Zusammenhängende Probleme? | Relationen mit Labels: Confirmed (gemeinsamer Parent), Rule-based (Syslog-Burst), Suspected (fw-zh-01 filterlog +420 %, gestrichelt in der Strata), Not available yet (Root-Cause-Ranking, planned). Bern: „Link to #1071 · none found“ |
| Negative Trends? | Drift: Latenz der 7 Hosts (log-Skala), Syslog-Rate gegen Baseline-Band, D: auf SRV-FILE-01 mit gestrichelter Projektion (voll in ~5 Tagen), Zertifikat 9 Tage gegen 14-Tage-Schwelle, Incidents/Tag |
| Nächste Aktion? | Jede Queue-Karte hat einen „Next step“, der auf Evidenz zeigt (z. B. 38 × %LINK-3-UPDOWN auf Te1/0/48). Aktionen, die Nodeglow nicht kann (Restart via Agent), sind als planned markiert |

## 6. Operational Intelligence

- Korrelation wird **in der Struktur** gezeigt, nicht als Liste von Alerts: Ein Incident ist ein
  Rahmen um einen Ast. Die Rahmenart kodiert die Gewissheit (durchgezogen = Confirmed, gestrichelt =
  Suspected bzw. stale).
- Jede Relation trägt eines der vier Labels aus dem Szenario. Die Parent-Herkunft jedes Hosts ist
  sichtbar (z. B. „Confirmed · Proxmox snapshot · VM/LXC → node“ oder „UniFi client table · sw_mac → switch“).
- Unterdrückte Downstream-Alerts werden als Zahl gezeigt („7 downstream alerts folded in“), was der
  heutigen Logik in `filter_upstream_failures` bzw. den Regeln `upstream_failure` / `multi_host_down` entspricht.
- Das Zeitfenster (Live / 1 h / 24 h / 7 d) steuert Marker in der Strata: Hosts, die im Fenster ein
  Problem hatten und wieder gesund sind, bekommen einen Ring (intranet #1069, AP-ZH-OG3 #1068).

## 7. Signatur-Element: Strata

**Was es ist:** ein Icicle- bzw. Partition-Layout, auf den Kopf gestellt (Wurzel unten). Jede
Zeile ist eine Topologie-Tiefe, jede Zelle ein Host oder Aggregat, die Breite ist die Anzahl Hosts
im Ast. Kinder liegen exakt über ihrem Parent. Eine Lücke über einem Parent ist der Parent selbst.

**Datenbasis (heute schon vorhanden):** `build_topology()` liefert `{host_id: parent_id}` aus
manuellem `parent_id`, Proxmox (VM/LXC → Node) und UniFi (Gateway → Switch → AP, Client-Tabelle
über `sw_mac`). Die Standort-Ebene leitet sich aus der Collector-Zuordnung ab (direkt vs.
`probe-bern-01`).

**Operativer Vorteil gegenüber Globus, Liste oder Force-Graph:**
1. **Blast Radius auf einen Blick.** Was über einer kaputten Zelle liegt, ist betroffen. Man muss
   keine Kanten verfolgen. CORE-02 mit 7 Hosts darüber ist sofort als Ursache-Kandidat lesbar,
   während die Liste 7 einzelne Alarme zeigen würde.
2. **Ursache vs. Symptom ist Geometrie.** Ein Problem tief unten (breit) ist strukturell, viele kleine
   Probleme oben verstreut sind Einzelfälle. Das unterscheidet #1071 (Ast) von #1072 (Einzelzelle) ohne Lesen.
3. **Blinde Flecken sind sichtbar.** Bern erscheint als schraffierte Säule mit gestricheltem Rahmen,
   nicht als grüne Fläche. Der Schaden „9 Hosts ohne Daten“ hat dieselbe räumliche Sprache wie ein
   Ausfall, aber eine klar andere Textur.
4. **Stabile Lage.** Ein Layout ohne Physik: dieselbe Infrastruktur sieht jedes Mal gleich aus, und
   man lernt „Bern ist rechts, die Hypervisoren links“. Ein Force-Graph springt bei jedem Laden.
5. **Skaliert durch Aggregation** (siehe Abschnitt 9), weil Breite additiv ist.

**Interaktion:**
- Hover/Fokus: Inspector unter der Strata (live region) mit Zustand, Lineage-Pfad, Parent-Herkunft
  mit Label, Anzahl betroffener Hosts darunter. Nicht betroffene Äste werden gedimmt.
- Klick auf einen Ast: Zoom (Re-Root) mit Breadcrumb. Klick auf ein Blatt: Host-Drawer. Klick auf das
  Blast-Radius-Label: Incident-Drawer. Backspace zoomt heraus.
- Tastatur in der Strata: ← → Nachbarn, ↑ Kinder (Richtung Oberfläche), ↓ Parent, Enter zoomt
  oder öffnet, Space öffnet Details.
- Synchronisierte **Outline** (ARIA-Tree, Roving Tabindex): dieselbe Auswahl, dieselben Zustände
  als Text. Auswahl in einer Ansicht markiert die andere.
- Toggles: Blast radius, Changed since 09:12, Group leaves by state, Scale (Szenario 48 / Preview 10 000).

## 8. UX-Strategie

- **Erst urteilen, dann zeigen:** Verdict-Satz vor der Visualisierung, damit niemand die Strata
  „lesen lernen“ muss, um zu wissen, ob es brennt.
- **Ein Drill-down:** ein Drawer (Host oder Incident) statt Seitenwechsel. Die volle Hostseite ist
  explizit „nicht im Prototyp“.
- **Ehrlichkeit:** „Simulated data“-Stempel dauerhaft in der Kontextleiste, planned-Badges in
  gepunktetem Rahmen, deaktivierte Navigation mit Hinweis.
- **Kontrollierte Bewegung:** nur ein kurzer Fade beim Zoom. Keine Ambient-Animation, kein Pulsieren.
  Bei `prefers-reduced-motion` gar keine Animation.

## 9. Skalierung

| Hosts | Darstellung |
|---|---|
| 10 | Alle Zellen beschriftet, keine Gruppierung nötig |
| 100 | Szenario-Niveau. Oberflächenzellen ~15–20 px, Glyphe ohne Namen. Namen per Hover oder Zoom |
| 1 000 | Blätter **gruppiert nach Zustand pro Parent** („24 healthy“, „1 down“). Infrastruktur-Ebenen bleiben einzeln |
| 10 000 | Wie 1 000, zusätzlich **Mindestbreite 4 px für Problemzellen** (Breiten dann bewusst nicht streng proportional, im Legendentext erklärt). Äste unter 3 px werden in der Produktion zu „+N“-Bündeln. Ab Standort-Ebene zoomen, Breadcrumb als Orientierung |

Der Prototyp enthält eine synthetische **Preview mit 10 000 Hosts** (12 Standorte, ein synthetischer
Switch-Ausfall in Basel, ein stiller Collector in Lugano), klar als Layout-Demonstration markiert.
Die Outline lädt Äste lazy.

## 10. Barrierefreiheit

- Nicht-visuelle Alternative: die **Outline** ist ein vollständiger ARIA-Tree mit Zustand,
  Host-Anzahl und „changed since 09:12“ im accessible name. Sie ist kein Fallback zweiter Klasse:
  Auswahl, Zoom (Enter) und Details sind dort genauso erreichbar.
- Die Strata selbst ist eine fokussierbare Gruppe mit Pfeiltasten-Navigation. Der Inspector ist
  `aria-live="polite"`, angesagt wird beim Bewegen der Zustand.
- Status immer als Form + Muster + Text. Kontrast der Glyphen auf Füllung ≥ 3:1, Texte ≥ 4.5:1 in
  beiden Themes.
- Sichtbarer Fokusring im Akzent, Drawer mit Fokusfalle und Esc, Kommandopalette als Combobox mit
  `aria-activedescendant`. Charts haben `role="img"` mit ausformuliertem Label.

## 11. Machbarkeit auf dem heutigen Stack

- **Next.js / Tailwind:** Tokens als CSS Custom Properties passen direkt zu Tailwind (`theme.extend.colors`
  mit `var(--…)`). Archivo und IBM Plex Mono über `next/font/google`.
- **Strata-Rendering:** SVG genügt bis einige hundert Zellen (mit Gruppierung auch bei 10 000 Hosts).
  Für sehr große Bäume Canvas mit derselben Layout-Funktion und Hit-Testing über die Layout-Rechtecke.
  ECharts hat einen `sunburst`/`treemap`-Typ, aber kein umgedrehtes Icicle mit Overlays. Eigene
  Komponente ist einfacher, das Layout ist ~40 Zeilen (Partition nach Gewicht).
- **Daten:** `build_topology()` liefert die Parents bereits. Host-Status, Incidents, Regeln
  (`multi_host_down`, `host_down_syslog`, `upstream_failure`), Acknowledge und Wartungsfenster existieren.

## 12. Backend-Daten, die noch fehlen

1. **Topologie-API** mit Parent-Herkunft pro Kante (`source: manual|proxmox|unifi_device|unifi_client`).
   Heute gibt `build_topology()` nur `{id: parent}` zurück, die Herkunft geht verloren.
2. **Standort/Collector-Zuordnung** als explizites Feld (welcher Probe, welche Site).
3. **Probe-Heartbeat und Stale-Zustand** als eigener Host-Status `unknown`, propagiert auf alle Hosts
   der Probe.
4. **„Since last visit“:** pro User gespeicherter letzter Besuch und ein Changes-Feed (State-Wechsel,
   Incidents, Agent-Updates, Scanner-Funde).
5. **Aggregations-Endpoint** für große Bestände: Zählungen pro Ast und Zustand serverseitig, damit
   der Client bei 10 000 Hosts nicht alle Hosts lädt.
6. **Relation-Labels** für Incidents (Confirmed / Rule-based / Suspected) als Feld statt implizit
   im Regelnamen; Baseline-Abweichungen (filterlog +420 %) als „Suspected“-Kandidaten.
7. **Linear-Projektion** für Volumes (heute nur Momentwert).

## 13. Risiken

- **Lernkurve:** Ein umgedrehtes Icicle ist ungewohnt. Gegenmittel: Verdict-Satz oben, Zeilenlabels
  (TENANT, SITE, DEPTH n), Legende mit Lesehilfe, Inspector-Text in Klartext. Mit 3–5 Nutzern testen,
  ob „was liegt über der roten Zelle“ ohne Erklärung verstanden wird.
- **Topologie-Qualität:** Die Strata ist nur so gut wie die Parent-Links. Hosts ohne Parent landen
  direkt auf der Standort-Ebene (flache, breite Zone). Das muss sichtbar sein („42 ohne Parent“) und
  einen Weg zum Zuordnen bieten, sonst sieht schlechte Datenlage nach flacher Infrastruktur aus.
- **Proportionalität bei Mindestbreiten:** Bei 10 000 Hosts ist eine Problemzelle breiter als ihr
  Anteil. Das ist gewollt, muss aber in der Legende stehen (im Prototyp umgesetzt).
- **Performance:** Re-Layout bei jedem Status-Update. Layout ist O(n), Rendering nur sichtbarer
  Zellen. Bei > 2 000 Zellen auf Canvas wechseln und Updates auf 1×/5 s drosseln.
- **Mehrere Parents:** Nodeglow modelliert genau einen Parent pro Host. Redundante Uplinks (CORE-01
  und CORE-02) lassen sich im Baum nicht abbilden. Das ist eine bewusste Vereinfachung, die im
  Inspector benannt werden sollte, sobald das Backend Redundanz kennt.
