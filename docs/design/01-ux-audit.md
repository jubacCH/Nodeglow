# 01 — Produkt- und UX-Audit (Phase A)

| | |
|---|---|
| **Stand** | 10.10.2026, Basis-Branch `design/enterprise-redesign` (`84dd7d3`) |
| **Umfang** | Frontend `frontend/` (Next.js 15, React 19, Tailwind), Backend-Router und -Services `backend/`, Specs unter `docs/specs/` |
| **Quellen** | Code (Datei:Zeile), Screenshots der aktuellen Produktiv-UI (Homelab des Owners, nicht im Repo abgelegt), frühere Review-Befunde (hier verifiziert) |
| **Zielgruppen** | Admins, NOC, IT-Leitung, MSPs; Schweizer KMU bis Enterprise |
| **Owner-Entscheide** | SaaS und On-Prem, Multi-Tenancy geplant (`docs/specs/2026-10-10-multi-tenancy-design.md`), Open Core, KI nur opt-in |
| **Folgedokument** | `02-information-architecture.md` (Phase B) |

Pfade im Text sind relativ zu `frontend/src/` (Frontend) bzw. `backend/` (Backend), wenn nicht anders angegeben.
Status-Legende: **Ja** = implementiert und in der UI erreichbar · **Teilweise** = vorhanden, aber unvollständig, versteckt oder nur im Backend · **Nein** = nicht vorhanden.

---

## 0. Kurzfassung: die zehn wichtigsten Befunde

1. **Veraltete Daten erscheinen als gesund.** Hostliste, Dashboard und Sidebar verwenden das letzte Ping-Ergebnis. Die probe-bewusste „unknown“-Logik (`backend/services/probes.py:180`) wird nur in der Topologie genutzt. Fällt eine Remote-Probe aus, bleiben ihre Hosts grün. Das verletzt die Kernregel „fehlende oder veraltete Daten sind nie gesund“.
2. **Fehlende Werte werden als 0 bzw. „All clear“ angezeigt.** `AnimatedCounter value={value ?? 0}` (`app/(app)/page.tsx:775`), Disk „0 % / 32 GB“ in Grün auf der Host-Detailseite (Screenshot), „No anomalies detected“ in Grün, auch wenn die Baselines noch nicht gelernt sind.
3. **Navigation ohne Modell.** 13 flache Hauptpunkte, eine eingeklappte Integrationsliste und 5 Systempunkte (`components/layout/Sidebar.tsx:39-61`). Konfiguration (Credentials, Rules, SNMP-MIBs), Betrieb (Hosts, Alerts) und Analyse (Digest, Bandwidth) liegen gemischt. `/backups` ist gar nicht verlinkt.
4. **Alert, Incident und Event sind begrifflich vermischt.** Ein Alert-Objekt gibt es nicht. Der Tab „Alerts“ zeigt offene Incidents, Incidents liegen unter `/alerts?tab=incidents`, Wartung ebenfalls unter Alerts. Sidebar-Badge und Dashboard zählen verschieden: Die Sidebar zählt nur `open`, das Dashboard `open` + `acknowledged`.
5. **Listen laden alles und filtern im Browser.** Hosts, Syslog (200 Zeilen), Incidents (die 50 zuletzt aktualisierten) werden clientseitig gefiltert. Ein Incident ausserhalb der 50 fehlt in „Alerts“, und die Seite meldet trotzdem „All clear“.
6. **Das Dashboard hat keine Prioritätsordnung.** KPI-Reihe, 3D-Globus (ca. 330 px, Hero-Fläche ohne Handlungswert) und rund 14 gleichgewichtete Widgets. Die Fragen „Was braucht jetzt meine Aufmerksamkeit?“ und „Was hat sich seit meinem letzten Besuch geändert?“ beantwortet es nicht.
7. **Wertvolle Backend-Analytik ist unsichtbar.** `/api/v1/syslog/intelligence` (Bursts, Fleet-Patterns, Precursors, Trends), `/syslog/api/smart-feed`, `/syslog/api/reverse-cause`, `/api/v1/predictor/eval`, gespeicherte Dashboard-Layouts und die Tabelle `SyslogView` (gespeicherte Ansichten) haben keine UI.
8. **Stille Fehlfunktionen.** Der Probe-Schalter auf `/agents` sendet `is_probe`, das Backend ignoriert das Feld (`backend/routers/api_v1.py:1092`, Allowlist), die UI meldet trotzdem Erfolg. Bulk-Edit ruft `PATCH /api/v1/hosts/bulk` auf, die Route liegt aber hinter `/hosts/{host_id}` (`api_v1.py:877` vs. `:939`) und ist damit sehr wahrscheinlich nicht erreichbar.
9. **Theme und Barrierefreiheit sind nicht tragfähig.** 1577 hartcodierte `slate-*`-Klassen und rund 1000 hartcodierte Farbklassen. Light Mode funktioniert nur über CSS-Overrides einzelner Utility-Klassen (`app/globals.css:74-131`), und Charts nutzen immer die Dark-Palette (`styles/tokens.gen.ts`). Dazu 140 `<label>`, aber nur 26 `htmlFor`, keine `role="tab"`, kein `aria-live`, Topologie nur als `<canvas>`.
10. **Kein Objektkontext, keine Querverlinkung.** Incident → betroffene Hosts gibt es nur als Zahl. Syslog → Host läuft über eine Textsuche (`/hosts?q=hostname`). Host → Topologie, Host → Regeln und Incident → hostgefilterte Logs fehlen. Tabs und Filter stehen nicht in der URL, also sind keine Deep Links und keine gespeicherten Ansichten möglich.

---

## 1. Aktuelle Architektur

### 1.1 Shell und globale Elemente

| Element | Datei | Verhalten heute | Bewertung |
|---|---|---|---|
| App-Shell | `components/layout/AppShell.tsx` | Sidebar links/rechts (konfigurierbar), Hauptbereich max. 1920 px, Page-Fade bei jedem Routenwechsel (`key={pathname}`, :137), Mobile-Overlay | Kein Topbar-Bereich für globalen Kontext (Tenant, Zeit, Verbindung) |
| Sidebar | `components/layout/Sidebar.tsx` | Suche, „Main“ (13), „Integrations“ (eingeklappt, nur konfigurierte Typen), „System“ (5), Glow-Schalter, User mit Theme-Toggle und Logout | Siehe 1.2 |
| Sidebar-Suche | `Sidebar.tsx:240-307` | Seiten, Hosts (Server-Suche, max. 10), Integrationsinstanzen | Doppelt zur Command Palette |
| Command Palette | `components/ui/CommandPalette.tsx` | Cmd/Ctrl+K, Navigation, Hosts, Integrationen | Keine Incidents, Agents, Logs, Aktionen; Navigationsliste dupliziert (`NAV_ENTRIES` :28) |
| Tastaturkürzel | `components/ui/KeyboardShortcuts.tsx` und `Sidebar.tsx:131-165` | `g d/h/a/s/r/i/t`, `?` | **Zwei Listener** für dieselben g-Sequenzen, die Router-Navigation wird doppelt ausgelöst |
| Glow (KI) | `components/copilot/CopilotPanel.tsx` | Seitenpanel, Chat via SSE `/api/v1/glow/chat`; „off“-Label, wenn KI deaktiviert | Opt-in korrekt umgesetzt; als Nav-Punkt aber mit gleichem Gewicht wie Arbeitsbereiche |
| Live-Daten | `stores/websocket.ts`, `lib/liveUpdates.ts` | WS `/ws/live` (nur `ping_update`, `agent_metric`), sonst Polling (10 s bis 120 s) | Verbindungsstatus ist nirgends sichtbar; „LIVE“ im Dashboard hängt am Polling, nicht am WS (`page.tsx:166-180`) |
| Breadcrumbs | `components/layout/Breadcrumbs.tsx` | Nur auf 4 Detailseiten; Home-Icon ohne Text, `<nav>` ohne `aria-label` | Uneinheitlich |
| Erststart | `components/dashboard/FirstRunWelcome.tsx` | Agent-Installationsbefehle, CTA | Gut, aber nur auf dem Dashboard |

### 1.2 Navigation heute (Sidebar, wörtlich)

