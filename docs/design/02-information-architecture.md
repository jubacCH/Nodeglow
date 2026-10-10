# 02 — Informationsarchitektur (Phase B)

| | |
|---|---|
| **Stand** | 10.10.2026, aufbauend auf `01-ux-audit.md` (Befunde F-01 bis F-58) |
| **Gilt für** | Core (Single-Tenant, On-Prem) und SaaS; MSP-Ebene als `ee`-Erweiterung markiert |
| **Markierungen** | **[heute]** = mit vorhandenen Daten umsetzbar · **[BE]** = braucht Backend-Arbeit (Liste in Abschnitt 8) · **[ee]** = kommerzielle Erweiterung laut Open-Core-Schnitt · **[Zukunft]** = geplant, nicht spezifiziert |

---

## 0. Leitprinzipien

1. **Arbeitsabläufe vor Funktionsliste.** Die Navigation folgt dem, was Betrieb tut: erkennen → reagieren → verstehen → verbessern → verwalten. Sie folgt nicht der Reihenfolge, in der Funktionen gebaut wurden.
2. **Objekt zuerst.** Host, Incident, Integration und Standort haben je eine kanonische Detailseite. Jede Erwähnung eines Objekts irgendwo in der UI ist ein Link dorthin oder öffnet eine Vorschau (Drawer).
3. **Ehrlicher Zustand.** Fehlende, veraltete oder unbeobachtete Daten werden nie als gesund dargestellt (Abschnitt 6.5). Jede Zahl hat eine Definition und einen Zeitstempel.
4. **Die URL ist der Zustand.** Tabs, Filter, Zeitbereich, gespeicherte Ansicht und Vorschau stehen in der URL. Das ergibt teilbare Links, einen funktionierenden Zurück-Button und gespeicherte Ansichten.
5. **Betrieb und Konfiguration getrennt, aber nah.** Konfiguration liegt in dem Bereich, dessen Verhalten sie steuert (Alerting-Regeln bei Incidents), und ist nach Rolle sichtbar. Plattformweite Einstellungen liegen unter Administration.
6. **Kontext statt Navigation für Tenant und Standort.** Tenant (Zukunft) und Standort sind Filterdimensionen in der Kopfzeile bzw. in Listen, keine zusätzlichen Menüebenen.
7. **KI kontextuell und opt-in.** KI-Funktionen erscheinen nur, wenn sie aktiviert sind, und zwar dort, wo sie helfen (Incident erklären, Postmortem). Sie bekommen keinen eigenen Arbeitsbereich.

---

## 1. Neue Navigationsstruktur

### 1.1 Bewertung der Hypothese

Hypothese: *Overview / Infrastructure / Observability / Incidents / Analytics / Administration.*

| Bereich (Hypothese) | Tragfähig? | Anpassung und Begründung |
|---|---|---|
| Overview | Ja, als **eine** Seite | Kein Bereich mit Unterseiten. Das Lagebild ist der Startpunkt; ein Wallboard-Modus ist eine Darstellungsvariante, keine eigene Seite. |
| Infrastructure | Ja | Wird zum Inventar aller beobachteten Objekte: Hosts, Topologie, Agents/Probes, Integrationen (inkl. SNMP), Discovery, Zertifikate, Backups. Standorte kommen mit der Tenancy dazu. |
| Observability | Teilweise | Heute stecken darin nur Logs und Traffic; Metriken leben am Host. Der Bereich bleibt, ist aber klein: **Logs** (mit Erkenntnissen) und **Traffic**. Ein eigener Metrik-Explorer ist [Zukunft]. |
| Incidents | Ja, **erweitert** | Wird zum Bereich „Incidents & Alerting“: neben der Incident-Queue auch Wartung, Alert-Regeln, Korrelation und Benachrichtigungskanäle. So liegt die Frage „warum wurde ich (nicht) alarmiert?“ an einem Ort. Heute sind diese Teile auf `/alerts`, `/rules` und zwei Settings-Tabs verteilt. |
| Analytics | Ja, bewusst schmal | Berichte (Digest), Verfügbarkeit, Kapazität und Prognosen, Alert-Qualität. Es werden nur Seiten gezeigt, für die Daten existieren. |
| Administration | Ja | Unterteilt in **Organisation** (Benutzer, Authentifizierung, Zugangsdaten, API, KI) und **Plattform** (Monitoring-Richtlinien, Backup, Systemstatus/Updates, Audit). Persönliche Einstellungen ziehen ins **Profil** um. |

Die Reihenfolge ändert sich: **Incidents vor Infrastruktur**. Ein NOC beginnt beim Handlungsbedarf, nicht beim Inventar.

### 1.2 Neue Navigation (kompakt)

```
[Kopfzeile]  Tenant-Switcher [ee] · Standort-Filter [BE] · Suche (Cmd+K) · Verbindungsstatus
             · Glow (nur wenn KI aktiv) · Hilfe (?) · Benutzermenü (Profil, Darstellung, Abmelden)

Übersicht                      /
Incidents & Alerting
  Incidents                    /incidents                 Queue (offen + quittiert) und Verlauf
  Wartung                      /maintenance
  Alert-Regeln                 /alerting/rules
  Korrelation                  /alerting/correlation
  Benachrichtigungen           /alerting/channels         Kanäle + Versandprotokoll
Infrastruktur
  Hosts                        /hosts
  Topologie                    /topology
  Agents & Probes              /agents
  Integrationen                /integrations              inkl. Katalog und SNMP
  Discovery                    /discovery                 Inbox + Scans
  Zertifikate                  /certificates
  Backups                      /backups
  Standorte [BE]               /sites
Observability
  Logs                         /logs                      Explorer · Übersicht · Muster · Erkenntnisse
  Traffic                      /traffic
Analytics
  Berichte                     /analytics/reports
  Verfügbarkeit                /analytics/availability
  Kapazität & Prognosen        /analytics/capacity
  Alert-Qualität               /analytics/alert-quality
Administration (rollenabhängig)
  Organisation: Benutzer · Authentifizierung · Zugangsdaten · API · KI
  Plattform:    Allgemein · Monitoring-Richtlinien · Backup & Restore · Systemstatus · Audit-Log
  [ee]          Tenants · Provider

Gepinnte Ansichten (pro Benutzer) erscheinen unter dem jeweiligen Bereich, z. B. „Hosts › Standort Bern down“.
```

Die Navigation hat **6 Bereiche**: 20 Arbeitsseiten plus 10 rollenabhängige Administrationsseiten. Heute sind es 18 flache Punkte plus bis zu 19 Integrationseinträge, und die Administration steckt in einer einzigen Settings-Seite. Die Integrationstypen verschwinden aus der Sidebar und werden zur Tabelle auf `/integrations`.

### 1.3 Vollständiges Mapping: heute → neu

Jede heutige Seite, jeder Tab und jede globale Funktion hat einen neuen Ort. Grundlage ist das Inventar in `01-ux-audit.md`, Abschnitt 1.

#### Seiten und Tabs

