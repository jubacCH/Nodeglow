# Konzept E1 — „Vercel-style“

Prototyp: [`../prototypes/concept-e1/index.html`](../prototypes/concept-e1/index.html) (eine Datei,
simulierte Daten aus [`SCENARIO.md`](../prototypes/SCENARIO.md)).

> Kurzform: Bildsprache des Vercel-Dashboards (bzw. UniFi): dunkel, ruhig, grosse Zahlen in
> Karten, viel Weissraum. Farbe gibt es nur für Zustände.

Anlass: Rückmeldung zu A–D. Die dichten, zeitachsenlastigen Entwürfe (A–C) waren zu unruhig,
die reine Liste (D) zu karg und ohne Wertigkeit. E1 behält Ds Prioritäten (Lage, Jetzt zu tun,
Neu seit letztem Besuch), gibt ihnen aber die Form einer Projektübersicht mit Kennzahlkarten.

## Stilregeln

- **Dark first.** Seite `#000`, Karten `#0A0A0A`, Hover/Flächen `#111` / `#171717`, Rahmen 1 px
  `rgba(255,255,255,.09–.24)`, Radius 8–12 px. Keine Schatten, keine Verläufe, kein Glow.
- **Light** (Vercel light): Weiss, Flächen `#FAFAFA`, Rahmen `#EAEAEA`, Primärbutton schwarz.
  Umschalter oben rechts; Systemeinstellung ist Standard, Wahl wird (falls möglich) gemerkt.
- **Monochrom.** Farbe nur für Zustand: Grün ok, Amber Warnung/degradiert, Rot kritisch/down,
  Blau Info/Wartung, Grau unbekannt. Punkte 8 px mit dezentem Ring.
- **Unbekannt sieht nie gesund aus:** gestrichelter grauer Punkt, schraffierte Balkenzellen,
  Text „No data“. Bern zeigt „No data — probe silent 11 min“.
- **Typografie:** Geist / Geist Mono. Seitentitel 32 px, Kennzahlen 36 px, Gewicht 600,
  enges Letter-Spacing, `tabular-nums`. Labels klein, gedämpft, normale Schreibweise (keine
  Versalien). Hostnamen, Incident-Nummern und Regeln in Mono.
- **Ehrlichkeit:** Marker „Simulated data“ in Kopfzeile und Seitentitel; Mandanten-Umschalter
  und Root-Cause-Ranking als „Planned“; jede Beziehung mit Label Confirmed / Rule-based /
  Suspected (Linienstil: gefüllt, blau, gestrichelt).

## Layout

1. **Kopfzeile:** Marke, Mandant „Muster Treuhand AG“ mit „Planned“, rechts „Simulated data“,
   Suche (⌘K / Ctrl K), Theme-Umschalter, Avatar.
2. **Tab-Leiste** (sticky): Overview, Incidents (Zähler 2), Hosts, Logs, Integrations, Settings;
   aktiver Tab mit Unterstrich.
3. **Seitenkopf:** „Overview“, Zeitpunkt, letzter Besuch; rechts Zeitraum-Auswahl und weisser
   Primärbutton „Add host“.
4. **Vier Kennzahlkarten:** Hosts online 37 / 48 (48-Zellen-Balken), Open incidents 2
   (Balken pro Tag, 14 Tage), Availability 30 d 99.94 % (Balken gegen Ziel 99.90 %),
   Syslog 24 h 11,622 (Sparkline, Spitze 46/min in Amber).
5. **Zwei Spalten:** links „Active incidents“ (Zeilen im Stil der Vercel-Deployment-Liste:
   Titel + Regel, Zustand + Dauer, Betroffene, Startzeit, Chevron; darunter „Resolved today“)
   und „Since your last visit“; rechts „Infrastructure“ (Zürich HQ / Bern / Cloud mit
   segmentiertem Zustandsbalken) und „Upcoming“ (Disk ~5 Tage, Zertifikat 9 Tage,
   Wartungsende 15:00).
6. **Breite Diagrammkarte:** Latenz der 7 Hosts hinter SW-ZH-CORE-02, letzte 2 h, Median und
   Maximum, Beginn 14:05 markiert, 14:07 Ausfall von zwei Hosts.

Hinweis zu den Zahlen: Die Vorgabe „38 / 48“ passt nicht zu `SCENARIO.md`
(29 gesund + 5 degradiert + 2 Warnung + 1 Wartung = 37). Der Prototyp zeigt 37 / 48.

## Interaktiv

- **Sheet rechts** (600 px, auf dem Handy volle Breite): Klick auf Incident-Zeile, Bern-Zeile
  oder Upcoming-Eintrag. Inhalt: Zusammenfassung, Faktenraster, nächster Schritt mit
  „Acknowledge“ (nur lokal), betroffene Hosts, Beziehungen mit Labels, Log-Auszug.
  Fokusfalle, Esc und Klick auf den Hintergrund schliessen, Fokus kehrt zurück.
- **Tabs** per Klick und Pfeiltasten. Hosts: Tabelle aller 48 Hosts mit Filter (All /
  Problems / No data / Maintenance) und Namensfilter. Incidents: Liste. Logs, Integrations,
  Settings: einfache Platzhalter.
- **Command-Menü** ⌘K / Ctrl K: Incidents, Seiten, Aktionen, Hostsuche; Pfeiltasten, Enter, Esc.
- **Latenzdiagramm:** Hover zeigt Fadenkreuz und Werte (Median, Max, antwortende Hosts).
- Zeitraum-Auswahl, „Add host“, Mandanten-Umschalter melden per Toast, dass sie im Prototyp
  nicht verdrahtet bzw. geplant sind.
- Responsiv bei 1920 / 1440 / 1280 / 400 px, `prefers-reduced-motion` respektiert.