```
MAIN         Dashboard · Hosts · Alerts · Rules · Syslog · Agents · Scanner · SNMP · SSL
             Credentials · Tasks · Topology · Bandwidth
INTEGRATIONS (eingeklappt) je konfiguriertem Typ ein Eintrag mit Health-Punkt · + Add Integration
SYSTEM       Status · Audit Log* · Digest · Settings* · Users*        (* nur Admin)
             Glow (KI-Panel)
```

Ohne Nav-Eintrag erreichbar: `/incidents/[id]`, `/syslog/dashboard`, `/syslog/templates` (nur über Link-Buttons auf `/syslog`), `/integration/store`, `/integration/[type]/[id]`. **Nicht erreichbar** (keinerlei Link im Frontend): `/backups`.

Jeder Hauptpunkt hat eine eigene Icon-Farbe (13 Farben, `Sidebar.tsx:40-52`). Das widerspricht der eigenen Regel „Color = meaning“ (`DESIGN_GUIDE.md`, Regel 7): Grün bei „SSL“ und Rot-Rosa bei „Tasks“ tragen keine Statusbedeutung.

### 1.3 Seiteninventar

LOC = Zeilen der `page.tsx`. Endpunkte gekürzt.

| Route | Datei (LOC) | Zweck | Hauptdaten (Endpoint) | Hauptaktionen | Einstieg |
|---|---|---|---|---|---|
| `/login` | `app/login/page.tsx` (110) | Anmeldung, Setup-Weiche | `POST /api/auth/login`, `GET /setup/status` | Login, Redirect `?next=` | direkt |
| `/` | `app/(app)/page.tsx` (893) | Dashboard | `GET /api/dashboard` (Aggregat, ca. 25 Schlüssel), SSE `/syslog/stream` | Drilldown über KPI-Kacheln und Listen; Live-Feed WARN+/ALL | Nav, `g d` |
| `/hosts` | `hosts/page.tsx` (625) | Hostliste | `GET /hosts/api/status` (alle aktiven Hosts) | Host anlegen (Modal inkl. HTTP-Optionen), Suche, Status-Pills, Sortieren, Auswahlmodus → Bulk-Edit/Wartung/Löschen, Export | Nav, `g h` |
| `/hosts/[id]` | `hosts/[id]/page.tsx` (1920) | Host-Detail mit Tabs Overview / Ports / Timeline / Syslog | `/api/v1/hosts/{id}`, `/history`, `/timeline`, Syslog nach Host | Bearbeiten (inkl. Probe-Zuweisung), Wartung, Checks ein/aus, TCP-Ports, entdeckte Ports übernehmen, Refresh | Liste, Suche, Topologie, Dashboard |
| `/alerts` | `alerts/page.tsx` (366) | Tabs: Alerts (= offene Incidents), Incidents (alle), Maintenance | `/api/v1/incidents` (Default-Limit 50), `/api/v1/maintenance-windows`, `/api/v1/hosts?status=maintenance` | Suche, Severity-/Status-Filter (clientseitig), Wartungsfenster CRUD, Wartung beenden | Nav, `g a` |
| `/incidents/[id]` | `incidents/[id]/page.tsx` (553) | Incident-Detail | `/api/v1/incidents/{id}` (200 Events, Logs ±5 min, Log-Analyse, Postmortem) | Acknowledge, Resolve, Feedback „Echt/Rauschen“, Postmortem (KI) erzeugen | nur über Links |
| `/rules` | `rules/page.tsx` (579) | Schwellwertregeln auf Integrations-, Ping- und Syslog-Quellen | `/api/v1/rules`, `/api/rules/fields`, `/sources`, `/test` | CRUD, Toggle, Testlauf | Nav, `g r` |
| `/syslog` | `syslog/page.tsx` (354) | Log-Tabelle und Live-Tail | `/api/v1/syslog?limit=200`, SSE | Severity (Server), Text- und Hostfilter (Client), Sortieren, Export, Live an/aus, Zeile aufklappen | Nav, `g s` |
| `/syslog/dashboard` | `syslog/dashboard/page.tsx` (310) | Syslog-Statistik | `/api/v1/syslog/stats` | Zeitraum | Link-Button |
| `/syslog/templates` | `syslog/templates/page.tsx` (467) | „Log Intelligence“: Templates, Noise, Root Cause | `/syslog/api/templates` (paginiert), `/api/root-cause/{hash}` | Tags bearbeiten, Root Cause anzeigen | Link-Button |
| `/agents` | `agents/page.tsx` (334) | Agentenliste, Installation | `/api/v1/agents`, `/api/enrollment-info`, `/api/v1/hosts` | Installationsbefehl, Probe-Schalter (wirkungslos, siehe F-07), Deaktivieren, Stilllegen | Nav |
| `/agents/[id]` | `agents/[id]/page.tsx` (561) | Agent-Detail | `/api/v1/agents/{id}` | Log-Kanäle und -Level, überwachte Dienste, Deinstallation | Liste, Host-Detail |
| `/scanner` | `scanner/page.tsx` (551) | Subnetz-Scan, Zeitpläne | `/api/subnet-scanner/*` | Scan, gefundene Hosts übernehmen, Zeitpläne CRUD/Run | Nav |
| `/snmp` | `snmp/page.tsx` (948) | SNMP-Hosts und MIB-Verwaltung | `/api/snmp/*` | Host-Konfiguration, Poll, MIB-Upload/-Bibliothek, OID-Browser | Nav |
| `/ssl` | `ssl/page.tsx` (387) | Zertifikatsablauf | `/api/ssl/certs`, `/detail/{id}` | Alle aktualisieren, Detail, Export | Nav |
| `/credentials` | `credentials/page.tsx` (384) | Verschlüsselte Zugangsdaten (SNMP, WinRM, SSH) | `/api/credentials/*` | CRUD | Nav |
| `/tasks` | `tasks/page.tsx` (478) | Inbox: neu entdeckte Ports und Zertifikate | `/api/tasks` | Port/SSL überwachen oder ignorieren, Scan-all, Verlauf | Nav (Badge) |
| `/topology` | `topology/page.tsx` (473) | Abhängigkeitsbaum (Canvas) | `/api/v1/topology` (probe-bewusst) | Zoom, Klick → Host | Nav |
| `/bandwidth` | `bandwidth/page.tsx` (379) | Traffic, Interfaces, Top Talkers | `/api/bandwidth*` | Zeitraum 1 h bis 7 d | Nav |
| `/backups` | `backups/page.tsx` (427) | Backup-Monitoring und Compliance | `/api/backups*` | Sync, Verlauf | **keiner** |
| `/digest` | `digest/page.tsx` (334) | Wochenbericht | `/api/v1/digest` | nur Lesen | Nav (System) |
| `/integration/store` | `integration/store/page.tsx` (160) | Integrationskatalog | `/api/integrations` | Typ wählen | Sidebar „+ Add Integration“ |
| `/integration/[type]` | `integration/[type]/page.tsx` (488) | Instanzen eines Typs, Konfiguration | `/api/integration/{type}/fields`, CRUD | Anlegen, Bearbeiten, Löschen | Sidebar |
| `/integration/[type]/[id]` | `integration/[type]/[id]/page.tsx` (140) und 19 `components/integrations/*Detail.tsx` | Instanzdetail je Typ | `/api/v1/integrations/{id}` | nur Lesen | Liste, Dashboard |
| `/system/status` | `system/status/page.tsx` (706) | Self-Monitoring, Updates | `/api/system/status`, `/api/update/*` | Update prüfen/anwenden | Nav (System), `g t` |
| `/system/audit` | `system/audit/page.tsx` (199) | Audit-Log | `/api/v1/audit` (offset-paginiert) | Filter, Blättern | Nav (Admin) |
| `/settings` | `settings/page.tsx` (2400) | 8 Tabs: System, Monitoring, Notifications, Appearance, API, AI, Authentication, Backup | `/settings/*`, `/api/v1/backup/*` | siehe 1.4 | Nav (Admin), `g i` |
| `/users` | `users/page.tsx` (330) | Benutzer, eigenes Passwort | `/api/users`, `/users/me/password` | CRUD, Rolle, Passwort ändern | Nav (Admin) |
| Overlays | `CopilotPanel`, `CommandPalette`, `KeyboardShortcuts`, `Modal` | KI-Chat, Suche, Hilfe | | | global |
| Systemseiten | `not-found.tsx`, `(app)/error.tsx`, `(app)/loading.tsx` | Fehler und Laden | | | |

### 1.4 Settings: Inhalt der 8 Tabs