| Heute | Neu (Bereich › Seite › Tab) | Neue URL | Bemerkung |
|---|---|---|---|
| `/` Dashboard | Übersicht | `/` | Neu aufgebaut, siehe Abschnitt 7 |
| `/hosts` | Infrastruktur › Hosts | `/hosts` | Server-Filter [BE], gespeicherte Ansichten [BE] |
| `/hosts/[id]` Tab Overview | Hosts › Detail › Übersicht | `/hosts/{id}` | |
| `/hosts/[id]` Tab Ports | Hosts › Detail › Ports & Clients | `/hosts/{id}/ports` | nur bei Switch/Gateway |
| `/hosts/[id]` Tab Timeline | Hosts › Detail › Verlauf | `/hosts/{id}/timeline` | |
| `/hosts/[id]` Tab Syslog | Hosts › Detail › Logs | `/hosts/{id}/logs` | eingebetteter Log-Explorer mit `host_id` |
| `/hosts/[id]` Monitoring-Karte (Checks, TCP-Ports) | Hosts › Detail › Checks | `/hosts/{id}/checks` | |
| `/hosts/[id]` Agent-/Geräte-Metriken | Hosts › Detail › Metriken | `/hosts/{id}/metrics` | |
| `/hosts/[id]` „Recent incidents“ | Hosts › Detail › Incidents | `/hosts/{id}/incidents` | [BE] `host_id`-Filter |
| `/hosts/[id]` Edit-Modal (inkl. Probe), Wartungskarte | Hosts › Detail › Einstellungen | `/hosts/{id}/settings` | Wartung zusätzlich als Kopfaktion |
| `/hosts/[id]` entdeckte Ports | Hosts › Detail › Checks (Abschnitt „Vorgeschlagen“) | `/hosts/{id}/checks` | zusätzlich in der Discovery-Inbox |
| — (Topologie-Kontext fehlt) | Hosts › Detail › Topologie | `/hosts/{id}/topology` | Parent, Kinder, Pfad [BE] |
| `/alerts` Tab Alerts (offene Incidents) | Incidents › gespeicherte Ansicht „Offen“ | `/incidents` (Standard) | Status `open` + `acknowledged` [BE] |
| `/alerts` Tab Incidents | Incidents › Ansicht „Alle“ | `/incidents?status=all` | |
| `/alerts` Tab Maintenance (Fenster) | Incidents & Alerting › Wartung | `/maintenance` | |
| `/alerts` Tab Maintenance (Hosts in Wartung) | Wartung › Tab „Aktiv“ | `/maintenance?state=active` | |
| `/incidents/[id]` | Incidents › Detail | `/incidents/{id}` | neue Gliederung, siehe 6.2 |
| `/incidents/[id]` Ack/Resolve/Feedback | Incidents › Detail › Kopfaktionen | — | |
| `/incidents/[id]` Error Analysis, Precursor-Hinweise | Incidents › Detail › Analyse | `/incidents/{id}/analysis` | |
| `/incidents/[id]` Postmortem (KI) | Incidents › Detail › Postmortem | `/incidents/{id}/postmortem` | nur bei aktiver KI oder vorhandenem Text |
| `/rules` | Incidents & Alerting › Alert-Regeln | `/alerting/rules`, `/alerting/rules/{id}` | |
| Settings › Notifications › Incident Thresholds | Incidents & Alerting › Korrelation | `/alerting/correlation` | Regel-Liste [BE] |
| Settings › Monitoring › Predictive correlation | Korrelation › Abschnitt „Prädiktiv“ | `/alerting/correlation#predictive` | |
| Settings › Notifications › Enable, Grace Period | Benachrichtigungen › Richtlinie | `/alerting/channels` | |
| Settings › Notifications › 7 Kanäle | Benachrichtigungen › Kanäle | `/alerting/channels` | Admin-Rolle für Geheimnisse |
| Settings › Notifications › Notification History | Benachrichtigungen › Versandprotokoll | `/alerting/channels/log` | [BE] mehr als 50 Einträge |
| Settings › Notifications › Public URL | Administration › Allgemein | `/admin/general` | betrifft Links in allen Kanälen |
| Settings › Notifications › Weekly Digest Email | Analytics › Berichte › Zustellung | `/analytics/reports/delivery` | |
| `/syslog` | Observability › Logs › Explorer | `/logs` | Server-Suche [BE] |
| `/syslog` Live-Tail | Logs › Explorer › Modus „Live“ | `/logs?live=1` | |
| `/syslog/dashboard` | Logs › Übersicht | `/logs/overview` | |
| `/syslog/templates` | Logs › Muster | `/logs/patterns`, `/logs/patterns/{hash}` | Root Cause + Reverse Cause [heute] |
| — (`/api/v1/syslog/intelligence`, Smart-Feed) | Logs › Erkenntnisse | `/logs/insights` | [heute]: Daten vorhanden |
| `/agents` | Infrastruktur › Agents & Probes › Agents | `/agents` | |
| `/agents` Probe-Schalter | Agents & Probes › Tab „Probes“ | `/agents?role=probe` | [BE] `is_probe` |
| `/agents` „Add New Agent“ | Agents & Probes › Aktion „Agent installieren“ | `/agents/install` | auch im Erststart |
| `/agents/[id]` | Agents › Detail | `/agents/{id}` | Tabs Übersicht / Logs-Erfassung / Dienste / Wartung |
| `/scanner` | Infrastruktur › Discovery › Scans | `/discovery/scans` | |
| `/tasks` (Ports, Zertifikate, Verlauf, Scan-all) | Infrastruktur › Discovery › Inbox | `/discovery` | Badge bleibt |
| `/snmp` Host-Konfigurationen | Integrationen › SNMP › Geräte | `/integrations/snmp` | SNMP-Werte zusätzlich am Host (Metriken) |
| `/snmp` MIBs, Bibliothek, OID-Browser | Integrationen › SNMP › MIBs | `/integrations/snmp/mibs` | |
| `/ssl` | Infrastruktur › Zertifikate | `/certificates` | |
| `/credentials` | Administration › Organisation › Zugangsdaten | `/admin/credentials` | Auswahl bleibt in SNMP- und Integrationsformularen |
| `/topology` | Infrastruktur › Topologie | `/topology` | Listenansicht als Alternative |
| `/bandwidth` | Observability › Traffic | `/traffic` | |
| `/backups` (unverlinkt) | Infrastruktur › Backups | `/backups` | |
| `/digest` | Analytics › Berichte | `/analytics/reports` | |
| `/integration/store` | Integrationen › Katalog | `/integrations/catalog` | |
| `/integration/[type]` | Integrationen › Liste, gefiltert | `/integrations?type={type}` | Konfiguration im Drawer bzw. auf der Detailseite |
| `/integration/[type]/[id]` | Integrationen › Detail | `/integrations/{id}` | Tabs Übersicht / Objekte / Verlauf / Konfiguration |
| Sidebar-Einträge je Integrationstyp | Integrationen › Liste mit Health-Spalte | `/integrations` | |
| `/system/status` (inkl. Updates) | Administration › Plattform › Systemstatus | `/admin/system` | Tab „Updates“ |
| `/system/audit` | Administration › Plattform › Audit-Log | `/admin/audit` | |
| Settings › System | Administration › Plattform › Allgemein | `/admin/general` | |
| Settings › Monitoring (Ping, Retention, Syslog) | Administration › Plattform › Monitoring-Richtlinien | `/admin/monitoring` | |
| Settings › Appearance | Profil › Darstellung | `/profile/appearance` | [BE] serverseitig speichern |
| Settings › API (Doku, Keys) | Administration › Organisation › API | `/admin/api` | Doku verlinkt auf OpenAPI |
| Settings › AI | Administration › Organisation › KI | `/admin/ai` | |
| Settings › Authentication (LDAP) | Administration › Organisation › Authentifizierung | `/admin/auth` | |
| Settings › Backup | Administration › Plattform › Backup & Restore | `/admin/backup` | |
| `/users` | Administration › Organisation › Benutzer | `/admin/users` | |
| `/users` „Change my password“ | Profil › Sicherheit | `/profile/security` | für alle Rollen |
| `/login`, `/setup` (Backend-Template) | unverändert | `/login`, `/setup` | |

#### Dashboard-Widgets

| Widget heute | Neu | Ort |
|---|---|---|
| KPI-Kacheln Online / Offline / Not Observed / Total | Übersicht › Ebene 1 „Betriebszustand“ (Statusleiste inkl. Wartung und Unbekannt) | `/` |
| KPI Avg Latency | entfällt; ersetzt durch „Hosts über Latenzschwelle“ in Ebene 2 | `/` |
| KPI Incidents | Übersicht › Ebene 1 (offen/quittiert, kritisch) | `/` |
| KPI Syslog 24h | Logs › Übersicht; auf `/` nur bei Abweichung von der Baseline | `/logs/overview` |
| Gravity-Globus | entfällt; optional im Wallboard-Modus | `/?mode=wallboard` |
| Hosts (erste 20) | ersetzt durch „Objekte mit Problemen“ | `/` Ebene 2 |
| Recent Incidents | „Offene Incidents“ (Ebene 2) + „Kürzlich gelöst“ (Ebene 3) | `/` |
| Syslog Rate 24h | Logs › Übersicht; auf `/` Sparkline in Ebene 4 | `/logs/overview` |
| Live Feed (WARN+) | Logs › Explorer (Live); auf `/` entfällt | `/logs?live=1&severity=warning` |
| 30-Day Availability (Heatmap) | Analytics › Verfügbarkeit; auf `/` Kennzahl vs. Ziel | `/analytics/availability` |
| Uptime Ranking | Analytics › Verfügbarkeit | `/analytics/availability` |
| Highest Latency | Hosts-Ansicht „Höchste Latenz“ (gespeicherte Sortierung) | `/hosts?sort=-latency` |
| Alert Trends (14d) | Übersicht › Ebene 4 + Analytics › Alert-Qualität | `/` |
| Integrations | Übersicht › Ebene 1 „Datenquellen“ (nur Fehler) + `/integrations` | `/` |
| Anomalies / Warnings | Übersicht › Ebene 2 („Abweichungen“) + Logs › Erkenntnisse | `/` |
| Storage (+ Tage bis voll) | Übersicht › Ebene 4 „Bald kritisch“ + Analytics › Kapazität | `/` |
| Speedtest, UPS, Containers | Übersicht › Ebene 3 (Infrastrukturkontext, nur wenn eingerichtet) + Integrations-Detail | `/` |
| SSL Certificates | Übersicht › Ebene 4 „Bald kritisch“ (≤ 30 Tage) + `/certificates` | `/` |
| Header „Uptime“ (Laufzeit von Nodeglow) | Administration › Systemstatus | `/admin/system` |
| Erststart (FirstRunWelcome) | Übersicht bei leerem Inventar + Checkliste „Einrichtung“ | `/` |

#### Globale Funktionen

