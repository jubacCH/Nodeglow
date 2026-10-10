"""Finds and loads optional plugins — the only core module that knows ``ee/`` exists.

A plugin is an object (or module) with a ``register(registry)`` function and
an optional ``name``/``edition`` attribute. ``register`` receives
:data:`extensions.registry` and adds routers, scheduler hooks, feature flags
and so on (see ``extensions.py``).

Discovery, in this order (the first source that yields a plugin of a given
name wins; nothing found is the normal community case):

1. Python entry points in the group ``nodeglow.plugins`` — an installed
   package, e.g. ``pip install ./ee/backend``.
2. The enterprise source tree: the directory in ``NODEGLOW_EE_PATH``, else
   ``../ee/backend`` relative to this file (a repository checkout). If it
   contains the ``nodeglow_ee`` package, that directory is put on
   ``sys.path`` and ``nodeglow_ee.plugin`` is loaded.

``NODEGLOW_DISABLE_EE=1`` skips discovery entirely: the process runs as the
community edition even when the enterprise code is present. The core test
suite runs this way to prove it does not need ``ee/``.

Loading is best effort: a plugin that fails to import or register is logged
and skipped; the core always starts.
"""
from __future__ import annotations

import importlib
import logging
import os
import sys
from pathlib import Path

from extensions import EDITION_ENTERPRISE, Registry, registry

log = logging.getLogger("nodeglow.ee_loader")

ENTRY_POINT_GROUP = "nodeglow.plugins"
DISABLE_ENV = "NODEGLOW_DISABLE_EE"
PATH_ENV = "NODEGLOW_EE_PATH"
EE_PACKAGE = "nodeglow_ee"

_TRUE = ("1", "true", "yes", "on")
_loaded = False


def ee_disabled() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip().lower() in _TRUE


def _source_tree() -> Path | None:
    configured = os.environ.get(PATH_ENV, "").strip()
    candidates = [Path(configured)] if configured else []
    candidates.append(Path(__file__).resolve().parent.parent / "ee" / "backend")
    for path in candidates:
        if (path / EE_PACKAGE / "__init__.py").is_file():
            return path
    return None


def _from_entry_points() -> list[tuple[str, object]]:
    from importlib.metadata import entry_points

    found = []
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            found.append((ep.name, ep.load()))
        except Exception:
            log.exception("Plugin entry point %r failed to load — skipped", ep.name)
    return found


def _from_source_tree() -> list[tuple[str, object]]:
    path = _source_tree()
    if path is None:
        return []
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    try:
        module = importlib.import_module(EE_PACKAGE)
    except Exception:
        log.exception("Enterprise package at %s failed to import — running as community", path)
        return []
    return [(getattr(module, "NAME", "ee"), getattr(module, "plugin", module))]


def discover() -> list[tuple[str, object]]:
    """``[(name, plugin), ...]`` that would be loaded, without registering them."""
    if ee_disabled():
        return []
    plugins: dict[str, object] = {}
    for name, plugin in _from_entry_points() + _from_source_tree():
        plugins.setdefault(name, plugin)
    return list(plugins.items())


def load_plugins(reg: Registry = registry) -> list[str]:
    """Discover plugins and let each register itself. Idempotent per process."""
    global _loaded
    if _loaded and reg is registry:
        return list(reg.plugins)
    if ee_disabled():
        log.info("%s is set — running as the community edition", DISABLE_ENV)
    for name, plugin in discover():
        register = getattr(plugin, "register", None)
        if not callable(register):
            log.error("Plugin %r has no register() — skipped", name)
            continue
        try:
            register(reg)
        except Exception:
            log.exception("Plugin %r failed to register — skipped", name)
            continue
        reg.plugins.append(name)
        if getattr(plugin, "EDITION", None) == EDITION_ENTERPRISE or name == "ee":
            reg.edition = EDITION_ENTERPRISE
        log.info("Plugin loaded: %s", name)
    if reg is registry:
        _loaded = True
    log.info("Nodeglow edition: %s", reg.edition)
    return list(reg.plugins)


def mount_routers(app, reg: Registry = registry) -> None:
    """Include every router the plugins registered."""
    for router in reg.routers:
        app.include_router(router)