| Tab | Inhalt | Gehört inhaltlich zu |
|---|---|---|
| System | Instanzname, Agent-Server-Adresse, Zeitzone | Administration → Allgemein |
| Monitoring | Ping-Intervall, Datenaufbewahrung, Syslog-Einstellungen, „Predictive correlation“ | Administration → Monitoring-Richtlinien |
| Notifications | Ein/Aus, Grace Period, **Incident-Schwellen (Korrelation)**, 7 Kanäle (Telegram, Discord, Webhook, SMTP, Teams, Slack, ntfy), Public URL, Digest-Mail, Versandhistorie (letzte 50) | Alerting → Benachrichtigung bzw. Korrelation |
| Appearance | Akzentfarbe, Sidebar-Position, Dichte, Schriftgrösse (nur `localStorage`, `stores/theme.ts:37`) | Benutzerprofil |
| API | Endpoint-Doku (statisch), API-Keys | Administration → API |
| AI | Opt-in, Provider, Redaction, Verbindungstest, Nutzung (`components/settings/AiSettingsTab.tsx`) | Administration → KI |
| Authentication | LDAP/AD | Administration → Identität |
| Backup | DB-Übersicht, verschlüsseltes Backup/Restore | Administration → System |

### 1.5 Backend-Fähigkeiten ohne UI

| Endpoint / Modell | Inhalt | Datei |
|---|---|---|
| `GET /api/v1/syslog/intelligence` | Baseline-Anomalien, Bursts, Fleet-Patterns, steigende Trends, Precursors (je Top 10) | `backend/routers/api_v1.py:1866` |
| `GET /syslog/api/smart-feed` | Log-Feed nach Noise-Score gefiltert | `backend/routers/syslog.py:598` |
| `GET /syslog/api/reverse-cause/{hash}` | Was folgt typischerweise auf ein Muster | `backend/routers/syslog.py:437` |
| `GET /api/v1/predictor/eval` | Precision und Noise-Rate je Regel (aus dem Feedback) | `backend/routers/api_v1.py:1702` |
| `POST /api/dashboard-layout`, `/reset` | Speicherbares Dashboard-Layout (global, nicht pro User) | `backend/routers/dashboard.py:1391` |
| Modell `SyslogView` | Gespeicherte Log-Ansichten (`filters_json`), ohne Endpoint | `backend/models/syslog.py:53` |
| `DashboardData.layout`, `health_score` je Host | im Payload enthalten, nicht genutzt bzw. nicht angezeigt | `hooks/queries/useDashboard.ts:25,38` |

---

## 2. Feature-Inventar gegen die Produktvision

### 2.1 Infrastructure Monitoring

| Fähigkeit | Status | Referenz / Bemerkung |
|---|---|---|
| Host-Checks ICMP, TCP, HTTP/HTTPS (kombinierbar, HTTP-Optionen) | Ja | `backend/models/ping.py:13`, `components/hosts/HttpOptionsFields.tsx` |
| Agent-Metriken (CPU, RAM, Disk, Load, Uptime) Linux/Windows | Ja | `agents/[id]/page.tsx`, `hosts/[id]/page.tsx:405-450` |
| Überwachte Windows-/Linux-Dienste | Ja | `PUT /api/v1/agents/{id}/services`, `lib/agentServices.ts` |
| Windows-Eventlog-Kanäle über den Agenten | Ja | `agents/[id]/page.tsx:336-353` |
| 19 Integrationen (Proxmox, UniFi, TrueNAS, Synology, Portainer, Redfish, NUT, …) | Ja | `backend/integrations/*`, `components/integrations/*Detail.tsx` |
| Container-Übersicht und Updates | Teilweise | nur Dashboard-Widget (`page.tsx:636-694`), keine eigene Ansicht |
| Backup-Monitoring und Compliance | Teilweise | `/backups` existiert, ist aber nicht verlinkt |
| Remote-Probes (Standorte) | Teilweise | Stufe 1 im Backend (`probe_id`, Report-Transport); UI-Schalter wirkungslos; „unknown“ nur in der Topologie (`docs/specs/2026-08-28-remote-probes-design.md`) |
| Wartungsfenster (wiederkehrend und einmalig) | Ja | `components/maintenance/MaintenanceWindowsPanel.tsx`, `backend/routers/maintenance.py` |
| Gruppen, Tags, Standorte, Kritikalität, Besitzer | Nein | `PingHost` hat keine solchen Felder |
| Auto-Discovery (Subnetz-Scan, Port-Discovery, SSL-Discovery) | Ja | `/scanner`, `/tasks` |

### 2.2 Network Monitoring

| Fähigkeit | Status | Referenz / Bemerkung |
|---|---|---|
| SNMP-Polling, MIB-Verwaltung, OID-Browser | Ja | `snmp/page.tsx`, `backend/routers/snmp.py` |
| Bandbreite je Interface, Top Talkers | Ja | `bandwidth/page.tsx` |
| Topologie (Parent-Beziehung, Proxmox VM→Node, UniFi Gateway→Switch→AP) | Ja | `backend/services/topology.py`, `topology/page.tsx` |
| Switch-Port-Tabelle, verbundene Clients | Ja | `hosts/[id]/page.tsx:1203` (`PortsTab`) |
| Upstream-Unterdrückung (Kaskaden) | Ja (Backend) | Korrelationsregel `upstream_failure`; in der UI nicht als Unterdrückung sichtbar |
| Speedtest, ISP (Swisscom), DNS (Pi-hole, AdGuard, Technitium), Reverse Proxy, Cloudflare | Ja | Integrations-Details |
| NetFlow/sFlow, Layer-2-Discovery (LLDP/CDP) | Nein | |

### 2.3 System Health

| Fähigkeit | Status | Referenz / Bemerkung |
|---|---|---|
| Health-Score je Host | Teilweise | in `/api/v1/hosts/{id}` inline berechnet (`api_v1.py:446-474`), im Dashboard-Payload enthalten, nicht angezeigt |
| Health-Score je Integration | Teilweise | `backend/services/health.py`, nur als ok/nicht ok sichtbar |
| Verfügbarkeit 24 h / 7 d / 30 d, 30-Tage-Heatmap | Ja | Hostliste, Dashboard |
| Self-Monitoring von Nodeglow (Jobs, DB, Pool, Datenfrische) | Ja | `/system/status`, `backend/services/self_check.py` |
| Datenfrische als sichtbarer Zustand je Datenquelle | Teilweise | `self_check` erzeugt Incidents; Hosts und Integrationen zeigen kein „stale“ |
| SLA/SLO-Ziele | Nein | im Open-Core-Schnitt als `ee` vorgesehen |

### 2.4 Alerting

| Fähigkeit | Status | Referenz / Bemerkung |
|---|---|---|
| Schwellwertregeln auf Snapshot-Feldern, Ping, Syslog | Ja | `rules/page.tsx`, `backend/services/rules.py` |
| Testlauf einer Regel | Ja | `POST /api/rules/test` |
| Benachrichtigungskanäle (7) mit Mindest-Severity | Ja | Settings → Notifications |
| Grace Period, Mindestanzahl Fehlversuche bzw. Zyklen | Ja | Settings → Notifications (`settings/page.tsx:1087-1146`) |
| Alert-Objekt mit Historie | Nein | Host down/up und Regeltreffer auf Ping-/Integrationsquellen hinterlassen nur einen `NotificationLog` (`backend/scheduler.py:682-709`, `services/rules.py:330-360`) |
| Routing, Eskalation, On-Call, Ruhezeiten je Kanal | Nein | Kanäle sind globale Settings-Keys, kein Kanalobjekt |
| Versandhistorie | Teilweise | letzte 50, versteckt in Settings |

### 2.5 Incident Management

| Fähigkeit | Status | Referenz / Bemerkung |
|---|---|---|
| Automatische Incidents über Korrelation (11 Regeln) | Ja | `backend/services/correlation.py:987-1031` |
| Dedup, Event-Timeline je Incident | Ja | `_find_or_create_incident` (`correlation.py:102`), `IncidentEvent` |
| Acknowledge, Resolve, Auto-Resolve | Ja | `api_v1.py:1553/1581`, `correlation.py:809` |
| Operator-Feedback (echt/Rauschen) → Precursor-Blacklist | Ja | `incidents/[id]/page.tsx:179-210` |
| KI-Postmortem (opt-in) | Ja | `backend/services/postmortem.py` |
| Liste betroffener Hosts mit Links | Nein | nur `affected_hosts` als Zahl in der Log-Analyse; das Modell speichert nur `host_ids_hash` |
| Zuständigkeit, Kommentare, Reopen, Un-Ack, Eskalation | Nein | |
| Eigene Incident-Liste mit Server-Filtern | Teilweise | Liste unter `/alerts?tab=incidents`, Default-Limit 50, Filter im Client |