| Heute | Neu |
|---|---|
| Sidebar-Suche + Command Palette | **eine** globale Suche in der Kopfzeile (Cmd/Ctrl+K), Umfang siehe 5.2 |
| Tastaturkürzel `g …`, `?` | beibehalten, aus einer Navigations-Registry erzeugt; ergänzt um `g n` (Incidents), `g l` (Logs), `/` (Suche in Liste) |
| Glow-Schalter in der Sidebar | Kopfzeile, nur bei aktiver KI; Einstieg auch kontextuell („Incident erklären“) |
| Theme-Toggle, Logout in der Sidebar | Benutzermenü |
| Integrations-Health-Punkte in der Sidebar | Ebene 1 der Übersicht + Health-Spalte in `/integrations` |
| Sidebar-Badges (Hosts offline, Alerts, Tasks) | Badges an „Incidents“ (offen, ungequittiert) und „Discovery“ (Inbox); Host-Badge entfällt (wird über Incidents abgedeckt) |

#### Backend-Fähigkeiten, die eine UI bekommen

| Backend | Neuer Ort |
|---|---|
| `/api/v1/syslog/intelligence` | Logs › Erkenntnisse; Übersicht › Ebene 2/4 |
| `/syslog/api/smart-feed` | Logs › Explorer, Voreinstellung „Ohne Rauschen“ |
| `/syslog/api/reverse-cause/{hash}` | Logs › Muster › Detail („Was folgt typischerweise“) |
| `/api/v1/predictor/eval` | Analytics › Alert-Qualität; Korrelation (Precision je Regel) |
| `/api/dashboard-layout` | Übersicht › Ebene 3/4 anpassbar ([BE] pro Benutzer) |
| Modell `SyslogView` | Gespeicherte Ansichten (generisch, [BE]) |
| `health_score` je Host | Host-Kopf, Hostliste (optionale Spalte) |

---

## 2. Seitenhierarchie und URL-Schema

### 2.1 Hierarchie

```
/                                   Übersicht (Lagebild)
/incidents                          Liste (Standardansicht: offen + quittiert)
/incidents/{id}                     Detail: Übersicht
/incidents/{id}/analysis            Korrelierte Logs, Muster, Precursors
/incidents/{id}/postmortem          Postmortem
/maintenance                        Wartungsfenster (Tabs: Geplant | Aktiv | Verlauf)
/maintenance/{id}
/alerting/rules                     Alert-Regeln
/alerting/rules/{id}
/alerting/correlation               Eingebaute Korrelationsregeln, Schwellen, Precision
/alerting/channels                  Kanäle + Richtlinie
/alerting/channels/log              Versandprotokoll
/hosts                              Hostliste
/hosts/new                          Host anlegen (oder Drawer)
/hosts/{id}                         Übersicht
/hosts/{id}/checks | metrics | incidents | logs | timeline | topology | ports | settings
/topology                           Karte | Liste
/agents                             Agents | Probes
/agents/install
/agents/{id}                        Übersicht | Log-Erfassung | Dienste | Wartung
/integrations                       Instanzen (Health-Tabelle)
/integrations/catalog               Katalog
/integrations/snmp                  SNMP-Geräte
/integrations/snmp/mibs             MIB-Bibliothek, OID-Browser
/integrations/{id}                  Übersicht | Objekte | Verlauf | Konfiguration
/discovery                          Inbox (Ports, Zertifikate, neue Hosts)
/discovery/scans                    Manuelle und geplante Scans
/certificates
/backups
/sites, /sites/{id}                 [BE] Standort mit Hosts, Probes, Scans
/logs                               Explorer
/logs/overview | patterns | patterns/{hash} | insights
/traffic
/analytics/reports | reports/delivery | availability | capacity | alert-quality
/admin/general | monitoring | users | auth | credentials | api | ai | backup | system | audit
/admin/tenants, /admin/providers    [ee]
/profile, /profile/appearance, /profile/security, /profile/notifications [BE]
/portal                             [ee] MSP-Übersicht über Tenants
/login, /setup
```

### 2.2 URL-Regeln

| Regel | Beispiel |
|---|---|
| Slugs englisch, klein, Plural für Sammlungen | `/hosts`, `/incidents` |
| Objekt-IDs numerisch, global eindeutig (wie heute) | `/hosts/42` |
| Haupt-Tabs einer Detailseite als Pfadsegment | `/hosts/42/logs` |
| Filter, Sortierung, Suche als Query-Parameter | `/hosts?status=down,unknown&site=3&q=sw-&sort=-latency` |
| Zeitbereich einheitlich | `?range=24h` (relativ) oder `?from=2026-10-10T12:00Z&to=…` (absolut) |
| Gespeicherte Ansicht | `?view=17` (Parameter überschreiben die Ansicht, Hinweis „geändert“) |
| Vorschau-Drawer | `?peek=host:42`, `?peek=incident:1071`, `?peek=log:<id>` |
| Live-Modus | `?live=1` |
| Anker für Abschnitte | `/alerting/correlation#predictive` |
| Alte URLs leiten dauerhaft um | siehe 2.4 |

### 2.3 Tenant und Standort in URLs

- Die Spec verwirft Tenant-Pfade (`/t/{slug}`). Die Auswahl erfolgt über den Header `X-Nodeglow-Tenant` aus dem Tab-Zustand und die Session.
- Vorschlag für Deep Links: optionaler Parameter `?tenant={slug}`. Das Frontend übernimmt ihn in den Tab-Zustand und entfernt ihn aus der Adresszeile. Ohne Mitgliedschaft antwortet das Backend mit 404 (wie in der Spec). In Benachrichtigungen erzeugte Links enthalten den Parameter immer. **Entscheid des Owners nötig** (Abschnitt 9).
- Der Standort ist ein normaler Filter (`?site=3`), weil er kein Isolationsrand ist.

### 2.4 Weiterleitungen

| Alt | Neu |
|---|---|
| `/alerts`, `/alerts?tab=alerts` | `/incidents` |
| `/alerts?tab=incidents` | `/incidents?status=all` |
| `/alerts?tab=maintenance` | `/maintenance` |
| `/rules` | `/alerting/rules` |
| `/syslog` | `/logs` |
| `/syslog/dashboard` | `/logs/overview` |
| `/syslog/templates` | `/logs/patterns` |
| `/scanner` | `/discovery/scans` |
| `/tasks` | `/discovery` |
| `/snmp` | `/integrations/snmp` |
| `/ssl` | `/certificates` |
| `/credentials` | `/admin/credentials` |
| `/bandwidth` | `/traffic` |
| `/digest` | `/analytics/reports` |
| `/integration/store` | `/integrations/catalog` |
| `/integration/{type}` | `/integrations?type={type}` |
| `/integration/{type}/{id}` | `/integrations/{id}` |
| `/system/status` | `/admin/system` |
| `/system/audit` | `/admin/audit` |
| `/settings?tab=…` | passende `/admin/*`, `/alerting/*` oder `/profile/*` |
| `/users` | `/admin/users` |

Benachrichtigungen, die Backend-seitig Links erzeugen (z. B. `services/rules.py` mit Link `/rules`, Incident-Links), werden auf die neuen Pfade umgestellt; die Weiterleitungen fangen alte Mails ab.

---

## 3. Objektmodell

### 3.1 Objekte, wie Nodeglow sie heute implementiert

