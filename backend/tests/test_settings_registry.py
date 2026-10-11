"""The settings registry: every key the code uses is declared, and keys are
routed to the right table and fail closed (multi-tenancy phase 3).

The static part walks the source of the core (``backend/``, tests excluded)
and of ``ee/backend/nodeglow_ee``: every key passed to ``get_setting`` /
``set_setting`` or used to build a ``Setting`` / ``TenantSetting`` row must
resolve to a declared key — literals, module-level constants (also imported
ones, by name), tuples of them, loop variables over those, and f-strings
(which must match a declared key or prefix). What cannot be resolved
statically is listed in ``DYNAMIC_KEY_SITES`` with the reason; a new dynamic
site fails the test until it is reviewed.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from services import settings_registry as reg
from services.tenancy import TenantContextError, system_scope, tenant_scope

BACKEND = Path(__file__).resolve().parent.parent
EE = BACKEND.parent / "ee" / "backend" / "nodeglow_ee"

# (relative path, unparsed key expression) -> why it is fine.
DYNAMIC_KEY_SITES: dict[tuple[str, str], str] = {
    ("routers/auth.py", "_lockout_key(username)"):
        "builds '_lockout:<username>', a declared prefix key",
    ("models/settings.py", "key"): "get_setting/set_setting themselves",
    ("models/settings.py", "row.key"):
        "adopt_legacy_tenant_settings copies rows selected by tenant_keys()",
}

_SETTING_FUNCS = {"get_setting", "set_setting"}
_ROW_MODELS = {"Setting", "TenantSetting"}


def _sources() -> list[Path]:
    files = [p for p in BACKEND.rglob("*.py")
             if "tests" not in p.relative_to(BACKEND).parts
             and "alembic" not in p.relative_to(BACKEND).parts
             and "static" not in p.relative_to(BACKEND).parts]
    if EE.is_dir():
        files += list(EE.rglob("*.py"))
    return files


def _rel(path: Path) -> str:
    try:
        return path.relative_to(BACKEND).as_posix()
    except ValueError:
        return "ee/" + path.relative_to(EE.parent).as_posix()


def _module_constants(trees: dict[Path, ast.Module]) -> dict[str, list]:
    """NAME -> values of every module-level ``NAME = <str | tuple of str>``."""
    out: dict[str, list] = {}
    for tree in trees.values():
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                if value is None:
                    continue
                for t in targets:
                    if isinstance(t, ast.Name):
                        out.setdefault(t.id, []).append(value)
    return out


class _Resolver:
    def __init__(self, consts: dict[str, list]):
        self.consts = consts

    def values(self, node, loops: dict[str, ast.AST], depth: int = 0) -> list[str] | None:
        """Every string the expression can be, or None if unknown."""
        if depth > 5:
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.Dict):
            return self.values(ast.Tuple(elts=list(node.keys), ctx=ast.Load()), loops, depth + 1)
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            out = []
            for elt in node.elts:
                v = self.values(elt, loops, depth + 1)
                if v is None:
                    return None
                out += v
            return out
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self.values(node.left, loops, depth + 1)
            right = self.values(node.right, loops, depth + 1)
            return None if left is None or right is None else left + right
        if isinstance(node, ast.Name) and node.id in loops:
            bound = loops[node.id]
            rest = {k: v for k, v in loops.items() if k != node.id}
            return self.values(bound, rest, depth + 1)
        name = node.id if isinstance(node, ast.Name) else (
            node.attr if isinstance(node, ast.Attribute) else None)
        if name and name in self.consts:
            out = []
            for value in self.consts[name]:
                v = self.values(value, {}, depth + 1)
                if v is None:
                    return None
                out += v
            return out
        return None


def _loop_bindings(func: ast.AST) -> dict[str, ast.AST]:
    """Loop variable / local name -> the expression of the values it takes
    (per function). A name bound several times takes all of their values."""
    multi: dict[str, list[ast.AST]] = {}

    def bind(name: str, value: ast.AST) -> None:
        multi.setdefault(name, []).append(value)

    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and not isinstance(func, ast.Module):
            bind(node.targets[0].id, node.value)
            continue
        if not isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            continue
        target, it = node.target, node.iter
        if isinstance(target, ast.Name):
            bind(target.id, it)
        elif isinstance(target, ast.Tuple):
            if isinstance(it, (ast.List, ast.Tuple)):
                for i, elt in enumerate(target.elts):
                    if isinstance(elt, ast.Name):
                        bind(elt.id, ast.Tuple(elts=[
                            row.elts[i] for row in it.elts
                            if isinstance(row, ast.Tuple) and len(row.elts) > i
                        ], ctx=ast.Load()))
            elif isinstance(it, ast.Call) and isinstance(it.func, ast.Attribute) \
                    and it.func.attr == "items" and isinstance(target.elts[0], ast.Name):
                bind(target.elts[0].id, it.func.value)  # the dict: its keys
    out: dict[str, ast.AST] = {}
    for name, values in multi.items():
        if len(values) == 1:
            out[name] = values[0]
        elif all(not isinstance(v, ast.JoinedStr) for v in values):
            out[name] = ast.Tuple(elts=values, ctx=ast.Load())
        else:
            out[name] = values[0]
    return out


def _fstring_pattern(node: ast.JoinedStr) -> re.Pattern:
    parts = []
    for v in node.values:
        if isinstance(v, ast.Constant):
            parts.append(re.escape(str(v.value)))
        else:
            parts.append(".+")
    return re.compile("^" + "".join(parts) + "$")


def _key_nodes(tree: ast.Module):
    """(enclosing function, key expression) of every settings access."""
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def func_of(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node
        return tree

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
        if name in _SETTING_FUNCS and len(node.args) >= 2:
            yield func_of(node), node.args[1]
        elif name in _ROW_MODELS:
            for kw in node.keywords:
                if kw.arg == "key":
                    yield func_of(node), kw.value
        elif name == "get" and len(node.args) == 2 and isinstance(node.args[0], ast.Name) \
                and node.args[0].id == "Setting":
            yield func_of(node), node.args[1]


def _call_site_args(tree: ast.Module, func: ast.AST, name: str) -> list[ast.AST] | None:
    """When ``name`` is a parameter of ``func`` (a small key helper such as
    ``async def g(key, default="")``): the matching argument of every call of
    ``func`` in the module. None if it is not a parameter or never called."""
    if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    params = [a.arg for a in func.args.args]
    if name not in params:
        return None
    idx = params.index(name)
    args = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == func.name:
            if len(node.args) > idx:
                args.append(node.args[idx])
            else:
                for kw in node.keywords:
                    if kw.arg == name:
                        args.append(kw.value)
    return args or None


def _scan():
    trees = {p: ast.parse(p.read_text(encoding="utf-8")) for p in _sources()}
    resolver = _Resolver(_module_constants(trees))
    keys: dict[str, set[str]] = {}
    patterns: list[tuple[str, str]] = []
    unresolved: set[tuple[str, str]] = set()
    for path, tree in trees.items():
        rel = _rel(path)
        for func, expr in _key_nodes(tree):
            bindings = _loop_bindings(func)
            exprs = [expr]
            if isinstance(expr, ast.Name) and expr.id not in bindings:
                exprs = _call_site_args(tree, func, expr.id) or [expr]
            for e in exprs:
                while isinstance(e, ast.Name) and isinstance(bindings.get(e.id), ast.JoinedStr):
                    e = bindings[e.id]
                if isinstance(e, ast.JoinedStr):
                    patterns.append((rel, ast.unparse(e)))
                    continue
                values = resolver.values(e, bindings)
                if values is None:
                    unresolved.add((rel, ast.unparse(expr)))
                    continue
                for v in values:
                    keys.setdefault(v, set()).add(rel)
    return keys, patterns, unresolved


def test_every_key_used_in_the_code_is_declared():
    keys, _, _ = _scan()
    assert len(keys) > 80, "the scan found suspiciously few keys"
    undeclared = {}
    for key, files in keys.items():
        try:
            reg.lookup(key)
        except reg.UnknownSettingError:
            undeclared[key] = sorted(files)
    assert undeclared == {}, f"declare these in services/settings_registry.py: {undeclared}"


def test_every_fstring_key_matches_a_declared_key():
    _, patterns, _ = _scan()
    declared = list(reg.REGISTRY) + [p.key + "x" for p in reg.PREFIXES]
    bad = []
    for rel, src in patterns:
        node = ast.parse(src, mode="eval").body
        rx = _fstring_pattern(node)
        if not any(rx.match(k) for k in declared):
            bad.append((rel, src))
    assert bad == []


def test_dynamic_key_sites_are_reviewed():
    _, _, unresolved = _scan()
    assert unresolved == set(DYNAMIC_KEY_SITES), (
        "settings keys the scan cannot resolve; make them resolvable or list them "
        f"in DYNAMIC_KEY_SITES: {sorted(unresolved - set(DYNAMIC_KEY_SITES))}; "
        f"stale entries: {sorted(set(DYNAMIC_KEY_SITES) - unresolved)}"
    )


def test_spec_classification():
    """The keys the spec names, in the scope it gives them."""
    for key in ("setup_complete", "syslog_port", "agent_server_url", "nodeglow_ip",
                "geoip_enabled", "geoip_license_key", "disk_usage_history",
                "agent_enrollment_key", "ldap_server", "claude_api_key"):
        assert reg.lookup(key).scope == reg.INSTALLATION, key
    assert reg.lookup("_lockout:alice").scope == reg.INSTALLATION
    for key in ("site_name", "timezone", "notify_enabled", "telegram_bot_token",
                "smtp_host", "digest_day", "daily_ai_summary_last_sent", "ai_enabled",
                "ping_interval", "anomaly_threshold", "ping_retention_days",
                "correlation_min_failures", "syslog_allowlist_only", "phpipam_url"):
        assert reg.lookup(key).scope == reg.TENANT, key
    assert reg.lookup("smtp_host").fallback
    assert "dashboard_layout" not in reg.REGISTRY  # a per-user preference now


def test_migration_042_moved_exactly_the_tenant_keys():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "m042", BACKEND / "alembic" / "versions" / "042_settings_split.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # A tenant key added after 042 needs its own data migration (or no
    # existing rows); this pins the list 042 froze to today's registry.
    assert set(m.TENANT_KEYS) == reg.tenant_keys()


def test_unknown_key_is_refused():
    with pytest.raises(reg.UnknownSettingError):
        reg.lookup("no_such_setting")
    with pytest.raises(reg.UnknownSettingError):
        reg.lookup("_lockout:")  # a bare prefix is not a key


# ── Routing and fail-closed ─────────────────────────────────────────────────

async def _unbound(db):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(db.bind, expire_on_commit=False)()


async def test_tenant_key_without_context_raises(db):
    from models.settings import get_setting, set_setting

    s = await _unbound(db)
    async with s:
        with pytest.raises(TenantContextError):
            await get_setting(s, "site_name")
        with pytest.raises(TenantContextError):
            await set_setting(s, "notify_enabled", "1")
        with system_scope("test"), pytest.raises(TenantContextError):
            await get_setting(s, "timezone")  # no single tenant in system scope
        # Installation keys need no tenant.
        await set_setting(s, "syslog_port", "1600")
        assert await get_setting(s, "syslog_port") == "1600"


async def test_tenant_keys_are_per_tenant(db):
    from models.settings import Setting, get_setting, set_setting
    from models.tenant import Tenant, TenantSetting

    with system_scope("test: second tenant"):
        db.add(Tenant(id=2, provider_id=1, name="B", slug="b", status="active"))
        await db.commit()
    s = await _unbound(db)
    async with s:
        with tenant_scope(1):
            await set_setting(s, "site_name", "Alpha")
        with tenant_scope(2):
            await set_setting(s, "site_name", "Bravo")
            assert await get_setting(s, "site_name") == "Bravo"
        with tenant_scope(1):
            assert await get_setting(s, "site_name") == "Alpha"
            # An explicit tenant must agree with the context.
            with pytest.raises(TenantContextError):
                await get_setting(s, "site_name", tenant_id=2)
        # Explicit tenant without a context (setup wizard, system jobs).
        assert await get_setting(s, "site_name", tenant_id=2) == "Bravo"
        with system_scope("test"):
            assert await get_setting(s, "site_name", tenant_id=1) == "Alpha"
            assert await s.get(Setting, "site_name") is None  # never in the installation table
            rows = (await s.execute(
                __import__("sqlalchemy").select(TenantSetting.tenant_id, TenantSetting.value)
                .where(TenantSetting.key == "site_name").order_by(TenantSetting.tenant_id)
            )).all()
            assert [tuple(r) for r in rows] == [(1, "Alpha"), (2, "Bravo")]


async def test_fallback_key_reads_the_installation_relay(db):
    from models.settings import Setting, get_setting, set_setting

    db.add(Setting(key="smtp_host", value="relay.example"))
    await db.commit()
    assert await get_setting(db, "smtp_host") == "relay.example"
    await set_setting(db, "smtp_host", "mail.tenant.example")
    assert await get_setting(db, "smtp_host") == "mail.tenant.example"
    # smtp_to has no fallback: a tenant's recipients are its own.
    db.add(Setting(key="smtp_to", value="ops@operator.example"))
    await db.commit()
    assert await get_setting(db, "smtp_to", "") == ""


async def test_registry_default_applies_when_caller_gives_none(db):
    from models.settings import get_setting

    assert await get_setting(db, "syslog_port") == "1514"
    assert await get_setting(db, "ntfy_server_url") == "https://ntfy.sh"
    assert await get_setting(db, "ntfy_server_url", "x") == "x"