### 2.6 Log Intelligence

| Fähigkeit | Status | Referenz / Bemerkung |
|---|---|---|
| Syslog-Empfang, ClickHouse-Speicher, TTL nach Severity | Ja | `clickhouse/init.sql` |
| Live-Tail (SSE) | Ja | `components/syslog/SyslogLiveTail.tsx` |
| Template-Extraktion, Noise-Score, Tags | Ja | `syslog/templates/page.tsx` |
| Root Cause je Template | Ja | `/api/root-cause/{hash}` |
| Baselines (Stunde × Wochentag), Burst-, Anomalie-, Trend-Erkennung, Precursors | Teilweise | Backend vollständig (`backend/services/log_intelligence.py`); in der UI nur indirekt über Incidents |
| Suche über die gesamte Aufbewahrung, Zeitbereich, Paginierung | Nein | 200 Zeilen, Max-Limit 1000, kein Offset/Cursor |
| Gespeicherte Log-Ansichten | Nein | Modell vorhanden, keine API |

### 2.7 Infrastructure Analytics

| Fähigkeit | Status | Referenz / Bemerkung |
|---|---|---|
| Wochen-Digest | Ja | `/digest`, Mail über Settings |
| Uptime-Ranking, höchste Latenz, Incident-Trend 14 d | Ja | Dashboard-Widgets |
| Disk-Full-Prognose (lineare Regression) | Teilweise | nur im Dashboard-Widget „Storage“ (`backend/services/predictions.py`) |
| Kapazitätsplanung, Reports, Export über Zeiträume | Nein | Export nur als aktuelle Tabelle (`components/ui/ExportButton.tsx`) |
| Precision der Regeln | Teilweise | `/api/v1/predictor/eval` ohne UI |

### 2.8 Operational Intelligence

| Fähigkeit | Status | Referenz / Bemerkung |
|---|---|---|
| Korrelation Host + Syslog + Topologie | Ja (Backend) | Regeln `host_down_syslog`, `upstream_failure`, `multi_host_down` |
| Prädiktive Incidents (gelernte Precursors) | Ja (Backend) | `learned_precursor_*`; in der UI als „Error Analysis / precursor hints“ |
| KI-Copilot (opt-in) | Ja | Glow-Panel |
| „Was hat sich seit meinem letzten Besuch geändert?“ | Nein | keine `since`-Parameter, kein Event-Stream |
| Globale Event-Timeline | Nein | nur je Host (`/api/v1/hosts/{id}/timeline`) |
| Kennzeichnung von Beziehungen (bestätigt / regelbasiert / vermutet) | Nein | in `docs/design/prototypes/SCENARIO.md` definiert, in der UI fehlend |

---

## 3. Scorecard (1 = mangelhaft, 5 = sehr gut)

| Kriterium | Note | Begründung |
|---|---|---|
| Visuelle Professionalität | 3 | Konsistenter Dark-Look mit sauberer Typografie, aber Gamer-Ästhetik (3D-Globus mit Sternen, Gradient-Logo, 13 Icon-Farben), die bei Enterprise-Käufern nicht als „Leitstand“ gelesen wird. |
| Informationshierarchie | 2 | Gleichgewichtete Widgets; die wichtigste Information (was ist kaputt, was ist neu) hat keinen privilegierten Platz; der Hostname steht auf der Detailseite dreimal. |
| Usability | 3 | Gute Einzelstücke (Status-Pills, Command Palette, Bulk-Auswahl, Erststart), aber clientseitige Filter auf abgeschnittenen Daten, keine URL-Zustände, stille Fehlschläge. |
| Navigationslogik | 2 | Flache Liste ohne Arbeitsmodell; Incidents unter „Alerts“, Wartung unter „Alerts“, Digest unter „System“, Backups unerreichbar. |
| Konsistenz | 2 | Vier Tab-Implementierungen ohne gemeinsame Komponente, drei API-Generationen, gemischte Sprache (z. B. „War dieser Alert nützlich?“ in englischer UI), unterschiedliche Zählweisen für dieselbe Grösse. |
| Wiedererkennbarkeit | 3 | Eigenständiger Stil und Name; aber Status- und Markenfarben (Sky/Violett) kollidieren mit Statusfarben (Grün/Amber/Rot) und Kategoriefarben. |
| Informationsdichte | 3 | Hostliste nach Density-Pass gut (ca. 28 px Zeilen); Dashboard und Host-Detail verschwenden Fläche (Globus, leere Uptime-Kacheln, Hero-Zeile). |
| Skalierbarkeit | 1 | Alles laden und im Client filtern; Syslog 200 Zeilen; Incidents Limit 50; keine Gruppen, Standorte oder Tenant-Kontext; Navigation wächst mit jeder Funktion linear. |
| Barrierefreiheit | 2 | Teilweise gute ARIA (`aria-sort`, `aria-current`, Modal mit `aria-modal`), aber Labels ohne Zuordnung, keine Tab-Semantik, Canvas ohne Alternative, Status nur über Farbe, 181× Schrift ≤ 11 px. |
| Operative Effizienz | 2 | Kein Weg „Alert → betroffene Hosts → Logs → Lösung“ ohne Umwege; kein „seit letztem Besuch“; Incidents ohne Zuständigkeit; Morgencheck verteilt über 5 Seiten. |

Mittelwert: **2,3 / 5**.

---

## 4. Befunde

Priorität: **P1** = Vertrauen oder Funktion beschädigt bzw. blockiert Kernworkflow · **P2** = deutliche Effizienz- oder Verständnisbremse · **P3** = Politur und Konsistenz.
Abhängigkeit: „FE“ = rein Frontend · „BE: …“ = benötigt Backend-Arbeit.

### 4.1 Datenwahrheit und Zustände