| Objekt | Heute im Code | Identität | Zustand / Lebenszyklus | Wichtige Beziehungen | Lücken |
|---|---|---|---|---|---|
| **Host** | `PingHost` (`backend/models/ping.py:13`) | `id`; Name, Hostname, IP, MAC | `enabled`, `maintenance(_until)`, letztes Ergebnis (ClickHouse `ping_checks`), `port_error`, `check_detail`, `check_errors` | `parent_id` (Topologie), `probe_id` (prüfende Probe), `source` (manuell, Agent, Proxmox, phpIPAM, …) | Keine Tags, Gruppen, Standort, Kritikalität, Besitzer; Status ohne Frische |
| **Check** | Teil des Hosts: `check_type` (ICMP, TCP, HTTP/S, DNS, kombinierbar), `port`, `http_options`, `latency_threshold_ms` | kein eigenes Objekt | Ergebnis je Check im letzten Zyklus (`check_detail`) | gehört zu genau einem Host | Kein eigener Verlauf je Check, keine eigenen Schwellen ausser Latenz |
| **Dienst (Service)** | Vom Agenten überwachter Windows-/Linux-Dienst (`Agent.watched_services`, `service_states`) | Name am Agenten | läuft / gestoppt → Incident `agent_service` | Agent → Host | **Begriff doppelt belegt:** „Business Service“ (Anwendung über mehrere Hosts) gibt es nicht. Empfehlung: in der UI „überwachter Dienst“ verwenden; „Service“ für ein späteres Service-Modell reservieren |
| **Agent** | `Agent` (`backend/models/agent.py:13`) | `id`, Token | online, wenn `last_seen` < 120 s; Version, Plattform | liefert Metriken für einen Host (Hostname-Zuordnung), Logs (Eventlog, Dateien) | Agent und Host werden separat gepflegt |
| **Probe** | Agent mit `is_probe = true`, Intervall `probe_interval_seconds` | wie Agent | Frische je Probe (`services/probes.py`), Hosts ohne frische Probe = unbekannt | 1 Probe : n Hosts über `PingHost.probe_id` | Kein Setzen über die API, keine Liste; „unknown“ nur in der Topologie |
| **Integration** | `IntegrationConfig` + `Snapshot` (`backend/models/integration.py`) | `id`, Typ, Name | `enabled`, letzter Snapshot `ok` / Fehler | Snapshots je Entität (VM, Pool, Container, AP); kann Hosts erzeugen (`source`) und Topologie liefern | Keine Wartung für Integrationen; Health-Score nur intern |
| **Integrations-Objekt** | Snapshot-Entität (`entity_type`, `entity_id`) | Typ + ID | aus dem Snapshot | gehört zu einer Integration, teils zu einem Host | Nicht als eigenständiges, verlinkbares Objekt modelliert |
| **Standort (Site)** | nicht vorhanden; Spec: `sites` | — | — | gruppiert Hosts, Probes, Scans | ganz [BE] |
| **Tenant** | nicht vorhanden; Spec: `tenants` | — | active / suspended / deleting | Isolationsrand | ganz [BE]; Mehrfach-Tenants [ee] |
| **Alert** | **kein Objekt.** Host down/up (`backend/scheduler.py:682-709`) und Regeltreffer auf Ping-/Integrationsquellen (`services/rules.py:330-360`) lösen nur Benachrichtigungen aus; Syslog-Regeln öffnen ein Incident | — | implizit (Host gerade offline, Regel gerade erfüllt) | — | Kein „gerade feuernd“-Zustand, keine Historie ausser `NotificationLog` |
| **Event** | `IncidentEvent` (nur innerhalb eines Incidents, Typen u. a. `created`, `host_down`, `host_up`, `syslog_error`, `integration_error`, `acknowledged`, `resolved`, `feedback`, `predicted_incident`, `service_down`, `disk_warning`); Host-Timeline (zur Laufzeit zusammengesetzt aus Statuswechseln, Incidents, Syslog, Audit) | `id` je IncidentEvent | unveränderlich | Incident; Host (nur im Text) | Kein globaler Event-Stream, keine strukturierte Objekt-Referenz |
| **Incident** | `Incident` (`backend/models/incident.py:10`) | `id`; Dedup über `(rule, host_ids_hash)` | `open` → `acknowledged` → `resolved` (manuell oder automatisch); Severity critical / warning / info | Events; Hosts nur als Hash; Regelname als String | Keine betroffenen Hosts als Liste, kein Zuständiger, keine Kommentare, kein Reopen |
| **Alert-Regel** | `AlertRule` (`backend/models/alert_rule.py:9`) | `id` | aktiv / inaktiv, `last_triggered_at`, Cooldown, `required_consecutive` | Quelle (Integration, Ping, Syslog), Kanäle | Treffer nicht nachvollziehbar (siehe Alert) |
| **Korrelationsregel** | im Code (`services/correlation.py`: `host_down_syslog`, `upstream_failure`, `multi_host_down`, `integration_host`, `port_error`, `syslog_spike`, `log_anomaly`, `fleet_wide_issue`, `severity_trend`, `content_anomaly`, `learned_precursor_*`) | Name | global über Schwellen gesteuert | erzeugt Incidents | Keine API, keine Erklärung, nicht einzeln abschaltbar |
| **Wartungsfenster** | `MaintenanceWindow` (`backend/models/maintenance.py:15`), Ad-hoc-Wartung am Host | `id` | wöchentlich / einmalig, aktiv / geplant | alle Hosts oder `host_ids` | Nur Hosts, keine Integrationen, Standorte, Regeln |
| **Log-Nachricht** | ClickHouse `syslog_messages` | (Zeitstempel, Host) | TTL nach Severity | `host_id`, `template_hash` | Keine stabile ID für Deep Links |
| **Log-Muster** | `LogTemplate` | `hash` | Noise-Score, Trend, Tags | Precursors, Baselines | — |
| **Baseline / Precursor / Fleet-Pattern** | `HostBaseline`, `PrecursorPattern`, `FleetPattern` | — | gelernt, mit Konfidenz | Host, Muster, Event-Typ | Reifegrad nicht abfragbar |
| **Prognose** | Disk-Full-Regression (`services/predictions.py`) | — | berechnet je Pool | Integration (Storage) | Nur Speicher |
| **Zertifikat** | `PingHost.ssl_expiry_days`, `DiscoveredPort.ssl_*` | Host bzw. Port | Tage bis Ablauf | Host | Kein eigenes Objekt (Aussteller, Kette nur im Detail-Endpoint) |
| **Backup-Job** | `BackupJob`, `BackupHistory` | `id` | ok / failed / running / warning / unknown, erwartete Frequenz | Quelle (Proxmox, UNAS) | — |
| **Benachrichtigungskanal** | Settings-Keys (`notification_channels.py:30`) | Typ | aktiv, Mindest-Severity | — | Kein Objekt, kein Routing |
| **Benutzer, API-Key, Audit-Eintrag, Zugangsdaten** | `User`, `ApiKey`, `AuditLog`, `Credential` | `id` | Rolle admin / editor / readonly | — | Keine Präferenzen, keine Teams |

### 3.2 Zielbegriffe: Alert, Event, Incident

Diese Definitionen gelten in der UI ab sofort. Wo das Backend sie noch nicht stützt, wird der Begriff in der UI nicht verwendet.

| Begriff | Definition (Ziel) | Heute | Verwendung in der UI bis zum Backend-Ausbau |
|---|---|---|---|
| **Event** | Unveränderliche Tatsache mit Zeitpunkt, Quelle und Objektbezug: Statuswechsel, Check-Fehlschlag, Log-Burst, Konfigurationsänderung, Benutzeraktion | Nur als `IncidentEvent` und in der Host-Timeline | „Verlauf“ (Host), „Timeline“ (Incident) |
| **Alert** | Ein Zustand „Bedingung X ist für Objekt Y erfüllt“, mit Beginn, Ende und Severity; entsteht aus Events, kann Benachrichtigungen auslösen und wird einem Incident zugeordnet | Nicht persistiert | **Nicht als Listenbegriff verwenden.** „Benachrichtigung“ für das Versandprotokoll; „Alert-Regel“ für die Konfiguration |
| **Incident** | Arbeitseinheit, die einen oder mehrere zusammengehörige Alerts bündelt, mit Lebenszyklus (offen → quittiert → gelöst), Zuständigem und Ergebnis | Vorhanden, ohne Alert-Bezug und ohne Zuständigen | Hauptobjekt des Bereichs „Incidents & Alerting“ |

Folge für die Navigation: Der Menüpunkt „Alerts“ entfällt. „Incidents“ ist die Arbeitsliste, und „Alert-Regeln“ ist die Konfiguration. Ein späteres Alert-Objekt erscheint als Tab „Alerts“ im Incident-Detail und als Filter in der Incident-Liste, nicht als eigener Menüpunkt.

### 3.3 Beziehungsdiagramm (Ziel, [BE] markiert neue Kanten)

```
Tenant [ee/BE] ─┬─ Standort [BE] ─┬─ Probe (Agent, is_probe) ── prüft ──┐
                │                  └─ Scan-Zeitplan [BE]                │
                ├─ Integration ── Snapshot ── Integrations-Objekt ──────┤
                │        └── erzeugt / ergänzt ──────────────────────────┤
                └─ Host ◄────────────────────────────────────────────────┘
                     ├─ parent_id ─► Host            (Topologie)
                     ├─ Checks, Zertifikat, Ports
                     ├─ Agent ── überwachter Dienst
                     ├─ Log-Nachrichten (host_id) ── Log-Muster ── Precursor / Baseline
                     ├─ Wartungsfenster
                     └─ Incident [BE: host_ids] ── Events ── (Alert [BE])
                                └─ ausgelöst von Alert-Regel | Korrelationsregel
```

---

## 4. Hauptworkflows

Notation: Schritt → Bildschirm. „Klicks“ zählt Navigationsaktionen ab Startpunkt.

### W1 — Alert → Incident → betroffene Hosts → Logs → Lösung

| | Heute | Neu |
|---|---|---|
| Einstieg | Benachrichtigung (Mail/Chat) mit Link oder Sidebar-Badge „Alerts“ | Benachrichtigung mit Deep Link `/incidents/{id}?tenant=…` oder Badge „Incidents“ |
| 1 | `/alerts` → Karte klicken → `/incidents/{id}` | `/incidents/{id}` direkt |
| 2 | Betroffene Hosts: nur Anzahl in „Error Analysis“; Hostnamen aus dem Eventtext ablesen, Hostliste suchen | Panel „Betroffene Objekte“: Hosts mit aktuellem Status und Beziehungslabel (bestätigt über Topologie / regelbasiert / vermutet) |
| 3 | Host öffnen → Tab Syslog (Zeitfenster manuell) | Klick auf Host öffnet Drawer (`?peek=host:42`) mit Status, letzten Events und Logs im Incident-Zeitfenster; „Alle Logs“ springt zu `/logs?host=42&from=…&to=…` |
| 4 | Topologie getrennt aufrufen und Host suchen | Panel „Topologie-Ausschnitt“: Parent, Geschwister, betroffene Kinder; Upstream-Ursache hervorgehoben (Regel `upstream_failure`) |
| 5 | zurück zum Incident → Acknowledge → später Resolve | Kopfaktionen „Übernehmen“ (Ack + Zuständig [BE]), „Notiz“ [BE], „Lösen“; Wartung starten aus dem Drawer |
| 6 | Feedback „Echt/Rauschen“ | unverändert, als Abschluss des Lösens |
| Klicks bis zu den Logs des betroffenen Hosts | ca. 6–8 inkl. Suchen | 2 |
| Backend-Bedarf | — | `host_ids` am Incident (B-05), hostgefilterte Logs im Incident (B-06), Zuständigkeit/Notizen (B-11) |

