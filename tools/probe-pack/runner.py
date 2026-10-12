"""Collector-pack runner: runs integration collect() for the probe agent.

Protocol (JSON lines; one object per line on stdin, one per line on stdout):

    -> {"op": "collect", "id": "42", "type": "proxmox", "config": {...}}
    <- {"op": "result", "id": "42", "type": "proxmox", "success": true,
        "data": {...}, "error": null, "duration_ms": 812.4}
    -> {"op": "ping"}       <- {"op": "pong", "python": "...", "pack": {...}}
    -> {"op": "stats"}      <- {"op": "stats", "rss_bytes": ..., "peak_rss_bytes": ...}
    -> {"op": "selftest"}   <- {"op": "selftest", "ok": true, "modules": {...}}
    -> {"op": "shutdown"}   (or EOF) — exits after in-flight collects finish

Collects run concurrently (at most --max-concurrent); the agent enforces
timeouts and kills a runtime that stops answering. Anything a collector
prints goes to stderr, so stdout carries the protocol only.

Spike mode: ``runner.py --replay FIXTURE [--expect EXPECTED]`` runs one
collect() against a recorded fixture (see replay.py) and compares the data
with the expected file after canonical JSON.

Start with ``python -I -X pycache_prefix=DIR runner.py`` (or ``-I -B``): -I
ignores PYTHON* variables, the user site and the current directory; this file
puts its own directory and ``vendor/`` on sys.path. Without -B or a
pycache_prefix, the stdlib imports that run before this file's first line
would write __pycache__ into the pack.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(HERE, "vendor"), HERE):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

# Never write bytecode into the (read-only) pack. The agent starts the runtime
# with ``-I -X pycache_prefix=<its work dir>/pycache`` (warm starts) or ``-I -B``.
if os.environ.get("NODEGLOW_PYCACHE_PREFIX") and not sys.pycache_prefix:
    sys.pycache_prefix = os.environ["NODEGLOW_PYCACHE_PREFIX"]
if not sys.pycache_prefix:
    sys.dont_write_bytecode = True

logger = logging.getLogger("collector-runner")
_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# What a selftest must be able to import: every collector plus the stdlib
# parts they and httpx rely on.
SELFTEST_STDLIB = ("asyncio", "ssl", "socket", "json", "ipaddress", "hashlib",
                   "hmac", "base64", "zlib", "ctypes", "sqlite3", "subprocess",
                   "shutil", "urllib.parse", "email.utils", "http.cookiejar",
                   "concurrent.futures", "decimal", "uuid", "xml.etree.ElementTree")


def memory_stats() -> dict:
    """Current and peak resident set size of this process, in bytes."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return {"rss_bytes": pmc.WorkingSetSize, "peak_rss_bytes": pmc.PeakWorkingSetSize,
                "private_bytes": pmc.PagefileUsage}
    out = {}
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                key, _, val = line.partition(":")
                if key in ("VmRSS", "VmHWM", "RssAnon", "RssFile"):
                    out[key] = int(val.split()[0]) * 1024
    except OSError:
        pass
    return {"rss_bytes": out.get("VmRSS"), "peak_rss_bytes": out.get("VmHWM"),
            "rss_anon_bytes": out.get("RssAnon"), "rss_file_bytes": out.get("RssFile")}