| ID | Ansicht / Komponente | Problem heute | Auswirkung | Vorschlag | Prio | Technische Abhängigkeit |
|---|---|---|---|---|---|---|
| F-01 | Hostliste, Dashboard, Sidebar-Badge | `online` stammt aus dem letzten Ergebnis (`backend/routers/ping.py:153`); `probes.statuses_for` wird nur in `/api/v1/topology` genutzt (`api_v1.py:1961`). Fällt eine Probe aus, bleiben ihre Hosts grün. | Ausfall eines ganzen Standorts bleibt unsichtbar; Vertrauensbruch. | Status serverseitig einheitlich berechnen (`up / down / degraded / unknown / maintenance / disabled`) mit `observed_at` und `stale_reason`; UI zeigt „unknown“ grau gestreift, nie grün. | P1 | BE: probe-bewusster Status in `/hosts/api/status`, `/api/v1/hosts`, `/api/dashboard` |
| F-02 | Dashboard-KPI-Kacheln | `AnimatedCounter value={value ?? 0}` (`page.tsx:775`): ein fehlender Wert wird als „0“ angezeigt, „Offline 0“ wirkt gesund. | Falsche Entwarnung bei Teilausfällen des Payloads. | Fehlender Wert = „—“ mit Zustand „keine Daten“; Kachel-Rand neutral, nicht grün. | P1 | FE |
| F-03 | Host-Detail, Metrik-Kacheln | Disk „0 % · 0 / 32 GB“ grün, obwohl die Integration für die VM keinen realen Wert liefert (Screenshot Host-Detail); `device.disk_pct` wird ungeprüft gerendert (`hosts/[id]/page.tsx:569-574`), `pctColor` färbt alles unter 75 % grün (:145). | Falsches Sicherheitsgefühl bei Speicherproblemen. | Werte mit `null` oder unplausibel (used = 0 bei total > 0) als „nicht gemeldet“ darstellen; Quelle und Zeitstempel je Kachel. | P1 | FE; BE optional: `null` statt 0 liefern |
| F-04 | Dashboard „Anomalies“, „Alert Trends“, „Recent Incidents“ | Leerer Zustand ist grün („No anomalies detected“, „All clear“), auch wenn die Baselines noch lernen, die Korrelation aus ist oder die Daten fehlen (`page.tsx:313,413,458`). | „Nichts gefunden“ wird mit „alles gut“ verwechselt. | Leerzustände unterscheiden: „keine Treffer“ (neutral) vs. „Analyse nicht aktiv / lernt noch“ (Info) vs. „Fehler“. Grün nur bei positiv bestätigtem Zustand. | P1 | BE: Status der Analyse (Baseline-Reife) im Payload |
| F-05 | Alerts-Tab | Zeigt `status === 'open'` aus den 50 zuletzt aktualisierten Incidents (`alerts/page.tsx:67`, `api_v1.py:1248`); acknowledged verschwindet aus „Alerts“. | Offene Incidents können fehlen, „All clear“ trotzdem; quittierte, ungelöste Probleme sind unsichtbar. | Server-Filter `status=open,acknowledged`, Gesamtzahl im Response, Hinweis bei Abschneiden. | P1 | BE: Multi-Status-Filter, `total`, Cursor |
| F-06 | Sidebar-Badge vs. Dashboard | Badge = `open` (`/api/v1/status`, `api_v1.py:269`), Kachel = `open + acknowledged` (`dashboard.py:642`); Screenshot zeigt 7 vs. 5. Kachel „Total 43“ zählt Wartungshosts nicht, der Globus nennt 48. | Widersprüchliche Zahlen zerstören Vertrauen. | Eine Definition je Kennzahl (Glossar), ein Endpoint für Zähler; „Total“ inkl. Wartung, mit Aufschlüsselung. | P1 | BE: gemeinsamer Summary-Endpoint |
| F-07 | Agents, Probe-Schalter | UI sendet `is_probe`, `PATCH /api/v1/agents/{id}` lässt das Feld nicht zu (`api_v1.py:1092`), `GET /agents` liefert es nicht; der Toast meldet trotzdem Erfolg (`agents/page.tsx:203-212`). | Admin glaubt, einen Standort angebunden zu haben; Hosts werden nie geprüft. | Schalter erst zeigen, wenn die API das Feld unterstützt; Antwort prüfen; Probe-Status (letzter Heartbeat, zugewiesene Hosts) anzeigen. | P1 | BE: `is_probe` in PATCH und GET; Probe-Liste |
| F-08 | Hostliste, Bulk-Edit | `PATCH /api/v1/hosts/bulk` ist nach `PATCH /hosts/{host_id}` deklariert (`api_v1.py:877/939`); „bulk“ trifft vermutlich die Int-Route → 422. | Mehrfachbearbeitung schlägt fehl oder wirkt zufällig. | Route vor die Param-Route verschieben bzw. `{host_id:int}`; E2E-Test. Fehler im UI sichtbar machen. | P1 | BE: Routenreihenfolge (zu verifizieren) |
| F-09 | Dashboard-Header „LIVE“ | Das Label leuchtet beim Polling-Refresh, unabhängig von der WebSocket-Verbindung (`page.tsx:166-180`); einen globalen Verbindungsindikator gibt es nicht. | Bei Verbindungsverlust wirkt die Seite live, zeigt aber alte Daten. | Globaler Verbindungsstatus in der Topbar (verbunden / verzögert / getrennt seit …), „Stand: hh:mm:ss“ je Ansicht. | P1 | FE (`stores/websocket.ts` exponiert den Status bereits intern) |
| F-10 | Hostliste, Spalte „Availability“ | Zeigt den schlechtesten Wert aus 24 h/7 d/30 d (`hosts/page.tsx:46`); Hosts in Wartung erscheinen mit „0.0 %“ in Rot (Screenshot). | Rot ohne Handlungsbedarf, Alarmmüdigkeit. | Ein definierter Zeitraum je Spalte (Standard 7 d), Wartungszeiten aus der Verfügbarkeit herausrechnen oder kennzeichnen. | P2 | BE: Verfügbarkeit exkl. Wartung |
| F-11 | Host-Detail, Uptime-Kacheln | Kopf zeigt „100 %“, die Kacheln 24 h/7 d/30 d zeigen „--“ (Screenshot), zwei Datenpfade (`uptime` vs. Balken). | Unklar, welchem Wert man trauen kann. | Eine Quelle; bei fehlenden Daten „keine Messwerte im Zeitraum“ mit Grund. | P2 | BE prüfen: `uptime` im Host-Detail |
| F-12 | Topologie | Ohne `isError`-Zweig: Ein Fehler zeigt „No topology data / Add hosts and integrations“ (`topology/page.tsx:449-456`). | Fehler sieht aus wie Ersteinrichtung. | `QueryState` verwenden (Fehler, leer, Teilausfall). | P2 | FE |
| F-13 | Dashboard „Live Feed“ | „Waiting for messages…“ für leer, getrennt und Syslog nicht eingerichtet gleichermassen (`page.tsx:860-865`). | Kein Unterschied zwischen ruhig und kaputt. | Drei Zustände: verbunden und ruhig / Verbindung getrennt / keine Quelle eingerichtet (mit Link zur Einrichtung). | P2 | FE (SSE-Status ist in `useSSE` vorhanden) |

### 4.2 Navigation und Struktur

| ID | Ansicht / Komponente | Problem heute | Auswirkung | Vorschlag | Prio | Technische Abhängigkeit |
|---|---|---|---|---|---|---|
| F-14 | Sidebar | 13 flache Hauptpunkte + Integrationen + 5 System (`Sidebar.tsx:39-61`), Mischung aus Betrieb, Konfiguration und Analyse. | Hohe Suchkosten, neue Funktionen verschlechtern die Navigation weiter. | 6 Bereiche mit Unterseiten (siehe 02-IA), Konfigurationsobjekte unter Administration. | P1 | FE |
| F-15 | `/backups` | Keine Verlinkung im Frontend (Sidebar, Palette, Links). | Funktion faktisch nicht vorhanden. | Unter Infrastruktur → Backups einordnen. | P1 | FE |
| F-16 | `/alerts` | Tabs Alerts / Incidents / Maintenance; „Alerts“ ist ein Filter von Incidents; Wartung ist kein Alert. | Begriffliche Verwirrung, Deep Links auf Incidents über `?tab=`. | Eigene Bereiche: Incidents (Liste + Detail), Alerting-Regeln, Wartung als eigene Seite. | P1 | FE |
| F-17 | Syslog-Unterseiten | `/syslog`, `/syslog/dashboard`, `/syslog/templates` über Link-Buttons statt Tabs; „Templates“ heisst im Header „Log Intelligence“. | Funktionen werden nicht gefunden, Begriffe wechseln. | Bereich „Logs“ mit Tabs Explorer / Übersicht / Muster / Erkenntnisse (inkl. `/syslog/intelligence`). | P2 | FE; BE für Erkenntnisse bereits vorhanden |
| F-18 | Rules | Untertitel „alert and correlation rules“, verwaltet werden aber nur Schwellwertregeln; Korrelationsschwellen liegen in Settings → Notifications (`settings/page.tsx:1104`). | Nutzer suchen Korrelation an der falschen Stelle. | Alerting → Regeln (Schwellwert) und Alerting → Korrelation (eingebaute Regeln, Schwellen, Precision aus `predictor/eval`). | P2 | BE: Liste der Korrelationsregeln als API |
| F-19 | Tasks | Name „Tasks“ für „neu entdeckte Ports und Zertifikate“. | Erwartet werden Aufgaben oder Tickets. | Umbenennen in „Discovery-Inbox“ unter Infrastruktur → Discovery, zusammen mit Scanner. | P2 | FE |
| F-20 | Digest | Unter „System“ eingeordnet, ist aber ein Analysebericht. | Wird von IT-Leitung nicht gefunden. | Analytics → Berichte. | P3 | FE |
| F-21 | Credentials, SNMP-MIBs | Zugangsdaten und MIB-Verwaltung im Hauptmenü auf gleicher Ebene wie Hosts. | Täglich ungenutzte Punkte verdrängen die Arbeitsbereiche. | Administration → Zugangsdaten; SNMP-Konfiguration als Teil von Integrationen/Datenquellen, SNMP-Werte am Host. | P2 | FE |
| F-22 | Integrationen in der Sidebar | Standardmässig eingeklappt (`Sidebar.tsx:105`), ein Eintrag je Typ; Health nur als 1,5-px-Punkt. | Integrationsausfälle bleiben unbemerkt; Liste wächst mit jedem Typ. | Eine Seite „Integrationen“ mit Health-Tabelle; Fehler fliessen in „Aufmerksamkeit“ auf dem Dashboard. | P2 | FE |
| F-23 | Sidebar-Suche vs. Command Palette | Zwei Suchen mit unterschiedlichem Umfang; Navigationsliste dreifach gepflegt (`Sidebar.tsx`, `CommandPalette.tsx:28`, `KeyboardShortcuts.tsx`). | Inkonsistenz; neue Seiten fehlen in einer der Listen (z. B. Backups überall). | Eine Navigations-Registry (Route, Label, Icon, Rolle, Shortcut), daraus Sidebar, Palette, Breadcrumbs. | P2 | FE |
| F-24 | Tastaturkürzel | g-Sequenzen in `Sidebar.tsx:131` und `KeyboardShortcuts.tsx:67` registriert. | Doppelte Navigation, Flackern. | Einen Listener behalten. | P3 | FE |
| F-25 | Settings | 2400 Zeilen, 8 Tabs, Tab nicht in der URL (nur Initialwert aus `?tab=`); Admin-Warnung wird auch Admins gezeigt (Screenshot Settings). | Schwer zu finden, nicht verlinkbar, überflüssige Warnung. | Administration mit Unterseiten und eigenen URLs; Rollenhinweis nur bei fehlender Berechtigung je Feld. | P2 | FE |
| F-26 | Benutzerprofil | Passwort ändern über die Users-Seite (Admin), Appearance in Settings (Admin-only), Theme-Toggle in der Sidebar. | Nicht-Admins finden ihr Profil nicht. | Benutzermenü → Profil (Passwort, Sprache, Zeitzone, Darstellung, Benachrichtigungen). | P2 | BE: Benutzereinstellungen serverseitig (`user_preferences` aus der Tenancy-Spec) |
| F-27 | Glow im Nav | KI-Panel als Nav-Punkt gleichrangig mit Arbeitsbereichen. | Signalisiert „KI zuerst“, widerspricht dem Opt-in-Entscheid. | Als kontextuelle Aktion (Topbar-Button, „Erklären“ im Incident) nur bei aktivierter KI; sonst ein Eintrag unter Administration → KI. | P3 | FE |

