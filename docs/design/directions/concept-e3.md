# Konzept E3 — „Nodeglow“

Prototyp: [`../prototypes/concept-e3/index.html`](../prototypes/concept-e3/index.html) (eine Datei,
simulierte Daten aus [`SCENARIO.md`](../prototypes/SCENARIO.md)).

> Kurzform: Layout, Karten und Interaktion von E2 („UniFi-style“) bleiben gleich. Dazu kommen drei
> eigene Zutaten: **Glow als Signatur**, **eigener Akzent** statt UniFi-Blau, **eigenes Zeichen und
> eigene Typografie**. Kein Firmen-Branding.

## Glow-Regeln

Der Name wird zur Bildidee: **Licht heisst Aufmerksamkeit.**

- **Es leuchtet nur, was Aufmerksamkeit braucht.** Weicher, unscharfer Halo in der Statusfarbe
  (`box-shadow`, `filter: drop-shadow`, `radial-gradient`, keine Bilder).
  - Kritisch (stärker): Icon von #1071, Kopf-Knoten SW-ZH-CORE-02, der betroffene Ast (Leitung
    Firewall → Switch und linke Astlinie), Punkte der Down-Hosts, Incident-Zähler in der Leiste.
  - Warnung (schwächer, statisch): Icon von #1072, Punkte von SRV-BACKUP-01 und web-01.
- **Gesund, Wartung und „No data“ leuchten nie.** Unbekannt bleibt grau, gestrichelt, schraffiert,
  Icons leicht abgedunkelt: **kein Licht = keine Daten**. Die Legende der Topologie sagt das wörtlich.
- **Health-Ring:** Die Mitte bekommt einen schwachen inneren Schein in der Gesamtlage (hier
  Rot-Orange, weil kritisch). Die Ringsegmente bleiben scharf.
- **Atmen:** Nur kritische Halos atmen, sehr langsam (6 s, Deckkraft 0.62 → 1). Aus bei
  `prefers-reduced-motion`, aus im Light-Mode. Ein bestätigter Incident atmet nicht mehr und
  behält nur noch einen halben Halo.
- **Akzent-Glow** nur auf dem Primär-Button (Hover/Fokus) und im Logo, sonst nirgends.
- **Light-Mode:** Glow wird zu weichem, farbigem Schatten bzw. Tönung (nach unten versetzt,
  geringe Deckkraft), nie leuchtendes Licht auf Weiss. Leitungen im Light-Mode ohne Glow.
- Alles über Tokens (`--glow-crit`, `--glow-warn`, `--glow-dot-*`, `--wire-glow`, `--branch-glow`,
  `--ring-core`, `--accent-glow`, `--breathe`), pro Theme definiert.

## Drei Akzent-Kandidaten

Umschalter „Accent · Prototype“ (gestrichelt markiert) im Seitenkopf neben „Since your last
visit“. Gesetzt über `data-accent` auf `:root`, je für Dunkel und Hell definiert, gespeichert in
`localStorage` (abgesichert). Default: Glow Violet. Auswahl, Primär-Button, Charts, Logo, aktiver
Navigationspunkt und Links folgen dem Akzent. Text-Akzent (`--accent-text`) und Button-Fläche
(`--accent-btn`) sind getrennte Tokens, damit die Kontraste stimmen.

