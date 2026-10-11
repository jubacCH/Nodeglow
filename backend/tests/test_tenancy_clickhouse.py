"""ClickHouse tenant isolation (multi-tenancy phase 6), without a server.

* query() and every helper fail closed without a context, refuse a tenant-table
  read without the tenant predicate, bind the tenant, and send the server-side
  filters (additional_table_filters; the row-policy setting when the restricted
  user is configured).
* Inserts take tenant_id from the row and refuse rows without one; every
  producer stamps it (syslog, agents, probes, ping job, bandwidth).
* A static scan: every SQL string in the core and ee that reads a tenant table
  carries the predicate (the runtime guard only fires on the paths a test
  happens to execute).

What real ClickHouse does with all of this is tests/test_live_clickhouse.py.
"""
from __future__ import annotations

import ast
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import clickhouse_client as ch
from services.tenancy import TenantContextError, system_scope, tenant_scope

A, B = 3, 4
ROOT = Path(__file__).resolve().parents[2]


class _Result:
    def __init__(self, rows=None, columns=("v",)):
        self.result_rows = rows or []
        self.column_names = list(columns)


class FakeClient:
    """Records query/insert/command calls like clickhouse-connect's async client."""

    def __init__(self, name="main", rows=None, columns=("v",)):
        self.name = name
        self.queries: list[dict] = []
        self.inserts: list[tuple] = []
        self.commands: list[tuple] = []
        self._rows = rows
        self._columns = columns

    async def query(self, sql, parameters=None, settings=None):
        self.queries.append({"sql": sql, "params": dict(parameters or {}), "settings": settings})
        return _Result(self._rows, self._columns)

    async def insert(self, table, data, column_names=None):
        self.inserts.append((table, data, column_names))

    async def command(self, cmd, parameters=None, settings=None):
        self.commands.append((cmd, dict(parameters or {})))
        return 0


@pytest.fixture
def main_client(monkeypatch):
    client = FakeClient("main")

    async def _get():
        return client
    monkeypatch.setattr(ch, "get_client", _get)
    monkeypatch.setattr(ch, "CLICKHOUSE_TENANT_URL", "")
    return client


# ── query(): fail closed, predicate guard, tenant binding ────────────────────


async def test_query_without_context_raises(main_client):
    for call in (
        lambda: ch.query("SELECT 1"),
        lambda: ch.query_scalar("SELECT 1"),
        lambda: ch.query_chunked("SELECT 1 WHERE x IN {v:Array(UInt32)}", "v", [1]),
    ):
        with pytest.raises(TenantContextError):
            await call()
    assert main_client.queries == []


async def test_helpers_without_context_raise(main_client):
    since = datetime(2026, 10, 1)
    helpers = [
        lambda: ch.get_latest_ping_per_host([1]),
        lambda: ch.get_ping_uptime([1]),
        lambda: ch.get_ping_history(1),
        lambda: ch.get_ping_status_transitions(1),
        lambda: ch.count_syslog_by_host([1], since),
        lambda: ch.count_syslog_received_by_source(since),
        lambda: ch.count_syslog_received_by_host(since, [1]),
        lambda: ch.get_syslog_events_for_host(1, "", "", since),
        lambda: ch.get_offline_hosts_since([1], 3),
        lambda: ch.get_latest_agent_metrics([1]),
        lambda: ch.get_agent_history(1),
        lambda: ch.get_latest_bandwidth_per_iface(),
        lambda: ch.get_bandwidth_history_ch(),
        lambda: ch.get_previous_bandwidth_sample("agent", "1", "eth0"),
        lambda: ch.enforce_retention("ping_checks", 3),
    ]
    for helper in helpers:
        with pytest.raises(TenantContextError):
            await helper()
    with pytest.raises(TenantContextError):
        ch._where_clauses(since)
    assert main_client.queries == []


async def test_tenant_read_without_predicate_is_refused(main_client):
    with tenant_scope(A):
        for sql in (
            "SELECT count() FROM syslog_messages",
            "SELECT count() FROM nodeglow.ping_checks WHERE host_id = 1",
            # Two reads, one predicate: the subquery is unfiltered.
            "SELECT * FROM agent_metrics WHERE tenant_id = {nodeglow_tenant:UInt32} "
            "AND (agent_id, timestamp) IN (SELECT agent_id, max(timestamp) FROM agent_metrics GROUP BY agent_id)",
            "SELECT * FROM x JOIN bandwidth_metrics USING (source_id)",
        ):
            with pytest.raises(TenantContextError):
                await ch.query(sql)
    assert main_client.queries == []


