#!/usr/bin/env python3
"""Build a Nodeglow probe collector pack (P0 spike).

Standard library only. Downloads a pinned python-build-standalone runtime and
pinned pure-Python wheels (SHA-256 checked against the pins below), adds the
backend's integration modules + utils/net_safety.py + the runner, strips what
a collector runtime does not need and writes the archive.

    python build_pack.py --target windows-x86_64 --out build/
    python build_pack.py --target linux-x86_64 --out build/
    python build_pack.py --target linux-x86_64-musl --out build/

Cross-target builds work from any OS: the runtime is a tarball and every
dependency is a py3-none-any wheel, so nothing has to run on the target.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BACKEND = REPO / "backend"

# ── Pins ─────────────────────────────────────────────────────────────────────
# Python and dependency versions equal the backend image's (python:3.12-slim,
# backend/requirements.txt httpx==0.28.1 and the versions it resolves to).
PBS_RELEASE = "20261009"
PBS_PYTHON = "3.12.15"
PBS_FLAVOR = "install_only_stripped"
PBS_URL = ("https://github.com/astral-sh/python-build-standalone/releases/download/"
           "{rel}/cpython-{py}+{rel}-{triple}-{flavor}.tar.gz")
# From the release's SHA256SUMS.
TARGETS = {
    "linux-x86_64": {
        "triple": "x86_64-unknown-linux-gnu", "archive": "tar.gz",
        "sha256": "ffa8f85f1b56e88b687f08d743d817015524b86a9365fd921243132b80f1a36d",
    },
    "linux-x86_64-musl": {
        "triple": "x86_64-unknown-linux-musl", "archive": "tar.gz",
        "sha256": "227ef2e296d65bda845d1890ff312ff142dbbbe7dad3c8c09aa060ba0ff22bea",
    },
    "windows-x86_64": {
        "triple": "x86_64-pc-windows-msvc", "archive": "zip",
        "sha256": "11ed9f8a6ac64fb1b5a55e40e305a6744a3f77a70863c8f8197a6e4349abc64a",
    },
}
# Pure-Python wheels (py3-none-any): the same set works on glibc, musl and
# Windows. sniffio is not needed: neither httpx 0.28 nor anyio 4.15 requires it.
WHEELS = [
    ("https://files.pythonhosted.org/packages/2a/39/e50c7c3a983047577ee07d2a9e53faf5a69493943ec3f6a384bdc792deb2/httpx-0.28.1-py3-none-any.whl",
     "d909fcccc110f8c7faf814ca82a9a4d816bc5a6dbfea25d6591d6985b8ba59ad"),
    ("https://files.pythonhosted.org/packages/7e/f5/f66802a942d491edb555dd61e3a9961140fd64c90bce1eafd741609d334d/httpcore-1.0.9-py3-none-any.whl",
     "2d400746a40668fc9dec9810239072b40b4484b640a8c38fd654a024c7a1bf55"),
    ("https://files.pythonhosted.org/packages/04/4b/29cac41a4d98d144bf5f6d33995617b185d14b22401f75ca86f384e87ff1/h11-0.16.0-py3-none-any.whl",
     "63cf8bbe7522de3bf65932fda1d9c2772064ffb3dae62d55932da54b31cb6c86"),
    ("https://files.pythonhosted.org/packages/12/b8/4bd346e22b28902df4d651910f5242c28d84e4a5c2435ca5c3f797ed7e2e/anyio-4.15.1-py3-none-any.whl",
     "6152fdbbf9a77fdec97731721bebf7c4c44f7c29b424b0065826173efc7ed101"),
    ("https://files.pythonhosted.org/packages/0b/a7/71ac2cff56fec219ed242bb11b8efb69fcc4bec75db06fb7bfe35de520e6/certifi-2026.7.22-py3-none-any.whl",
     "62f22742b58a1a33014a2b6b706588a8d7e2a88ae7bd1a6ebe8c992928483775"),
    ("https://files.pythonhosted.org/packages/58/a2/bb081bab032533a855d44de1d56f8e8426114ff1ba5d1f07a438a0a654f8/idna-3.20-py3-none-any.whl",
     "ab7ae7122974553370f0bdb919e1a960b2cd1bc1ef0276416d896db81c14582c"),
    ("https://files.pythonhosted.org/packages/49/d3/b8441a820a491ddfc024b0b0cf0393375b75ea13866d9c66727e54c2fc80/typing_extensions-4.16.0-py3-none-any.whl",
     "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8"),
]

# ── Stripping ────────────────────────────────────────────────────────────────
# Relative to the stdlib directory (python/Lib or python/lib/python3.12).
STDLIB_DROP = ["test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3",
               "pydoc_data", "turtle.py", "site-packages", "venv", "unittest",
               "__phello__", "config-3.12-x86_64-linux-gnu", "config-3.12-x86_64-linux-musl"]
# Globs relative to the runtime root (python/). Tcl/Tk is tkinter's.
ROOT_DROP = ["include", "share", "libs", "Scripts", "tcl", "lib/pkgconfig",
             "lib/itcl*", "lib/thread*", "lib/tcl*", "lib/tk*", "lib/tdbc*", "lib/libtcl*", "lib/libtk*",
             "bin/idle3*", "bin/pydoc3*", "bin/2to3*", "bin/pip*", "bin/python*-config",
             "pythonw.exe"]
# Extension modules a collector runtime never imports (lib-dynload / DLLs).
EXT_DROP_PREFIXES = ("_tkinter", "_test", "_ctypes_test", "xxlimited", "xxsubtype", "_xxtestfuzz",
                     "_xxsubinterpreters", "_xxinterpchannels", "tcl86t", "tk86t",
                     "_dbm", "_gdbm", "_crypt", "_msi", "winsound")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, sha256: str, cache: Path) -> Path:
    """Download into its own cache directory (keyed by hash) and verify."""
    dest_dir = cache / sha256[:16]
    dest = dest_dir / url.rsplit("/", 1)[1].replace("%2B", "+")
    if not dest.exists():
        dest_dir.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        log(f"download {url}")
        with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as fh:
            shutil.copyfileobj(resp, fh)
        tmp.replace(dest)
    got = sha256_file(dest)
    if got != sha256:
        dest.unlink()
        raise SystemExit(f"SHA-256 mismatch for {url}: got {got}, pinned {sha256}")
    return dest


def tree_size(path: Path) -> tuple[int, int]:
    total = count = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            p = os.path.join(root, f)
            if not os.path.islink(p):
                total += os.path.getsize(p)
                count += 1
    return total, count


def runtime_size(runtime_tar: Path, stage: Path | None = None) -> tuple[int, int]:
    """Regular-file bytes/count of the runtime as a Linux host unpacks it
    (symlinks count zero; a Windows extraction turns them into copies).
    With ``stage``, only members that survived stripping."""
    total = count = 0
    with tarfile.open(runtime_tar) as tf:
        for m in tf:
            if not m.isfile():
                continue
            if stage is not None and not os.path.lexists(stage / m.name.removeprefix("./")):
                continue
            total += m.size
            count += 1
    return total, count


def rm(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def is_windows_runtime(runtime: Path) -> bool:
    return (runtime / "python.exe").is_file()


def stdlib_dir(runtime: Path) -> Path:
    # Not "Lib".is_dir(): on a case-insensitive filesystem that also matches lib/.
    if is_windows_runtime(runtime):
        return runtime / "Lib"
    return runtime / "lib" / f"python{PBS_PYTHON.rsplit('.', 1)[0]}"


def strip_runtime(runtime: Path, keep_pyc: bool) -> list[str]:
    removed = []
    lib = stdlib_dir(runtime)
    for rel in STDLIB_DROP:
        p = lib / rel
        if p.exists():
            rm(p)
            removed.append(f"stdlib/{rel}")
    windows = is_windows_runtime(runtime)
    for pattern in ROOT_DROP:
        if windows and pattern.startswith(("lib/", "bin/")):
            continue  # lib/ is Lib/ (the stdlib) on a case-insensitive filesystem
        for p in sorted(runtime.glob(pattern)):
            rm(p)
            removed.append(p.relative_to(runtime).as_posix())
    for ext_dir in (runtime / "DLLs", lib / "lib-dynload"):
        if ext_dir.is_dir():
            for f in ext_dir.iterdir():
                if f.name.startswith(EXT_DROP_PREFIXES):
                    rm(f)
                    removed.append(f"{ext_dir.name}/{f.name}")
    if not is_windows_runtime(runtime):
        # libpython*.a (static, for embedding) always goes. The shared
        # libpython only when the interpreter does not link it: the
        # python-build-standalone binary embeds libpython, so the .so is
        # there for embedders only.
        exe = (runtime / "bin" / f"python{PBS_PYTHON.rsplit('.', 1)[0]}").read_bytes()
        patterns = ["libpython*.a"] + ([] if b"libpython3" in exe else ["libpython*.so*"])
        for pattern in patterns:
            for f in sorted((runtime / "lib").glob(pattern)):
                rm(f)
                removed.append(f"lib/{f.name}")
    if not keep_pyc:
        for d in list(runtime.rglob("__pycache__")):
            rm(d)
        removed.append("**/__pycache__")
    return removed


def install_wheels(wheels: list[Path], vendor: Path) -> list[str]:
    vendor.mkdir(parents=True)
    binary = []
    for whl in wheels:
        if not whl.name.endswith("-py3-none-any.whl"):
            raise SystemExit(f"{whl.name} is not a pure-Python wheel")
        with zipfile.ZipFile(whl) as zf:
            for name in zf.namelist():
                if name.startswith("/") or ".." in Path(name).parts:
                    raise SystemExit(f"unsafe path {name!r} in {whl.name}")
            zf.extractall(vendor)
    for p in vendor.rglob("*"):
        if p.suffix.lower() in (".so", ".pyd", ".dll", ".dylib") or ".so." in p.name:
            binary.append(str(p.relative_to(vendor)))
    if binary:
        raise SystemExit(f"binary files in vendored deps (musl runtime cannot load them): {binary}")
    return [w.name for w in wheels]


def copy_app(app: Path) -> list[str]:
    (app / "integrations").mkdir(parents=True)
    (app / "utils").mkdir()
    modules = []
    for src in sorted((BACKEND / "integrations").glob("*.py")):
        shutil.copy2(src, app / "integrations" / src.name)
        modules.append(src.name)
    shutil.copy2(BACKEND / "utils" / "net_safety.py", app / "utils" / "net_safety.py")
    with open(app / "utils" / "__init__.py", "w", encoding="utf-8", newline="\n") as fh:
        fh.write('"""utils subset shipped in the probe collector pack."""\n')
    for name in ("runner.py", "replay.py"):
        shutil.copy2(HERE / name, app / name)
    return modules


def _write_tar(tf: tarfile.TarFile, stage: Path, runtime_tar: Path) -> None:
    """Runtime members are copied from the original tarball (modes, symlinks
    and ownership survive a build on Windows), minus what stripping removed;
    the app directory is added with normalised permissions."""
    with tarfile.open(runtime_tar) as src:
        for m in src:
            rel = m.name.removeprefix("./")
            if not os.path.lexists(stage / rel):
                continue
            m.uid = m.gid = 0
            m.uname = m.gname = ""
            tf.addfile(m, src.extractfile(m) if m.isfile() else None)
    tf.add(stage / "app", arcname="app", filter=_app_filter)


def _app_filter(ti: tarfile.TarInfo) -> tarfile.TarInfo:
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    ti.mtime = 0
    ti.mode = 0o755 if ti.isdir() else 0o644
    return ti


def make_archive(stage: Path, out_file: Path, fmt: str, runtime_tar: Path) -> None:
    if fmt == "zip":
        with zipfile.ZipFile(out_file, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for p in sorted(stage.rglob("*")):
                if p.is_file():
                    zf.write(p, p.relative_to(stage).as_posix())
        return
    if fmt == "tar.zst":
        try:
            from compression import zstd  # Python 3.14+
        except ImportError:
            raise SystemExit("tar.zst needs Python 3.14+ (compression.zstd)") from None
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
            _write_tar(tf, stage, runtime_tar)
        out_file.write_bytes(zstd.compress(buf.getvalue(), level=19))
        return
    if fmt not in ("tar.gz", "tar.xz"):
        raise SystemExit(f"unknown archive format {fmt}")
    kwargs = {"preset": 9} if fmt == "tar.xz" else {"compresslevel": 9}
    with tarfile.open(out_file, "w:" + fmt[4:], format=tarfile.PAX_FORMAT, **kwargs) as tf:
        _write_tar(tf, stage, runtime_tar)


def mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", required=True, choices=sorted(TARGETS))
    ap.add_argument("--out", required=True, type=Path, help="output directory")
    ap.add_argument("--cache", type=Path, default=HERE / "build" / "cache",
                    help="download cache (each file in its own directory)")
    ap.add_argument("--keep-pyc", action="store_true", help="keep the runtime's __pycache__")
    ap.add_argument("--no-strip", action="store_true")
    ap.add_argument("--extra-formats", default="", help="comma list: tar.xz,tar.zst,zip,tar.gz")
    args = ap.parse_args(argv)

    tgt = TARGETS[args.target]
    url = PBS_URL.format(rel=PBS_RELEASE, py=PBS_PYTHON, triple=tgt["triple"], flavor=PBS_FLAVOR)
    runtime_tgz = fetch(url, tgt["sha256"], args.cache)
    wheels = [fetch(u, h, args.cache) for u, h in WHEELS]

    out = args.out.resolve()
    stage = out / f"stage-{args.target}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    with tarfile.open(runtime_tgz) as tf:
        tf.extractall(stage, filter="data")  # archive root is python/
    runtime = stage / "python"
    unpacked, unpacked_n = runtime_size(runtime_tgz)

    app = stage / "app"
    modules = copy_app(app)
    install_wheels(wheels, app / "vendor")

    removed = [] if args.no_strip else strip_runtime(runtime, args.keep_pyc)
    stripped, stripped_n = runtime_size(runtime_tgz, stage)
    app_size, _ = tree_size(app)

    manifest = {
        "format": 1, "target": args.target, "built_at": int(time.time()),
        "python": PBS_PYTHON, "pbs_release": PBS_RELEASE, "pbs_flavor": PBS_FLAVOR,
        "pbs_triple": tgt["triple"], "pbs_sha256": tgt["sha256"],
        "wheels": {Path(u).name: h for u, h in WHEELS},
        "integrations": modules, "stripped": removed,
        "python_exe": "python/python.exe" if args.target.startswith("windows") else "python/bin/python3",
        "entrypoint": "app/runner.py",
    }
    with open(app / "pack-manifest.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=1)

    formats = [tgt["archive"]] + [f for f in args.extra_formats.split(",") if f and f != tgt["archive"]]
    archives = {}
    for fmt in formats:
        dest = out / f"nodeglow-collector-pack-{args.target}.{fmt}"
        make_archive(stage, dest, fmt, runtime_tgz)
        archives[fmt] = {"path": str(dest), "bytes": dest.stat().st_size, "sha256": sha256_file(dest)}

    report = {
        "target": args.target,
        "download_bytes": runtime_tgz.stat().st_size,
        "runtime_unpacked_bytes": unpacked, "runtime_unpacked_files": unpacked_n,
        "runtime_stripped_bytes": stripped, "runtime_stripped_files": stripped_n,
        "app_bytes": app_size, "pack_on_disk_bytes": stripped + app_size,
        "archives": archives,
    }
    log(f"{args.target}: download {mb(report['download_bytes'])}, unpacked {mb(unpacked)} "
        f"({unpacked_n} files), stripped {mb(stripped)} ({stripped_n} files) + app {mb(app_size)}")
    for fmt, a in archives.items():
        log(f"  {fmt}: {mb(a['bytes'])}  sha256 {a['sha256']}")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
