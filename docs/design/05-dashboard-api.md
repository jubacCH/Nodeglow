# 05 — API für die neu gestaltete Oberfläche (Dashboard E3)

Stand: Branch `feat/dashboard-api`. Ergänzt [`02-information-architecture.md`](02-information-architecture.md)
Abschnitt 8 (B-01 … B-15) und das freigegebene Konzept
[`directions/concept-e3.md`](directions/concept-e3.md).

Grundsätze, die jede Antwort einhält:

- **Keine Daten ist nie „up“.** Ein Host ohne frische Messung ist `unknown`, nicht sein letzter Wert.
- **Nicht ableitbar heisst `null`**, mit Grund in dieser Datei — nie eine erfundene Zahl.
- **Eine Definition je Kennzahl.** Dashboard, Seitenleisten-Badge und gefilterte Liste rechnen mit
  denselben Funktionen (`services/host_state.py`, `services/incident_view.py`).
- Zeiten sind UTC, ISO-8601 mit `Z`. Die Zeitzone für „heute“ kommt aus der Einstellung `timezone`.

---

## 1. Einheitlicher Host-Zustand (B-01)

`services/host_state.py` ist die einzige Regel. Reihenfolge, die erste zutreffende gewinnt:

| `state` | Wann |
|---|---|
| `disabled` | Überwachung des Hosts ausgeschaltet |
| `maintenance` | manuelles Flag (mit `maintenance_until`) oder aktives Wartungsfenster (`services/maintenance.py`) — gewinnt gegen jedes Messergebnis |
| `unknown` | niemand beobachtet den Host gerade: Probe stale (`services/probes.py`), Probe existiert nicht mehr, gar kein Ergebnis, oder neuestes Ergebnis älter als 3 × Prüfintervall (Core: `ping_interval`, mind. 180 s; Probe: `probes.result_window`) |
| `down` | neueste Prüfung fehlgeschlagen |
| `warning` | Host antwortet, aber ein Dienst nicht: `check_errors`, `port_error` oder offener Incident der Regeln `agent_service` / `port_error`, der den Host nennt |
| `degraded` | Latenz über Host- oder globaler Schwelle (`latency_threshold_ms`), oder der Host steht in einem anderen offenen Incident (ausser Vorhersagen `learned_precursor*` und `self_check`) |
| `up` | frische, erfolgreiche Prüfung und nichts davon |

Felder an jedem Host: `state`, `state_reason` (kurzer Satz, z. B. „Probe probe-bern-01 silent 11 min
(threshold 3 min)“, „Not responding, behind SW-ZH-CORE-02 (down)“), `observed_at` (Zeit der neuesten
echten Prüfung, auch wenn der Zustand `unknown` ist).

Verwendet in:

| Endpoint | Neu | Geändert (kompatibel) |
|---|---|---|
| `GET /api/v1/hosts` | `state`, `state_reason`, `observed_at`, `probe_id`; Filter `state=a,b` | `status` (online/offline/unknown/maintenance/disabled) wird aus `state` abgeleitet → hinter stiller Probe jetzt `unknown` statt `online` |
| `GET /api/v1/hosts/{id}` | dieselben Felder + `status` | `health_score` 0.8 bei `unknown` |
| `GET /hosts/api/status` | `state`, `state_reason`, `observed_at`, `probe_id` | `online` ist `null`, wenn der Host `unknown` ist |
| `GET /api/dashboard` (alt) | `host_state_counts`, `host_state_reasons`, je `host_stats[]` die drei Felder | `online_count` zählt unbeobachtete Hosts nicht mehr |
| `GET /api/v1/topology` | `state`… je Knoten, `provenance` je Kante | `status` unverändert |

## 2. Behobene Fehler

| Befund | Ursache | Behebung |
|---|---|---|
| F-01/F-34: Hosts hinter stiller Probe grün | nur die Topologie nutzte `statuses_for` | einheitlicher Zustand (oben) überall |
| F-07: Probe-Schalter ohne Wirkung | `is_probe` fehlte in der Allowlist von `PATCH /api/v1/agents/{id}` | `is_probe` und `probe_interval_seconds` werden gespeichert (nur Admin, Audit `agent.probe`); `GET /api/v1/agents` und `/agents/{id}` liefern `is_probe`, `probe_interval_seconds`, `probe: {stale, staleness_window_seconds, last_report, host_count}` |
| F-08: Bulk-Edit unerreichbar | `PATCH /hosts/bulk` stand hinter `PATCH /hosts/{host_id}` (422) | Route vor die Einzelroute; Felder `check_type`, `enabled`, `latency_threshold_ms`, `maintenance`, `probe_id`; Antwort `{updated, ids, missing, ignored_fields}`; Audit `host.bulk_update` |
| Incident-Liste nur 50 Einträge, ein Status | kein Mehrfachfilter, keine Gesamtzahl | siehe 3. |