async def test_tenant_read_binds_tenant_and_sends_table_filters(main_client):
    with tenant_scope(A):
        await ch.query(
            f"SELECT count() FROM syslog_messages WHERE {ch.TENANT_FILTER} AND severity <= {{s:Int8}}",
            {"s": 3},
        )
    q = main_client.queries[-1]
    assert q["params"] == {"s": 3, ch.TENANT_PARAM: A}
    filters = q["settings"]["additional_table_filters"]
    for table in ch.TENANT_TABLES:
        assert f"'{table}': 'tenant_id = {A}'" in filters
    # No row-policy setting without the restricted user: the server would
    # reject an unknown custom setting on an install without the prefix.
    assert ch.ROW_POLICY_SETTING not in q["settings"]


async def test_tenant_read_rejects_a_foreign_tenant_parameter(main_client):
    with tenant_scope(A):
        with pytest.raises(TenantContextError):
            await ch.query(
                f"SELECT 1 FROM ping_checks WHERE {ch.TENANT_FILTER}", {ch.TENANT_PARAM: B},
            )


async def test_query_without_tenant_tables_adds_no_parameter(main_client):
    # clickhouse-connect formats a query without {name:Type} placeholders on
    # the client side with %-substitution; an injected parameter would break
    # a LIKE '%x%'.
    with tenant_scope(A):
        await ch.query("SELECT 1 WHERE 'a' LIKE '%a%'")
    assert main_client.queries[-1]["params"] == {}


async def test_system_scope_reads_installation_wide(main_client):
    with system_scope("test: installation job"):
        await ch.query("SELECT count() FROM syslog_messages")
        await ch.get_ping_uptime([1])
    plain, helper = main_client.queries
    assert plain["settings"] is None
    assert ch.TENANT_PARAM not in helper["params"]
    assert ch.TENANT_FILTER not in helper["sql"]


async def test_table_filters_can_be_switched_off(main_client, monkeypatch):
    monkeypatch.setattr(ch, "TABLE_FILTERS_ENABLED", False)
    with tenant_scope(A):
        await ch.get_ping_history(1)
    q = main_client.queries[-1]
    assert not q["settings"]
    # The predicate in the SQL stays — it is not optional.
    assert ch.TENANT_FILTER in q["sql"] and q["params"][ch.TENANT_PARAM] == A


async def test_row_policy_mode_uses_the_restricted_client(main_client, monkeypatch):
    restricted = FakeClient("tenant")

    async def _tenant_client():
        return restricted
    monkeypatch.setattr(ch, "CLICKHOUSE_TENANT_URL", "http://nodeglow_app:pw@clickhouse:8123/nodeglow")
    monkeypatch.setattr(ch, "get_tenant_client", _tenant_client)

    with tenant_scope(B):
        await ch.get_ping_history(9)
    with system_scope("test: installation read"):
        await ch.query("SELECT count() FROM ping_checks")

    assert len(restricted.queries) == 1
    assert restricted.queries[0]["settings"][ch.ROW_POLICY_SETTING] == str(B)
    assert restricted.queries[0]["params"][ch.TENANT_PARAM] == B
    # System scope stays on the main (unrestricted) connection.
    assert len(main_client.queries) == 1 and main_client.queries[0]["settings"] is None


def test_tenant_tables_read_counts_every_read():
    sql = (
        "SELECT * FROM `syslog_messages` s JOIN nodeglow.ping_checks p ON 1 "
        "WHERE x IN (SELECT 1 FROM agent_metrics) AND y IN (SELECT 1 FROM ping_checks__pre_tenant)"
    )
    assert ch.tenant_tables_read(sql) == ["syslog_messages", "ping_checks", "agent_metrics"]


# ── Row policies ──────────────────────────────────────────────────────────────


def test_row_policy_statements():
    stmts = ch.row_policy_statements("nodeglow_app", "p'w", "nodeglow")
    joined = "\n".join(stmts)
    assert "CREATE USER IF NOT EXISTS `nodeglow_app` IDENTIFIED WITH sha256_password BY 'p\\'w'" in joined
    for table in ch.TENANT_TABLES:
        assert f"GRANT SELECT ON `nodeglow`.`{table}` TO `nodeglow_app`" in joined
        assert (
            f"CREATE ROW POLICY OR REPLACE nodeglow_tenant_isolation ON `nodeglow`.`{table}` FOR SELECT "
            "USING tenant_id = toUInt32(getSetting('SQL_nodeglow_tenant')) AS permissive TO `nodeglow_app`"
        ) in joined
        assert (
            f"CREATE ROW POLICY OR REPLACE nodeglow_unrestricted ON `nodeglow`.`{table}` FOR SELECT "
            "USING 1 AS permissive TO ALL EXCEPT `nodeglow_app`"
        ) in joined
    # Not the whole database: the step-B work tables stay out of reach.
    assert "ON `nodeglow`.* " not in joined


