"""SNMP service – MIB parsing, OID resolution, SNMP polling, and MIB library."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.base import decrypt_value, encrypt_value
from models.credential import Credential
from models.snmp import SnmpHostConfig, SnmpMib, SnmpOid, SnmpResult

logger = logging.getLogger(__name__)

# ── MIB Library (online) ─────────────────────────────────────────────────────

MIB_SEARCH_URL = "https://mibs.observium.org/search.php"
MIB_DOWNLOAD_URLS = [
    # Try LibreNMS first (most comprehensive), then standard dirs
    "https://raw.githubusercontent.com/librenms/librenms/master/mibs/{name}",
    "https://raw.githubusercontent.com/librenms/librenms/master/mibs/{vendor}/{name}",
    "https://raw.githubusercontent.com/observium/observium-community/master/mibs/{vendor}/{name}",
]

# Simple in-memory cache for search results
_search_cache: dict[str, tuple[float, list]] = {}
_SEARCH_CACHE_TTL = 300  # 5 minutes


async def search_mib_library(query: str) -> list[dict]:
    """Search the online MIB library (mibs.observium.org)."""
    if len(query) < 2:
        return []

    cache_key = query.lower()
    now = time.time()
    if cache_key in _search_cache:
        ts, results = _search_cache[cache_key]
        if now - ts < _SEARCH_CACHE_TTL:
            return results

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(MIB_SEARCH_URL, params={"q": query})
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning("MIB library search failed: %s", e)
        return []

    results = []
    for item in data.get("results", []):
        if item.get("type") != "mib":
            continue
        results.append({
            "name": item.get("name", ""),
            "description": item.get("description", ""),
            "vendor": item.get("dir", ""),
            "oid_count": item.get("oid_count", 0),
        })

    _search_cache[cache_key] = (now, results)
    return results


async def download_mib_from_library(mib_name: str, vendor: str = "") -> str | None:
    """Download a MIB file from online repositories. Returns MIB text or None."""
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        # Try each URL pattern
        for pattern in MIB_DOWNLOAD_URLS:
            url = pattern.format(name=mib_name, vendor=vendor)
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    text = resp.text
                    # Basic validation — should contain DEFINITIONS ::= BEGIN
                    if "DEFINITIONS" in text and "BEGIN" in text:
                        logger.info("Downloaded MIB %s from %s", mib_name, url)
                        return text
            except Exception:
                continue

        # If vendor-specific didn't work, try common vendor subdirectories
        if vendor:
            common_dirs = [vendor.lower(), vendor.upper(), vendor.capitalize()]
        else:
            common_dirs = ["rfc", "ietf", "cisco", "net-snmp"]

        for vdir in common_dirs:
            for pattern in MIB_DOWNLOAD_URLS:
                if "{vendor}" not in pattern:
                    continue
                url = pattern.format(name=mib_name, vendor=vdir)
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        text = resp.text
                        if "DEFINITIONS" in text and "BEGIN" in text:
                            logger.info("Downloaded MIB %s from %s", mib_name, url)
                            return text
                except Exception:
                    continue

    logger.warning("Could not download MIB %s from any source", mib_name)
    return None

# ── Default OIDs (always available without MIB import) ───────────────────────

DEFAULT_OIDS = {
    # System
    "1.3.6.1.2.1.1.1.0": ("sysDescr", "SNMPv2-MIB", "DisplayString"),
    "1.3.6.1.2.1.1.3.0": ("sysUpTime", "SNMPv2-MIB", "TimeTicks"),
    "1.3.6.1.2.1.1.5.0": ("sysName", "SNMPv2-MIB", "DisplayString"),
    "1.3.6.1.2.1.1.6.0": ("sysLocation", "SNMPv2-MIB", "DisplayString"),
    "1.3.6.1.2.1.1.4.0": ("sysContact", "SNMPv2-MIB", "DisplayString"),
    # Interfaces
    "1.3.6.1.2.1.2.1.0": ("ifNumber", "IF-MIB", "Integer32"),
    # Host Resources (CPU, Memory, Disk)
    "1.3.6.1.2.1.25.1.1.0": ("hrSystemUptime", "HOST-RESOURCES-MIB", "TimeTicks"),
    "1.3.6.1.2.1.25.2.2.0": ("hrMemorySize", "HOST-RESOURCES-MIB", "KBytes"),
    "1.3.6.1.2.1.25.3.3.1.2": ("hrProcessorLoad", "HOST-RESOURCES-MIB", "Integer32"),
    # Storage table (walk)
    "1.3.6.1.2.1.25.2.3.1.2": ("hrStorageType", "HOST-RESOURCES-MIB", "OID"),
    "1.3.6.1.2.1.25.2.3.1.3": ("hrStorageDescr", "HOST-RESOURCES-MIB", "DisplayString"),
    "1.3.6.1.2.1.25.2.3.1.4": ("hrStorageAllocationUnits", "HOST-RESOURCES-MIB", "Integer32"),
    "1.3.6.1.2.1.25.2.3.1.5": ("hrStorageSize", "HOST-RESOURCES-MIB", "Integer32"),
    "1.3.6.1.2.1.25.2.3.1.6": ("hrStorageUsed", "HOST-RESOURCES-MIB", "Integer32"),
    # Interface table (walk)
    "1.3.6.1.2.1.2.2.1.2": ("ifDescr", "IF-MIB", "DisplayString"),
    "1.3.6.1.2.1.2.2.1.8": ("ifOperStatus", "IF-MIB", "Integer32"),
    "1.3.6.1.2.1.2.2.1.10": ("ifInOctets", "IF-MIB", "Counter32"),
    "1.3.6.1.2.1.2.2.1.16": ("ifOutOctets", "IF-MIB", "Counter32"),
    "1.3.6.1.2.1.31.1.1.1.6": ("ifHCInOctets", "IF-MIB", "Counter64"),
    "1.3.6.1.2.1.31.1.1.1.10": ("ifHCOutOctets", "IF-MIB", "Counter64"),
}

# Common OID sets for quick configuration
OID_PRESETS = {
    "system": [
        "1.3.6.1.2.1.1.1.0", "1.3.6.1.2.1.1.3.0", "1.3.6.1.2.1.1.5.0",
    ],
    "host_resources": [
        "1.3.6.1.2.1.25.1.1.0", "1.3.6.1.2.1.25.2.2.0",
        "1.3.6.1.2.1.25.3.3.1.2",  # CPU load (walk)
        "1.3.6.1.2.1.25.2.3.1.3", "1.3.6.1.2.1.25.2.3.1.5",
        "1.3.6.1.2.1.25.2.3.1.6",  # Storage (walk)
    ],
    "interfaces": [
        "1.3.6.1.2.1.2.1.0",
        "1.3.6.1.2.1.2.2.1.2", "1.3.6.1.2.1.2.2.1.8",
        "1.3.6.1.2.1.31.1.1.1.6", "1.3.6.1.2.1.31.1.1.1.10",
    ],
    "full": None,  # all of the above
}


# ── MIB Parser ───────────────────────────────────────────────────────────────

_RE_MODULE = re.compile(r"^(\S+)\s+DEFINITIONS\s*::=\s*BEGIN", re.MULTILINE)

# Catch ALL  ::= { parent num } assignments (MODULE-IDENTITY, OBJECT-TYPE,
# OBJECT-IDENTITY, NOTIFICATION-TYPE, OBJECT IDENTIFIER, etc.)
_RE_ANY_ASSIGN = re.compile(
    r"(\w+)\s+(?:OBJECT\s+IDENTIFIER|MODULE-IDENTITY|OBJECT-IDENTITY"
    r"|OBJECT-TYPE|NOTIFICATION-TYPE|NOTIFICATION-GROUP"
    r"|MODULE-COMPLIANCE|OBJECT-GROUP|AGENT-CAPABILITIES"
    r"|TEXTUAL-CONVENTION)"
    r"\b.*?::=\s*\{\s*(\w[\w-]*)\s+(\d+)\s*\}",
    re.DOTALL,
)

# OBJECT-TYPE with SYNTAX (for extracting type info)
_RE_OBJECT_TYPE = re.compile(
    r"(\w+)\s+OBJECT-TYPE\s+"
    r"(.*?)"
    r"::=\s*\{\s*(\w[\w-]*)\s+(\d+)\s*\}",
    re.DOTALL,
)

_RE_SYNTAX = re.compile(r"SYNTAX\s+([\w\d]+(?:\s*\([^)]*\))?)", re.MULTILINE)
_RE_DESCRIPTION = re.compile(r'DESCRIPTION\s*"((?:[^"\\]|\\.|"")*)"', re.DOTALL)

# Well-known OID tree roots (RFC standard)
_KNOWN_ROOTS: dict[str, str] = {
    "iso": "1",
    "org": "1.3",
    "dod": "1.3.6",
    "internet": "1.3.6.1",
    "directory": "1.3.6.1.1",
    "mgmt": "1.3.6.1.2",
    "mib-2": "1.3.6.1.2.1",
    "system": "1.3.6.1.2.1.1",
    "interfaces": "1.3.6.1.2.1.2",
    "at": "1.3.6.1.2.1.3",
    "ip": "1.3.6.1.2.1.4",
    "icmp": "1.3.6.1.2.1.5",
    "tcp": "1.3.6.1.2.1.6",
    "udp": "1.3.6.1.2.1.7",
    "egp": "1.3.6.1.2.1.8",
    "transmission": "1.3.6.1.2.1.10",
    "snmp": "1.3.6.1.2.1.11",
    "host": "1.3.6.1.2.1.25",
    "hrSystem": "1.3.6.1.2.1.25.1",
    "hrStorage": "1.3.6.1.2.1.25.2",
    "hrDevice": "1.3.6.1.2.1.25.3",
    "ifMIB": "1.3.6.1.2.1.31",
    "experimental": "1.3.6.1.3",
    "private": "1.3.6.1.4",
    "enterprises": "1.3.6.1.4.1",
    "security": "1.3.6.1.5",
    "snmpV2": "1.3.6.1.6",
    "snmpDomains": "1.3.6.1.6.1",
    "snmpProxys": "1.3.6.1.6.2",
    "snmpModules": "1.3.6.1.6.3",
    "snmpMIB": "1.3.6.1.6.3.1",
    "zeroDotZero": "0.0",
}


def parse_mib_text(mib_text: str) -> tuple[str, list[dict]]:
    """Parse a MIB file and extract OID definitions."""
    m = _RE_MODULE.search(mib_text)
    module_name = m.group(1) if m else "UNKNOWN"

    # Phase 1: Build the OID tree from ALL assignment types
    oid_tree: dict[str, tuple[str, int]] = {}
    for m in _RE_ANY_ASSIGN.finditer(mib_text):
        name, parent, num_str = m.group(1), m.group(2), m.group(3)
        try:
            oid_tree[name] = (parent, int(num_str))
        except ValueError:
            pass

    # Also catch plain  OBJECT IDENTIFIER ::= { parent num } on one line
    for m in re.finditer(
        r"(\w+)\s+OBJECT\s+IDENTIFIER\s*::=\s*\{\s*(\w[\w-]*)\s+(\d+)\s*\}",
        mib_text,
    ):
        name, parent, num_str = m.group(1), m.group(2), m.group(3)
        if name not in oid_tree:
            try:
                oid_tree[name] = (parent, int(num_str))
            except ValueError:
                pass

    # Phase 2: Resolve full numeric OIDs
    known = dict(_KNOWN_ROOTS)

    def resolve(name: str, seen: set | None = None) -> str | None:
        if name in known:
            return known[name]
        if name not in oid_tree:
            return None
        if seen is None:
            seen = set()
        if name in seen:
            return None
        seen.add(name)
        parent, num = oid_tree[name]
        parent_oid = resolve(parent, seen)
        if parent_oid:
            full = f"{parent_oid}.{num}"
            known[name] = full
            return full
        return None

    for name in list(oid_tree.keys()):
        resolve(name)

    # Phase 3: Extract OBJECT-TYPE entries with syntax/description
    entries = []
    seen_oids: set[str] = set()

    for m in _RE_OBJECT_TYPE.finditer(mib_text):
        obj_name = m.group(1)
        body = m.group(2)
        parent_name = m.group(3)
        try:
            num = int(m.group(4))
        except ValueError:
            continue

        parent_oid = known.get(parent_name)
        if not parent_oid:
            continue
        full_oid = f"{parent_oid}.{num}"
        known[obj_name] = full_oid

        if full_oid in seen_oids:
            continue
        seen_oids.add(full_oid)

        # Extract SYNTAX
        syntax = ""
        sm = _RE_SYNTAX.search(body)
        if sm:
            syntax = sm.group(1).split()[0]

        # Extract DESCRIPTION
        description = ""
        dm = _RE_DESCRIPTION.search(body)
        if dm:
            description = dm.group(1).replace('""', '"').strip()[:500]

        is_table = bool(sm and "SEQUENCE" in sm.group(1))

        entries.append({
            "oid": full_oid,
            "name": obj_name,
            "syntax": syntax,
            "description": description,
            "is_table": is_table,
        })

    # Phase 4: Also add MODULE-IDENTITY, OBJECT-IDENTITY etc. as browsable entries
    # (these appear in MIB browsers but aren't polled directly)
    for name, full_oid in known.items():
        if name in _KNOWN_ROOTS or full_oid in seen_oids:
            continue
        if name not in oid_tree:
            continue
        seen_oids.add(full_oid)
        entries.append({
            "oid": full_oid,
            "name": name,
            "syntax": "",
            "description": "",
            "is_table": False,
        })

    return module_name, entries


async def import_mib(db: AsyncSession, filename: str, mib_text: str) -> tuple[str, int]:
    """Import a MIB file: parse it and store OID definitions."""
    module_name, entries = parse_mib_text(mib_text)

    # Upsert MIB record
    q = await db.execute(select(SnmpMib).where(SnmpMib.name == module_name))
    mib = q.scalar_one_or_none()
    if mib:
        mib.filename = filename
        mib.raw_text = mib_text
        mib.uploaded_at = datetime.utcnow()
        # Delete old OIDs from this MIB
        await db.execute(delete(SnmpOid).where(SnmpOid.mib_name == module_name))
    else:
        mib = SnmpMib(name=module_name, filename=filename, raw_text=mib_text)
        db.add(mib)

    # Insert OID entries
    added = 0
    for entry in entries:
        # Check if OID already exists (from another MIB)
        existing = await db.execute(
            select(SnmpOid).where(SnmpOid.oid == entry["oid"])
        )
        if existing.scalar_one_or_none():
            continue
        db.add(SnmpOid(
            oid=entry["oid"],
            name=entry["name"],
            mib_name=module_name,
            syntax=entry.get("syntax"),
            description=entry.get("description"),
            is_table=entry.get("is_table", False),
        ))
        added += 1

    mib.oid_count = added
    await db.commit()
    return module_name, added


async def seed_default_oids(db: AsyncSession) -> int:
    """Seed the default OID definitions (run once at startup)."""
    added = 0
    mib_names_seen: set[str] = set()
    for oid, (name, mib_name, syntax) in DEFAULT_OIDS.items():
        # Ensure a SnmpMib record exists for each referenced MIB
        if mib_name not in mib_names_seen:
            mib_names_seen.add(mib_name)
            existing_mib = await db.execute(
                select(SnmpMib).where(SnmpMib.name == mib_name)
            )
            if not existing_mib.scalar_one_or_none():
                db.add(SnmpMib(name=mib_name, filename="built-in", oid_count=0))

        existing = await db.execute(select(SnmpOid).where(SnmpOid.oid == oid))
        if existing.scalar_one_or_none():
            continue
        db.add(SnmpOid(oid=oid, name=name, mib_name=mib_name, syntax=syntax))
        added += 1

    # Update oid_count on seeded MIB records
    if added:
        for mib_name in mib_names_seen:
            count_q = await db.execute(
                select(func.count(SnmpOid.id)).where(SnmpOid.mib_name == mib_name)
            )
            count = count_q.scalar() or 0
            mib_q = await db.execute(
                select(SnmpMib).where(SnmpMib.name == mib_name)
            )
            mib_rec = mib_q.scalar_one_or_none()
            if mib_rec:
                mib_rec.oid_count = count
        await db.commit()
    return added


# ── OID Resolution Cache ────────────────────────────────────────────────────

_oid_cache: dict[str, str] = {}
_oid_cache_ts: float = 0


async def get_oid_name(db: AsyncSession, oid: str) -> str:
    """Resolve an OID to its human-readable name."""
    global _oid_cache, _oid_cache_ts
    import time
    now = time.time()
    if now - _oid_cache_ts > 300:  # refresh every 5 minutes
        q = await db.execute(select(SnmpOid))
        _oid_cache = {r.oid: r.name for r in q.scalars().all()}
        _oid_cache_ts = now

    # Exact match
    if oid in _oid_cache:
        return _oid_cache[oid]

    # Try prefix match (for table entries like 1.3.6.1.2.1.2.2.1.2.3)
    parts = oid.split(".")
    for i in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:i])
        if prefix in _oid_cache:
            index = ".".join(parts[i:])
            return f"{_oid_cache[prefix]}.{index}"

    return oid


# ── SNMP Polling ─────────────────────────────────────────────────────────────


async def snmp_get(host: str, port: int, community: str, oids: list[str],
                   timeout: float = 5) -> dict[str, str | int | float]:
    """
    Perform SNMP GET for a list of OIDs using subprocess (snmpget).
    Falls back to snmpwalk for table OIDs.
    Returns {oid: value} dict.
    """
    results = {}

    # Use net-snmp CLI tools (available in most Linux containers)
    for oid in oids:
        try:
            proc = await asyncio.create_subprocess_exec(
                "snmpget", "-v2c", "-c", community, "-t", str(int(timeout)),
                "-r", "1", "-On", "-Oq",
                "--", f"{host}:{port}", oid,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout + 2)
            output = stdout.decode("utf-8", errors="replace").strip()

            if proc.returncode == 0 and output:
                # Parse "OID value" format
                for line in output.splitlines():
                    parts = line.split(None, 1)
                    if len(parts) == 2:
                        raw_oid = parts[0].lstrip(".")
                        value = parts[1].strip().strip('"')
                        results[raw_oid] = _parse_snmp_value(value)
            elif proc.returncode == 2 or "noSuchInstance" in output or "noSuchObject" in output:
                # Try walk for table OIDs
                walk_results = await snmp_walk(host, port, community, oid, timeout)
                results.update(walk_results)
        except (asyncio.TimeoutError, Exception) as e:
            logger.debug("SNMP GET %s:%d %s failed: %s", host, port, oid, e)

    return results


async def snmp_walk(host: str, port: int, community: str, oid: str,
                    timeout: float = 10) -> dict[str, str | int | float]:
    """SNMP WALK a subtree."""
    results = {}
    try:
        proc = await asyncio.create_subprocess_exec(
            "snmpwalk", "-v2c", "-c", community, "-t", str(int(timeout)),
            "-r", "1", "-On", "-Oq",
            "--", f"{host}:{port}", oid,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout + 5)
        output = stdout.decode("utf-8", errors="replace").strip()

        if proc.returncode == 0 and output:
            for line in output.splitlines():
                parts = line.split(None, 1)
                if len(parts) == 2:
                    raw_oid = parts[0].lstrip(".")
                    value = parts[1].strip().strip('"')
                    results[raw_oid] = _parse_snmp_value(value)
    except (asyncio.TimeoutError, Exception) as e:
        logger.debug("SNMP WALK %s:%d %s failed: %s", host, port, oid, e)

    return results


async def snmp_v3_get(host: str, port: int, username: str, auth_proto: str,
                      auth_pass: str, priv_proto: str, priv_pass: str,
                      oids: list[str], timeout: float = 5) -> dict:
    """SNMP v3 GET using net-snmp CLI."""
    results = {}
    sec_level = "noAuthNoPriv"
    args = ["snmpget", "-v3", "-t", str(int(timeout)), "-r", "1", "-On", "-Oq",
            "-u", username]

    if auth_pass:
        sec_level = "authNoPriv"
        args += ["-a", auth_proto or "SHA", "-A", auth_pass]
    if priv_pass:
        sec_level = "authPriv"
        args += ["-x", priv_proto or "AES", "-X", priv_pass]

    args += ["-l", sec_level, "--", f"{host}:{port}"]

    for oid in oids:
        try:
            proc = await asyncio.create_subprocess_exec(
                *args, oid,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout + 2)
            output = stdout.decode("utf-8", errors="replace").strip()
            if proc.returncode == 0 and output:
                for line in output.splitlines():
                    parts = line.split(None, 1)
                    if len(parts) == 2:
                        raw_oid = parts[0].lstrip(".")
                        results[raw_oid] = _parse_snmp_value(parts[1].strip().strip('"'))
        except (asyncio.TimeoutError, Exception) as e:
            logger.debug("SNMPv3 GET %s:%d %s failed: %s", host, port, oid, e)

    return results


def _parse_snmp_value(value: str):
    """Try to parse SNMP value to appropriate Python type."""
    # Integer
    try:
        return int(value)
    except ValueError:
        pass
    # Float
    try:
        return float(value)
    except ValueError:
        pass
    # Timeticks: (123456) 1:23:45.67
    if value.startswith("(") and ")" in value:
        try:
            return int(value[1:value.index(")")])
        except ValueError:
            pass
    return value


# ── Credential helpers ───────────────────────────────────────────────────────


def encrypt_credential(data: dict) -> str:
    return encrypt_value(json.dumps(data))


def decrypt_credential(encrypted: str) -> dict:
    return json.loads(decrypt_value(encrypted))


async def get_credential(db: AsyncSession, credential_id: int) -> dict | None:
    """Load and decrypt a credential."""
    q = await db.execute(select(Credential).where(Credential.id == credential_id))
    cred = q.scalar_one_or_none()
    if not cred:
        return None
    return {"id": cred.id, "name": cred.name, "type": cred.type,
            **decrypt_credential(cred.data_json)}


# ── Poll a single host ──────────────────────────────────────────────────────


async def poll_host(db: AsyncSession, config: SnmpHostConfig,
                    hostname: str) -> dict:
    """Poll a single host's SNMP OIDs and store the result."""
    # Load credential
    cred_data = {}
    if config.credential_id:
        cred_data = await get_credential(db, config.credential_id) or {}

    # Determine OIDs to poll
    oids_to_poll = []
    if config.oids_json:
        oids_to_poll = json.loads(config.oids_json)
    else:
        # Default: system + host resources
        oids_to_poll = list(DEFAULT_OIDS.keys())

    port = config.port or 161
    cred_type = cred_data.get("type", "snmp_v2c")

    # Execute SNMP query
    if cred_type == "snmp_v3":
        raw = await snmp_v3_get(
            hostname, port,
            username=cred_data.get("username", ""),
            auth_proto=cred_data.get("auth_protocol", "SHA"),
            auth_pass=cred_data.get("auth_password", ""),
            priv_proto=cred_data.get("priv_protocol", "AES"),
            priv_pass=cred_data.get("priv_password", ""),
            oids=oids_to_poll,
        )
    else:
        community = cred_data.get("community", "public")
        raw = await snmp_get(hostname, port, community, oids_to_poll)

    if not raw:
        return {}

    # Resolve OID names
    resolved = {}
    for oid, value in raw.items():
        name = await get_oid_name(db, oid)
        resolved[name] = value

    # Store result
    db.add(SnmpResult(
        host_id=config.host_id,
        timestamp=datetime.utcnow(),
        data_json=json.dumps(resolved),
    ))

    # Update config last_poll
    config.last_poll = datetime.utcnow()
    config.last_ok = bool(raw)

    return resolved