## 3. Incidents (B-04, B-05)

`GET /api/v1/incidents`

- `status=open,acknowledged` (Kommaliste; `all` = kein Filter), `severity=critical,warning`, `rule`,
  `host_id` (über die gespeicherten Hosts), `from`/`to` (ISO, auf `created_at`), `search`, `host_name`.
- `sort=updated|created|severity`, `limit` (≤ 500), `offset`.
- Gesamtzahl immer im Header `X-Total-Count`. Mit `envelope=true` kommt
  `{items, total, limit, offset, has_more}`; ohne bleibt es die bisherige Liste.
- Jeder Eintrag zusätzlich: `acknowledged`, `host_ids`, `host_count`, `hosts` (bis 20 kompakte
  Einträge `{id, name, hostname, state, state_reason}`).

`GET /api/v1/incidents/{id}`: zusätzlich `host_ids`, `host_count`, `hosts` (alle, mit aktuellem
Zustand), `acknowledged`.

**Betroffene Hosts gespeichert** (Migration `037`, Spalte `incidents.host_ids`, sortierte JSON-Liste):
geschrieben von `_find_or_create_incident` für alle Korrelationsregeln, von Agent-Service-Incidents
(der Ping-Host des Agents) und von Self-Check-Probe-Incidents (die Hosts der stillen Probe).
`null` = nicht erfasst (alle Incidents vor 037, Regeln ohne Hostbezug) → die UI zeigt „unbekannt“,
nicht „keine Hosts“. `[]` = erfasst, betrifft keinen Host (Syslog-Spike, Self-Check). Kein Backfill:
der Hash ist nicht umkehrbar.

## 4. `GET /api/v2/dashboard`

Ein Aufruf für alle Karten von E3. Basisdaten (aktive Hosts, neueste Prüfung je Host, Wartungsfenster,
offene Incidents, Topologie) werden einmal geladen und von allen Abschnitten geteilt. Jeder Abschnitt
ist isoliert: scheitert einer, ist er `null` und steht in `errors[]` (`{section, error}`), der Rest
kommt normal (IA 6.5 „Teilausfall“). `timings_ms` nennt die Dauer je Abschnitt.

Parameter: `since` (optional, ISO) überschreibt „seit dem letzten Besuch“.