def pack_info() -> dict:
    try:
        with open(os.path.join(HERE, "pack-manifest.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def integration_class(type_name: str):
    """Class registered under ``type_name`` — the integration's ``name``, which
    is not always its module name (``hass`` is homeassistant.py, ``ups`` nut.py)."""
    if not _TYPE_RE.match(type_name or ""):
        raise LookupError(f"invalid integration type {type_name!r}")
    from integrations import get_integration

    cls = get_integration(type_name)
    if cls is None:
        raise LookupError(f"no integration named {type_name!r}")
    return cls


def selftest() -> dict:
    modules: dict[str, str] = {}
    for name in SELFTEST_STDLIB + ("httpx", "httpcore", "h11", "anyio", "certifi", "idna",
                                    "utils.net_safety"):
        try:
            importlib.import_module(name)
            modules[name] = "ok"
        except Exception as exc:  # noqa: BLE001
            modules[name] = f"{type(exc).__name__}: {exc}"
    integ_dir = os.path.join(HERE, "integrations")
    for fn in sorted(os.listdir(integ_dir)):
        if fn.endswith(".py") and not fn.startswith("_"):
            name = f"integrations.{fn[:-3]}"
            try:
                importlib.import_module(name)
                modules[name] = "ok"
            except Exception as exc:  # noqa: BLE001
                modules[name] = f"{type(exc).__name__}: {exc}"
    from integrations import get_registry

    types = sorted(get_registry())
    imported = sum(1 for k, v in modules.items() if k.startswith("integrations.") and v == "ok")
    modules["registry"] = "ok" if len(types) == imported else f"{len(types)} types for {imported} modules"
    try:
        import ssl

        import certifi
        ssl.create_default_context(cafile=certifi.where())
        modules["ssl+certifi context"] = "ok"
    except Exception as exc:  # noqa: BLE001
        modules["ssl+certifi context"] = f"{type(exc).__name__}: {exc}"
    return {"ok": all(v == "ok" for v in modules.values()), "modules": modules, "types": types}


async def collect(type_name: str, config: dict) -> dict:
    t0 = time.perf_counter()
    try:
        cls = integration_class(type_name)
        result = await cls(config).collect()
        out = {"success": bool(result.success), "data": result.data, "error": result.error}
    except Exception as exc:  # a broken collector must not kill the runtime
        logger.exception("collect %s failed", type_name)
        out = {"success": False, "data": None, "error": f"{type(exc).__name__}: {exc}"}
    out["duration_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


class Server:
    def __init__(self, out, max_concurrent: int):
        self.out = out
        self.sem = asyncio.Semaphore(max_concurrent)
        self.lock = asyncio.Lock()
        self.tasks: set[asyncio.Task] = set()

    async def send(self, obj: dict) -> None:
        line = json.dumps(obj, default=str, separators=(",", ":")) + "\n"
        async with self.lock:
            self.out.write(line)
            self.out.flush()

    async def run_collect(self, msg: dict) -> None:
        async with self.sem:
            res = await collect(str(msg.get("type", "")), msg.get("config") or {})
        await self.send({"op": "result", "id": msg.get("id"), "type": msg.get("type"), **res})

    async def handle(self, msg: dict) -> bool:
        op = msg.get("op")
        if op == "collect":
            task = asyncio.create_task(self.run_collect(msg))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        elif op == "ping":
            await self.send({"op": "pong", "id": msg.get("id"), "python": sys.version,
                             "pack": pack_info()})
        elif op == "stats":
            await self.send({"op": "stats", "id": msg.get("id"), **memory_stats()})
        elif op == "selftest":
            await self.send({"op": "selftest", "id": msg.get("id"), **selftest()})
        elif op == "shutdown":
            return False
        else:
            await self.send({"op": "error", "id": msg.get("id"), "error": f"unknown op {op!r}"})
        return True

    async def serve(self, inp) -> None:
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, inp.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                if not isinstance(msg, dict):
                    raise TypeError("not an object")
            except (ValueError, TypeError) as exc:
                await self.send({"op": "error", "error": f"bad request: {exc}"})
                continue
            if not await self.handle(msg):
                break
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


async def replay_main(args, out) -> int:
    """Spike: run collect() against each fixture and compare with its
    ``<name>.expected.json`` sibling (canonical JSON). One report line per
    fixture, then a summary line with memory before/after."""
    import replay

    mem_before = memory_stats()
    rc = 0
    for path in args.replay:
        fixture = replay.load_fixture(path)
        res = await replay.run_fixture(fixture)
        report = {"fixture": os.path.basename(path), "type": fixture["type"]}
        report.update({k: res[k] for k in ("ok", "problems", "success", "error", "duration_ms")})
        report["requests"] = len(res["calls"])
        if not res["ok"]:
            rc = max(rc, 1)
        actual = replay.canonical(res["data"])
        if args.write_actual:
            name = os.path.basename(path).replace(".json", ".actual.json")
            _write_text(os.path.join(args.write_actual, name), actual)
        expected_path = path[:-5] + ".expected.json" if path.endswith(".json") else None
        if expected_path and os.path.exists(expected_path):
            same = actual == replay.canonical(json.loads(_read_text(expected_path)))
            report["matches_expected"] = same
            if not same:
                rc = max(rc, 2)
        out.write(json.dumps(report, default=str) + "\n")
        out.flush()
    out.write(json.dumps({"summary": True, "rc": rc, "python": sys.version,
                          "memory_after_import": mem_before, "memory_after_collects": memory_stats()})
              + "\n")
    out.flush()
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--max-concurrent", type=int, default=4)
    ap.add_argument("--replay", metavar="FIXTURE", nargs="+",
                    help="spike: run collect() against fixtures, compare with <name>.expected.json")
    ap.add_argument("--write-actual", metavar="DIR", help="with --replay: write canonical data here")
    ap.add_argument("--selftest", action="store_true", help="import everything, print the result")
    args = ap.parse_args(argv)

    logging.basicConfig(level=os.environ.get("NODEGLOW_RUNNER_LOG", "WARNING"), stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for stream in (sys.stdin, sys.stdout, sys.stderr):  # pipes on Windows default to the ANSI codepage
        stream.reconfigure(encoding="utf-8", errors="backslashreplace" if stream is sys.stderr else "strict")
    out = sys.stdout
    sys.stdout = sys.stderr  # stray prints must not corrupt the protocol
    if args.selftest:
        res = selftest()
        res["memory"] = memory_stats()
        out.write(json.dumps(res, indent=1) + "\n")
        return 0 if res["ok"] else 1
    if args.replay:
        from integrations import get_registry

        get_registry()  # import every collector up front, as the long-lived runtime would
        return asyncio.run(replay_main(args, out))
    asyncio.run(Server(out, max(1, args.max_concurrent)).serve(sys.stdin))
    return 0


if __name__ == "__main__":
    sys.exit(main())
