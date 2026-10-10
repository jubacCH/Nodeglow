# Konzept E2 — „UniFi-style“

Prototyp: [`../prototypes/concept-e2/index.html`](../prototypes/concept-e2/index.html) (eine Datei,
simulierte Daten aus [`SCENARIO.md`](../prototypes/SCENARIO.md)).

> Kurzform: Die Bildsprache von UniFi Network / Site Manager (bzw. Vercel), übertragen auf
> Nodeglow. Dunkle, ruhige Kartenoberfläche, grosse Zahlen, viel Luft, ein einziger Akzent.
> Keine Ubiquiti-Marke, kein Logo: Nodeglow behält Namen und ein eigenes, schlichtes Zeichen.

Anlass: A–C waren zu dicht und zeitachsenlastig, D („Calm“, reine Liste) zu karg. E2 zeigt
wieder Überblick auf einen Blick, aber in Karten mit je einer klaren Aussage.

## Stilregeln

- **Flächen:** App-Hintergrund `#141519`, Karten `#1E1F24`, Hover `#25262C`. Rahmen nur
  1 px `rgba(255,255,255,.06)`, Radius 10 px. Kein Glow, keine Schatten auf Karten, Verläufe nur
  als sehr zarte Chart-Füllung.
- **Ein Akzent:** Blau `#0A84FF` (hell: `#006FFF`) für Auswahl, Primär-Button, Chart-Linien,
  aktiven Navigationspunkt. Sonst nur Statusfarben.
- **Status:** Grün = up, Gelb = degraded, Orange = warning, Rot = down, Grau-Blau = Wartung,
  **Grau schraffiert = keine Daten**. Unbekannte Hosts sehen nie gesund aus: hohler Punkt,
  Schraffur im Ring und in den Standortbalken, Text „No data“.
- **Typografie:** Inter Tight (Google Fonts) mit Tabellenziffern; Gewichte 400/500/600.
  Kennzahlen 28–40 px, Kartentitel 14 px medium, Labels 12 px gedämpft. Keine Versalien-Labels.
- **Ehrlichkeit:** Chip „Simulated data“ immer in der Kopfzeile (auf dem Telefon „Simulated“).
  Mandantenwechsel und Root-Cause-Ranking sind als „Planned“ markiert. Beziehungen tragen
  immer ein Label: Confirmed / Rule-based / Suspected / Planned.
- **Theme:** Dark-first; Light-Mode (`#F5F6F7`, weisse Karten, Rahmen `#E4E6EA`) über
  `prefers-color-scheme` oder Umschalter in der Kopfzeile. Alle Farben als CSS-Variablen.

## Layout

- **Linke Icon-Leiste (60 px):** Dashboard, Topology, Devices, Hosts, Incidents (Zähler 2), Logs,
  Integrations, Settings. Beschriftung als Tooltip bei Hover/Fokus. Unter 760 px wird sie zur
  Tab-Leiste am unteren Rand.
- **Kopfzeile:** Mandant „Muster Treuhand AG“ (Multi-tenant · Planned), Standortwahl
  „All sites · 3“, Suche (Ctrl K), Chip „Simulated data“, Theme-Umschalter.
- **Kartenraster (12 Spalten ab 1200 px):**
  1. **Health** (Hero): Ring mit 48 Hosts nach Zustand, Legende mit Zahlen und Kurzgrund,
     „39 / 48 with current data · 14 agents · 13 integrations“.
  2. **Internet:** 942 / 98 Mbit/s, Verlauf der letzten 24 Speedtests (ab null skaliert),
     Latenz 6 ms, Gateway.
  3. **Incidents:** „2 open“, Mini-Balken 14 Tage, zwei Zeilen #1071/#1072, heute behoben.
  4. **Topology** (breit): Internet → fw-zh-01 → drei Äste. SW-ZH-CORE-02 mit den 7 betroffenen
     Hosts rot hervorgehoben, Bern-Ast (probe-bern-01) gestrichelt und grau „No data“.
  5. **Sites:** Zürich HQ 37 / Bern 9 / Cloud 2 mit gestapelten Zustandsbalken.
  6. **Upcoming:** Wartung bis 15:00, D: 91 % (~5 Tage), Zertifikat 9 Tage.
  7. **Latency** (2 h, Median + Max der 7 Hosts, Onset 14:05), **Syslog** (46/min vs. 8–14,
     24 h), **Availability** (99.94 % vs. Ziel 99.90 %, 26 von 43 min Budget).
  8. **Since your last visit** (09:12): 4 eröffnet, 2 behoben, 2 neue Hosts, Wartung, Agent-Update.
- 760–1199 px: zwei Spalten; unter 760 px eine Spalte, Topologie-Äste untereinander.

## Interaktiv

- **Seitenpanel rechts** (UniFi-Stil) per Klick auf ein Gerät in der Topologie, einen Incident
  oder einen Suchtreffer: Status, Kennzahlen, Was passiert, nächster Schritt, betroffene Hosts,
  gelabelte Beziehungen, Log-Auszug. Fokusfalle, Esc schliesst, Fokus kehrt zurück.
  „Acknowledge“ ändert den Zustand nur im Prototyp.
- **Ctrl K:** Befehlspalette über Incidents, Hosts/Geräte und Dashboard-Bereiche (Pfeiltasten,
  Enter).
- **Charts:** Tooltips bei Hover; Latenz- und Syslog-Chart per Tastatur (Pfeile, Home/End).
  Tooltips auch auf Ring, 14-Tage-Balken und Speedtest-Balken.
- **Theme-Umschalter** setzt `data-theme` (in `localStorage`, abgesichert).
- Navigationspunkte ohne Seite im Prototyp zeigen einen kurzen Hinweis; Topology, Incidents
  und Logs springen zur jeweiligen Karte.
- Review-Deeplinks: `#inc-1071`, `#dev-SW-ZH-CORE-02`, `#dev-probe-bern-01`, `#search`.
