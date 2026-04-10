"""MonitoringSource implementations.

Each module in this package defines one MonitoringSource subclass. Importing
the package auto-discovers and registers them via MonitoringSource's
__init_subclass__ hook.

To add a new source: drop a file here, subclass MonitoringSource, set `name`
and `interval_seconds`, implement `async def poll(self)`. The scheduler will
pick it up on next startup.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil

logger = logging.getLogger(__name__)


def discover_all() -> int:
    """Import every submodule so its MonitoringSource subclass registers.

    Called once from the scheduler at startup. Returns the number of
    submodules successfully imported. Per-module import failures are logged
    but never propagate — a single broken source must not block all the rest.
    """
    count = 0
    for mod_info in pkgutil.iter_modules(__path__, prefix=__name__ + "."):
        if mod_info.name.endswith(".__init__"):
            continue
        try:
            importlib.import_module(mod_info.name)
            count += 1
        except Exception as exc:
            logger.warning("Failed to import monitoring source %s: %s",
                           mod_info.name, exc)
    return count
