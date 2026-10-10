# Designrichtungen: Vergleich und Empfehlung

Stand 10.10.2026. Grundlage: drei isolierte Dashboard-Prototypen mit demselben simulierten
Szenario (`prototypes/SCENARIO.md`), das UX-Audit (`01-ux-audit.md`) und die neue
Informationsarchitektur (`02-information-architecture.md`).

**Empfehlung:** Konzept A „Precision Operations“ als Fundament (Designsystem, App-Shell,
Dashboard-Aufbau), ergänzt um die Strata-Landschaft aus Konzept C als Infrastruktur-Signatur
und um die Evidenzkette mit Quellenangabe aus Konzept B im Incident-Workspace.

## Nachtrag: Konzept D „Calm“

Rückmeldung zu A–C: zu unübersichtlich. Konzept D vereinfacht A radikal
(`directions/concept-d.md`, `prototypes/concept-d/index.html`):

- Die Startseite zeigt nur noch drei Dinge: Lage in einem Satz mit Host-Balken,
  „Jetzt zu tun“ (höchstens 4 Einträge, je eine Zeile) und „Neu seit 09:12“ (3 Zeilen).
- Zeitachse, Zusammenhänge und Logs stehen im Drawer, je ein Abschnitt offen.
- Bestand und Trends liegen unter Infrastruktur; die Navigation hat 5 Punkte.

**Neue Empfehlung:** D als Startseite und Grundton. Die Elemente aus A–C (Causality
Strip, Strata, Evidenzkette) kommen nur dort zum Einsatz, wo jemand bewusst tiefer
schaut: im Drawer, im Incident-Workspace und unter Infrastruktur.

## Die drei Konzepte

| | A · Precision Operations | B · Operational Intelligence | C · Strata |
|---|---|---|---|
| Charakter | Ruhiges Messinstrument: Haarlinien, harte Ecken, Farbe nur bei Abweichung | Lagebild: Probleme, Veränderungen, Zusammenhänge | Vermessung und Kartenschnitt: Infrastruktur als Schichtenlandschaft |
| Typografie | Instrument Sans + IBM Plex Mono | Archivo (gedehnt/schmal) + Atkinson Hyperlegible + IBM Plex Mono | Archivo (breit/schmal) + IBM Plex Mono |
| Akzent | Ultramarin, nur Auswahl, Fokus, „Jetzt“-Linie | Ultramarin als „Beziehungs-Tinte“ | Kobalt, nur Auswahl, Fokus, Navigation |
| Navigation | Linke Leiste nach Tätigkeit: Operate, Analyse, Configure, Admin | Linke Leiste nach Absicht: Now, Context, Configure | Horizontal nach Arbeitsmodus: Landscape, Incidents, Changes, Trends, Inventory, Logs, Configure |
| Signatur-Element | **Causality Strip:** alle Evidenzquellen eines Incidents auf einer Zeitachse, Zeilen nach Beziehungs-Label gruppiert | **Kontextfaden:** sechs Spuren auf einer gestauchten/gedehnten Zeitachse, Verbindungen mit Linienstil je Label | **Strata:** umgedrehtes Icicle aus den echten Topologie-Parents, Blast-Radius als Fläche, Bern als schraffierte „keine Daten“-Säule |
| Drill-down | Incident-Drawer | Incident-Workspace | Host- und Incident-Drawer |
| Prototyp | `prototypes/concept-a/index.html` | `prototypes/concept-b/index.html` | `prototypes/concept-c/index.html` |

Alle drei sind in hell und dunkel, bei 1280–1920 px und bei 400 px geprüft, ohne
Konsolenfehler und ohne horizontales Scrollen.

## Was alle drei unabhängig voneinander gleich gelöst haben

Diese Übereinstimmung ist ein starkes Signal. Die folgenden Muster gelten unabhängig von der
gewählten Richtung als gesetzt:

1. **Lagesatz statt KPI-Kacheln.** Ein ganzer Satz beantwortet „Ist meine Infrastruktur
   gesund?“ und nennt die Ursache.
2. **Eine Zelle pro Host,** sortiert nach Schwere. Bei 48 Hosts ist das ein Balken, bei
   10'000 eine aggregierte Verteilung.
3. **„Braucht Aufmerksamkeit“ mit „Nächster Schritt“** je Eintrag, abgeleitet aus Regel,
   Topologie oder Check, nie aus einem Sprachmodell ohne Opt-in.
4. **„Seit Ihrem letzten Besuch“** als eigener Bereich.
5. **Zustand immer mit Form und Text,** nicht nur Farbe. „Unbekannt / veraltet“ ist
   schraffiert oder gestrichelt und sieht nie gesund aus.
6. **Vier Beziehungs-Labels** (Confirmed / Rule-based / Suspected / Not available yet) mit
   eigenem Linienstil, überall gleich.
