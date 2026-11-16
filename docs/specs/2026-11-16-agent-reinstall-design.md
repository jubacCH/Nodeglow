# Agent reinstall

Status: design · 2026-11-16, approved by the owner the same day. First of three
steps (A reinstall, B server-side SSH deploy, C probe-side deploy); B and C get
their own designs.

## Why

An agent whose host lost its installation (container rebuilt, `/opt/nodeglow`
deleted, config gone) cannot come back. The installer enrols with the
hostname, and `_enroll_in_tenant` refuses a known hostname unless the request
carries that agent's current token (`409 agent_exists`) — deliberately, since
otherwise anyone holding an install token could take over an enrolled agent by
naming its hostname. The only way out today is to delete the agent and install
again, which:

- gives the host a new agent id, so history, watched services, log settings
  and site assignment of the old id are gone;
- needs an admin to know that deleting is the way, which the UI does not say;
- leaves the `agent_silent` incident of the old id to be cleaned up.

On 2026-11-16 two production agents (homepage, semaphore) were in exactly this
state for seven months; the fix took a database script on the server. A
customer without console access to the Nodeglow server could not have done it.

## Goals

- An admin can reinstall an existing agent from the UI: one command for the
  host, and the agent comes back under its **existing id** with everything
  attached to it.
- The takeover protection stays: re-enrolling a known hostname still needs
  proof, now either the agent's current token or an admin-issued token bound
  to that very agent.
- The `agent_silent` incident points at the fix and resolves by itself when
  the agent reports again.

## Non-goals

- Running the install remotely (SSH, Proxmox, probe): steps B and C.
- Repairing the existing Proxmox `deploy-agent` endpoint (it pipes
  `/install/linux` without a token and has been failing with 401): step B.
- Moving an agent to a renamed host.

## Anti-goals

- A window in which a hostname can be claimed by any install token.
- A reinstall token that can be used more than once or for another host.

## Approaches

1. **Install token bound to an agent** (chosen). An admin issues a token for
   agent X; enrolment with it rotates X's token. The proof of identity is the
   admin's decision, recorded in the audit log.
2. *Release the agent for re-enrolment for a while.* Any install token could
   then take it over during the window — the hole the 409 closed.
3. *Issue a new agent token and have the admin write it into the config on the
   host.* Manual editing on the host, awkward on Windows, and no installer run
   (binary and service unit stay whatever they are).

## Design

### Data model

Migration `055_install_token_agent.py`:

- `agent_install_tokens.agent_id` INTEGER NULL, composite tenant FK to
  `agents` (same pattern as `fk_integration_configs_probe_id` in 048), `ON
  DELETE CASCADE` — a token for a deleted agent is useless. Indexed.
- No new column for single use: a bound token is spent when `used_count >= 1`.

`AgentInstallToken.agent_id` in the model; the module docstring mentions the
bound kind.

### Enrolment (`routers/agents.py`)

`_consume_install_token` gains the bound case:

- A bound token whose `used_count` is already ≥ 1 is refused (`reason =
  "spent"`).
- A bound token whose agent's hostname does not match the enrol hostname
  (case-insensitive) is refused (`reason = "bound_hostname_mismatch"`), and is
  **not** consumed.
- As for every refusal, the reason goes to the server log only; the client
  gets the generic 403.

`_enroll_in_tenant` receives the token row. If it is bound:

- the existing agent is the bound one (looked up by id in the token's tenant,
  not by hostname);
- the agent token is rotated, `platform`, `arch` updated, `last_seen` set, as
  in today's re-enrolment branch; the response is `{"ok", "token",
  "agent_id"}` with the unchanged id;
- the 409 check is skipped — the bound token is the proof;
- an audit entry `agent.reinstalled` (agent id, token prefix, client IP).

Unbound tokens behave as today. The 409 message changes to: "An agent with
this hostname is already enrolled. Use 'Reinstall agent' on its page in
Nodeglow, or re-run the installer with the existing config."

### API

`POST /api/agents/{agent_id}/reinstall` (admin, `rate_limit(10/60s)`, same
guard as `create_install_token`):

- body `{"ttl_hours": 1..24}`, default 1;
- creates a bound token (`note = "reinstall <agent name>"`, `site_id` = the
  agent's site);
- audit `install_token.create` with `{"agent_id", "ttl_hours", "reinstall":
  true}`;
- response: the same shape as `POST /api/agents/install-tokens` (`id`,
  `token`, `prefix`, `expires_at`, `install_linux`, `install_windows`) plus
  `agent_id`;
- 404 for an unknown agent or one of another tenant. Probes are agents and
  reinstall the same way.

`GET /api/agents/install-tokens` returns `agent_id` and `agent_name` per row.

### UI

- `AddAgentDialog` gets a reinstall mode (prop `agent`): title "Reinstall
  agent", platform preselected from `agent.platform`, validity choice 1 h /
  24 h, issues via `/api/agents/{id}/reinstall`. "Connected" means this
  agent's `last_seen` is newer than the moment the command was issued.
- Agent detail page (`agents/[id]/page.tsx`): button "Reinstall agent"
  (admin only), opens the dialog.
- Incident detail (`incidents/[id]/page.tsx`): for rule `agent_silent` the
  agent id comes from the dedup key `ag:<id>`; an action "Reinstall agent"
  opens the dialog (admin only). If the API does not expose the dedup key, the
  incident detail response adds `agent_id` for this rule.
- Install-token list: bound tokens show "Reinstall: <agent name>".

### Silent-agent text

`services/agent_silence.py`: the incident text ends with "Check that the agent
service is running on the host and can reach this server, or reinstall it from
the agent's page."

## Tests

Backend:

- bound token: enrolment keeps the agent id, rotates the token (old token gets
  401 on `/api/agent/report`), updates platform/arch, writes the audit entry;
- bound token used twice → second refused; hostname mismatch → refused and not
  consumed; expired / revoked → refused; token of tenant A bound to an agent of
  tenant A cannot touch tenant B's agent of the same hostname;
- unbound token on a known hostname → 409 with the new message (unchanged
  behaviour otherwise);
- `POST /api/agents/{id}/reinstall`: admin only, 404 for unknown / other
  tenant, TTL clamped to 1..24, audit entry, response shape;
- token deleted with its agent (cascade) — PostgreSQL migration test for 055
  (upgrade of a populated 054 database, FK, downgrade);
- `agent_silent` resolves after the reinstalled agent reports (existing
  condition-incident path, one test).

Frontend: dialog in reinstall mode (endpoint, preselected platform, connected
state on `last_seen`), button visibility for admin / non-admin, incident
action for `agent_silent` only.

## Left to the builder

- Exact component structure of the reinstall mode (prop vs. small wrapper).
- Wording of UI strings beyond the ones above.
- Whether the incident detail already carries what is needed to find the
  agent, or `agent_id` has to be added to its response.