| Feld | Inhalt | Quelle | Ehrlich ableitbar? |
|---|---|---|---|
| `previous_seen_at` | letzter gespeicherter Besuch | `user_preferences` | ja; `null` beim ersten Besuch |
| `summary` | wie `/api/v2/summary` | | ja |
| `health.counts` | Hosts je Zustand (alle sieben) | Host-Zustand | ja |
| `health.reasons` | je Zustand die häufigsten Gründe `{text, count}`, z. B. „behind SW-ZH-CORE-02“ ×7, „probe probe-bern-01 silent 11 min“ ×9 | Host-Zustand | ja |
| `health.totals` | `hosts`, `disabled`, `with_current_data` (up/degraded/warning/down), `agents`, `agents_reporting`, `integrations`, `integrations_error`, `probes`, `probes_stale` | | ja |
| `health.failing_integrations` | bis 10 `{id, type, name, error}` | neuester Snapshot | ja |
| `internet` | `source` (`speedtest`/`unifi`), `latest {download_mbps, upload_mbps, latency_ms, at, server}`, `history` (letzte 24 Ergebnisse, älteste zuerst), `wan {status, latency_ms, source}`, `gateway {id, name, state}` | Speedtest-Snapshots; Fallback UniFi-Snapshot (`speedtest`, `wan`) | Speedtest ja. **WAN-Status nur mit UniFi** (`wan.status` „ok“ → `up`), sonst `null`. Ganzer Abschnitt `null` ohne Speedtest/UniFi |
| `incidents.counts` | `open` (= offen + quittiert), `acknowledged`, `unacknowledged`, `critical`, `warning`, `info` | | ja |
| `incidents.items` | offen/quittiert, sortiert nach Schwere, unquittiert vor quittiert: `id, title, severity, rule, status, acknowledged, acknowledged_by, opened_at, age_seconds, host_count, host_ids, summary` | | ja; `host_count` `null`, wo nicht erfasst |
| `incidents.per_day` | 14 Tage (lokale Tage), `count` + je Schwere | `created_at` | ja |
| `incidents.resolved_today` | `id, title, severity, opened_at, resolved_at, duration_seconds` | `resolved_at` ≥ lokaler Tagesbeginn | ja |
| `topology.roots` | oberste Eltern (Gateway zuerst) | `services/topology.py` | ja |
| `topology.parents` | bis 12 Eltern, schlimmste zuerst: `child_count`, `descendant_count`, `descendant_states`, `worst_state`, `affected`, `link_provenance {manual, proxmox, unifi}`, `is_gateway`; bei betroffenen Eltern `children[]` mit Zustand | | ja |
| `topology.internet_root` | WAN-Knoten `{state, latency_ms, gateway, source}` | aus `internet` | nur mit UniFi/Speedtest |
| `topology.links_by_provenance`, `unlinked_hosts` | Herkunft der Kanten, Hosts ohne Kante | | ja |
| `groups` | **keine Standorte**: `kind=direct` („Direct“, vom Core geprüft) und je Probe `kind=probe` mit `fresh`, `last_report`, `staleness_window_seconds`, `host_count`, `by_state` | `probe_id`, Probe-Heartbeat | ja. Echte Standorte gibt es erst mit B-14 |
| `upcoming.items` | sortiert nach `due_at`: Wartung (`phase` `active`/`scheduled`, nächste 24 h, Fenster und manuelle mit Ende), Zertifikate ≤ 30 Tage (Host-HTTPS-Check, NPM), Disk voll ≤ 30 Tage (`days`, `current_pct`, `trend_pct_per_day`, `confidence` ≥ 0.3, `method`) | Wartungsfenster, `ssl_expiry_days`, Integrations-Zertifikate, `services/predictions.py` | ja; Zertifikat und Disk mit `estimated_due: true` (Tage, keine exakte Uhrzeit). `agent_disk_predictions_ready=false`, bis der Scheduler die Agent-Prognosen einmal gerechnet hat |
| `latency` | für den schwersten offenen Incident mit erfassten Hosts: je Minute `median_ms`, `max_ms`, `ok`, `failed`, letzte 2 h; `onset_at`, `median_before_ms`, `median_since_ms`, `host_names` | ClickHouse `ping_checks` | ja; `null`, wenn kein offener Incident Hosts erfasst hat |
| `syslog` | `buckets` (15 min, 24 h, `count`, `errors` = Severity ≤ 3), `current_per_min` (letzte 15 min), `usual {low_per_min, high_per_min, mean_per_min, sources, method}`, `total_24h`, `errors_24h`, `receiving` | ClickHouse `syslog_messages` (Empfangszeit), `host_baselines` | Band nur, wenn Baselines für diese Stunde/Wochentag gelernt sind (Mittel ± 1σ, Summe über Quellen), sonst `null` |
| `availability` | `pct`, `target_pct` (Einstellung `availability_target`, Standard 99.9), `status` (`above_target`/`below_target`/`no_data`), `downtime_minutes`, `budget_minutes`, `budget_used_pct`, `checks`, `failed_checks`, `hosts_with_data`, `method` | ClickHouse `ping_checks`, 30 Tage | siehe unten |
| `since_last_visit` | `since`, `fallback` (true = kein gespeicherter Besuch, letzte 24 h), `counts` je Typ, `total`, `items` (20 neueste) | `/api/v2/changes` | siehe 6. |

**Verfügbarkeit, wie gerechnet:** erfolgreiche Prüfungen ÷ alle Prüfungen der letzten 30 Tage über
alle aktiven Hosts. Hosts in Wartung werden nicht geprüft (keine Zeilen), Zeiten ohne Daten erzeugen
ebenfalls keine Zeilen — beides fliesst also nicht ein und zählt nie als „up“. `downtime_minutes` ist
flottenäquivalent: (1 − Verfügbarkeit) × 30 Tage; `budget_minutes` = (1 − Ziel) × 30 Tage
(99.9 % → 43.2 min). Nicht ableitbar: Verfügbarkeit je Dienst/Kunde und ein Ziel je Gruppe (B-15
Rest, B-24).

**Performance:** lokal (SQLite, ClickHouse gemockt, 1020 Hosts, 3000 Incidents, 2000 Snapshots)
≈ 35 ms warm; die Zahl der SQL-Statements ist unabhängig von der Zahl der Hosts und Incidents (Test).
Auf Produktion kommen fünf ClickHouse-Abfragen dazu (neueste Prüfung je Host, Latenz 2 h, Syslog-
Histogramm, Syslog letzte 15 min, Verfügbarkeit 30 Tage). Die beiden schweren sind kurz gecacht
(Verfügbarkeit 5 min, Syslog-Histogramm 60 s), die Topologie 60 s, Prognosen rechnet der Scheduler
(`refresh_disk_predictions`, alle 15 min). Ziel < 300 ms warm ist auf Produktion noch zu messen.