def test_dsn_credentials():
    assert ch.dsn_credentials("http://app%40x:s%3Acret@ch:8123/db") == ("app@x", "s:cret", "db")
    assert ch.dsn_credentials("http://ch:8123") == ("default", None, None)


async def test_provisioning_runs_through_the_main_connection():
    client = FakeClient()
    await ch.provision_tenant_user(client, "http://nodeglow_app:pw@clickhouse:8123/nodeglow")
    cmds = [c for c, _ in client.commands]
    assert cmds[0].startswith("CREATE USER IF NOT EXISTS `nodeglow_app`")
    assert sum("CREATE ROW POLICY" in c for c in cmds) == 2 * len(ch.TENANT_TABLES)


async def test_ensure_schemas_provisions_only_when_configured(monkeypatch):
    issued: list[str] = []

    class Client:
        async def command(self, ddl, parameters=None):
            issued.append(" ".join(ddl.split()))

    monkeypatch.setattr(ch, "_schemas_applied", False)
    monkeypatch.setattr(ch, "CLICKHOUSE_TENANT_URL", "")
    await ch._ensure_schemas(Client())
    assert not any("ROW POLICY" in s for s in issued)

    issued.clear()
    monkeypatch.setattr(ch, "_schemas_applied", False)
    monkeypatch.setattr(ch, "CLICKHOUSE_TENANT_URL", "http://u:p@h:8123/nodeglow")
    monkeypatch.setattr(ch, "PROVISION_TENANT_USER", False)
    await ch._ensure_schemas(Client())
    assert not any("ROW POLICY" in s for s in issued)

    monkeypatch.setattr(ch, "_schemas_applied", False)
    monkeypatch.setattr(ch, "PROVISION_TENANT_USER", True)
    await ch._ensure_schemas(Client())
    assert any("ROW POLICY" in s for s in issued)


def test_allow_custom_setting_registers_for_the_0_8_client():
    inner = SimpleNamespace(server_settings={"max_threads": object()})
    wrapper = SimpleNamespace(client=inner)
    ch.allow_custom_setting(wrapper, ch.ROW_POLICY_SETTING)
    setting = inner.server_settings[ch.ROW_POLICY_SETTING]
    assert setting.readonly == 0
    # Idempotent, and harmless for clients without the attribute.
    ch.allow_custom_setting(wrapper, ch.ROW_POLICY_SETTING)
    ch.allow_custom_setting(SimpleNamespace(), ch.ROW_POLICY_SETTING)


# ── Schema: step A ────────────────────────────────────────────────────────────


def test_step_a_adds_tenant_column_to_every_table():
    alters = " ".join(ch._PHASE3_ALTERS)
    for table in ch.TENANT_TABLES:
        assert f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS tenant_id UInt32 DEFAULT 1" in alters
    assert "ALTER TABLE syslog_messages ADD COLUMN IF NOT EXISTS site_id UInt32 DEFAULT 0" in alters
    assert "ALTER TABLE ping_checks ADD COLUMN IF NOT EXISTS site_id" not in alters


def test_fresh_tables_lead_their_sort_key_with_tenant():
    ddl = " ".join(" ".join(s.split()) for s in ch._PHASE2_SCHEMAS)
    assert "ORDER BY (tenant_id, host_id, timestamp)" in ddl
    # The app creates syslog_messages too (init.sql may not have run in the
    # database the app uses).
    assert "CREATE TABLE IF NOT EXISTS syslog_messages" in ddl
    assert "ORDER BY (tenant_id, severity, source_ip, timestamp)" in ddl
    assert "ORDER BY (tenant_id, agent_id, timestamp)" in ddl
    assert "ORDER BY (tenant_id, source_type, source_id, interface_name, timestamp)" in ddl
    init_sql = (ROOT / "clickhouse" / "init.sql").read_text(encoding="utf-8")
    assert "ORDER BY (tenant_id, severity, source_ip, timestamp)" in init_sql
    for table in ch.TENANT_TABLES:
        body = init_sql.split(f"CREATE TABLE IF NOT EXISTS {table}")[1].split(";")[0]
        assert "tenant_id" in body.split("ENGINE")[0]
    config = (ROOT / "clickhouse" / "config.xml").read_text(encoding="utf-8")
    assert "<custom_settings_prefixes>SQL_</custom_settings_prefixes>" in config


