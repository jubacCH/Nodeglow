# Single sign-on per tenant (OIDC)

Status: design · 2026-11-14 · owner decisions taken 2026-11-14 (see "Owner decisions") · nothing implemented. Phase 13 of
[2026-10-10-multi-tenancy-design.md](2026-10-10-multi-tenancy-design.md)
("Identity, LDAP, SSO"), narrowed to OIDC.

## Why

A tenant cannot bring its own sign-in today. What exists:

- local accounts (`users`, global usernames) with an optional second factor
  (TOTP, revision 051, `routers/mfa.py`);
- exactly one LDAP configuration for the whole installation (`ldap_*` in
  `services/settings_registry.py`), which maps to the default tenant
  (`routers/auth.py:_try_ldap_login`).

There is no OIDC or SAML code in the core or in `ee/`. A customer whose staff
live in Entra ID, authentik, Keycloak, Google or Infomaniak has to keep a
second set of passwords in nodeglow, and offboarding a person means remembering
to delete them here as well. For a product sold to companies that is a gap,
and with more than one tenant it has to be solved per tenant: the identity
provider of customer A must never be able to say anything about customer B.

Owner decisions this design builds on (2026-11-14):

- The feature lives in `ee/`; the core gets only the extension points.
- OIDC first. SAML and SCIM later, not in this piece.
- This document is read before any code. Then three or four pull requests, a
  trial against authentik, then Infomaniak, and an independent security review
  of the finished flow before any deployment with sign-in enabled.
- No special case per vendor. Infomaniak's discovery document
  (`https://login.infomaniak.com/.well-known/openid-configuration`) shows a
  plain provider: authorization code flow, PKCE S256, RS256 id tokens, scopes
  `openid profile email phone`, claims `sub`, `email`, `email_verified`,
  `name`, `groups`.

## Goals

- A tenant admin connects one or more OIDC providers to their tenant in the
  UI, without a file on the server and without the operator.
- A sign-in through the provider of tenant A opens a session in tenant A and
  nowhere else, whatever that provider claims.
- No account is ever taken over because a provider asserts a name or mail
  address that already exists in nodeglow.
- Setting it up cannot lock the tenant out: there is a test that changes
  nothing, and a local way in when the provider is down.
- A core-only installation behaves exactly as today.

## Out of scope (deliberately)

