# Prototype scenario (simulated data)

All three dashboard prototypes show this **same** situation, so they can be compared on
presentation and workflow rather than on content. Every value here is **simulated**. It is
modelled on what Nodeglow can actually detect today, but it is not production data and must
be labelled as simulated in every prototype (a visible "Simulated data" marker).

Nothing in this folder is imported by the application.

## Environment

- **Tenant:** "Muster Treuhand AG" (fictional Swiss SME, 120 employees). Multi-tenancy is
  planned, not built: a tenant switcher may appear only as a clearly marked future element.
- **Sites:** Zürich HQ (server room, monitored directly), Bern branch (monitored through the
  remote probe `probe-bern-01`), Cloud/Internet (HTTP/SSL checks).
- **Now:** Friday 10.10.2026, 14:32 (Europe/Zurich). The viewer's previous visit: 09:12.

### Inventory (48 monitored hosts)

| Group | Count | Examples |
|---|---|---|
| Hypervisors (Proxmox) | 2 | `pve-zh-01`, `pve-zh-02` (21 guests) |
| Windows servers | 5 | `SRV-DC-01`, `SRV-FILE-01`, `SRV-BACKUP-01`, `SRV-APP-01`, `SRV-RDS-01` |
| Linux VMs | 9 | `web-01`, `db-01`, `mon-01`, … |
| Network: switches | 6 | `SW-ZH-CORE-01`, `SW-ZH-CORE-02`, `SW-ZH-ACC-01..03`, `SW-BE-01` |
| Network: firewalls | 2 | `fw-zh-01` (pfSense), `fw-be-01` |
| Wireless APs | 8 | `AP-ZH-OG1..OG4`, `AP-BE-01..04` |
| Storage (Synology) | 2 | `NAS-ZH-01`, `NAS-BE-01` |
| Printers / IoT | 3 | `PRN-ZH-01`, … |
| Bern branch clients/devices | 9 | behind `probe-bern-01` |
| Cloud endpoints (HTTP) | 2 | `portal.mustertreuhand.ch`, `intranet` |

Agents: 14 (Windows 6, Linux 8). Integrations: 13 (Proxmox, UniFi, pfSense, Synology,
Technitium DNS, Nginx Proxy Manager, Cloudflare, phpIPAM, Speedtest, …).

### Current state of the 48 hosts

| State | Count | Notes |
|---|---|---|
| Healthy | 29 | |
| Degraded (latency) | 5 | behind `SW-ZH-CORE-02`, 3–40 ms instead of ~0.3 ms |
| Down | 2 | `SRV-APP-01`, `PRN-ZH-02`, both behind `SW-ZH-CORE-02` |
| Warning | 2 | `SRV-BACKUP-01` (service stopped), `web-01` (port 443 check failed) |
| Unknown / stale | 9 | Bern hosts: probe silent for 11 min (threshold 3 min). **Must never render as healthy.** |
| Maintenance | 1 | `NAS-ZH-01`, window "NAS firmware update" 14:00–15:00 |

## What is happening

1. **Incident #1071 — critical, open, since 14:07 (25 min).** Rule `multi_host_down` +
   `host_down_syslog`. Switch `SW-ZH-CORE-02` logs `%LINK-3-UPDOWN` flapping on uplink
   `Te1/0/48` (38 events in 10 min). Behind it: 5 hosts degraded, 2 down.
   - **Confirmed** relation: all 7 hosts share the parent `SW-ZH-CORE-02` in the topology
     (Proxmox/UniFi-derived `parent_id`); downstream alerts are suppressed into this incident.
   - **Rule-based** relation: syslog burst on the parent within the incident window.
2. **Suspected relation (not confirmed):** `fw-zh-01` filterlog volume +420 % vs. the hourly
   baseline for Friday 14:00. Overlaps #1071 in time only. Label it as suspected.
3. **Incident #1072 — warning, open, since 14:26 (6 min).** Windows service
   `VeeamBackupSvc` stopped on `SRV-BACKUP-01` for 3 consecutive agent reports.
4. **Trend:** `SRV-FILE-01` volume `D:` at 91 %, linear projection: full in ~5 days.
5. **Expiring certificate:** `portal.mustertreuhand.ch`, 9 days left.
6. **Stale data:** `probe-bern-01` last heartbeat 14:21. Its 9 hosts are "unknown", not "up".
7. **Maintenance:** window "NAS firmware update" active on `NAS-ZH-01` until 15:00.
8. **Recently resolved:** #1069 HTTP check `intranet` returned 503 (13:36–13:48);
   #1068 latency spike on `AP-ZH-OG3` (11:02–11:09).

### Since the previous visit (09:12)

4 incidents opened (#1069–#1072), 2 resolved (#1068, #1069), 2 new hosts discovered by the
subnet scanner (awaiting review), agent `SRV-RDS-01` updated to 0.4.2.

### Numbers for charts

- Latency of the 7 affected hosts, last 2 h, 1-min steps: ~0.3 ms until 14:05, then
  3–40 ms with spikes; the 2 down hosts drop out at 14:07.
- Syslog rate, last 24 h, 15-min buckets: baseline 8–14 msgs/min, current 46 msgs/min
  (14:15–14:30). Total 24 h: 11,622 messages, 214 at error or worse.
- Incidents per day, last 14 days: 2,1,0,3,1,1,0,2,4,1,0,1,2,4 (today last).
- Availability last 30 days, all hosts: 99.94 % (target 99.90 %).
- Internet (Speedtest): 942 / 98 Mbit/s, 6 ms.

## Correlation labels (honesty rule)

Every relation shown must carry one of these labels, and nothing beyond what Nodeglow can
derive today may be presented as working:

| Label | Meaning | Source in Nodeglow |
|---|---|---|
| Confirmed | Structural link | topology `parent_id`, same probe, same integration |
| Rule-based | A correlation rule fired | `services/correlation.py` rules |
| Suspected | Temporal overlap or baseline deviation only | baselines, time windows |
| Not available yet | Planned analysis | e.g. cross-metric root-cause ranking |