# ── Inserts stamp tenant_id from the row ─────────────────────────────────────


async def test_inserts_refuse_rows_without_tenant(main_client):
    now = datetime(2026, 10, 1)
    cases = [
        (ch.insert_ping_checks, {"timestamp": now, "host_id": 1, "success": True}),
        (ch.insert_agent_metrics, {"timestamp": now, "agent_id": 1}),
        (ch.insert_bandwidth_metrics, {"timestamp": now, "source_type": "agent", "source_id": "1",
                                       "interface_name": "eth0"}),
        (ch.insert_batch, {"timestamp": now, "message": "x", "source_ip": "10.0.0.1"}),
    ]
    for insert, row in cases:
        with pytest.raises(TenantContextError):
            await insert([row])
        with pytest.raises(TenantContextError):
            await insert([{**row, "tenant_id": 0}])
    assert main_client.inserts == []


async def test_inserts_write_each_rows_tenant(main_client):
    now = datetime(2026, 10, 1)
    # Inserts do not consult the ambient context: a batch mixes tenants.
    with tenant_scope(A):
        await ch.insert_ping_checks([
            {"tenant_id": A, "timestamp": now, "host_id": 1, "success": True},
            {"tenant_id": B, "timestamp": now, "host_id": 2, "success": False},
        ])
    await ch.insert_agent_metrics([{"tenant_id": B, "timestamp": now, "agent_id": 5}])
    await ch.insert_bandwidth_metrics([{"tenant_id": A, "timestamp": now, "source_type": "unifi",
                                        "source_id": "aa:bb", "interface_name": "p1"}])
    await ch.insert_batch([{"tenant_id": B, "site_id": 2, "timestamp": now, "message": "m",
                            "source_ip": "10.0.0.1"}])
    by_table = {t: (data, cols) for t, data, cols in main_client.inserts}
    for table, expected in (
        ("ping_checks", [A, B]), ("agent_metrics", [B]),
        ("bandwidth_metrics", [A]), ("syslog_messages", [B]),
    ):
        data, cols = by_table[table]
        idx = cols.index("tenant_id")
        assert [row[idx] for row in data] == expected
    data, cols = by_table["syslog_messages"]
    assert data[0][cols.index("site_id")] == 2


def test_syslog_rows_carry_the_message_tenant():
    from services import syslog

    batch = [
        {syslog.TENANT_KEY: A, "message": "a", "is_new_template": True},
        {"message": "orphan"},
        {syslog.TENANT_KEY: B, "message": "b", "tags": ["x"]},
    ]
    rows = syslog._clean_rows(batch)
    assert [(r["tenant_id"], r["message"]) for r in rows] == [(A, "a"), (B, "b")]
    assert all(syslog.TENANT_KEY not in r and "is_new_template" not in r for r in rows)


async def test_bandwidth_extraction_stamps_the_source_tenant(monkeypatch):
    from services import bandwidth

    written: list[dict] = []

    async def _insert(rows):
        written.extend(rows)

    async def _prev(*a, **k):
        return None
    monkeypatch.setattr(bandwidth.ch, "insert_bandwidth_metrics", _insert)
    monkeypatch.setattr(bandwidth.ch, "get_previous_bandwidth_sample", _prev)

    with tenant_scope(A):
        await bandwidth.extract_agent_bandwidth(
            1, {"network": {"interfaces": [{"name": "eth0", "rx_bytes": 1, "tx_bytes": 2}]}},
            tenant_id=A,
        )
        await bandwidth.extract_unifi_bandwidth(
            2, {"devices": [{"mac": "aa", "name": "sw", "rx_bytes_r": 1, "tx_bytes_r": 1}]},
            tenant_id=A,
        )
        await bandwidth.extract_proxmox_bandwidth(
            3, {"nodes": [{"node": "pve", "netin": 1, "netout": 2}]}, tenant_id=A,
        )
    assert len(written) == 3 and {r["tenant_id"] for r in written} == {A}


# ── Retention per tenant ─────────────────────────────────────────────────────