| Not in this piece | Why / what it would need later |
|---|---|
| SAML | Second protocol, XML signature handling is its own attack surface. The table has a `type` column so it can be added without a schema change. |
| SCIM | Provisioning and deprovisioning pushed by the provider. Until then a removed person loses access when their session ends (see "When things change at the provider"). |
| Provider-level identity providers (one IdP for an MSP's staff across its tenants) | Belongs to the MSP portal (phase 11), which does not exist yet. See "A provider-level one, later". |
| Claims mapping beyond groups | Only the name of the groups claim is configurable. No expressions, no attribute-to-role rules, no custom claim to site mapping. |
| Per-tenant LDAP | The installation-wide LDAP stays as it is. |
| Back-channel / front-channel logout, refresh tokens | No tokens are stored at all; see "Session". |
| Mail-domain discovery on the sign-in page | Needs proof that a tenant owns a domain. See "Finding the provider". |

## Data model

Three tables and two columns. The schema lives in the **core**
(`backend/models/identity.py`, one Alembic revision), the logic in `ee/`. That
is the precedent set by `provider_memberships` and `support_grants`: "schema
only in the core". The reason is practical: Alembic, `create_all` for fresh
installs, the classification test, row-level security, the purge order
(`tenant_tables()`) and the backup table list all work off the core's model
registry, and a second migration chain in `ee/` would have to be kept in step
with it forever.

```
identity_providers            TenantScoped
  id               serial PK
  tenant_id        FK tenants               UNIQUE (tenant_id, id)
  type             'oidc'                   (saml later)
  name             shown on the button, e.g. "xdigits (Infomaniak)"
  login_key        UNIQUE, installation-wide; part of the sign-in address
  enabled          bool, default false
  issuer           https URL, exactly as the provider states it
  client_id
  client_secret    encrypted with the tenant's data key (v2:<key_id>:…)
  scopes           default "openid profile email"
  groups_claim     default "groups"
  ca_pem           optional private CA for the provider's TLS certificate
  discovery_json   last discovery document that passed the checks
  discovery_at
  jit_enabled      bool, default true
  allowed_domains  JSON list, lower case; empty = no domain restriction
  required_groups  JSON list; empty = no group restriction
  role_rules       JSON list of {group, role}; empty = no mapping
  default_role     'readonly' | 'editor' | 'none'     (never admin)
  session_hours    default 12
  sso_required     bool, default false
  config_hash      hash of everything a test depends on
  tested_hash      config_hash of the last successful test sign-in
  tested_at, tested_by
  created_at, updated_at

user_identities               TenantScoped
  id                    serial PK
  tenant_id             FK tenants
  identity_provider_id  (tenant_id, identity_provider_id) → identity_providers, ON DELETE CASCADE
  user_id               FK users, ON DELETE CASCADE
  external_subject      the provider's `sub`; NULL while an invitation is pending
  email                 as last asserted (lower case), for display and invitations
  display_name
  created_via           'jit' | 'invite' | 'link'
  created_at, last_login_at
  UNIQUE (tenant_id, identity_provider_id, external_subject)
  UNIQUE (tenant_id, identity_provider_id, user_id)

sso_flows                     TenantScoped, rows live ten minutes
  state_hash            UNIQUE, installation-wide; HMAC of the state value
  tenant_id, identity_provider_id (composite FK, ON DELETE CASCADE)
  purpose               'login' | 'test' | 'link'
  browser_hash          HMAC of the flow cookie
  nonce, code_verifier
  user_id               the signed-in user for 'test' and 'link', else NULL
  next_path             validated relative path, else NULL
  created_at, consumed_at

sessions
  + identity_provider_id   nullable, FK identity_providers ON DELETE CASCADE
users
  auth_source gains the value 'oidc'; username widened from 64 to 255
```

Decisions:

1. **One row per provider, per tenant, with `tenant_id NOT NULL`.** A provider
   is tenant-owned data like an integration config. It gets the tenant filter,
   the composite parent key and the standard `tenant_isolation` policy, so in
   tenant A's context B's providers cannot even be read. Several providers per
   tenant are allowed (a company with staff in Entra and contractors in
   Google); nothing in the model needs "exactly one".
2. **The link is `user_identities`, not columns on `users`.** A user is global
   and may be linked to more than one provider; the identity is the pair
   `(identity_provider_id, external_subject)` from the multi-tenancy design.
   The mail address is stored for display and invitations and is **never** a
   lookup key at sign-in. `sub` is the only thing a provider cannot change
   under a person.
3. **The client secret is encrypted with the tenant's data key**
   (`services.tenant_keys.encrypt_secret`, format `v2:<key_id>:…`), not with
   the installation key. It is a tenant's secret; a purge must shred it, and a
   key rotation must re-encrypt it. `tenant_keys._secret_rows` gains
   `identity_providers.client_secret`, so `rotate_tenant_key`,
   `reencrypt_legacy` and the `verify` scan cover it. The API never returns
   it; the form shows "set" and takes a new value.
4. **Two columns stay unique across the installation:** `login_key` and
   `state_hash`. Both are looked up before the tenant is known, and the row
   found names it. They are added to `GLOBAL_UNIQUE_COLUMNS` with that reason,
   and the lookups run in a small `system_scope("sso: …")` followed by
   `tenant_scope(row.tenant_id)`, the shape agent tokens and API keys use.
5. **Flow state is a database row, not a signed cookie.** A row can be consumed
   exactly once (`UPDATE … SET consumed_at WHERE consumed_at IS NULL
   RETURNING`), which is the replay protection, and it works with several
   backend workers without Redis. The verifier is worthless without the code
   and the client secret and lives ten minutes, so it is stored plain. A
   scheduled job deletes rows older than an hour.
6. **`sessions.identity_provider_id`** records how a session was opened. It is
   what confines the session to one tenant (see "Session"), and the cascade
   means deleting a provider ends its sessions.
7. **Usernames of SSO users.** `users.username` is globally unique because
   local sign-in is by username before a tenant is known. An SSO user never
   types it, but it is shown in user lists and written to the audit log, so it
   must be readable and must not collide. It is `<mail address>#<provider id>`,
   for example `jdoe@acme.ch#7` (falling back to `preferred_username`, then
   `sub`). The suffix makes the namespace disjoint by construction: a tenant
   admin who points a provider at an issuer of their own cannot use username
   collisions to find out which names exist in other tenants. The core refuses
   new local usernames ending in `#<digits>`. The column grows to 255 because a
   mail address does not fit in 64.

### Revision, row-level security

- **Revision 052** (`052_identity_providers.py`, down-revision 051; the number
  is taken when the PR is written, `tests/test_alembic_heads.py` decides). It
  creates the three tables, the two unique indexes, the composite keys, adds
  `sessions.identity_provider_id` and widens `users.username`. Guards as in
  034/039, because fresh installs build the schema with `create_all`.
- **RLS:** nothing special. The three tables are `TenantScoped`, so
  `pg_rls.ensure_policies` gives them the standard policy and `FORCE ROW LEVEL
  SECURITY` like every tenant table, and `tests/test_tenancy_classification.py`
  and `tests/test_pg_rls.py` cover them without new cases beyond the two
  global-unique columns. The app role keeps full DML on them (they are not
  append-only).

### Purge, backup, export

- **Purge** (`ee/backend/nodeglow_ee/tenancy/purge.py`). The purge derives its
  table list from `tenant_tables()`, children before parents, so the three
  tables are deleted without a change there. Step 1 of the purge
  (crypto-shred) already makes the client secret unreadable before any row is
  deleted. `_purge_identities` already removes the sessions working in the
  tenant and the users who belong to no other tenant; a JIT user is exactly
  such a user. One addition: sessions with `identity_provider_id` of that
  tenant are deleted, not only detached, before the provider rows go (the
  cascade would do it, the explicit delete makes the order independent of it).
- **Suspended tenant.** `tenant_is_active` is checked at the start and at the
  callback; a suspended tenant's sign-in address answers like an unknown one.
- **Backup / restore** (`services/backup.py`). `identity_providers` and
  `user_identities` are added to `EXPORT_TABLES` after `users` and
  `tenant_keys`; `sso_flows` and sessions are not exported. The secret
  travels as ciphertext under the tenant's wrapped key, like integration
  configs. After a restore onto another address the redirect URI has changed;
  the settings page shows the URI to register and the provider's test fails
  with a clear message until it is updated at the provider.
- **Per-tenant export** (phase 12, not built). Providers and identities are
  tenant-owned rows and go into the archive with the others; the client secret
  is decrypted into the passphrase-encrypted archive like every other secret.
  On import the identities are remapped with the users. Recorded here so
  phase 12 does not have to rediscover it.
- **Deleting a provider** removes its identities and sessions (cascade). Users
  with `auth_source = 'oidc'` who are left without any identity lose their
  membership in this tenant, and are deleted if they have no other membership
  (the rule `routers/users.py:_remove_from_tenant` applies today). The
  confirmation names the number of people affected.

### A provider-level one, later

An MSP that wants its own staff to sign in through its own IdP across all its
tenants needs a row that belongs to a *provider*, not to a tenant. That must
not be a tenant-scoped row with a NULL tenant: a NULL would have to be let
through the RLS policy, and the policy is the net under everything. It becomes
its own global table (`provider_identity_providers`, read before a tenant is
known, like `provider_memberships`) reusing the flow code, with roles mapped to
provider roles. `user_identities` would gain a nullable reference to it. Not
built now; nothing in this design blocks it.

## The OIDC flow

Authorization code flow with PKCE (S256), `state` and `nonce`, as a
confidential client. No implicit or hybrid flow, `response_mode=query`.

```
browser                     nodeglow (ee)                         provider
   │ GET /api/auth/sso/start?key=…   │                                │
   │────────────────────────────────▶│ find provider by login_key     │
   │                                 │ create sso_flows row           │
   │ 302 + flow cookie               │                                │
   │◀────────────────────────────────│                                │
   │ GET authorization_endpoint?client_id&redirect_uri&state&nonce&code_challenge
   │─────────────────────────────────────────────────────────────────▶│
   │ 302 redirect_uri?code&state     │                                │
   │◀─────────────────────────────────────────────────────────────────│
   │ GET /api/auth/sso/callback      │                                │
   │────────────────────────────────▶│ consume flow (state + cookie)  │
   │                                 │ POST token_endpoint (code, verifier, secret)
   │                                 │───────────────────────────────▶│
   │                                 │ validate id_token              │
   │                                 │ resolve identity, role         │
   │ 303 /login/sso/done + session   │ open session in ONE tenant     │
   │◀────────────────────────────────│                                │
```

### State, bound to browser, tenant and provider

- `state` is 32 random bytes; only its HMAC is stored (`state_hash`). The row
  carries the tenant, the provider, the purpose, the nonce and the verifier.
  The callback takes **nothing** about tenant or provider from the request:
  no query parameter, no header, no host name. Whatever the callback URL says,
  the flow row decides.
- The start sets a flow cookie (`ng_sso`, 32 random bytes, `HttpOnly`,
  `Secure` as the session cookie decides it, `SameSite=Lax`, `Path=/api/auth/sso`,
  ten minutes). The row stores its HMAC. A callback without the matching
  cookie is refused. That binds the flow to the browser that started it: a
  callback link sent to a victim (to sign them in to the attacker's account)
  does nothing in the victim's browser. `Lax`, not `Strict`: the callback is
  a top-level navigation coming from the provider's site, and a `Strict`
  cookie would not be sent with it.
- A flow is consumed atomically before the token request. A second callback
  with the same state finds no open flow.
- Flows older than ten minutes are refused.

### Discovery and keys

- The admin enters the **issuer**. Discovery is
  `<issuer>/.well-known/openid-configuration`. The document's `issuer` must
  equal the configured one character for character; `authorization_endpoint`,
  `token_endpoint` and `jwks_uri` must be `https`.
- Fetched on save, on test, and when the cached copy is older than 24 hours;
  the last document that passed the checks is kept in `discovery_json`, so a
  provider that is briefly unreachable does not stop sign-ins that only need
  the authorization endpoint.
- JWKS: cached in the process per provider for one hour. An id token with an
  unknown `kid` triggers one refetch, at most once a minute per provider (key
  rotation without waiting an hour, and no fetch storm from forged tokens).
- All outbound requests (discovery, JWKS, token, userinfo) go through
  `utils.net_safety.safe_async_client`: address checked at connect time, no
  redirects followed, five second timeout, one megabyte response cap, TLS
  verification always on. A private CA is pasted into the provider form
  (`ca_pem`), not dropped into the container.

### Id token checks (all mandatory)

| Check | Rule |
|---|---|
| Signature algorithm | Allow-list `RS256`, `PS256`, `ES256`. `none` and every `HS*` are refused before any key is looked at; the key's type must match the algorithm. |
| Signature | Against the key with the token's `kid` from the provider's JWKS. |
| `iss` | Equals the configured issuer exactly. |
| `aud` | Contains the provider's `client_id`; with several audiences `azp` must equal it. |
| `exp`, `iat` | `exp` in the future, `iat` not more than ten minutes old or ahead; 60 seconds leeway. |
| `nonce` | Equals the flow's nonce. |
| `sub` | Present, at most 255 characters. |

Also: if the callback carries an `iss` parameter (RFC 9207), it must equal the
provider's issuer. If the id token lacks `email` or the groups claim, userinfo
is called once and its `sub` must equal the id token's. Tokens are used for
this one validation and then dropped; no access, refresh or id token is stored.

Verification uses **PyJWT** (with the already pinned `cryptography`), called
with an explicit algorithm list, and our own guarded JWKS fetch instead of its
`PyJWKClient` (which would bypass the outbound checks). Owner decision 5.

### Redirect URI behind a proxy

One redirect URI for the whole installation:
`<public_url>/api/auth/sso/callback`.

- It is built from the installation setting `public_url` and **never from
  request headers**. `routers/agents.py:resolve_server_url` falls back to
  `X-Forwarded-Host` when `agent_server_url` is empty; that is right for an
  installer link and wrong here, where a forged `Host` header would put an
  attacker's address into an authorization request.
- Without `public_url` a provider cannot be saved as enabled. The settings
  page says so and offers what the proxy reports as a suggestion to confirm;
  the admin (operator) saves it once. An `http://` address works but shows a
  warning, as unencrypted LDAP does today.
- The page shows the exact URI to register at the provider, with a copy button.
- The path is under `/api/`, which `frontend/next.config.mjs` already forwards.

### Outbound checks for the issuer

`net_safety` allows RFC 1918 addresses on purpose (nodeglow monitors LANs) and
blocks loopback, link-local, metadata endpoints and the compose services. For
an issuer that is the right rule on an on-prem installation, where authentik
or Keycloak sits in the LAN, and the wrong one where strangers share a
backend: a tenant could make the installation fetch
`https://10.0.0.5/.well-known/…` inside the operator's network. So:

- providers of the **default tenant** may use private addresses (it is the
  operator's own network);
- providers of **other tenants** must resolve to public addresses, unless the
  operator turns on the installation setting "identity providers on private
  addresses" (Settings, operator only);
- error messages to the tenant admin are categories ("cannot connect", "TLS
  certificate not trusted", "not an OIDC discovery document", "issuer does not
  match"), never response bodies, so the test cannot be used to read internal
  services.

## Finding the provider on the sign-in page

| Option | Verdict |
|---|---|
| Tenant picker | Rejected. A list of tenants on an anonymous page is exactly the leak to avoid. |
| Mail domain ("enter your address") | Later. It needs domains that are unique across tenants and *proven* (DNS record), otherwise tenant B claims `acme.ch` and A's staff are sent to B's provider. Local users also have no mail address today. |
| Tenant-specific sign-in address | **First version.** |

How it works:

- Each provider has a `login_key`, unique in the installation, and a sign-in
  address `<public_url>/login/sso/<login_key>`. The default key is the tenant's
  short name plus six random characters (`xdigits-k3v9qa`); the tenant admin
  may change it to something they prefer. The admin hands the address to their
  people, or sets it as the application's launch URL in the provider's portal
  (authentik application, Infomaniak, Entra "home page URL").
- The page at that address shows "Sign in with <name>" and starts the flow.
- After a successful sign-in the browser keeps the key in a cookie
  (`ng_sso_hint`, 180 days, like the device cookie). The ordinary `/login` page
  in that browser then shows the same button above the password form.
- An installation with **exactly one tenant** shows the default tenant's
  enabled providers on `/login` directly. There is nothing to hide: the page
  already belongs to one organisation.

What an anonymous visitor can learn: nothing by listing, because no endpoint
enumerates. `GET /api/auth/sso/options?key=…` answers 200 with the same shape
for an unknown, disabled, unlicensed or suspended key (an empty list) and is
rate limited per address like the login. A key can be confirmed only by
guessing it, the default has about 31 bits of randomness on top of the name,
and what a correct guess reveals is the provider's display name and issuer.
An admin who picks a short memorable key accepts that trade knowingly; the
form says so.

## First sign-in

After the id token passed, the identity is resolved in this order. Every
refusal looks the same to the person ("Sign-in did not complete. Reference
K7Q2…") and is recorded with its real reason for the tenant admin.

1. **Known identity**: a `user_identities` row with this provider and `sub`.
   Sign in as that user. (Restrictions below are still evaluated: a person
   removed from the required group is refused.)
2. **Pending invitation**: a row for this provider with this mail address and
   no `sub` yet. Requires `email_verified = true`. The `sub` is written into
   the row; from then on rule 1 applies and the mail address no longer matters.
3. **Just-in-time provisioning**, if `jit_enabled`: a new user
   (`auth_source = 'oidc'`, random placeholder password hash as for LDAP
   users, username as described above), an identity row, and a membership in
   this tenant.
4. Otherwise refused ("not invited").

Restrictions, evaluated for every sign-in before any of the above writes:

| Setting | Rule |
|---|---|
| `allowed_domains` | The `email` claim's domain is in the list **and** `email_verified` is true. An address the provider does not vouch for is not an address. |
| `required_groups` | The groups claim contains at least one of them. |
| neither set | Everyone the issuer authenticates gets in (with JIT) or everyone invited (without). |

A provider cannot be enabled with JIT on and neither restriction set unless
the admin ticks "this issuer only authenticates people of my organisation".
That is true for an Entra tenant, an authentik or a Keycloak realm of one
company. It is false for Infomaniak, Google or any shared issuer, where every
account in the world authenticates, and the checkbox's text says so.

**Role of a new user:** the result of the group mapping if rules exist, else
`default_role`. `default_role` can be `readonly`, `editor` or `none` (no
access without a matching rule) and never `admin`: nobody becomes admin
because a setting was left on a default. Admins come from an explicit rule or
from an admin changing the role.

**An existing account is never linked silently.** A local user has no mail
address, so there is nothing to match on, and the design keeps it that way: a
provider asserting `admin` or `jdoe@acme.ch` creates a *new* user with a
disjoint username, or is refused. It never resolves to an existing one. This
is the lesson already written into `_try_ldap_login` ("whoever controls the
directory entry `admin` became nodeglow's local admin").

**Linking deliberately** has two forms:

- *By the person* (existing local account): signed in with their password,
  under Account, "Connect single sign-on". That starts a flow with purpose
  `link`; the callback writes an identity row for the **signed-in user** from
  the flow row, and requires the session that started it. The person has
  proven both sides. They can disconnect it again while they still have a
  password.
- *By the admin* (new person): "Invite" with a mail address and a role creates
  the user and a pending identity (rule 2). This is also how a tenant works
  with JIT off.

An admin cannot attach a provider identity to somebody else's existing
account. With the session confinement below that would not reach beyond the
tenant anyway, but there is no need for it and it is the shape account
takeover has.

## Role mapping from groups

- `role_rules` is a list of `{group, role}`; the group is compared as an exact,
  case-sensitive string against the entries of the groups claim (name
  configurable, default `groups`; a string claim counts as a one-element
  list). If several rules match, the highest role wins.
- **Rules exist:** the provider is authoritative. The membership role is set
  from the rules at every sign-in, as the core LDAP does today. A change made
  by hand in the users page lasts until the next sign-in, and the page says so
  for such users.
- **No rules:** the role is `default_role` at creation and is managed in
  nodeglow afterwards. Sign-ins do not touch it.
- **No rule matches:** `default_role`; with `none` the membership is revoked
  and the sign-in refused.
- A sync never demotes the tenant's last admin (`memberships.admin_count`):
  the role is kept and an audit entry says why.
- Every change is audited as `user.role_change` with the provider as the cause.

### When things change at the provider

There is no channel from the provider to nodeglow in this version, so changes
arrive at the next sign-in:

| At the provider | In nodeglow |
|---|---|
| Groups change | Role changes at the next sign-in. |
| Person removed or disabled | No new sign-in. The running session ends at its expiry. |
| Person removed from the required group | Refused at the next sign-in; the membership is revoked then. |

That is why SSO sessions are short: `session_hours` per provider, default
**12**, range 1 to 168 (local sessions are seven days). For an immediate
removal the tenant admin removes the user in nodeglow, which deletes their
sessions today. SCIM and back-channel logout close this window later.

## Session

`_succeed` in `routers/auth.py` moves into a small core helper
(`services/sessions.py:open_session`) that the password login and the `ee`
callback both use: resolve nothing itself, write the `sessions` row for a
given tenant, audit `auth.login`, set the cookie.

- **Confined to one tenant.** A session with `identity_provider_id` set can
  act only in that provider's tenant. `MultiTenancy.resolve_access` returns
  nothing else for it, so the header, `POST /api/auth/tenant` and the tenant
  list of `/api/auth/me` cannot take it elsewhere (404, as for any foreign
  tenant). Support grants and other memberships of the same user do not apply
  to such a session. This is the rule that makes a tenant admin's control over
  their own issuer harmless to everyone else.
- **Exception: the default tenant.** Its admins are the installation's owners
  and only they can configure its providers, so a session through one of them
  is an ordinary session: other memberships, support access and operator
  rights apply as for a password login. A company running one tenant can
  therefore do everything through SSO. A session through any *other* tenant's
  provider never carries operator rights, whatever flags the linked user has.
- **Session cookie.** Unchanged (`nodeglow_session`, `SameSite=Strict`). The
  callback answers with a redirect to the static page `/login/sso/done`,
  which calls `/api/auth/me` and then navigates to the validated `next` path.
  A `Strict` cookie is not sent on the redirect that follows a cross-site
  navigation, so the callback must not redirect straight into a page that
  needs the session. To be confirmed in real browsers in the flow PR.
- **Open redirects.** `next` is accepted at the start only as a relative path
  that passes the same check as `frontend/src/lib/redirect.ts:safeNextPath`
  (starts with one `/`, no scheme, no backslash), stored in the flow row, and
  never read from the callback.

## Interaction with what exists

| Existing piece | Behaviour with SSO |
|---|---|
| **Two-factor (TOTP)** | Not asked after an SSO sign-in. The second factor is the provider's job, and a JIT user has no TOTP here. A linked local account keeps its TOTP for password sign-ins. Not configurable per provider in the first version (owner decision 2). |
| **Lockout / throttling** | The per-username soft lock does not apply (no username, no password). Start, options and callback are rate limited per address; failed callbacks count into `failed_auth_throttled`. SSO failures never lock a local account. |
| **Local password of an SSO user** | `auth_source = 'oidc'` users have a placeholder hash; the password login refuses them explicitly, as it does for `ldap`. |
| **API keys** | Unchanged: a key belongs to a tenant, not to a person or a session. Removing a person at the provider does not revoke keys they created. Same as for local users today; named here so nobody assumes otherwise. |
| **Support access** | Unchanged. Operators sign in locally or through the default tenant's provider and enter a tenant through a grant (`via = support`). "SSO required" does not apply to them: they are not members. |
| **Installation-wide LDAP** | Unchanged, default tenant only. LDAP and OIDC can both be on; they never share accounts (`auth_source` differs, usernames are disjoint). |
| **Provider is down** | Running sessions continue. New sign-ins through it fail with a clear message; local accounts keep working unless "SSO required" is on, in which case the break-glass accounts do. |
| **"SSO required"** (per tenant, stored on its providers) | Password sign-ins into this tenant are refused after the password check ("This organisation signs in with single sign-on"), and a password session of a member of several tenants cannot switch into it. Exempt: the tenant's **break-glass accounts**, local admins the tenant admin marks for this, each of which must have TOTP on. Turning it on needs a successful test sign-in with the current configuration and at least one break-glass account. Not enforced while the licence does not make the feature active, so a lapsed licence cannot lock a tenant out. |
| **Audit log** | New actions in the tenant's log: `idp.create`, `idp.update`, `idp.delete`, `idp.enable`, `idp.disable`, `idp.test`, `auth.login` with `method = oidc` and the provider, `auth.sso_failed` with a reason code, `user.sso_provisioned`, `user.sso_linked`, `user.sso_unlinked`. |

## Core versus ee

| Core (AGPL) | `ee/` (`nodeglow_ee.sso`) |
|---|---|
| Models and revision 052; the two global-unique columns; `tenant_keys._secret_rows` entry; `EXPORT_TABLES` entries | Provider administration API (`/api/identity-providers`), validation, discovery and JWKS client |
| `services/sessions.py:open_session`; `get_current_user` puts `identity_provider_id` on `request.state` | The flow: `/api/auth/sso/options`, `/start`, `/callback`; id token validation; identity resolution, JIT, invitations, linking, role mapping |
| A sign-in policy hook in `extensions.Registry` (`set_sign_in_policy`): the password login asks it whether this user may open a password session in this tenant. Without a plugin: always yes | The policy: "SSO required" and break-glass accounts. Session confinement in `MultiTenancy.resolve_access` and `is_operator` |
| Feature flag `sso` in `DEFAULT_FEATURES` (false) | Licence feature `sso` in `KNOWN_FEATURES`, cleanup job for `sso_flows` |
| Password login refuses `auth_source = 'oidc'`; user creation refuses usernames ending in `#<digits>` | |
| All frontend code (as the ee README states): sign-in page, settings section, account section | |

- **Why the routes can live under `/api/auth/`.** That prefix skips the
  middleware's session and CSRF checks, which is right for the three anonymous
  flow routes and is where a reader expects them. The administration API is
  not under it: it needs the session, CSRF and the admin role.
- **Licence.** A feature of its own, `sso`, not part of `multi_tenancy`: the
  most common buyer is a company with one tenant that wants its staff to sign
  in with the company account. Gates: administration endpoints answer 402
  through `license_runtime.blocked("sso")`; the flow routes answer as for an
  unknown key. After the licence **expires**, existing providers keep signing
  people in and the administration becomes read-only, the same principle as
  for tenants (monitoring and access do not stop because a licence lapsed).
  With a **missing or invalid** licence nothing is active.
- **A core-only installation** has the three tables, empty, and no route that
  reads them. The sign-in page asks `GET /api/v2/features`, sees `sso: false`
  and renders exactly today's form. Settings, Authentication shows the LDAP
  section and one sentence: "Single sign-on with OpenID Connect per tenant is
  part of Nodeglow Enterprise." `tests/test_ee_boundary.py` keeps the core
  from importing any of it.

## Threats and countermeasures

| Threat | Countermeasure |
|---|---|
| A user of tenant A enters through the provider of tenant B | Identities are keyed by `(provider, sub)` and the provider belongs to one tenant. A session opened through a provider is confined to that tenant by `sessions.identity_provider_id`. Nothing in the callback request names a tenant. |
| Account takeover through an unverified or reused mail address | The mail address is never a lookup key for an existing account. It counts only for a pending invitation and for `allowed_domains`, and only with `email_verified = true`. After the first sign-in only `sub` identifies the person, so a reassigned address does not inherit an account. |
| Redirect or state manipulation moves the callback to another tenant | Tenant and provider come from the `sso_flows` row found by the state's HMAC. One fixed redirect URI built from `public_url`. The `iss` response parameter is checked when present. The code is redeemed only at the token endpoint of the flow's provider. |
| A callback link planted in a victim's browser (sign-in as the attacker) | Flow cookie bound to the row; a callback without it is refused. |
| Malicious or compromised issuer URL: SSRF | `safe_async_client` at connect time, no redirects, size and time limits, `https` only; private addresses refused for non-default tenants; error categories instead of response bodies. |
| Malicious or compromised issuer: key substitution | The issuer is fixed by the tenant admin, the discovery document must state the same issuer, keys come only from that document's `jwks_uri` over verified TLS, algorithm allow-list with key-type match, no `none`, no `HS*` (no confusion of the client secret or a public key with an HMAC key). What remains is inherent: whoever controls the issuer controls who signs in to *that* tenant. |
| Replay of a callback | Flow consumed atomically and once; ten-minute lifetime; nonce checked; codes are single-use at the provider. |
| A tenant admin points the provider at an issuer they control | Inside their own tenant: accepted. They are admin there already and gain nothing. Anywhere else: not possible. Session confinement, no operator rights from a non-default tenant's provider, no linking to existing accounts by an admin, disjoint usernames, default role never admin. |
| Open redirect after sign-in | `next` validated as a relative path at the start, stored server-side, never taken from the callback. Error redirects go to fixed paths with a code, not a message. |
| Log lines and error messages leak tokens or secrets | Codes, tokens, the verifier and the client secret are never logged; outbound errors are logged as category plus host. The provider's `error_description` is cut to 200 printable characters before it is stored. The API never returns the client secret. The test page shows claims, not the raw token. |
| Enumeration of tenants | No listing endpoint; uniform answers for unknown keys; rate limits; default keys carry a random part. |
| The audit log flooded by anonymous failures | `auth.sso_failed` is written only for a flow that was started and came back (state and cookie matched); everything else is counted in metrics. Starts are rate limited per address. |
| Lock-out through misconfiguration | Test sign-in changes nothing; enabling needs a passed test of the current configuration; "SSO required" needs a break-glass account; operators can still enter through support access. |

## Administration UI

Settings, **Authentication** (`frontend/src/components/settings/AuthTab.tsx`).
The tab gets a section "Single sign-on" above LDAP. It is the tenant's own
setting, visible to the tenant's admins; the LDAP section stays where the
operator sees it.

- **List** of the tenant's providers: name, issuer, state (draft, tested,
  enabled), last sign-in, last error.
- **Form:** name, issuer, client id, client secret, scopes, name of the groups
  claim, optional CA certificate. Next to it, read-only with copy buttons: the
  redirect URI to register and the tenant's sign-in address. "Check
  connection" fetches the discovery document and the keys and reports what it
  found (endpoints, algorithms, whether the claims the form relies on are
  listed).
- **Who may sign in:** create accounts on first sign-in (on or off); allowed
  mail domains; required groups; the "only my organisation" confirmation;
  default role.
- **Roles from groups:** a table of group and role, with the note that rules
  make the provider authoritative.
- **Test sign-in.** Available for a provider in any state, including a draft.
  It opens the provider in a new tab with purpose `test`; the flow runs to the
  end and stops before identity resolution. It creates no user, no identity
  and no session, and does not touch the admin's own session or account. The
  result page shows the claims received (`sub`, `email`, `email_verified`,
  name, groups) and the decision the current settings would make: allowed or
  refused and why, new or known account, resulting role. A pass records
  `tested_hash`; any later change to issuer, client, scopes or claim name makes
  the provider "untested" again, and it cannot be enabled or stay "SSO
  required" in that state.
- **Enable / disable.** Disabling ends the provider's sessions after a
  confirmation that names how many.
- **Last sign-in errors:** the last 20 `auth.sso_failed` entries for this
  provider from the tenant's audit log: time, reference (the one the person
  saw), reason in words ("mail domain not allowed", "not in a required group",
  "id token: audience does not match", "provider answered: access_denied",
  "token endpoint not reachable"), and the mail address if the failure
  happened after the token was validated. No tokens, no secrets.
- **Require single sign-on:** the switch, with the break-glass accounts picked
  underneath.
- **Users page:** SSO users show their provider and mail address; "Invite"
  appears when the tenant has a provider. **Account page:** "Connect single
  sign-on" for local users of a tenant with a provider.

## Testing

**Without a real provider.** A fake issuer in the test suite
(`ee/backend/tests/sso/fake_issuer.py`): an in-process ASGI app with a
generated RSA and EC key that serves discovery, JWKS, an authorization
endpoint that redirects back at once, token and userinfo. The `ee` code
obtains its HTTP client from one factory, which the tests point at the fake
through an ASGI transport, so no socket is opened. Each test configures what
the fake does wrong.

- Flow: happy path for login, test and link; state unknown, reused, expired;
  missing or foreign flow cookie; callback naming another tenant in header or
  query (ignored); `next` variants.
- Token: wrong issuer, wrong audience, `azp` mismatch, expired, future `iat`,
  wrong nonce, `alg: none`, `HS256` signed with the client secret and with the
  public key, key type not matching the algorithm, unknown `kid` then rotated
  key, tampered payload, userinfo with a different `sub`.
- Identity: JIT on and off, domain allowed with and without `email_verified`,
  required groups, invitation bound once, a name equal to an existing local
  username creates a separate user, link requires the starting session.
- Roles: highest rule wins, no match with `none`, last admin kept, rules
  absent leave the role alone.
- Isolation, in the style of the tenant-isolation suite: a session opened
  through tenant B's provider gets 404 for tenant A with header, switch and
  WebSocket parameter, and no operator rights; providers of A invisible in
  B's context (SQLite filter and, with `NODEGLOW_PG_TEST_URL`, RLS).
- Outbound: issuer on a blocked address, private address for a non-default
  tenant, redirecting discovery, oversized response.
- Lifecycle: purge removes providers, identities, flows, sessions and JIT
  users; key rotation re-encrypts the client secret; backup round trip.
- Licence: missing, valid, expired (sign-in continues, changes refused).
- Core: suite passes with `NODEGLOW_DISABLE_EE=1`; revision 052 up and down on
  PostgreSQL.
- Frontend: sign-in page with and without hint cookie and features flag,
  the done page, settings form (vitest). The repo has no browser test runner,
  so the `SameSite` behaviour of the real redirect chain is checked by hand in
  Chrome, Firefox and Safari against authentik and recorded in the flow PR.

**Against authentik** (Julian's instance, on the dev installation): create an
OAuth2/OpenID provider and application there, register the redirect URI, set
the launch URL to the sign-in address. Walk through: check connection, test
sign-in, enable, JIT with a required group, role rules with two groups, group
change and sign in again, disable the user in authentik, link an existing
local account, "SSO required" with a break-glass account and authentik
stopped, key rotation in authentik.

**Against Infomaniak**, after authentik passes: register an application in
the Infomaniak manager, run "check connection" and the test sign-in with an
xdigits account and with a private Infomaniak account. The test page answers
the two open points without writing code:

1. *What does `groups` contain?* Names, ids, team memberships of the
   organisation, or nothing. If it is usable, `required_groups` and role rules
   work as with authentik. If it is empty or unstable, the xdigits tenant uses
   `allowed_domains` (the company domain, relying on `email_verified`) or
   invitations with JIT off, and roles are managed in nodeglow.
2. *Can sign-in be limited to one organisation at Infomaniak?* If the
   application can be restricted there, good, but nodeglow does not rely on
   it: the tenant-side restriction is mandatory for a shared issuer (the
   enable check above), and the private account must be refused in the test.

**Security review.** Independent, of the finished flow (after the last feature
PR), against the threat table. Sign-in stays unreleased until it is done.

## Implementation plan

| PR | Contains | Deployable afterwards? |
|---|---|---|
| **1. Data model and administration** | Core: models, revision 052, global-unique columns, `_secret_rows`, `EXPORT_TABLES`, feature flag, username rule. `ee`: `nodeglow_ee.sso` with provider CRUD, validation, discovery/JWKS client with outbound rules, "check connection", licence gate, purge addition. Frontend: the settings section without test and enable. | Yes. Providers can be drafted; no flow route exists, nothing anonymous is reachable. |
| **2. The flow** | `sso_flows` handling, `/start` and `/callback`, id token validation, the fake issuer and the token and flow tests, the **test sign-in** end to end with its result page. Only purpose `test` is accepted, and only from a signed-in admin. | Yes. The callback can end in a result page for an admin, never in a session. This is the state to try against authentik and Infomaniak. |
| **3. Sign-in** | Core: `open_session`, sign-in policy hook, `request.state` field, password login refusing `oidc`. `ee`: purpose `login` and `link`, identity resolution, JIT, restrictions, invitations, session confinement, session lifetime, "SSO required" with break-glass, enable/disable. Frontend: sign-in address page, hint cookie, done page, account and users pages. Released behind `SIGN_IN_RELEASED = False` in `nodeglow_ee.sso`: with it off, purpose `login` and `link` are refused and "enable" is hidden. | Yes, inert: the constant keeps sign-in off whatever the licence says (a `*` licence would otherwise switch it on). |
| **4. Roles from groups** | `role_rules`, `required_groups` evaluation at every sign-in, last-admin guard, the "last sign-in errors" list, audit entries for role changes. | Yes, still inert. |
| **Release** (small) | Fixes from the security review, `SIGN_IN_RELEASED = True`, user documentation, the ee README row. | This is the deployment that turns sign-in on. |

Each PR states what was tested and what is not covered, and runs the backend
suite with and without `ee/`.

## Owner decisions

Decided by the owner on 2026-11-14, each as recommended in the first version
of this document.

| # | Question | Decision |
|---|---|---|
| 1 | How does the sign-in page find the provider? | A sign-in address per tenant (`/login/sso/<key>`) plus the hint cookie. Mail-domain discovery comes later, together with domain verification. |
| 2 | Does nodeglow's own second factor apply after SSO? | No, and not configurable for now. The provider enforces the second factor. A per-provider switch can follow if a customer asks. |
| 3 | Default lifetime of an SSO session? | 12 hours, adjustable per provider from 1 hour to 7 days. |
| 4 | How is the feature licensed? | Its own licence feature `sso`, independent of `multi_tenancy`, usable on a one-tenant installation. |
| 5 | New dependency for id token verification? | PyJWT, pinned, on top of the `cryptography` already in use, called with an explicit algorithm list and fed by our own guarded JWKS fetch. |
| 6 | Do providers of the default tenant give ordinary sessions (other tenants, support access, operator rights)? | Yes. Sessions through any other tenant's provider stay confined to that tenant. |
| 7 | Private issuer addresses for non-default tenants? | Refused unless the operator turns on the installation setting; default off. |
| 8 | Does sign-in continue after the licence expires? | Yes; the administration becomes read-only, as for tenants. |