### 4.3 Listen, Filter, Skalierung

| ID | Ansicht / Komponente | Problem heute | Auswirkung | Vorschlag | Prio | Technische Abhängigkeit |
|---|---|---|---|---|---|---|
| F-28 | Hostliste | Lädt alle Hosts (`/hosts/api/status`), Filter, Suche, Sortierung und Paginierung im Client (`hosts/page.tsx:154-201`). | Ab einigen hundert Hosts träge, bei MSPs nicht tragfähig. | Server-Paginierung mit Filtern (Status, Quelle, Probe/Standort, Tag, Text), Sortierung, Facettenzählern. | P1 | BE: `/api/v1/hosts?q&status&site&sort&cursor` |
| F-29 | Hostliste, Filterzustand | Status und Suche werden beim Ändern nicht in die URL geschrieben (`hosts/page.tsx:140,361`), Sortierung ebenfalls nicht. | Keine teilbaren Links, Zurück-Button verliert den Zustand. | URL als Quelle des Filterzustands (alle Listen). | P2 | FE |
| F-30 | Syslog | 200 Zeilen, Text- und Hostfilter nur über diese 200 (`syslog/page.tsx:76-106`); Host-Dropdown nur aus den geladenen Zeilen; kein Zeitbereich. | Wer einen Fehler von gestern sucht, findet ihn nicht; falsche „keine Treffer“. | Server-Suche mit Zeitbereich, Host-ID, Severity, App, Template; Cursor-Paginierung; Treffer-Zähler. | P1 | BE: Cursor/Offset auf `/api/v1/syslog`, Facetten |
| F-31 | Incident-Liste | Filter im Client über maximal 50 Einträge (`alerts/page.tsx:168-177`); kein Zeitraum, keine Regel- oder Host-Filter. | Historische Analyse unmöglich. | Server-Filter (Status, Severity, Regel, Host, Zeitraum), Cursor, Gesamtzahl. | P1 | BE: `/api/v1/incidents` erweitern |
| F-32 | Alle Listen | Keine gespeicherten Ansichten; Modell `SyslogView` ungenutzt. | Wiederkehrende Filter (z. B. „Firewall-Fehler Standort Bern“) müssen jedes Mal neu gebaut werden. | Gespeicherte Ansichten je Liste (persönlich und geteilt), als Einstieg in der Navigation pinbar. | P2 | BE: Saved-Views-API (generisch, pro User/Tenant) |
| F-33 | Hostliste, Spalte „Type“ | Rohe Check-Liste wie `icmp,dns:…,dns:…` (Screenshot Hostliste). | Schwer lesbar, Tabelle springt in der Breite. | Check-Icons mit Zähler und Tooltip; Fehler-Checks rot hervorgehoben. | P3 | FE |
| F-34 | Hostliste, Statuslabel | „Port Error“ für jeden fehlgeschlagenen Teil-Check (auch HTTPS), „Maint.“ abgekürzt. | Begriff passt nicht zu HTTP-Fehlern. | Zustand „degraded“ mit Grund („HTTPS fehlgeschlagen“). | P2 | FE; BE optional: einheitlicher Status |
| F-35 | Hostliste, `?q=` | Bei genau einem Treffer automatischer Redirect auf das Host-Detail (`hosts/page.tsx:203-208`); Syslog verlinkt Hosts per Hostname-Suche (`syslog/page.tsx:280`). | Unerwartete Sprünge, ungenaue Zuordnung (gleiche Namen an zwei Standorten). | Links per `host_id` (ClickHouse speichert `host_id` bereits); Redirect entfernen. | P2 | BE: `host_id` in Syslog-Antworten konsequent liefern |
| F-36 | Hosts ohne Metadaten | Keine Gruppen, Tags, Standorte, Kritikalität. | Keine Sicht „nach Standort/Kunde/Service“, keine Priorisierung. | Tags und Standort am Host (Standort = `site` aus der Tenancy-Spec). | P2 | BE: Felder und Filter |

### 4.4 Dashboard und Detailseiten