### W2 — Morgencheck (Admin/NOC, 5 Minuten)

| Schritt | Bildschirm | Daten | Status |
|---|---|---|---|
| 1 | Übersicht, Ebene 1: Betriebszustand, Datenquellen gesund? Probes frisch? | Summary, Integrations-Health, Probe-Frische | [BE] probe-bewusster Status (B-01) |
| 2 | Übersicht, Ebene 2: „Seit Ihrem letzten Besuch (09:12)“: neue/gelöste Incidents, neue Discovery-Funde, Agent-Updates | Changes-Endpoint | [BE] B-08 |
| 3 | Offene Incidents nach Severity und Dauer; ohne Zuständigen zuerst | Incident-Liste | [heute], Zuständigkeit [BE] |
| 4 | „Bald kritisch“: Zertifikate ≤ 30 Tage, Speicher-Prognose, steigende Log-Trends | SSL, Predictions, Intelligence | [heute] |
| 5 | Wartung heute | Wartungsfenster | [heute] |
| 6 | Discovery-Inbox abarbeiten (Badge) | `/discovery` | [heute] |

Heute verteilt sich das auf `/`, `/alerts`, `/ssl`, `/tasks` und die Sidebar-Integrationen (5 Seiten, ohne Delta). Neu genügen eine Seite und bei Bedarf ein Sprung.

### W3 — MSP: Welcher Kunde braucht Aufmerksamkeit? [ee][Zukunft]

1. `/portal`: Tabelle aller Tenants mit Spalten *offene Incidents (kritisch / gesamt)*, *älteste unquittierte*, *unbekannte Hosts / stale Probes*, *Integrationsfehler*, *Wartung aktiv*, *Verfügbarkeit 30 d vs. Ziel*. Sortiert nach „Aufmerksamkeit“ (kritisch unquittiert > stale > Rest).
2. Klick auf Tenant → setzt den Tenant-Kontext im Tab → `/` des Tenants (Lagebild).
3. Alternativ: übergreifendes Incident-Board `/portal/incidents` mit Tenant-Spalte; Klick öffnet `/incidents/{id}?tenant=…`.

Backend: Provider-Rollen, übergreifende Aggregation (B-16). Im Core wird die Kopfzeile nur für den Tenant-Switcher vorbereitet, aber nicht angezeigt.

### W4 — Standort mit einer Probe anbinden

| Schritt | Heute | Neu |
|---|---|---|
| 1 | `/agents` → „Add New Agent“ → Befehl auf dem Rechner am Standort ausführen | `/sites` → „Standort anlegen“ [BE] → Assistent |
| 2 | Probe-Schalter auf `/agents` (wirkungslos, F-07) | Assistent Schritt 2: Agent installieren (Befehl mit Standort-Token [BE]); Live-Warten auf ersten Heartbeat |
| 3 | Jeden Host einzeln bearbeiten und Probe wählen | Schritt 3: Netze scannen über die Probe (Discovery-Scan, [Zukunft] laut Probe-Spec) oder Hosts auswählen → Bulk „Probe zuweisen“ [BE] |
| 4 | Keine Rückmeldung, ob die Probe prüft | Schritt 4: Bestätigung „Probe prüft 9 Hosts, letzter Heartbeat vor 20 s“; Frische-Schwelle festlegen |
| Ergebnis | — | Standortseite mit Hosts, Probe-Status, Incidents; Ausfall der Probe macht die Hosts „unbekannt“ |

Bis Standorte existieren: Variante ohne Standort unter `/agents?role=probe` mit denselben Schritten 2–4.

### W5 — Integration hinzufügen

1. `/integrations` → „Integration hinzufügen“ → `/integrations/catalog` (Suche, Kategorien: Virtualisierung, Netzwerk, Speicher, DNS, Hardware, Dienste).
2. Typ wählen → Drawer mit Formular (Felder aus `/api/integration/{type}/fields`), Zugangsdaten aus `/admin/credentials` wählbar oder inline anlegen.
3. „Verbindung testen“ [BE: Test-Endpoint je Typ, heute nur nach dem Speichern sichtbar] → Speichern.
4. Weiter zu `/integrations/{id}`: erster Snapshot, gefundene Objekte, „Hosts übernehmen“ (bei Proxmox/UniFi), Hinweis auf erzeugte Topologie-Kanten.
5. Fehler im Betrieb erscheinen auf der Übersicht (Ebene 1 „Datenquellen“) und in der Health-Spalte.

Heute: Sidebar → aufklappen → „+ Add Integration“ → Store → Typseite → Modal → keine Folgeführung.

### W6 — Wartung planen

1. Aus dem Kontext: Host-Kopf, Bulk-Auswahl in der Hostliste oder Incident-Drawer → „Wartung …“ → Drawer (einmalig / wöchentlich, Dauer, Zeitzone, Objekte).
2. Oder `/maintenance` → „Neues Fenster“.
3. Während der Wartung: Hosts mit Wartungsstatus (eigene Farbe und Form), Incidents unterdrückt; Übersicht Ebene 3 „Wartung aktiv“.

### W7 — Neues Gerät über Discovery übernehmen

1. Badge „Discovery“ → `/discovery` (Inbox: neue Hosts aus Scans [BE: Scan-Funde persistieren], neue Ports, neue Zertifikate).
2. Mehrfachauswahl → „Überwachen“ (mit Check-Vorschlag) oder „Ignorieren“.
3. Übernommene Hosts erscheinen in `/hosts` mit Quelle „Scan“.

### W8 — Rauschen reduzieren

1. `/analytics/alert-quality`: Regeln mit hoher Noise-Rate (`/api/v1/predictor/eval`), Incidents pro Regel, Feedback-Anteil.
2. Klick auf Regel → `/alerting/rules/{id}` oder `/alerting/correlation#{regel}`: Schwelle, Mindestzyklen, Testlauf.
3. Log-Muster mit hohem Noise-Score → `/logs/patterns/{hash}` → Tag „Rauschen“.

---

## 5. Querverlinkung zwischen Objekten

### 5.1 Beziehungsmatrix

Zeile = Ausgangsobjekt, Spalte = verlinktes Ziel. Inhalt = Darstellung auf der Ausgangsseite.

| Von ↓ / Nach → | Host | Incident | Logs | Topologie | Agent / Probe | Integration | Standort | Wartung |
|---|---|---|---|---|---|---|---|---|
| **Host** | Parent / Kinder (Tab Topologie) | Tab Incidents (offen zuerst) [BE: `host_id`-Filter] | Tab Logs (`host_id`) [heute] | Tab Topologie, „In Karte zeigen“ | Agent-Link, prüfende Probe mit Frische | Quelle mit Link zur Instanz | Kopf [BE] | Kopfaktion, aktives Fenster |
| **Incident** | Panel „Betroffene Objekte“ [BE: `host_ids`] | „Ähnliche Incidents“ (gleiche Regel) [heute] | Panel „Korrelierte Logs“ (hostgefiltert [BE]) | Ausschnitt mit Pfad [heute über `parent_id`] | Probe, wenn Ursache „stale“ | wenn Regel `integration_host` | Kopf [BE] | „Wartung starten“ |
| **Log-Zeile / -Muster** | Hostname → `/hosts/{host_id}` [heute, statt `?q=`] | „Incidents mit diesem Muster“ [BE] | Muster-Detail | — | Agent bei Agent-Logs | — | [BE] | — |
| **Topologie-Knoten** | Klick → Drawer, dann Detail | Incident-Overlay [heute: Status; BE: Incident-ID je Knoten] | — | — | Probe-Gruppierung | Herkunft (Proxmox/UniFi) | Filter [BE] | Wartungsmarkierung |
| **Agent / Probe** | zugeordneter Host bzw. geprüfte Hosts | Incidents `agent_service`, `self_check` | Agent-Logs | — | — | — | [BE] | — |
| **Integration** | erzeugte Hosts, Objekte mit Host-Bezug | Incidents mit Bezug [BE] | — | gelieferte Kanten | — | — | [BE] | [BE] Wartung für Integrationen |
| **Standort [BE]** | Hostliste gefiltert | Incident-Liste gefiltert | Logs gefiltert | Karte gefiltert | Probes des Standorts | — | — | Fenster des Standorts |
| **Wartungsfenster** | betroffene Hosts | während des Fensters unterdrückte Incidents [BE] | — | — | — | — | — | — |

Regel: Jede Objektnennung (Name, ID, Hostname) ist ein Link. Bei einfachem Klick öffnet sich der Drawer, mit Ctrl/Cmd-Klick oder Mittelklick die volle Seite in einem neuen Tab.