async def test_retention_deletes_only_the_tenants_old_rows(monkeypatch):
    client = FakeClient(rows=[(12,)], columns=("count()",))

    async def _get():
        return client
    monkeypatch.setattr(ch, "get_client", _get)
    monkeypatch.setattr(ch, "CLICKHOUSE_TENANT_URL", "")

    with tenant_scope(A):
        assert await ch.enforce_retention("ping_checks", 5) == 12
        # At or above the TTL ceiling the TTL already does it: nothing issued.
        assert await ch.enforce_retention("ping_checks", 30) == 0
        assert await ch.enforce_retention("ping_checks", 365) == 0
    (cmd, params), = client.commands
    assert cmd.startswith("DELETE FROM ping_checks WHERE tenant_id = {nodeglow_tenant:UInt32} AND ")
    assert params == {ch.TENANT_PARAM: A, "d": 5}
    with system_scope("test"):
        with pytest.raises(TenantContextError):
            await ch.enforce_retention("ping_checks", 5)


async def test_retention_issues_no_mutation_when_nothing_expired(monkeypatch):
    client = FakeClient(rows=[(0,)], columns=("count()",))

    async def _get():
        return client
    monkeypatch.setattr(ch, "get_client", _get)
    monkeypatch.setattr(ch, "CLICKHOUSE_TENANT_URL", "")
    with tenant_scope(A):
        assert await ch.enforce_retention("ping_checks", 5) == 0
    assert client.commands == []


# ── Static scan: every SQL string that reads a tenant table is filtered ──────

_SCAN_DIRS = [ROOT / "backend", ROOT / "ee" / "backend"]
_SKIP_PARTS = {"tests", "alembic", "__pycache__", "static", "templates"}
# The helpers build their predicate with tenant_clause(); their own tests check
# each one's SQL against the guard (test_clickhouse_helpers.py).
_SKIP_FILES = {"clickhouse_client.py", "clickhouse_rebuild.py"}
# Expressions interpolated into SQL that carry the predicate themselves.
_FILTERED_CALLS = {"_where_clauses", "ch_where", "tenant_clause"}


def _sources():
    for base in _SCAN_DIRS:
        for path in base.rglob("*.py"):
            if _SKIP_PARTS & set(path.relative_to(base).parts) or path.name in _SKIP_FILES:
                continue
            yield path


def _filtered_names(func: ast.AST) -> set[str]:
    """Names assigned from a call that returns an already filtered WHERE."""
    names: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            callee = node.value.func
            fname = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
            if fname in _FILTERED_CALLS:
                for target in node.targets:
                    elts = target.elts if isinstance(target, ast.Tuple) else [target]
                    if elts and isinstance(elts[0], ast.Name):
                        names.add(elts[0].id)
    return names


def _render(node: ast.AST, filtered: set[str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                out.append(part.value)
                continue
            expr = part.value
            text = ast.unparse(expr)
            if text.endswith("TENANT_FILTER") or text in filtered or (
                isinstance(expr, ast.Call) and getattr(expr.func, "id", getattr(expr.func, "attr", "")) == "tenant_clause"
            ):
                out.append(ch.TENANT_FILTER)
            else:
                out.append("{expr}")
        return "".join(out)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _render(node.left, filtered), _render(node.right, filtered)
        if left is not None and right is not None:
            return left + right
    return None


_PLACEHOLDER = re.compile(r"\{\{?nodeglow_tenant:UInt32\}\}?")


def test_every_sql_string_reading_a_tenant_table_has_the_predicate():
    verdicts: dict[tuple, tuple[bool, str]] = {}
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip: set[int] = set()
        for n in ast.walk(tree):
            is_doc_owner = isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            if (is_doc_owner and n.body and isinstance(n.body[0], ast.Expr)
                    and isinstance(n.body[0].value, ast.Constant)):
                skip.add(id(n.body[0].value))  # docstrings
            if isinstance(n, ast.JoinedStr):
                skip.update(id(v) for v in n.values)
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
                skip.update((id(n.left), id(n.right)))
        scopes = [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for scope in scopes:
            filtered = _filtered_names(scope)
            for node in ast.walk(scope):
                if id(node) in skip or not isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)):
                    continue
                text = _render(node, filtered)
                if not text:
                    continue
                reads = ch.tenant_tables_read(text)
                if not reads:
                    continue
                key = (str(path.relative_to(ROOT)), node.lineno)
                ok = len(_PLACEHOLDER.findall(text)) >= len(reads)
                # A nested function may use a WHERE its enclosing function
                # built: one passing scope is enough.
                if ok or key not in verdicts:
                    verdicts[key] = (ok or verdicts.get(key, (False, ""))[0], " ".join(text.split())[:140])
    assert len(verdicts) > 50  # the scan actually sees the queries
    problems = [f"{f}:{line}: {text}" for (f, line), (ok, text) in sorted(verdicts.items()) if not ok]
    assert not problems, "\n".join(problems)