| ID | Ansicht / Komponente | Problem heute | Auswirkung | Vorschlag | Prio | Technische Abhängigkeit |
|---|---|---|---|---|---|---|
| F-37 | Dashboard, Gravity-Globus | Hero-Fläche (ca. 330 px), dekorativ, Three.js-Bundle, kein Handlungswert; Legende „Close orbit = healthy“ muss gelernt werden (`components/dashboard/GravityWidget.tsx`). | Verdrängt Handlungsinformation über den Falz; wirkt verspielt. | Entfernen bzw. optional als „Wallboard“-Modus; Hero = „Betriebszustand + Aufmerksamkeit“. | P1 | FE |
| F-38 | Dashboard, Widgetraster | Rund 14 gleich grosse Karten, Reihenfolge fest, gespeichertes Layout im Backend ungenutzt. | Keine Priorität; jede Rolle sieht dasselbe. | Vier Ebenen (siehe 02-IA, Abschnitt 6), feste obere zwei Ebenen, unten konfigurierbar. | P1 | FE; BE: Layout pro User |
| F-39 | Dashboard „Recent Incidents“ | Zeigt auch gelöste Incidents mit Label „5 active“ (Screenshot); kein Alter, keine Dauer, keine betroffenen Hosts. | Aktives und Erledigtes vermischt. | Liste „Offen“ (Severity, Dauer, betroffene Objekte, Quittiert-von) getrennt von „kürzlich gelöst“. | P1 | BE: betroffene Hosts je Incident |
| F-40 | Dashboard „Hosts“-Widget | Die ersten 20 Hosts (`page.tsx:252`) statt der problematischen. | Redundant zur Hostliste. | Ersetzen durch „Hosts mit Problemen“ (down, degraded, unknown) mit Grund. | P2 | FE |
| F-41 | Dashboard, Header | „Uptime: 5m“ meint die Laufzeit von Nodeglow selbst (`page.tsx:181`). | Missverständlich (wird als Infrastruktur-Uptime gelesen). | Nach Administration → Systemstatus verschieben. | P3 | FE |
| F-42 | Dashboard, „Avg Latency“ | Clientseitiger Durchschnitt über alle Hosts (`page.tsx:77-84`), 0 bei fehlenden Daten. | Kennzahl ohne Aussage (Mittel über LAN und WAN), Fehler wird zu „0 ms“. | Entfernen oder ersetzen durch „Hosts über Latenzschwelle“. | P2 | FE |
| F-43 | Incident-Detail | Keine Liste betroffener Hosts, Logs ±5 min aller Hosts (nicht hostgefiltert, `api_v1.py:1497-1503`), keine Topologie, Events nur als Typ-Badge. | Root-Cause-Arbeit erfordert manuelles Springen und Suchen. | Kopf: Status, Dauer, Severity, Zuständig; Panels: betroffene Objekte (mit Status), Timeline, korrelierte Logs (hostgefiltert), Topologie-Ausschnitt, Beziehungslabels. | P1 | BE: `host_ids` am Incident, hostgefilterte Logs |
| F-44 | Incident-Detail, Feedback | Deutsche Texte („War dieser Alert nützlich?“, „Echt“, „Rauschen“, „von“) in sonst englischer UI (`incidents/[id]/page.tsx:181-206`). | Unprofessionell, zeigt fehlendes i18n. | i18n-Gerüst (DE/EN, später FR/IT), alle Strings aus Katalogen. | P2 | FE |
| F-45 | Incident-Workflow | Kein Zuständiger, keine Notizen, kein Reopen/Un-Ack; `acknowledged_by` ist Freitext. | NOC-Übergaben und MSP-Teams können nicht koordinieren. | Zuständigkeit, Notizen/Kommentare, Statuswechsel mit Audit. | P2 | BE: Felder, Endpoints |
| F-46 | Host-Detail | 1920 Zeilen; Name dreifach (Breadcrumb, Titel, Karte); Tabs nur im State; keine Tabs für Incidents, Checks, Metriken; kein Topologie-Kontext (Parent/Kinder). | Wichtige Kontexte fehlen, Überblick leidet. | Detail-Pattern: Kopf (Status, Grund, Quelle, Standort, Aktionen), Tabs Übersicht / Checks / Metriken / Incidents / Logs / Topologie / Konfiguration, Tab in der URL. | P2 | BE: Parent/Kinder am Host-Detail |
| F-47 | Agents vs. Hosts | Agent und Host sind getrennte Objekte; die Verbindung ist nur im Host-Detail (`hosts/[id]/page.tsx:307`) sichtbar. | Doppelte Pflege, unklar, was „das Gerät“ ist. | Host als Hauptobjekt, Agent als Datenquelle am Host; Agentenliste bleibt für Rollout und Versionen. | P2 | FE |

### 4.5 Visuelles System, Theme, Barrierefreiheit, i18n

| ID | Ansicht / Komponente | Problem heute | Auswirkung | Vorschlag | Prio | Technische Abhängigkeit |
|---|---|---|---|---|---|---|
| F-48 | Gesamte UI, Light Mode | 1577 `slate-*`, ca. 520 `white/[x]`, ca. 1000 Farbklassen hart codiert; Light Mode über Overrides einzelner Klassen (`app/globals.css:74-131`); Charts immer mit Dark-Palette (`styles/tokens.gen.ts`, `lib/echarts-theme.ts`). | Light Mode faktisch defekt, NOC-Räume mit hellem Umfeld und Druck/Export leiden. | Semantische Tokens (Surface, Text, Border, Status inkl. `unknown` und `maintenance`) als CSS-Variablen, Tailwind auf Variablen mappen, Codemod für Klassen. | P1 | FE (Token-Pipeline erweitern) |
| F-49 | Statusfarben | Severity „warning“ wird als StatusDot „maintenance“ gerendert (`alerts/page.tsx:140`); Wartung und Warnung beide Amber; Status nur über Farbe. | Verwechslungsgefahr, nicht farbenblind-tauglich. | Statussystem mit Form + Farbe + Text (z. B. Kreis, Dreieck, Raute, Schraffur für unknown); eigene Farbe für Wartung (z. B. Blau-Grau). | P1 | FE |
| F-50 | Formulare | 140 `<label>`, 26 `htmlFor`; Toggles teils ohne Rolle. | Screenreader können Felder nicht zuordnen; Klickfläche kleiner. | `Field`-Komponente mit Label, Hilfe, Fehler, `aria-describedby`. | P2 | FE |
| F-51 | Tabs | Vier eigene Tab-Implementierungen (`useState<Tab>`), keine `role="tablist"`, keine Pfeiltasten. | Nicht barrierefrei, uneinheitlich. | Eine `Tabs`-Komponente mit URL-Sync und ARIA. | P2 | FE |
| F-52 | Mikro-Labels | 158× `uppercase`, 181× `text-[9–11px]`, als Regel im `DESIGN_GUIDE.md` („Labels are always 10px uppercase“). | Schlechte Lesbarkeit, besonders auf NOC-Wänden und für ältere Nutzer. | Mindestgrösse 12 px, Satzschreibung, Uppercase nur für sehr kurze Badges. | P2 | FE (Design-Guide anpassen) |
| F-53 | Live-Updates | Kein `aria-live` für neue Incidents oder Statuswechsel; Animationen (`animate-ping`) auch bei Steady-State. | Screenreader-Nutzer verpassen Änderungen; unruhige Oberfläche. | Polite-Live-Region für neue Incidents; Bewegung nur bei Zustandswechsel (< 60 s). | P3 | FE |
| F-54 | Topologie | Nur `<canvas>` mit Klick-Navigation (`topology/page.tsx:372`), keine Liste, keine Tastatur, keine Incident-Überlagerung, hartcodierte Hex-Farben. | Nicht barrierefrei; bei vielen Knoten unübersichtlich. | Baum-/Listenansicht als gleichwertige Alternative; Filter nach Standort/Status; Incident-Overlay. | P2 | FE; BE: Standort am Knoten |
| F-55 | i18n, Formate | Keine i18n-Bibliothek; 56× `toLocaleString()` ohne feste Locale/Zeitzone. | Gemischte Datumsformate, kein DE/FR/IT für Schweizer Kunden. | i18n-Gerüst, Datumsformat und Zeitzone aus Benutzerprofil bzw. Tenant (`timezone`). | P2 | FE; BE: Benutzer-Locale |
| F-56 | Passwort-Dialog | Kein Bestätigungsfeld, keine Richtlinie, kein Anzeigen-Schalter (Screenshot). | Tippfehler sperren Nutzer aus. | Profil-Seite mit Richtlinie, Bestätigung, Stärkeanzeige. | P3 | FE; BE: Passwort-Policy ausgeben |
| F-57 | App-Shell, Seitenwechsel | Der gesamte Inhalt wird bei jedem Pfadwechsel neu gemountet und eingeblendet (`AppShell.tsx:137`, `key={pathname}`). | Sobald Tabs und Filter in der URL stehen (F-29, F-51), flackert jede Tab- oder Filteränderung und lokaler Zustand geht verloren. | Übergang nur zwischen Bereichen, nicht innerhalb einer Objektseite; Layout-Routen (`layout.tsx`) für Bereiche nutzen. | P3 | FE |
| F-58 | API-Generationen | Frontend ruft `/api/v1/*`, `/api/*`, `/hosts/api/*`, `/syslog/api/*`, `/settings/*`, `/api/v2/nav-counts` auf. | Uneinheitliche Fehlerformate und Berechtigungen; Redesign-Aufwand höher. | Neue Ansichten nur gegen `/api/v1` (bzw. ein BFF), alte Pfade schrittweise ablösen. | P2 | BE: API-Konsolidierung |

Anzahl Befunde: 58 (P1: 21, P2: 29, P3: 8).

---

## 5. Technische Rahmenbedingungen für das Redesign

### 5.1 Token-Pipeline

| Aspekt | Heute | Konsequenz |
|---|---|---|
| Quelle | `frontend/design-tokens/tokens.json`: 12 Farben je Theme, 3 Radien, 2 Fonts | Keine Status-Tokens für `unknown`, `maintenance`, `info`, keine Spacing-, Typo-, Elevation- oder Motion-Tokens |
| Build | `frontend/scripts/build-tokens.mjs` erzeugt `styles/tokens.gen.css` (CSS-Variablen je `data-theme`) und `tokens.gen.ts` (nur **Dark**-Werte) | Tailwind-Farben `ng-*` sind statisch dunkel; JS-Konsumenten (ECharts, Canvas) bekommen im Light Mode falsche Farben |
| Konsum | 152× `var(--ng-*)` gegenüber ca. 3000 hartcodierten Farbklassen | Ein Theme-Wechsel braucht einen Codemod; ohne ihn bleibt Light Mode Flickwerk |
| Laufzeit | `AppShell.tsx:60-67` setzt `data-theme`, `--accent`, Schriftgrösse; Präferenzen in `localStorage` | Präferenzen gehen beim Gerätewechsel verloren; der Tenant-Kontext (Branding für MSPs) fehlt |