### 5.2 Globale Suche und Command Palette

| Umfang | Suchfelder | Treffer-Aktion | Status |
|---|---|---|---|
| Hosts | Name, Hostname, IP, MAC, Tag | Drawer / Detail | [heute] Name/Hostname (max. 10); IP/MAC/Tags [BE] |
| Incidents | `#1071`, Titel, Regel | Detail | [BE] |
| Agents / Probes | Name, Hostname | Detail | [BE] |
| Integrationen | Name, Typ | Detail | [heute] |
| Standorte, Tenants | Name | Kontext setzen | [BE] / [ee] |
| Log-Muster | Template-Text | Muster-Detail | [BE] |
| Gespeicherte Ansichten | Name | Ansicht öffnen | [BE] |
| Seiten und Einstellungen | Seitentitel, Einstellungsname (z. B. „Grace Period“ → `/alerting/channels#grace`) | Navigieren | [heute] aus der Navigations-Registry |
| Aktionen | „Host anlegen“, „Wartung starten“, „Agent installieren“, „Theme wechseln“ | Ausführen (rollengeprüft) | [heute] |

Syntax: freier Text durchsucht alles; Präfixe `host:`, `inc:` bzw. `#`, `log:` und `>` (nur Aktionen) schränken ein. Die Suche respektiert Tenant-Kontext und Rolle. Ein gemeinsamer Endpoint `/api/v1/search` liefert typisierte Treffer (B-09). Bis dahin fragt das Frontend die vorhandenen Quellen parallel ab.

### 5.3 Breadcrumbs

- Auf allen Seiten unterhalb der Bereichsebene: `Bereich › Seite › Objekt › Tab`, z. B. `Infrastruktur › Hosts › SW-ZH-CORE-02 › Logs`.
- Der Bereich ist kein Link, wenn er keine eigene Seite hat; die Seite verlinkt auf die Liste **mit den zuletzt verwendeten Filtern** (aus der URL-Historie).
- Kein Home-Icon ohne Text. `<nav aria-label="Brotkrumen">`, letztes Element mit `aria-current="page"`.
- Der Objektname erscheint einmal im Seitentitel; die Breadcrumb kürzt lange Namen.

### 5.4 Kontextuelle Seitenpanels (Drawer)

| Drawer | Öffnet aus | Inhalt | Aktionen |
|---|---|---|---|
| Host-Vorschau | Incident, Topologie, Logs, Suche, Übersicht | Status mit Grund und Frische, Checks, letzte 5 Events, offene Incidents, Mini-Latenzverlauf | Detail öffnen, Wartung, Check jetzt |
| Incident-Vorschau | Host, Übersicht, Topologie | Status, Dauer, Severity, betroffene Objekte, letzte Events | Übernehmen, Lösen, Detail |
| Log-Detail | Log-Explorer | Rohnachricht, Felder, Muster, Häufigkeit, Baseline-Abweichung | „Nur dieses Muster“, „Kontext ±5 min“, „Als Rauschen markieren“ |
| Formular-Drawer | Listen | Anlegen/Bearbeiten (Host, Regel, Wartung, Integration) | Speichern, Testen |

Regeln: Drawer rechts, maximal 480–640 px, URL-Parameter `peek`, Escape schliesst, Fokus kehrt zum Auslöser zurück. Es liegt nie ein Drawer über einem Drawer; ein zweiter Klick ersetzt den Inhalt mit Zurück-Pfeil.

---

## 6. Wiederverwendbare UX-Patterns

### 6.1 Listen und Tabellen

| Element | Regel |
|---|---|
| Kopf | Titel, Zähler „1–50 von 1 284“, Primäraktion rechts |
| Filterleiste | Suche (Debounce 250 ms), Facetten als Chips mit Zählern (Status, Standort, Quelle, Severity …), „Filter zurücksetzen“; alle Werte in der URL |
| Gespeicherte Ansichten | Menü links neben den Filtern: Systemansichten (z. B. „Probleme“, „Unbekannt“), persönliche und geteilte Ansichten; „Als Ansicht speichern“, „Anpinnen“ (erscheint in der Navigation) [BE] |
| Tabelle | Server-Modus (Paginierung oder Cursor, Sortierung) [BE]; Sticky Header; Spaltenwahl und -reihenfolge pro Benutzer; Statusspalte zuerst; numerische Spalten rechtsbündig mit `tabular-nums` |
| Zeilen | Ganze Zeile ist Link (Drawer), Checkbox für Auswahl; Hover-Aktionen nur als Zusatz, nie als einziger Weg |
| Bulk | Aktionsleiste erscheint bei Auswahl: „3 ausgewählt · Wartung · Probe zuweisen · Tags · Löschen“; Ergebnis mit Erfolg/Fehler je Objekt |
| Dichte | Komfort (36 px) und Kompakt (28 px) pro Benutzer |
| Export | Exportiert die **gefilterte Gesamtmenge** serverseitig (CSV/JSON), nicht nur die sichtbare Seite [BE] |
| Leer/Fehler | nach 6.5 |

### 6.2 Detailseite

```
Breadcrumb
[Status-Badge] Objektname                         [Primäraktion] [Sekundär ▾]
Grund des Zustands · Quelle · Standort · zuletzt beobachtet vor 20 s
───────────────────────────────────────────────────────────────────────────
Tabs: Übersicht | … | Einstellungen          (Pfadsegment, ARIA-Tabs)
───────────────────────────────────────────────────────────────────────────
Hauptspalte (Inhalt des Tabs)        | Seitenspalte: Beziehungen
                                     | (Incidents, Topologie, Agent, Integration, Wartung)
```

- Der Kopf beantwortet immer drei Fragen: Was ist der Zustand? Warum? Wie aktuell ist das?
- Für Incidents gilt dieselbe Struktur. Kopf: Severity, Status, Dauer, Zuständiger, Regel. Übersicht: Zusammenfassung, betroffene Objekte, Timeline. Analyse: Logs, Muster, Precursors, Beziehungslabels. Postmortem.

### 6.3 Side Drawer

Siehe 5.4. Ein Drawer dient dem Nachsehen und schnellen Handeln. Wer länger mit einem Objekt arbeitet, wechselt auf die Detailseite.

### 6.4 Zeitbereich

| Element | Regel |
|---|---|
| Steuerung | Eine `TimeRangePicker`-Komponente oben rechts auf Seiten mit Zeitbezug (Host-Metriken, Logs, Incident-Liste, Traffic, Analytics) |
| Voreinstellungen | 15 min, 1 h, 6 h, 24 h, 7 d, 30 d; absolut per Kalender; „Live“ als eigener Schalter |
| Grenzen | Die UI kennt die Backend-Grenzen (z. B. Logs ≤ 720 h, Ping-Verlauf 30 d Aufbewahrung) und deaktiviert grössere Bereiche mit Begründung |
| Zeitzone | Aus dem Profil, sonst Tenant-Zeitzone; immer angezeigt („Europe/Zurich“) |
| Übernahme | Beim Sprung Incident → Logs → Host bleibt der Zeitbereich erhalten (URL) |
| Zoom | Auswahl im Diagramm setzt den Zeitbereich |

### 6.5 Zustandskommunikation

**Grundregel: Fehlende, veraltete oder unbeobachtete Daten werden nie als gesund dargestellt.** Grün ist positiv bestätigten, frischen Messungen vorbehalten.

| Zustand | Wann | Darstellung | Beispieltext | Aktion |
|---|---|---|---|---|
| Laden (erstmals) | Erste Abfrage läuft | Skeleton in Form des Inhalts, keine Nullen | — | — |
| Aktualisieren | Hintergrund-Refetch | dezenter Indikator im Kopf, Inhalt bleibt | „Aktualisiert …“ | — |
| Leer, nicht eingerichtet | Funktion ohne Quelle (z. B. kein Syslog empfangen) | neutrale Illustration, Erklärung | „Noch keine Logs empfangen. Geräte senden an Port 514.“ | „Einrichten“ |
| Leer, keine Treffer | Filter liefert nichts | neutral | „Keine Hosts entsprechen den Filtern.“ | „Filter zurücksetzen“ |
| Leer, bestätigt gut | Positiv geprüft, z. B. 0 offene Incidents bei frischen Daten | grün, mit Zeitstempel | „Keine offenen Incidents · Stand 14:32:05“ | — |
| Analyse nicht bereit | Baselines lernen noch, Korrelation aus | Info (blau-grau) | „Anomalie-Erkennung lernt noch (Baseline 3 von 7 Tagen).“ | „Mehr erfahren“ |
| Keine Daten von der Quelle | Wert `null` oder Quelle meldet nichts | „—“ in Grau, Tooltip mit Quelle | „Proxmox liefert für diese VM keinen Disk-Wert.“ | — |
| Unbekannt / unbeobachtet | Keine frische Messung, Probe stale | Grau, schraffiert, eigenes Symbol | „Unbekannt: Probe probe-bern-01 seit 11 min still (Schwelle 3 min).“ | „Probe ansehen“ |
| Veraltet (stale) | Letzter erfolgreicher Abruf älter als Schwelle | Wert gedimmt, Uhr-Symbol, Alter | „Stand vor 6 min. Aktualisierung fehlgeschlagen.“ | „Erneut versuchen“ |
| Fehler | Abfrage fehlgeschlagen, keine Daten im Cache | Fehlerpanel (`QueryErrorState`) | „Hostliste konnte nicht geladen werden (Server 502).“ | „Erneut versuchen“ |
| Teilausfall | Ein Teil eines Aggregats fehlt (z. B. Syslog-Statistik) | betroffene Kachel mit Fehler, Rest normal | „Syslog-Statistik nicht verfügbar.“ | „Erneut versuchen“ |
| Verbindung getrennt | WebSocket/SSE getrennt | Banner in der Kopfzeile, Zeitstempel auf allen Live-Flächen | „Live-Verbindung getrennt seit 14:31. Daten werden alle 2 min abgefragt.“ | „Neu verbinden“ |
| Keine Berechtigung | 403 bzw. Rolle zu niedrig | Schloss-Symbol, Grund | „Nur Administratoren können Kanäle bearbeiten.“ | „Administrator kontaktieren“ |
| Wartung | Objekt in Wartung | eigene Farbe und Form (nicht Amber), Fensterende | „Wartung bis 15:00 (NAS-Firmware-Update)“ | „Wartung beenden“ |
| Deaktiviert | Objekt bewusst aus | Grau, durchgestrichenes Symbol | „Überwachung deaktiviert“ | „Aktivieren“ |