7. **Der Gravity-Globus entfällt** auf dem Dashboard.

## Bewertung (1 = schwach, 5 = sehr gut)

| Kriterium | A | B | C | Begründung |
|---|---|---|---|---|
| Enterprise-Tauglichkeit | 5 | 4 | 4 | A wirkt am reifsten und ruhigsten; B ist bei vielen Incidents dicht; C braucht Erklärung |
| Visuelle Qualität | 5 | 4 | 4 | A am konsequentesten; B stellenweise unruhig (Bögen im Faden) |
| Eigenständigkeit | 3 | 4 | 5 | A ist hochwertig, aber am nächsten am Branchenstandard |
| Wiedererkennbarkeit | 3 | 4 | 5 | Strata ist auf einen Blick „Nodeglow“ |
| Usability | 5 | 3 | 3 | A sofort lesbar; B und C haben eine Lernkurve (Faden-Notation, umgedrehtes Icicle) |
| Informationshierarchie | 5 | 4 | 4 | A trennt Lage, Handlung, Evidenz und Bestand am klarsten |
| Technische Umsetzbarkeit | 5 | 3 | 3 | A passt direkt zum heutigen Stack; B und C brauchen eigene Layout-Algorithmen und neue Backend-Felder |
| Skalierbarkeit (UX) | 4 | 3 | 4 | A skaliert pro Incident; B wird bei vielen parallelen Problemen voll; C hat Aggregation eingebaut, hängt aber an der Qualität der Parent-Links |
| Operative Effizienz | 5 | 4 | 4 | A hat die kürzesten Wege von „was ist los“ zu „was tun“ |
| **Summe** | **40** | **33** | **36** | |

## Warum die Kombination

- **A allein** wäre das beste Werkzeug für den Alltag, bleibt aber austauschbar. Ihm fehlt
  ein Element, an dem man Nodeglow sofort erkennt.
- **C allein** wäre am eigenständigsten. Die Landschaft als Startseite verlangt aber, dass
  jeder Nutzer das umgedrehte Icicle lernt, und sie verliert an Wert, wo Parent-Links fehlen.
- **B allein** zeigt am deutlichsten, wie ehrlich Nodeglow mit Zusammenhängen umgeht, wird
  aber bei mehreren gleichzeitigen Problemen schwer lesbar.

Die Kombination nimmt von jedem den stärksten Teil:

| Bereich | Quelle |
|---|---|
| Designsystem, Farben, Typografie, Formen, Dichte-Umschalter | A |
| App-Shell und Navigation (an die IA aus `02` angepasst) | A + IA |
| Dashboard-Aufbau: Lagesatz, Host-Zellen, Aufmerksamkeit, Seit-letztem-Besuch, Risiken | A (in allen drei gleich) |
| Infrastruktur-Signatur: Strata als Seite `Infrastruktur > Landschaft` und als kompaktes Modul auf dem Dashboard anstelle des Globus | C |
| Incident-Workspace: Evidenzkette mit „Based on“, Abschnitt „Was Nodeglow noch nicht sagen kann“ | B |
| Causality Strip als Zeitachse im Incident-Workspace | A |

## Was die gewählte Richtung im Backend braucht

Aus den drei Richtungsdokumenten zusammengefasst; Details und Prioritäten in
`02-information-architecture.md`, Abschnitt 8.

- Einheitlicher, probe-bewusster Host-Status (heute bleiben Hosts hinter einer stummen Probe grün)
- Summary-Endpoint mit allen Zählern
- `host_ids` und Beziehungen mit Label als Felder am Incident
- Herkunft jedes Parent-Links (manuell, Proxmox, UniFi) in der Topologie-API
- „Letzter Besuch“ pro Benutzer und ein Changes-Feed
- Zeitreihen-Endpoint für die Evidenzspuren eines Incidents
- Serverseitige Zählungen pro Topologie-Ast für grosse Bestände

## Offene Punkte im Szenario

- #1070 wird gezählt, aber nicht beschrieben. A und C lassen ihn offen, B belegt ihn mit
  der Firewall-Log-Anomalie. Vor Phase E festlegen.
- `SW-ZH-CORE-02` ist laut Szenario erreichbar und zählt zu den gesunden Hosts. Alle drei
  zeigen ihn deshalb als Quelle, nicht als gestört.

## Entscheidung

Bitte eine der Optionen wählen, bevor Phase D (Designsystem) beginnt:

- [ ] Konzept D „Calm“ als Startseite, Tiefe aus A–C nur auf Klick (neue Empfehlung)
- [ ] Kombination A + Strata aus C + Evidenzkette aus B
- [ ] Konzept A
- [ ] Konzept B
- [ ] Konzept C
- [ ] Andere Mischung
