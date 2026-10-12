# Probe collector pack (P0 spike)

The collector pack lets a probe run the backend's integration collectors
(`backend/integrations/`) next to the devices they poll. See
`docs/specs/2026-10-11-probe-side-integrations.md`, option A.

A pack contains:

- `python/`: a pinned [python-build-standalone](https://github.com/astral-sh/python-build-standalone)
  CPython 3.12 runtime (`install_only_stripped`), with tkinter, idlelib,
  ensurepip, unittest, headers and `__pycache__` removed.
- `app/vendor/`: httpx and its dependencies. All of them are pure-Python
  wheels, pinned by SHA-256.
- `app/integrations/`: every integration module.
- `app/utils/net_safety.py`: the SSRF guard the collectors use.
- `app/runner.py`: the JSON-lines runtime the agent supervises.
- `app/replay.py`: fixture replay for the spike and the tests.
- `app/pack-manifest.json`: the versions and hashes in this pack.

## Build

Building needs Python 3.12+ and only the standard library. Writing `.tar.zst`
needs 3.14+. Any host OS can build any target.

```
python tools/probe-pack/build_pack.py --target windows-x86_64 --out tools/probe-pack/build/out
python tools/probe-pack/build_pack.py --target linux-x86_64 --out tools/probe-pack/build/out
python tools/probe-pack/build_pack.py --target linux-x86_64-musl --out tools/probe-pack/build/out \
    --extra-formats tar.xz,tar.zst
```

The script downloads into `--cache` (default `build/cache/`, one directory per
file) and refuses any file whose SHA-256 does not match the pin in the script.
It then writes `nodeglow-collector-pack-<target>.{zip|tar.gz}` and prints the
sizes and the archive's SHA-256.

Tar archives copy the runtime's members from the upstream tarball. This keeps
symlinks and exec bits intact when the pack is built on Windows.

To update a pin, take the new hash from the release's `SHA256SUMS` or from
PyPI, and keep it equal to the backend image's versions.

The `linux-x86_64-musl` runtime is dynamically linked against musl
(`/lib/ld-musl-x86_64.so.1`), so it runs on Alpine and similar systems only.
Use `linux-x86_64` (glibc 2.17 or later) everywhere else.

## Run

```
python/bin/python3 -I -X pycache_prefix=<workdir>/pycache app/runner.py    # Linux
python\python.exe -I -B app\runner.py                                      # Windows
```

Keep `-I`: it ignores `PYTHON*` variables, the user site and the current
directory. Use `-B` or `pycache_prefix` so that no bytecode is written into
the pack.

The runner speaks JSON lines on stdin/stdout:

```
{"op":"collect","id":"1","type":"proxmox","config":{...}}
  -> {"op":"result","id":"1","type":"proxmox","success":true,"data":{...},"error":null,"duration_ms":812.4}
{"op":"ping"} {"op":"stats"} {"op":"selftest"} {"op":"shutdown"}
```

`type` is the integration's `name`, not its module name: `hass` and `ups`
live in `homeassistant.py` and `nut.py`.

## Spike / fixtures

```
<pack>/python/python.exe -I -B <pack>/app/runner.py --selftest
<pack>/python/python.exe -I -B <pack>/app/runner.py --replay \
    backend/tests/fixtures/integrations/proxmox.json \
    backend/tests/fixtures/integrations/unifi.json \
    backend/tests/fixtures/integrations/synology.json
```

`--replay` swaps each module's `safe_async_client` for an
`httpx.MockTransport` that serves the fixture. It also fakes
`integrations.unifi.reverse_lookup` from the fixture's `reverse_dns`.

A replay fails in each of these cases:

- `collect()` fails.
- A request has no fixture entry.
- A fixture entry is never requested.
- The data differs from `<name>.expected.json` after canonical JSON
  (sorted keys).

The backend runs the same check in
`backend/tests/test_services/test_integration_fixtures.py`.

When a collector change is intended, regenerate the expected file and review
the diff before committing:

```
cd backend && python -c "import asyncio,sys; sys.path.insert(0,'../tools/probe-pack'); import replay; \
  f=replay.load_fixture('tests/fixtures/integrations/proxmox.json'); r=asyncio.run(replay.run_fixture(f)); \
  assert r['ok'], r['problems']; open('tests/fixtures/integrations/proxmox.expected.json','w',newline='\n').write(replay.canonical(r['data']))"
```

Fixtures use documentation addresses and names only: RFC 5737 IPs,
`example.com`/`example.net` and `02:00:00:…` MACs. They contain no real
hosts, serials or secrets.