Statusvokabular für Hosts (einheitlich in Liste, Detail, Topologie, Übersicht):
`OK` · `Beeinträchtigt` (Teil-Check fehlgeschlagen, Latenz über Schwelle) · `Ausgefallen` · `Unbekannt` · `Wartung` · `Deaktiviert`. Jeder Zustand hat Farbe **und** Form **und** Text.

Beziehungslabels (aus `docs/design/prototypes/SCENARIO.md`): `Bestätigt` (Topologie, gleiche Probe, gleiche Integration) · `Regelbasiert` (Korrelationsregel hat gefeuert) · `Vermutet` (nur zeitliche Überlappung oder Baseline-Abweichung) · `Noch nicht verfügbar`. Jede angezeigte Beziehung trägt eines dieser Labels.

### 6.6 Kennzahlen

- Jede Kennzahl hat **eine** Definition im Glossar (z. B. „Offene Incidents = Status offen oder quittiert“) und kommt von **einem** Endpoint (F-06).
- Jede Kennzahl ist ein Link auf die gefilterte Liste, die sie ergibt.
- Gesamtzahlen schliessen Wartung und Unbekannt ein und schlüsseln sie auf („48 Hosts: 29 OK · 5 beeinträchtigt · 2 ausgefallen · 9 unbekannt · 1 Wartung · 2 Warnung“).

---

## 7. Dashboard: Informationshierarchie

### 7.1 Ebenen

| Ebene | Zweck | Lesezeit | Inhalt | Anpassbar |
|---|---|---|---|---|
| **E1 Betriebszustand** | Ist alles in Ordnung, und kann ich den Daten trauen? | < 2 s | Gesamtstatus-Satz („2 kritische Incidents, 9 Hosts unbekannt“), Statusleiste der Hosts, Datenquellen-Gesundheit (Integrationen, Probes, Syslog-Empfang), Live-Verbindung | Nein |
| **E2 Aufmerksamkeit & Aktion** | Was muss ich jetzt tun? | < 10 s | Offene Incidents (Severity, Dauer, betroffene Objekte, zuständig), Objekte mit Problemen inkl. Unbekannt, Abweichungen (Baseline, Bursts), „Seit Ihrem letzten Besuch“ | Nein (Reihenfolge fix) |
| **E3 Infrastrukturkontext** | Was ist geplant oder auffällig im Umfeld? | bei Bedarf | Aktive und heutige Wartung, kürzlich gelöste Incidents, Standorte/Probes, Infrastruktur-Kacheln aus Integrationen (USV auf Batterie, Container-Updates, Internet/Speedtest) | Ja |
| **E4 Trends & Intelligence** | Was wird bald zum Problem, und wie entwickelt sich die Zuverlässigkeit? | bei Bedarf | Bald kritisch (Zertifikate, Speicherprognose, steigende Log-Muster, Precursor-Warnungen), Verfügbarkeit vs. Ziel, Incident-Trend, Alert-Qualität | Ja |