| Akzent | Dunkel | Hell | Begründung, Kontrast |
|---|---|---|---|
| **Glow Violet** (Default) | `#7C6CFF`, Text `#9488FF`, Button `#6656F2` + Weiss | `#5B4BE0` (5.9:1 auf Weiss) | Elektrisch, aber ruhig. Farbton 247°, weit weg von allen Status (Grün 148°, Gelb 45°, Orange 28°, Rot 357°) und vom Wartungs-Grau-Blau (entsättigt). Sicherste Wahl. Button: Weiss auf `#6656F2` ca. 4.9:1. |
| **Ember** | `#FFB547`, Button mit dunkler Schrift (10.6:1) | Linien `#C77A0A`, Text `#9A5B00` (5.4:1) | Warm, „glühende Kohle“. **Riskant:** Ember (36°) liegt zwischen Warning und Degraded. Darum verschiebt Ember beide: Warning → `#F27C3A` (22°, hell `#C9560F`), Degraded → `#E5D45A` (53°, hell `#9A8A00`). Unterscheidbar, aber enger. Der Latenz-Chart in Amber liest sich nahe an „Warnung“. |
| **Aurora** | `#FF5FC8`, Button mit dunkler Schrift (6.9:1) | `#C0288C` (5.4:1) | Polarlicht-Magenta, klar anders als Violett (≈75° Abstand) und als Status. Petrol/Cyan und Mint sind ausgeschlossen (Firmenfarben des Owners), Lime ist ein Klischee. Damit Magenta (321°) nie „kritisch“ wirkt, rückt Down leicht nach Orange-Rot: `#F2584E` (4°, hell `#D7362A`). |

Empfehlung: **Glow Violet**. Aurora ist die eigenständigere Alternative; Ember nur, wenn die
Statusverschiebung akzeptiert wird.

## Logo

- **Zeichen** (`#ng-mark`, viewBox 32): Knoten (Kreis r 4.6) im Akzent, darum ein weicher Halo
  (r 10.5, radialer Verlauf im Akzent), ein dünner Orbit (r 11.5, Strich 1.7) mit 40°-Lücke um einen
  kleinen Satelliten (r 2.15) bei −45° = „verbunden“. Orbit und Satellit in Textfarbe.
- Funktioniert bei 40 px und 20 px (im About-Fenster beide gezeigt). Einfarbig per Klasse `.mono`
  (alles `currentColor`, Halo 16 %).
- **Wortmarke** „Nodeglow“ in Sora 600, Laufweite −0.035em; das **o in „glow“** steht im Akzent und
  leuchtet im Dark-Mode leicht (das Licht sitzt im Wort „glow“).
- Einsatz: Zeichen oben in der Leiste (Button → kleines About-Fenster mit Lockup) und Lockup in der
  Fusszeile.

## Typografie

- **Display: Sora** (Google Fonts) für grosse Zahlen und Überschriften: Gewicht 500 (Zahlen) / 600
  (Titel), Laufweite −0.035 bis −0.045em, Tabellenziffern (geprüft: „1111“ = „0000“ breit). Die
  engen, geometrischen Ziffern sind die Signatur.
- **UI/Text: Inter Tight** 400/500/600 wie in E2.
- **Mono: JetBrains Mono** für Regelnamen, Log-Auszüge, IPs.
- Fallbacks für alle drei im Font-Stack.

## Was sich gegenüber E2 ändert

- Hintergrund kühler und tiefer (`#101217`, Karten `#181A20`), damit Glow Bedeutung bekommt; sonst
  keine Verläufe, keine Sterne.
- Blau ersetzt durch drei umschaltbare Nodeglow-Akzente, abgeleitete Töne (`--accent-soft`,
  `--chart-fill`) per `color-mix` aus dem Akzent.
- Glow-System wie oben, inkl. Ring-Mitte, Ast-Glow und „bestätigt = atmet nicht mehr“.
- Neues Zeichen, Wortmarke, About-Fenster, Fusszeile.
- Sora + JetBrains Mono statt reinem Inter Tight / System-Mono.
- **Fix:** Bei 1200–1439 px läuft die Topologie über die volle Breite (Sites und Upcoming
  darunter nebeneinander). Hostnamen wie SRV-BACKUP-01, NAS-ZH-01, SW-ZH-ACC-03 werden bei 1280 px
  nicht mehr abgeschnitten (per Headless-Messung geprüft).
- Unverändert: Kartenraster, alle Karten, Seitenpanel (Fokusfalle, Esc), Ctrl K, Tooltips,
  Tastatur-Charts, Ehrlichkeitsregeln und Labels, Light-Mode, Responsive bis 400 px, Deeplinks
  (`#inc-1071`, `#dev-SW-ZH-CORE-02`, `#dev-probe-bern-01`, `#search`).
