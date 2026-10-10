# Konzept D — „Calm“

Prototyp: [`../prototypes/concept-d/index.html`](../prototypes/concept-d/index.html) (eine Datei,
simulierte Daten aus [`SCENARIO.md`](../prototypes/SCENARIO.md)).

> Kurzform: Konzept A, radikal vereinfacht. Die Startseite beantwortet drei Fragen und sonst
> nichts: Wie steht es? Was muss ich jetzt tun? Was ist seit meinem letzten Besuch passiert?

Anlass: Rückmeldung zu A, B und C: „das ist viel zu unübersichtlich“. Am Screenshot von A
(1440 × 900) waren gleichzeitig sichtbar: zweizeilige Schlagzeile mit Absatz, drei Kennzahlen,
eine Warteschlange mit sechs Einträgen à drei Zeilen und je zwei Buttons, ein Ledger mit neun
Zeilen, dazu weiter unten Causality Strip, Estate-Matrix und Risiken. Hinzu kamen
Grossbuchstaben-Labels, Badges und viele Rahmen.

## Startseite: drei Elemente

1. **Lage.** Ein Satz: „1 kritisches Problem · 9 Hosts ohne aktuelle Daten“. Darunter der
   Host-Balken (eine Zelle pro Host, 8 px hoch, nach Schwere sortiert) und eine Legendenzeile.
   Kein Absatz, keine Kennzahlen.
2. **Jetzt zu tun.** Höchstens vier Einträge. Pro Eintrag eine Zeile (Zustandsform, Titel, Alter)
   und eine gedämpfte Zeile mit dem nächsten Schritt. Die ganze Zeile ist die einzige Aktion und
   öffnet die Details. Keine Badges, kein „Quittieren“ in der Liste.
3. **Neu seit 09:12.** Drei Zeilen, die nicht schon unter „Jetzt zu tun“ stehen (Wartung,
   behobener #1069, neue Hosts). „Alle 9 anzeigen“ klappt die vollständige Liste auf.

Gemessen bei 1440 × 900: 22 Textelemente im Inhalt, dazu 9 in der Kopfzeile (Marke,
5 Navigationspunkte, Zähler, Suche, Marker „Simulierte Daten“) und die 6-teilige Legende des
Host-Balkens.

## Was entfernt wurde und wo es jetzt ist

| Aus A | In D |
|---|---|
| Absatz unter der Schlagzeile, Kennzahlen (Current data, Verfügbarkeit, Syslog 24 h) | Verfügbarkeit unter Infrastruktur › Trends, Syslog-Volumen unter Logs |
| Warteschlange mit 6 Einträgen, Badges, zwei Buttons je Eintrag | 4 Einträge; Risiken (Disk, Zertifikat) nach Infrastruktur › Trends; Quittieren im Drawer |
| Ledger mit 9 Zeilen | 3 Zeilen + „Alle 9 anzeigen“ |
| Causality Strip auf dem Dashboard | Drawer › Zeitachse, höchstens 3 Spuren |
| Estate-Matrix | Infrastruktur › Hosts (nach Standort und Parent gruppiert) |
| Risiken & Trends | Infrastruktur › Trends (3 Einträge mit je einer Mini-Sparkline) |
| Beziehungs-Labels überall sichtbar | Nur im Drawer unter „Zusammenhänge“ |
| 13 Navigationspunkte in 4 Gruppen mit Zählern | 5 Punkte, Zähler nur bei Incidents; Konfiguration gesammelt unter Einstellungen |
| Zeitfenster-Umschalter, Dichte-Umschalter | entfällt im Prototyp |
| Tenant-Kasten „planned“ | entfällt, bis Mandanten gebaut sind |

## Drawer (Details auf Abruf)

Reihenfolge, von oben: Titel mit Zustandsform und einer Metazeile (z. B. „Incident #1071 ·
kritisch · offen seit 14:07“), **Was passiert ist** (zwei Sätze), **Betroffene Hosts**
(nach Zustand gruppiert, kompakt), **Nächster Schritt** mit genau einer Aktion (Quittieren bei
Incidents, sonst „In Infrastruktur zeigen“). Darunter drei einklappbare Abschnitte, von denen
immer nur einer offen ist:

- **Zeitachse:** vereinfachter Causality Strip, 13:30 bis jetzt, höchstens drei Spuren
  (bei #1071: Syslog-Rate, Latenz, Erreichbarkeit). Fehlende Daten schraffiert.
- **Zusammenhänge:** jede Beziehung mit Label Bestätigt / Regelbasiert / Vermutet /
  Noch nicht verfügbar, Linienstil wie in A, und Quelle.
- **Logs:** die letzten passenden Zeilen, Link zur Log-Seite; „keine Logs“, wenn keine da sind.

Fokusfalle, Esc schliesst, Fokus kehrt zum auslösenden Eintrag zurück.

## Warum das ruhiger ist

- Eine Frage pro Bereich, und die Reihenfolge der Bereiche entspricht der Reihenfolge der
  Fragen eines Betreibers.
- Zwei Schriftgrössen für Fliesstext (15 / 13 px), Basis 15 px statt 13 px.
- Keine Grossbuchstaben-Labels, keine Badges. Trennung durch Abstand und eine leicht getönte
  Fläche beim Hover statt durch Rahmen.
- Höchstens ein Akzent gleichzeitig: Ultramarin erscheint nur im Fokusring und als primärer
  Button im Drawer. Farbe sonst nur für Zustände.
- Zahlen nur, wo sie eine Frage beantworten (wie viele betroffen, seit wann, bis wann).
- Inhaltsspalte auf 760 px begrenzt, auch bei 1920 px.

## Was bleibt aus A, B und C

- **A:** Palette, Instrument Sans + IBM Plex Mono, Ultramarin-Akzent, Zustandsformen
  ■ ▲ ◐ ◇ und schraffiertes „unbekannt“, Host-Balken, Causality Strip (jetzt im Drawer),
  Linienstile der Beziehungs-Labels.
- **B:** Ehrlichkeit bei Zusammenhängen: Quelle je Beziehung und „Noch nicht verfügbar“ als
  eigene Zeile statt stillem Weglassen.
- **C:** Gruppierung nach Standort und Parent; der Bern-Ast hinter der stummen Probe ist als
  schraffierte Gruppe markiert und sieht nie gesund aus. Die Strata-Landschaft selbst kann
  später als Ansicht unter Infrastruktur dazukommen.
- **Gemeinsam (03):** Lagesatz statt KPI-Kacheln, eine Zelle pro Host, nächster Schritt je
  Eintrag, „Seit Ihrem letzten Besuch“, Zustand immer mit Form und Text, kein Globus.

## Ehrlichkeit im Prototyp

Marker „Simulierte Daten · Stand 14:32“ immer sichtbar. Die 9 Bern-Hosts sind „ohne aktuelle
Daten“, nie grün. Quittieren meldet „Simuliert, es wurde nichts gesendet“. Logs-Suche und
Einstellungen sind ausdrücklich als nicht nachgebaut gekennzeichnet.

## Offen

- „web-01: Port 443“ hat im Szenario keinen Startzeitpunkt; der Prototyp zeigt „seit gestern“.
- Ob Trends eine eigene Hauptnavigation verdient, wenn mehr als drei Risiken anstehen.
