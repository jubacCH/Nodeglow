"""The collect boundary of the integrations (probe-side integrations, P1).

An integration's collect path runs in two places: in the backend and in the
probe's collector pack, which ships only the standard library, httpx and its
dependencies, ``utils/net_safety.py`` and ``integrations/``. So:

* importing an integration module pulls in only ``integrations.*``,
  ``utils.net_safety``, the standard library and an allowlist of third-party
  packages — checked statically (every import statement) and at runtime (a
  fresh interpreter in which backend packages cannot be imported at all);
* backend packages (``models``, ``services``, ``database``, ``fastapi``,
  ``sqlalchemy`` …) may be imported only inside ``on_snapshot`` or a function
  marked ``@backend_only`` — or under ``if TYPE_CHECKING``;
* backend-only code opens no network connections: in the SaaS it runs where
  the customer's targets are unreachable (enrichment belongs in collect()).
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
INTEGRATIONS = BACKEND / "integrations"

# Third-party packages the pack ships (httpx and its dependencies).
PACK_THIRD_PARTY = frozenset({"httpx", "httpcore", "h11", "anyio", "sniffio", "certifi", "idna"})
# Backend modules the pack ships.
PACK_BACKEND = frozenset({"integrations", "utils.net_safety"})
HOOK_NAMES = frozenset({"on_snapshot"})
# Names a hook / backend-only function must not touch.
NETWORK_NAMES = frozenset({
    "safe_async_client", "httpx", "httpcore", "socket", "open_connection", "subprocess",
    "create_subprocess_exec", "create_subprocess_shell", "urlopen", "gethostbyaddr",
    "gethostbyname", "getaddrinfo",
})

MODULES = sorted(p for p in INTEGRATIONS.glob("*.py"))


def _stdlib(root: str) -> bool:
    return root in sys.stdlib_module_names or root == "__future__"


def _allowed_in_pack(module: str) -> bool:
    root = module.split(".")[0]
    if _stdlib(root) or root in PACK_THIRD_PARTY or root == "integrations":
        return True
    return module in PACK_BACKEND or any(module.startswith(m + ".") for m in PACK_BACKEND)


def _is_backend_only(fn: ast.AST) -> bool:
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    if fn.name in HOOK_NAMES:
        return True
    for dec in fn.decorator_list:
        name = dec.id if isinstance(dec, ast.Name) else getattr(dec, "attr", None)
        if name == "backend_only":
            return True
    return False


def _is_type_checking(node: ast.AST) -> bool:
    test = getattr(node, "test", None)
    return isinstance(node, ast.If) and (
        (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING")
        or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"))


def _walk(node: ast.AST, stack: tuple[ast.AST, ...] = ()):
    """Yield (node, enclosing nodes) for every node below ``node``."""
    for child in ast.iter_child_nodes(node):
        yield child, stack
        yield from _walk(child, stack + (child,))


def _imported(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if isinstance(node, ast.ImportFrom):
        if node.level:  # relative: inside the integrations package
            return ["integrations"]
        return [node.module or ""]
    return []


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out = []
    for node, stack in _walk(tree):
        exempt = any(_is_backend_only(s) or _is_type_checking(s) for s in stack)
        for module in _imported(node):
            if not exempt and not _allowed_in_pack(module):
                out.append(f"{path.name}:{node.lineno} imports {module!r} on the collect path "
                           "(move it into on_snapshot / a @backend_only function)")
        backend_fn = next((s for s in stack if _is_backend_only(s)), None)
        if backend_fn is not None:
            name = node.id if isinstance(node, ast.Name) else (
                node.attr if isinstance(node, ast.Attribute) else None)
            mods = [m.split(".")[0] for m in _imported(node)]
            for hit in ({name} | set(mods)) & NETWORK_NAMES:
                out.append(f"{path.name}:{node.lineno} {backend_fn.name}() uses {hit!r}: "
                           "backend-only code must not open connections")
    return out


@pytest.mark.parametrize("path", MODULES, ids=[p.stem for p in MODULES])
def test_integration_imports_stay_inside_the_pack(path):
    assert _violations(path) == []


def test_the_static_check_catches_violations(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text(
        "from fastapi import APIRouter\n"
        "import sqlalchemy\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from models.ping import PingHost\n"
        "from integrations._base import backend_only\n"
        "async def collect():\n"
        "    from services import snapshot\n"
        "@backend_only\n"
        "async def apply(db):\n"
        "    from models.ping import PingHost\n"
        "    import socket\n"
        "class X:\n"
        "    async def on_snapshot(self, data, config, db):\n"
        "        from services.bandwidth import x\n"
        "        async with safe_async_client() as c:\n"
        "            pass\n",
        encoding="utf-8",
    )
    found = _violations(bad)
    assert [f.split(" ", 1)[0] for f in found] == [
        "bad.py:1", "bad.py:2", "bad.py:8", "bad.py:12", "bad.py:16"], found


# Run in a fresh interpreter: the backend packages are made unimportable, as
# they are in the pack, and every integration module must still import and
# register; afterwards no module outside the pack's set may be loaded.
_RUNTIME_SCRIPT = r"""
import importlib.abc, json, sys
backend, allowed_third, forbidden = sys.argv[1], set(json.loads(sys.argv[2])), json.loads(sys.argv[3])
sys.path.insert(0, backend)

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in forbidden or (name.startswith("utils.") and name != "utils.net_safety"):
            raise ImportError(f"not in the collector pack: {name}")
        return None

sys.meta_path.insert(0, Block())
before = set(sys.modules)
import integrations
registry = integrations.get_registry()
for cls in registry.values():
    cls(config={})
loaded = sorted({m.split(".")[0] for m in set(sys.modules) - before})
print(json.dumps({"registered": sorted(registry), "loaded": loaded}))
"""

FORBIDDEN_RUNTIME = ["fastapi", "starlette", "pydantic", "sqlalchemy", "models", "services",
                     "database", "config", "routers", "scheduler", "main", "extensions",
                     "nodeglow_ee", "asyncpg", "aiosqlite", "clickhouse_connect", "cryptography",
                     # httpx's optional command-line extras, absent from the pack
                     # (httpx falls back when they cannot be imported).
                     "click", "pygments", "rich"]


def test_integrations_import_without_the_backend():
    proc = subprocess.run(
        [sys.executable, "-I", "-c", _RUNTIME_SCRIPT, str(BACKEND),
         json.dumps(sorted(PACK_THIRD_PARTY)), json.dumps(FORBIDDEN_RUNTIME)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    # Failures to import are logged by the registry, not raised.
    assert "Failed to load integration module" not in proc.stderr, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    expected = sorted(p.stem for p in MODULES if not p.stem.startswith("_"))
    assert len(out["registered"]) == len(expected) == 19, out
    outside = [m for m in out["loaded"]
               if not (_stdlib(m) or m in PACK_THIRD_PARTY or m in ("integrations", "utils"))]
    # Packages httpx itself pulls in optionally are fine as long as they are
    # not backend packages (checked by the blocker above).
    assert set(outside) <= {"typing_extensions", "exceptiongroup"}, outside