## 5. `GET /api/v2/summary`

Kleine Zähler für Leiste und Seitenleiste, aus denselben Funktionen wie das Dashboard:

```json
{
  "incidents": {"open": 2, "acknowledged": 1, "unacknowledged": 1, "critical": 1, "warning": 1, "info": 0},
  "hosts": {"total": 48, "by_state": {"down": 2, "warning": 2, "degraded": 5, "unknown": 9,
            "maintenance": 1, "up": 29, "disabled": 0}, "attention": 18},
  "probes": {"total": 2, "stale": 1, "stale_unused": 0},
  "integrations": {"total": 13, "ok": 12, "error": 1, "no_data": 0},
  "definitions": {"incidents.open": "status open or acknowledged", "...": "..."}
}
```

`attention` = down + warning + degraded + unknown. `probes.stale` zählt nur stille Probes mit
zugewiesenen Hosts (eine unbenutzte Probe ist kein Problem). Integrationen: neuester Snapshot je
aktiver Konfiguration (Standby-Mitglieder zählen als ok); `data_json` wird dafür nicht geladen.

## 6. Letzter Besuch und Änderungen (B-08, B-10)

- `user_preferences` (Migration `038`): eine Zeile je Benutzer, `dashboard_seen_at`.
- `POST /api/v2/me/seen` setzt „jetzt“ und liefert `{dashboard_seen_at, previous_seen_at}`;
  `GET /api/v2/me/seen` liest. Braucht eine Sitzung (kein API-Key). Empfehlung an das Frontend:
  `POST` beim Verlassen des Dashboards oder nach einigen Sekunden Sichtbarkeit, nicht beim Laden —
  sonst ist „seit dem letzten Besuch“ nach jedem Neuladen leer.

`GET /api/v2/changes?since=ISO&until=ISO&types=a,b&limit=50&offset=0` — neueste zuerst, `counts` je
Typ, `total`, `has_more`. Zeitraum höchstens 31 Tage (älteres `since` wird gekürzt).

| Typ | Abgeleitet aus | Grenzen |
|---|---|---|
| `incident_opened` | `incidents.created_at` | — |
| `incident_resolved` | `incidents.resolved_at` (mit Dauer) | — |
| `incident_acknowledged` | `incident_events` (`acknowledged`) | — |
| `host_added` | `ping_hosts.created_at`; `discovered=true`, wenn die Quelle nicht `manual` ist | „wartet auf Prüfung“ gibt es als Zustand nicht; Subnet-Scanner legt Hosts direkt an |
| `port_discovered` | `discovered_ports.first_seen` | — |
| `agent_enrolled` | `agents.created_at` | — |
| `agent_updated` | Audit `agent.version_change`, **neu** vom Heartbeat geschrieben, wenn sich die gemeldete Version ändert | Updates vor diesem Stand sind nicht rekonstruierbar |
| `maintenance_started` | Fensterbeginne aus dem Zeitplan (`occurrences_between`), manuelle Schalter aus Audit `maintenance.toggle` | manuelles Setzen über `PATCH /hosts` erscheint als `config_change` |
| `probe_silent` | Probes, die **jetzt** still sind: `last_seen` + Schwelle liegt im Zeitraum | eine Probe, die still war und sich erholt hat, hinterlässt nur ihren Self-Check-Incident („Probe not reporting: …“ als `incident_opened`) |
| `config_change` | übrige Audit-Einträge (ohne Login/Logout) | nur für Admins |

**Nicht ableitbar** (nirgends gespeichert): Up/Down-Wechsel einzelner Hosts ausserhalb von Incidents
(nur als Rohdaten in ClickHouse, für einen Feed zu teuer), Fehler/Erholung von Integrationen
(Snapshots haben keine Übergangsmarke und nur ihre Aufbewahrungszeit), wer was angesehen hat.

## 7. Authentifizierung

Alle Routen in `routers/api_v2.py` tragen dieselbe Abhängigkeit wie `/api/v1` (`require_api_key`,
fällt auf die Sitzung zurück). Die Middleware lässt `/api/v2/*` heute nur mit Sitzung durch.
`tests/test_routers/test_route_auth_coverage.py` prüft die Abhängigkeit je Route und dass anonyme
Aufrufe sowie falsche Keys abgewiesen werden.

## 8. Offen / bewusst nicht gemacht

- Standorte (B-14), Zuständigkeit/Notizen am Incident (B-11), Beziehungslabels am Incident.
- Backfill von `incidents.host_ids` (Hash nicht umkehrbar).
- Live-Push der neuen Zähler (B-16); das Frontend fragt ab.
- `alembic check` gegen PostgreSQL lief lokal nicht (kein Postgres); die CI prüft es.