Skizze (Desktop, 1440 px):

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ E1  ● 2 kritische Incidents · 9 Hosts unbekannt (Probe Bern still)   Stand 14:32│
│     Hosts [████████████▒▒▒▒░░] 29 OK · 5 beeintr. · 2 aus · 9 unbek. · 1 Wartung│
│     Datenquellen: Integrationen 12/13 · Probes 1/2 frisch · Syslog empfängt     │
├──────────────────────────────────────────────┬────────────────────────────────┤
│ E2  Offene Incidents (2)                      │ Seit Ihrem letzten Besuch 09:12│
│     #1071 kritisch · 25 min · 7 Hosts · —     │ +4 Incidents, 2 gelöst         │
│     #1072 Warnung · 6 min · SRV-BACKUP-01     │ 2 neue Hosts in Discovery      │
│     Objekte mit Problemen (18)  Abweichungen  │ Agent SRV-RDS-01 → 0.4.2       │
├──────────────────────────────────────────────┴────────────────────────────────┤
│ E3  Wartung aktiv: NAS-ZH-01 bis 15:00 · Kürzlich gelöst · Standorte · USV …    │
├───────────────────────────────────────────────────────────────────────────────┤
│ E4  Bald kritisch: Zertifikat 9 d · D: voll in ~5 d │ Verfügbarkeit 99,94 % /   │
│     Incident-Trend 14 d                              │ Ziel 99,90 %              │
└───────────────────────────────────────────────────────────────────────────────┘
```

(Namen aus dem simulierten Szenario `docs/design/prototypes/SCENARIO.md`.)

### 7.2 Die acht Dashboard-Fragen

Die acht Fragen aus dem Brief, mit der Fläche, die sie beantwortet, und den Daten dahinter.

| # | Frage (Brief) | Ebene | Antwortende Fläche | Daten heute (Endpoint) | Lücke → Backend |
|---|---|---|---|---|---|
| 1 | Ist meine Infrastruktur aktuell gesund? | E1 | Statussatz + Host-Statusleiste | `/api/dashboard` (`online_count`, `offline_count`, `host_stats`), `/api/v1/status` | Einheitlicher, probe-bewusster Status mit Zählern je Zustand inkl. Wartung/Unbekannt (B-01, B-02) |
| 2 | Welche kritischen Probleme existieren? | E2 | „Braucht Aufmerksamkeit“, nach Schwere sortiert | `/api/v1/incidents?status=…` (Limit 50), `host_stats` | Multi-Status-Filter und Gesamtzahl (B-04) |
| 3 | Welche Systeme oder Services sind betroffen? | E2/E3 | Betroffene Objekte je Incident, Topologie-Ast | Topologie `/api/v1/topology`; Incident nur `host_ids_hash` | `host_ids` am Incident (B-05), Standorte (B-14) |
| 4 | Was hat sich seit meinem letzten Besuch verändert? | E2 | „Seit Ihrem letzten Besuch“ | — | Changes-Endpoint und „letzter Besuch“ je Benutzer (B-08, B-10) |
| 5 | Welche Incidents benötigen Aufmerksamkeit? | E2 | Offene/unquittierte Incidents mit Dauer und Zuständigkeit | `/api/v1/incidents`, Ack-Status | Zuständigkeit und Notizen (B-11) |
| 6 | Welche Probleme hängen möglicherweise zusammen? | E2/E4 | Beziehungen mit Label (Confirmed / Rule-based / Suspected / Not available yet) | Korrelationsregeln (`services/correlation.py`), Topologie-Parents, Baselines | Beziehungen und Label als eigenes Feld am Incident; Herkunft der Parent-Links (siehe Konzepte B und C) |
| 7 | Welche Systeme entwickeln sich negativ? | E4 | „Risiken & Trends“: Kapazität, Zertifikate, Baseline-Abweichungen | `ssl_certs`, `storage_pools.days_until_full` (`/api/dashboard`), `/api/v1/syslog/intelligence` (Trends, Precursors) | Baseline-Reife (B-12); weitere Prognosen [Zukunft] |
| 8 | Was muss ich als Nächstes tun? | E2 | „Nächster Schritt“ je Eintrag, jeweils mit der Datenquelle, aus der er abgeleitet ist | Regelname, betroffene Objekte, Log-Muster | Regelbasierte Schritt-Vorlagen je Incident-Typ; keine KI-Behauptungen ohne Opt-in |

Zwei weitere Fragen, die ein Monitoring-Dashboard zusätzlich beantworten muss:

| Frage | Ebene | Fläche | Daten heute | Lücke |
|---|---|---|---|---|
| Kann ich den Daten trauen? | E1 | Datenquellen-Zeile, Verbindungs- und Frischestatus | `integration_health`, `/api/system/status`, Self-Check-Incidents | Probe-Liste mit Frische (B-03), Frische je Integration (B-02) |
| Was ist geplant oder bewusst unterdrückt? | E3 | Wartung aktiv/heute | `/api/v1/maintenance-windows`, `/api/v1/hosts?status=maintenance` | Unterdrückte Incidents während Wartung (B-13, optional) |

### 7.3 Was das Dashboard nicht mehr enthält

| Entfällt | Grund | Neuer Ort |
|---|---|---|
| Gravity-Globus | Kein Handlungswert, belegt die beste Fläche | optional Wallboard |
| Durchschnittslatenz | Mittelwert ohne Aussage | „Hosts über Latenzschwelle“ |
| Hostliste (erste 20) | Doppelt zur Hostliste | „Objekte mit Problemen“ |
| Live-Syslog-Feed | Lenkt ab, ist kein Lagebild | `/logs?live=1` |
| Uptime-Ranking, höchste Latenz | Analyse, kein Lagebild | `/analytics/availability`, Hosts-Ansicht |
| „Uptime“ der Nodeglow-Instanz | Systemkennzahl | `/admin/system` |

Wallboard-Modus (`/?mode=wallboard`): nur E1 und E2, grosse Schrift, ohne Navigation, automatische Aktualisierung; zeigt die Verbindungs- und Frischezustände besonders deutlich.

---

## 8. Backend-Arbeit für diese IA

Sortiert nach Priorität. „Für“ verweist auf Befunde (F-xx, `01-ux-audit.md`) und Workflows (W-x).

> **API für die neu gestaltete Oberfläche:** umgesetzt sind B-01, B-02 (`/api/v2/summary`), B-03
> (`is_probe` in PATCH/GET, Probe-Frische), B-04 für Incidents, B-05, B-07, B-08
> (`/api/v2/changes`), aus B-10 der letzte Besuch, B-15 (Verfügbarkeit ohne Wartung, globales Ziel)
> und das Dashboard in einem Aufruf (`/api/v2/dashboard`). Felder, Herleitung und was nicht ableitbar
> ist: [`05-dashboard-api.md`](05-dashboard-api.md).

| ID | Arbeit | Inhalt | Für | Prio |
|---|---|---|---|---|
| B-01 | Einheitlicher, probe-bewusster Host-Status | `services/probes.statuses_for` in `/hosts/api/status`, `/api/v1/hosts`, `/api/dashboard` nutzen; Felder `state` (ok/degraded/down/unknown/maintenance/disabled), `state_reason`, `observed_at` | F-01, F-34, Frage 1, W2 | P1 |
| B-02 | Summary-Endpoint mit Definitionen | Ein Endpoint für alle Zähler (Hosts je Zustand inkl. Wartung, Incidents offen/quittiert/kritisch, Integrationen ok/Fehler, Probes frisch/stale, Syslog-Empfang); ersetzt abweichende Zählungen in `/api/v1/status` und `/api/dashboard` | F-06, Fragen 1 und 5 | P1 |
| B-03 | Probe-Verwaltung | `is_probe` in `PATCH`/`GET /api/v1/agents`; Probe-Liste mit Heartbeat, Frische-Schwelle, Anzahl Hosts; Bulk-Zuweisung `probe_id` | F-07, W4 | P1 |
| B-04 | Server-seitige Listen | Hosts: `q`, `status` (mehrfach), `source`, `probe`, `site`, `tag`, `sort`, Cursor, `total`, Facettenzähler. Incidents: `status` mehrfach, `severity`, `rule`, `host_id`, `from`/`to`, Cursor, `total`. Syslog: Cursor, `app`, `template`, Facetten | F-05, F-28, F-30, F-31 | P1 |
| B-05 | Betroffene Objekte am Incident | `host_ids` (Liste statt nur Hash) persistieren und im Detail mit aktuellem Status ausgeben; Relationstyp (Topologie, Regel, zeitlich) | F-43, W1, Frage 4 | P1 |
| B-06 | Hostgefilterte Logs im Incident | Related Logs auf betroffene Hosts und Upstream-Parents filtern (`api_v1.py:1497-1503`) | F-43, W1 | P1 |
| B-07 | Bulk-Route reparieren und erweitern | Reihenfolge `/hosts/bulk` vor `/hosts/{host_id}`; Felder Wartung, Probe, Tags; Ergebnis je Host | F-08, W4, W6 | P1 |
| B-08 | Änderungen seit Zeitpunkt | `GET /api/v1/changes?since=` (Incidents eröffnet/gelöst, Statuswechsel, neue Discovery-Funde, Agent-Updates, Konfigurationsänderungen aus dem Audit), Grundlage für einen späteren globalen Event-Stream | Frage 3, W2 | P2 |
| B-09 | Globale Suche | `GET /api/v1/search?q=&types=` über Hosts (inkl. IP/MAC), Incidents, Agents, Integrationen, Muster, Ansichten | 5.2 | P2 |
| B-10 | Benutzerpräferenzen und gespeicherte Ansichten | `user_preferences` (laut Tenancy-Spec) vorziehen: Darstellung, Zeitzone, Sprache, Spalten, letzter Besuch, Dashboard-Layout; generische Saved Views (Liste, Filter, persönlich/geteilt), `SyslogView` darin aufgehen lassen | F-26, F-32, F-38 | P2 |
| B-11 | Incident-Workflow | Zuständiger (User-FK), Notizen, Reopen, Un-Ack, Statuswechsel als Events mit Benutzer | F-45, W1 | P2 |
| B-12 | Reifegrad der Analytik | Baseline-Abdeckung je Host, Korrelation aktiv/aus, letzte Analysezeit im Payload von Intelligence und Dashboard | F-04, Frage 7 | P2 |
| B-13 | Korrelationsregeln als API | Liste der eingebauten Regeln mit Beschreibung, Schwellen, Aktiv-Flag, Treffern und Precision (`predictor/eval`) | F-18, W8 | P2 |
| B-14 | Standorte | `sites` aus der Tenancy-Spec vorziehen: Host/Probe/Scan-Zuordnung, Filter in allen Listen und in der Topologie | F-36, W4, Frage 4 | P2 |
| B-15 | Verfügbarkeit ohne Wartung, Ziele | Verfügbarkeit mit Wartungsabzug, konfigurierbares Ziel je Tenant/Gruppe | F-10, Frage 8 | P2 |
| B-16 | Live-Push für Incidents | `/ws/live` um `incident_opened/updated/resolved` und `integration_status` erweitern (Tenant-scoped laut Spec) | F-09, F-53 | P2 |
| B-17 | Alert-Objekt und Versandprotokoll | Persistente Alerts (Regel × Objekt, Beginn/Ende), Zuordnung zu Incidents; Versandprotokoll paginiert und filterbar | 3.2, 1.3 (Versandprotokoll) | P3 |
| B-18 | Benachrichtigungskanäle als Objekte | Kanal-CRUD statt Settings-Keys, Routing nach Severity/Standort/Tag, Ruhezeiten | 1.3 (Benachrichtigungen) | P3 |
| B-19 | Tags und Kritikalität am Host | Felder und Filter; Kritikalität fliesst in die Sortierung „Aufmerksamkeit“ | F-36 | P3 |
| B-20 | Server-Export | Export der gefilterten Gesamtmenge (CSV/JSON) für Hosts, Incidents, Logs | 6.1 | P3 |
| B-21 | Stabile Log-IDs | Eindeutige ID je Log-Zeile für Deep Links und Drawer | 5.4 | P3 |
| B-22 | Integrationstest vor dem Speichern | `POST /api/integration/{type}/test` mit Formularwerten | W5 | P3 |
| B-23 | API-Konsolidierung | Neue Ansichten nur gegen `/api/v1`; alte Pfade (`/hosts/api`, `/syslog/api`, `/settings/*`) mit Ablaufplan | F-58 | P3 |
| B-24 | MSP-Aggregation [ee] | Übergreifende Kennzahlen je Tenant für `/portal`, Provider-Rollen | W3 | Zukunft |

---

## 9. Offene Fragen an den Owner

1. **Tenant-Deep-Links:** Ist `?tenant={slug}` als Hinweis für Benachrichtigungslinks akzeptabel (Spec: Header + Session, keine Pfade)?
2. **Standorte vor der Multi-Tenancy:** Sollen `sites` (B-14) vorgezogen werden? Für W4 und Schweizer KMU mit Filialen ist das der grösste Gewinn, auch ohne mehrere Tenants.
3. **Alert-Objekt:** Reicht mittelfristig „Incident + Events“, oder ist ein persistentes Alert-Objekt (B-17) für Routing/Eskalation geplant? Davon hängt ab, ob „Alerts“ später als Tab im Incident erscheint.
4. **Sprache:** Deutsch und Englisch zum Start, Französisch und Italienisch später? URLs bleiben in jedem Fall englisch.
5. **Wallboard:** Soll der Gravity-Globus als optionaler Wallboard-Modus bleiben oder ganz entfallen?
6. **Open-Core-Schnitt in der IA:** Werden `ee`-Bereiche (Portal, Tenants, SLA-Berichte) im Core als gesperrte Einträge gezeigt oder ganz ausgeblendet? Empfehlung: ausblenden, Hinweis nur unter Administration.
