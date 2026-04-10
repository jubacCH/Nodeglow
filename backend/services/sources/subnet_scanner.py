"""Subnet scanner monitoring source.

Wraps the existing routers/subnet_scanner.run_scheduled_scans() function in
the MonitoringSource convention so it gets discovered, scheduled, and
instrumented automatically — no entry needed in scheduler.py.
"""
from __future__ import annotations

from services.monitoring_source import MonitoringSource


class SubnetScannerSource(MonitoringSource):
    name = "scheduled_scans"
    interval_seconds = 60

    async def poll(self) -> None:
        from routers.subnet_scanner import run_scheduled_scans
        await run_scheduled_scans()