Empfehlung: Token-Schema um semantische Ebenen erweitern (`color.status.*`, `color.surface.*`, `color.text.*`, `space`, `font.size`), Tailwind-Farben auf `rgb(var(--…) / <alpha-value>)` umstellen, ECharts-Theme aus CSS-Variablen zur Laufzeit lesen.

### 5.2 Komponenteninventar

| Vorhanden (`components/ui`, `layout`) | Bewertung | Fehlt für das neue IA |
|---|---|---|
| `Button`, `Badge`, `StatusDot`, `GlassCard`, `Modal`, `ConfirmDialog`, `Toast`, `Skeleton`, `Pagination`, `CopyButton`, `ExportButton`, `EmptyState`, `QueryState` (+ `QueryErrorState`, `StaleDataBanner`), `WidgetErrorBoundary`, `CommandPalette`, `KeyboardShortcuts`, `PageHeader`, `SectionHeader`, `Breadcrumbs`, `EChart`/`LazyEChart`, `HeatmapGrid` | Basis brauchbar; `QueryState` ist das richtige Muster, wird aber nur teilweise genutzt | `DataTable` (Server-Modus, Spaltenwahl, Auswahl, Sticky Header), `FilterBar` mit Chips, `SavedViewMenu`, `Tabs` (ARIA + URL), `SideDrawer`, `TimeRangePicker`, `StatusBadge` (Form + Farbe + Text + Frische), `Freshness`-Anzeige, `Field`/Form-Bausteine, `ObjectHeader`, `RelationLabel` (bestätigt/regelbasiert/vermutet), `ConnectionIndicator`, `TenantSwitcher` (Zukunft) |

Grosse Seitendateien (Host-Detail 1920, Settings 2400, SNMP 948, Dashboard 893 Zeilen) enthalten Unterkomponenten inline. Das Redesign sollte sie in feature-basierte Module zerlegen (`features/hosts/*`, `features/incidents/*`).

### 5.3 Datenbeschaffung im Frontend

| Muster | Ort | Bemerkung |
|---|---|---|
| TanStack Query mit Polling | `hooks/queries/*` | 10 s (Syslog), 30 s (Incidents, Topologie), 60 s (Integrationen, Status); `whileLive()` verlangsamt bei aktivem WS |
| WebSocket `/ws/live` | `stores/websocket.ts`, `lib/liveUpdates.ts` | Faltet `ping_update` und `agent_metric` in den Query-Cache; **keine** Incidents, Syslog, Konfigurationsänderungen |
| SSE | `hooks/useSSE.ts` | Syslog-Live (`/syslog/stream`), KI-Chat |
| Aggregat-Endpoint | `/api/dashboard` | Ein grosser Payload (ca. 25 Schlüssel); ein Teilfehler blockiert alles oder liefert Nullen |
| Filterung | Seiten | Überwiegend im Client nach Vollabruf |
| Fehlerdarstellung | `QueryState`, `StaleDataBanner` | Gut, aber nicht flächendeckend (Topologie, Teile des Dashboards) |

### 5.4 Was das Backend liefern kann und was nicht

| Bereich | Kann heute | Kann heute nicht |
|---|---|---|
| Hosts | Liste mit Status, Latenz, Uptime 24 h/7 d/30 d, Wartung, Quelle, `probe_id`, `parent_id`; Detail mit Agent- und Integrationsdaten, Health-Score; Timeline je Host (Status, Incidents, Syslog, Änderungen) | Server-Paginierung, Sortierung, Volltextsuche; Tags, Gruppen, Standort; probe-bewusster Status ausserhalb der Topologie |
| Incidents | Liste (Status, Severity, Titel-Suche, Host-Name-ILIKE, Limit ≤ 500), Detail mit bis zu 200 Events, Log-Analyse, Precursor-Hinweisen, Postmortem, Feedback | Cursor/Offset, Gesamtzahl, Zeitraum-, Regel- und `host_id`-Filter; betroffene Hosts als Liste (nur `host_ids_hash`); Zuständigkeit, Kommentare, Reopen |
| Alerts | Benachrichtigungen über 7 Kanäle, Versandlog (letzte 50) | Persistentes Alert-Objekt; Routing, Eskalation, On-Call |
| Events | `IncidentEvent` je Incident; Host-Timeline (on the fly) | Globaler Event-Stream; „Änderungen seit T“ |
| Korrelation | 11 eingebaute Regeln, Dedup, Auto-Resolve, Mindestzyklen | Liste und Erklärung der Regeln als API; Regel-Konfiguration ausser globalen Schwellen |
| Topologie | Knoten mit probe-bewusstem Status, `parent_id`-Kanten | Standort- oder Tenant-Zuordnung; Kanten anderer Art (L2, Abhängigkeit „Service nutzt“) |
| Logs | Abfrage (Limit ≤ 1000, Zeitraum ≤ 720 h, Severity max, Suche, `host_id`), Stats, Templates (paginiert), Root/Reverse Cause, Intelligence, Smart-Feed, SSE | Cursor-Paginierung, Facetten, gespeicherte Ansichten (Modell ohne API) |
| Baselines / Prognosen | Host-Baselines (Stunde × Wochentag), Precursors (Wilson), Fleet-Patterns, Disk-Full-Prognose | Allgemeine Metrik-Prognosen (CPU, Latenz), Baseline-Reife als Status |
| Wartung | Fenster (wöchentlich/einmalig, alle oder ausgewählte Hosts), Ad-hoc-Wartung | Wartung für Integrationen, Regeln, Standorte |
| Probes | `probe_id` am Host, Freshness je Probe (`services/probes.py`) | Probe-Liste, `is_probe` setzen, Status je Probe als API |
| Echtzeit | WS: Ping und Agent-Metriken; SSE: Syslog | Push für Incidents, Integrationsfehler, Konfiguration |
| Benutzer | Rollen admin/editor/readonly, LDAP, API-Keys mit Rolle | Benutzerpräferenzen, gespeicherte Ansichten, Benachrichtigungs-Abos je Benutzer |
| Tenancy | — | Tenants, Standorte, Mitgliedschaften (nur Spec) |

### 5.5 Folgen der Multi-Tenancy-Spec für die IA

- Tenant-Auswahl erfolgt über Header `X-Nodeglow-Tenant` und Session, **nicht** über den Pfad (`/t/{slug}` wurde verworfen). URLs bleiben tenant-neutral; Deep Links brauchen einen Tenant-Hinweis (Vorschlag in 02-IA, Abschnitt 2.3).
- `site` wird das Gruppierungsobjekt für Hosts, Probes und Scans; das IA sieht Standort als Filter und Gliederungsdimension vor.
- `user_preferences (user, tenant, key, value)` ist die natürliche Ablage für gespeicherte Ansichten, Dashboard-Layout, Spaltenwahl und „letzter Besuch“.
- MSP-Portal (übergreifende Übersicht, Incident-Board, Tenant-Switcher) ist `ee`. Das IA reserviert dafür eine Ebene über dem Tenant, ohne sie im Core zu zeigen.

### 5.6 Risiken für das Redesign

| Risiko | Gegenmassnahme |
|---|---|
| Neue IA verspricht Funktionen, die das Backend nicht liefert (z. B. „Änderungen seit letztem Besuch“) | Jede Fläche in 02-IA ist mit Datenquelle oder „Backend-Arbeit nötig“ markiert; ohne Daten wird die Fläche nicht gezeigt, statt Platzhalter zu füllen |
| Umbau grosser Seitendateien bricht Verhalten | Feature-Module schrittweise extrahieren, E2E-Tests je Workflow |
| Theme-Codemod kollidiert mit laufenden Branches | Codemod als eigener, mechanischer Commit nach dem Token-Ausbau |
| Status-Neudefinition (F-01) ändert Zahlen, an die Nutzer gewöhnt sind | Glossar und Changelog; „unknown“-Zustand bewusst einführen |
