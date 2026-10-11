"""Step B of the ClickHouse tenancy migration: put tenant_id first in the sort key.

Step A (``clickhouse_client.tenant_column_alters``) made isolation correct; the
tables still sort by ``(host_id, timestamp)`` & co., so a tenant-scoped read
scans every tenant's granules. ClickHouse cannot prepend a column to an
existing ORDER BY, so each table is rebuilt (spec: "Migrating existing tables",
step B). Irrelevant with one tenant — required before a second one exists.

How a table ``T`` is rebuilt, in a background job that resumes where it left off:

1. **Preflight.** Plain ``MergeTree`` in an ``Atomic`` database (EXCHANGE needs
   it), ``tenant_id`` present (step A), and free disk of at least twice what
   is left to copy — otherwise the table is skipped with the reason.
2. **Work table** ``T__tenant_rebuild``: T's columns and skipping indexes (from
   its CREATE statement, ``tenant_id`` without the step-A default) and T's own
   engine clause (partitioning, TTL, settings as the installation has them)
   with ``ORDER BY (tenant_id, <T's key>)``. Its TTL merges are stopped, so its
   row counts only change by our copies.
3. **Bulk copy, partition by partition, oldest first**:
   ``INSERT INTO work SELECT * FROM T WHERE _partition_id = p AND _part IN
   (<parts listed just before>)``. After each partition, the highest block
   number copied and the row count are persisted
   (``nodeglow_tenant_rebuild_progress``) — that is the resume point. A run
   stops after its time budget and the next run continues.
4. **Catch-up** (TTL merges on ``T`` paused): parts whose blocks are all newer
   than the recorded block number are rows inserted since — appended. A
   partition whose old rows no longer match the recorded count (TTL, a
   lightweight DELETE) or that has a part mixing old and new blocks (a merge)
   is copied again. Repeated until a pass changes nothing.
5. **Cutover**: ``SYSTEM STOP MERGES T``, a final catch-up against the now
   stable part set, every partition's count verified, ``EXCHANGE TABLES T AND
   T__tenant_rebuild`` (atomic), the old data renamed to ``T__pre_tenant``.
   Inserts never pause: whatever lands in the old table between the final
   pass and the exchange (in-flight inserts, other backend instances) is in
   parts newer than the recorded block numbers — the old table's merges stay
   stopped, so those parts are copied over afterwards (several passes over
   ~45 s). A partition that would need a full re-copy bigger than
   ``NODEGLOW_CH_REBUILD_CUTOVER_MAX_BYTES`` while merges are stopped
   postpones the cutover to a later run (a monthly syslog partition is small
   again at the start of the month).
6. **Grace**: the old table is kept ``NODEGLOW_CH_REBUILD_GRACE_HOURS`` (24),
   then dropped after one more delta pass. A mismatch after the exchange marks
   the table ``attention`` and the old table is never dropped automatically.

Row policies and grants are bound to table names, so they apply to the
rebuilt table from the moment of the exchange; the work tables are not
granted to the restricted tenant user.

``NODEGLOW_CH_TENANT_REBUILD=off`` disables the job (``auto``, the default,
runs it hourly). Everything here runs in ``system_scope``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass

from services import clickhouse_client as ch

log = logging.getLogger("nodeglow.clickhouse.rebuild")

WORK_SUFFIX = "__tenant_rebuild"
OLD_SUFFIX = "__pre_tenant"
PROGRESS_TABLE = "nodeglow_tenant_rebuild_progress"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


MODE = os.environ.get("NODEGLOW_CH_TENANT_REBUILD", "auto").strip().lower()
GRACE_HOURS = _env_int("NODEGLOW_CH_REBUILD_GRACE_HOURS", 24)
RUN_BUDGET_SECONDS = _env_int("NODEGLOW_CH_REBUILD_BUDGET_SECONDS", 20 * 60)
CUTOVER_MAX_BYTES = _env_int("NODEGLOW_CH_REBUILD_CUTOVER_MAX_BYTES", 2 * 1024 ** 3)
# A copy of one large partition can take a while; the default 30 s HTTP
# timeout would abandon the request while the server keeps inserting.
COPY_TIMEOUT_SECONDS = _env_int("NODEGLOW_CH_REBUILD_COPY_TIMEOUT", 6 * 3600)
# Delta passes after the exchange: an insert that started before it may still
# commit into the old table (the client's send/receive timeout is 30 s).
DELTA_WAITS = (0, 2, 10, 35)

_PARTITION_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_lock = asyncio.Lock()
_admin = None


class RebuildRefused(Exception):
    """A precondition does not hold; the table is left as it is."""


class CutoverPostponed(Exception):
    """The cutover would pause merges for too long; a later run retries."""


class NeedsAttention(Exception):
    """After the exchange the old and new data do not reconcile."""


@dataclass(frozen=True)
class Part:
    name: str
    min_block: int
    max_block: int
    rows: int
    bytes: int


@dataclass
class Progress:
    max_block: int = -1
    rows: int = 0


# ── Small SQL helpers ────────────────────────────────────────────────────────


async def _admin_client():
    """The main connection's user, with a timeout long enough for a copy."""
    global _admin
    if _admin is None:
        await ch.get_client()  # schemas (step A) first
        _admin = await ch.connect(ch.CLICKHOUSE_URL, send_receive_timeout=COPY_TIMEOUT_SECONDS)
    return _admin


async def _rows(c, sql: str, params: dict | None = None) -> list[dict]:
    result = await c.query(sql, parameters=params or {})
    return [dict(zip(result.column_names, row)) for row in result.result_rows]


async def _scalar(c, sql: str, params: dict | None = None):
    rows = await _rows(c, sql, params)
    return next(iter(rows[0].values())) if rows else None


def _pid(partition_id: str) -> str:
    if not _PARTITION_ID.match(partition_id):
        raise RebuildRefused(f"unexpected partition id {partition_id!r}")
    return partition_id


async def table_info(c, table: str) -> dict | None:
    rows = await _rows(c, (
        "SELECT engine, engine_full, sorting_key, primary_key, create_table_query, "
        "(SELECT engine FROM system.databases WHERE name = currentDatabase()) AS db_engine "
        "FROM system.tables WHERE database = currentDatabase() AND name = {t:String}"
    ), {"t": table})
    return rows[0] if rows else None


_TENANT_DEFAULT = re.compile(r"(`?tenant_id`?\s+UInt32)\s+DEFAULT\s+1\b")
_ENGINE_START = re.compile(r"\)\s*ENGINE\s*=")


def column_section(create_table_query: str) -> str | None:
    """The ``(columns, indexes, …)`` part of a CREATE TABLE statement, with
    tenant_id's DEFAULT 1 removed — the copy writes the value physically, and
    without a default a writer that forgets the column lands in tenant 0
    instead of tenant 1. None if the statement does not look as expected."""
    start = create_table_query.find("(")
    end = _ENGINE_START.search(create_table_query)
    if start < 0 or end is None or end.start() < start:
        return None
    columns = create_table_query[start:end.start() + 1]
    return _TENANT_DEFAULT.sub(r"\1", columns, count=1)


def leads_with_tenant(sorting_key: str) -> bool:
    return (sorting_key or "").split(",")[0].strip() == "tenant_id"


def rebuilt_engine(engine_full: str) -> str:
    """``engine_full`` with ``tenant_id`` prepended to its ORDER BY."""
    m = re.search(r"\bORDER BY\s+", engine_full)
    if m is None:
        raise RebuildRefused("no ORDER BY in the engine clause")
    if re.search(r"\bPRIMARY KEY\b", engine_full):
        raise RebuildRefused("an explicit PRIMARY KEY is not supported")
    i = m.end()
    if engine_full[i] == "(":
        depth, j = 0, i
        for j in range(i, len(engine_full)):
            if engine_full[j] == "(":
                depth += 1
            elif engine_full[j] == ")":
                depth -= 1
                if depth == 0:
                    break
        inner, rest = engine_full[i + 1:j], engine_full[j + 1:]
    else:
        nxt = re.search(r"\s+(?:SAMPLE BY|TTL|SETTINGS)\b", engine_full[i:])
        end = i + nxt.start() if nxt else len(engine_full)
        inner, rest = engine_full[i:end], engine_full[end:]
    if leads_with_tenant(inner):
        return engine_full
    return f"{engine_full[:m.start()]}ORDER BY (tenant_id, {inner.strip()}){rest}"


async def _has_column(c, table: str, column: str) -> bool:
    return bool(await _scalar(c, (
        "SELECT count() FROM system.columns WHERE database = currentDatabase() "
        "AND table = {t:String} AND name = {c:String}"
    ), {"t": table, "c": column}))


async def _column_names(c, table: str) -> list[str]:
    return [r["name"] for r in await _rows(c, (
        "SELECT name FROM system.columns WHERE database = currentDatabase() "
        "AND table = {t:String} ORDER BY position"
    ), {"t": table})]


async def partitions(c, table: str) -> dict[str, list[Part]]:
    out: dict[str, list[Part]] = {}
    for r in await _rows(c, (
        "SELECT partition_id, name, min_block_number, max_block_number, rows, bytes_on_disk "
        "FROM system.parts WHERE database = currentDatabase() AND table = {t:String} AND active "
        "ORDER BY partition_id, min_block_number"
    ), {"t": table}):
        out.setdefault(r["partition_id"], []).append(Part(
            r["name"], int(r["min_block_number"]), int(r["max_block_number"]),
            int(r["rows"]), int(r["bytes_on_disk"]),
        ))
    return out


async def count_rows(c, table: str, partition_id: str, parts: list[str] | None = None) -> int:
    sql = f"SELECT count() FROM {table} WHERE _partition_id = {{p:String}}"
    params: dict = {"p": partition_id}
    if parts is not None:
        if not parts:
            return 0
        sql += " AND _part IN ({parts:Array(String)})"
        params["parts"] = parts
    return int(await _scalar(c, sql, params) or 0)


async def free_bytes(c, table: str) -> int:
    """Free space on the disks the table lives on (and the default disk)."""
    value = await _scalar(c, (
        "SELECT min(free_space) FROM system.disks WHERE name = 'default' OR name IN ("
        "SELECT disk_name FROM system.parts WHERE database = currentDatabase() "
        "AND table = {t:String} AND active)"
    ), {"t": table})
    return int(value or 0)


async def table_bytes(c, table: str) -> int:
    return int(await _scalar(c, (
        "SELECT sum(bytes_on_disk) FROM system.parts WHERE database = currentDatabase() "
        "AND table = {t:String} AND active"
    ), {"t": table}) or 0)


# ── Progress (resume point) ──────────────────────────────────────────────────


async def _ensure_progress_table(c) -> None:
    await c.command(f"""
        CREATE TABLE IF NOT EXISTS {PROGRESS_TABLE}
        (
            table_name   String,
            partition_id String,
            max_block    Int64,
            rows         UInt64,
            state        String DEFAULT '',
            detail       String DEFAULT '',
            -- the server's clock: also what the grace period is measured on
            updated_at   DateTime64(6, 'UTC') DEFAULT now64(6)
        )
        ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY (table_name, partition_id)
    """)


async def load_progress(c, table: str) -> dict[str, Progress]:
    rows = await _rows(c, (
        f"SELECT partition_id, max_block, rows FROM {PROGRESS_TABLE} FINAL "
        "WHERE table_name = {t:String} AND partition_id != ''"
    ), {"t": table})
    return {r["partition_id"]: Progress(int(r["max_block"]), int(r["rows"])) for r in rows}


_PROGRESS_COLUMNS = ["table_name", "partition_id", "max_block", "rows", "state", "detail"]


async def _save_progress(c, table: str, partition_id: str, prog: Progress) -> None:
    await c.insert(
        PROGRESS_TABLE, [[table, partition_id, prog.max_block, prog.rows, "", ""]],
        column_names=_PROGRESS_COLUMNS,
    )


async def get_state(c, table: str) -> tuple[str, str, float | None]:
    """(state, detail, age in seconds) of a table's rebuild; ("", "", None) if none."""
    rows = await _rows(c, (
        f"SELECT state, detail, dateDiff('second', updated_at, now64(6)) AS age "
        f"FROM {PROGRESS_TABLE} FINAL WHERE table_name = {{t:String}} AND partition_id = ''"
    ), {"t": table})
    if not rows:
        return "", "", None
    return rows[0]["state"], rows[0]["detail"], float(rows[0]["age"])


async def _set_state(c, table: str, state: str, detail: str = "") -> None:
    await c.insert(
        PROGRESS_TABLE, [[table, "", -1, 0, state, detail]], column_names=_PROGRESS_COLUMNS,
    )


# ── Copying ──────────────────────────────────────────────────────────────────


def plan(parts: list[Part], prog: Progress | None, src_old_rows: int | None,
         dst_rows: int | None) -> str:
    """What a partition needs: ``full`` | ``append`` | ``in_sync``.

    ``prog`` is what was copied (rows, up to which block). Parts entirely above
    that block hold rows inserted since (``append``). A part straddling it was
    merged from old and new rows and cannot be split (``full``), and so can a
    partition whose old rows no longer add up (TTL, deletes, a copy that read
    parts merged away under it). ``dst_rows=None`` skips the work-table check
    (after the exchange the live table also receives new inserts).
    """
    if prog is None or prog.max_block < 0:
        return "full" if parts else "in_sync"
    new = [p for p in parts if p.min_block > prog.max_block]
    old = [p for p in parts if p.max_block <= prog.max_block]
    if len(new) + len(old) != len(parts):
        return "full"
    if dst_rows is not None and dst_rows != prog.rows:
        return "full"
    if (src_old_rows or 0) != prog.rows:
        return "full"
    return "append" if new else "in_sync"


async def _insert_parts(c, src: str, dst: str, partition_id: str, names: list[str]) -> None:
    qid = f"nodeglow-rebuild-{dst}-{_pid(partition_id)}"
    # A copy abandoned by a previous run (timeout, restart) must not still be
    # writing when this one starts — it would duplicate rows.
    await c.command(f"KILL QUERY WHERE query_id = '{qid}' SYNC")
    await c.command(
        f"INSERT INTO {dst} SELECT * FROM {src} "
        "WHERE _partition_id = {p:String} AND _part IN ({parts:Array(String)})",
        parameters={"p": partition_id, "parts": names},
        settings={"query_id": qid},
    )


async def _full_copy(c, src: str, dst: str, partition_id: str, parts: list[Part],
                     *, stable: bool) -> Progress:
    size = sum(p.bytes for p in parts)
    free = await free_bytes(c, src)
    if free < 2 * size:
        raise RebuildRefused(
            f"partition {partition_id} of {src}: {size} bytes to copy, {free} free "
            "(needs twice the size)"
        )
    await c.command(f"ALTER TABLE {dst} DROP PARTITION ID '{_pid(partition_id)}'")
    names = [p.name for p in parts]
    expected = await count_rows(c, src, partition_id, names) if stable else None
    await _insert_parts(c, src, dst, partition_id, names)
    copied = await count_rows(c, dst, partition_id)
    if stable and copied != expected:
        raise RuntimeError(
            f"{src} partition {partition_id}: copied {copied} rows, expected {expected}"
        )
    return Progress(max((p.max_block for p in parts), default=-1), copied)


async def sync_partition(c, src: str, dst: str, partition_id: str, parts: list[Part],
                         prog: Progress | None, *, stable: bool,
                         max_full_bytes: int | None = None) -> tuple[str, Progress]:
    """Bring ``dst``'s copy of one partition up to date with ``src`` (before
    the exchange: the work table holds nothing but our copies)."""
    old_names = [p.name for p in parts if prog and p.max_block <= prog.max_block]
    src_old = await count_rows(c, src, partition_id, old_names) if prog else None
    dst_rows = await count_rows(c, dst, partition_id) if prog else None
    action = plan(parts, prog, src_old, dst_rows)
    if action == "full":
        size = sum(p.bytes for p in parts)
        if max_full_bytes is not None and size > max_full_bytes:
            raise CutoverPostponed(
                f"{src} partition {partition_id} needs a full copy of {size} bytes "
                "while merges are stopped"
            )
        prog = await _full_copy(c, src, dst, partition_id, parts, stable=stable)
    elif action == "append":
        new = [p for p in parts if p.min_block > prog.max_block]
        expected = await count_rows(c, src, partition_id, [p.name for p in new]) if stable else None
        await _insert_parts(c, src, dst, partition_id, [p.name for p in new])
        copied = await count_rows(c, dst, partition_id)
        if stable and copied != prog.rows + expected:
            raise RuntimeError(f"{src} partition {partition_id}: append did not add up")
        prog = Progress(max(p.max_block for p in new), copied)
    return action, prog


async def copy_delta(c, old: str, live: str, table: str) -> int:
    """After the exchange: copy rows that reached the old table late.

    The old table's merges are stopped, so its part set only grows by inserts
    — new parts lie entirely above the recorded block numbers. Anything else
    (a merge after a server restart, a count that does not add up) is not
    something to repair automatically.
    """
    progress = await load_progress(c, table)
    copied = 0
    for partition_id, parts in sorted((await partitions(c, old)).items()):
        prog = progress.get(partition_id, Progress())
        if prog.max_block < 0:
            # A partition that first appeared after the final pass.
            action = "append" if parts else "in_sync"
        else:
            old_names = [p.name for p in parts if p.max_block <= prog.max_block]
            src_old = await count_rows(c, old, partition_id, old_names)
            action = plan(parts, prog, src_old, None)
        if action == "in_sync":
            continue
        if action == "full":
            raise NeedsAttention(
                f"{old} partition {partition_id} changed after the exchange "
                f"({prog.rows} rows copied up to block {prog.max_block})"
            )
        new = [p.name for p in parts if p.min_block > prog.max_block]
        n = await count_rows(c, old, partition_id, new)
        await _insert_parts(c, old, live, partition_id, new)
        prog = Progress(max(p.max_block for p in parts), prog.rows + n)
        await _save_progress(c, table, partition_id, prog)
        copied += n
    if copied:
        log.info("Rebuild %s: copied %d late rows from %s", table, copied, old)
    return copied


async def verify_old(c, old: str, table: str) -> None:
    progress = await load_progress(c, table)
    for partition_id in sorted(await partitions(c, old)):
        have = await count_rows(c, old, partition_id)
        copied = progress.get(partition_id, Progress()).rows
        if have != copied:
            raise NeedsAttention(f"{old} partition {partition_id}: {have} rows, {copied} copied")


# ── One table ────────────────────────────────────────────────────────────────


async def _create_work_table(c, table: str, work: str, info: dict) -> None:
    engine = rebuilt_engine(info["engine_full"])
    columns = column_section(info.get("create_table_query") or "")
    if columns is not None:
        await c.command(f"CREATE TABLE IF NOT EXISTS {work} {columns} ENGINE = {engine}")
    else:
        # Same structure, tenant_id keeps its DEFAULT 1.
        await c.command(f"CREATE TABLE IF NOT EXISTS {work} AS {table} ENGINE = {engine}")
    # The column order must be the source's: the copy is SELECT *.
    src_cols = await _column_names(c, table)
    if await _column_names(c, work) != src_cols:
        await c.command(f"DROP TABLE IF EXISTS {work}")
        raise RebuildRefused(f"{work} would not have the columns of {table}")
    have = {r["name"] for r in await _rows(c, (
        "SELECT name FROM system.data_skipping_indices "
        "WHERE database = currentDatabase() AND table = {t:String}"
    ), {"t": work})}
    for idx in await _rows(c, (
        "SELECT name, expr, type_full, granularity FROM system.data_skipping_indices "
        "WHERE database = currentDatabase() AND table = {t:String}"
    ), {"t": table}):
        if idx["name"] not in have:
            await c.command(
                f"ALTER TABLE {work} ADD INDEX IF NOT EXISTS {idx['name']} {idx['expr']} "
                f"TYPE {idx['type_full']} GRANULARITY {int(idx['granularity'])}"
            )


async def _sync_all(c, table: str, work: str, *, stable: bool, deadline: float | None,
                    max_full_bytes: int | None = None) -> int:
    """One pass over every partition; returns how many changed."""
    progress = await load_progress(c, table)
    src_parts = await partitions(c, table)
    changed = 0
    for partition_id in sorted(src_parts):
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError
        action, prog = await sync_partition(
            c, table, work, partition_id, src_parts[partition_id], progress.get(partition_id),
            stable=stable, max_full_bytes=max_full_bytes,
        )
        if action != "in_sync":
            changed += 1
            await _save_progress(c, table, partition_id, prog)
    # Partitions gone from the source (expired) go from the copy as well.
    for partition_id in set(await partitions(c, work)) - set(src_parts):
        await c.command(f"ALTER TABLE {work} DROP PARTITION ID '{_pid(partition_id)}'")
        await _save_progress(c, table, partition_id, Progress())
        changed += 1
    return changed


async def _finish(c, table: str, *, waits=DELTA_WAITS, sleep=asyncio.sleep) -> str:
    old = table + OLD_SUFFIX
    state, detail, age_s = await get_state(c, table)
    if state == "attention":
        log.warning("ClickHouse rebuild of %s needs attention: %s (old data kept in %s)",
                    table, detail, old)
        return f"attention: {detail}"
    try:
        if state != "delta_done":
            for wait in waits:
                await sleep(wait)
                await copy_delta(c, old, table, table)
            await verify_old(c, old, table)
            await _set_state(c, table, "delta_done")
            log.info("ClickHouse rebuild of %s complete; %s kept for %d h", table, old, GRACE_HOURS)
            return "rebuilt"
        if (age_s or 0) < GRACE_HOURS * 3600:
            return "grace"
        await copy_delta(c, old, table, table)
        await verify_old(c, old, table)
    except NeedsAttention as exc:
        await _set_state(c, table, "attention", str(exc))
        log.error("ClickHouse rebuild of %s needs attention: %s", table, exc)
        return f"attention: {exc}"
    await c.command(f"DROP TABLE IF EXISTS {old}")
    await _set_state(c, table, "done")
    log.info("ClickHouse rebuild of %s: dropped %s after the grace period", table, old)
    return "done"


async def rebuild_table(c, table: str, *, budget_seconds: int | None = None,
                        waits=DELTA_WAITS, sleep=asyncio.sleep) -> str:
    """Advance the rebuild of one table as far as this run gets. Returns its state."""
    work, old = table + WORK_SUFFIX, table + OLD_SUFFIX
    info = await table_info(c, table)
    if info is None:
        return "missing"
    if leads_with_tenant(info["sorting_key"]):
        work_info = await table_info(c, work)
        if work_info is not None and not leads_with_tenant(work_info["sorting_key"]):
            # Exchanged, but the run ended before the rename.
            await c.command(f"RENAME TABLE {work} TO {old}")
            await _set_state(c, table, "exchanged")
        if await table_info(c, old) is not None:
            return await _finish(c, table, waits=waits, sleep=sleep)
        return "done"

    # ── Preflight ──
    if info["engine"] != "MergeTree":
        raise RebuildRefused(f"engine {info['engine']} (only MergeTree is rebuilt)")
    if info["db_engine"] != "Atomic":
        raise RebuildRefused(f"database engine {info['db_engine']} has no EXCHANGE TABLES")
    if not await _has_column(c, table, "tenant_id"):
        raise RebuildRefused("tenant_id column missing (step A did not run)")
    work_info = await table_info(c, work)
    if work_info is not None and not leads_with_tenant(work_info["sorting_key"]):
        raise RebuildRefused(f"{work} exists with an unexpected sort key")
    if await table_info(c, old) is not None:
        raise RebuildRefused(f"{old} exists although {table} was not rebuilt")
    remaining = max(await table_bytes(c, table) - (await table_bytes(c, work) if work_info else 0), 0)
    free = await free_bytes(c, table)
    if free < 2 * remaining:
        raise RebuildRefused(f"{remaining} bytes left to copy, {free} free (needs twice that)")

    if work_info is None:
        await _create_work_table(c, table, work, info)
        log.info("ClickHouse rebuild of %s started (%d bytes)", table, remaining)
    # Only our copies may change the work table's counts.
    await c.command(f"SYSTEM STOP TTL MERGES {work}")

    budget = RUN_BUDGET_SECONDS if budget_seconds is None else budget_seconds
    deadline = time.monotonic() + budget
    try:
        await _sync_all(c, table, work, stable=False, deadline=deadline)
    except TimeoutError:
        log.info("ClickHouse rebuild of %s: run budget used, continuing next run", table)
        return "copying"

    # ── Catch-up with TTL paused on the source, then cutover ──
    exchanged = False
    await c.command(f"SYSTEM STOP TTL MERGES {table}")
    try:
        for _ in range(3):
            if not await _sync_all(c, table, work, stable=False, deadline=None):
                break
        await c.command(f"SYSTEM STOP MERGES {table}")
        await sleep(1)  # merges being cancelled may still commit
        await _sync_all(c, table, work, stable=True, deadline=None,
                        max_full_bytes=CUTOVER_MAX_BYTES)
        progress = await load_progress(c, table)
        for partition_id in await partitions(c, work):
            if await count_rows(c, work, partition_id) != progress.get(partition_id, Progress()).rows:
                raise RuntimeError(f"{work} partition {partition_id} does not match its progress")
        await c.command(f"EXCHANGE TABLES {table} AND {work}")
        exchanged = True
    except CutoverPostponed as exc:
        log.info("ClickHouse rebuild of %s: cutover postponed (%s)", table, exc)
        return "postponed"
    finally:
        if not exchanged:
            await c.command(f"SYSTEM START MERGES {table}")
            await c.command(f"SYSTEM START TTL MERGES {table}")
    log.info("ClickHouse rebuild of %s: exchanged", table)
    await _set_state(c, table, "exchanged")
    await c.command(f"RENAME TABLE {work} TO {old}")
    # The rebuilt table is the former work table: its TTL merges were stopped.
    await c.command(f"SYSTEM START TTL MERGES {table}")
    await c.command(f"SYSTEM START MERGES {table}")
    return await _finish(c, table, waits=waits, sleep=sleep)


# ── Job entry points ─────────────────────────────────────────────────────────


async def run(tables: tuple[str, ...] = ch.TENANT_TABLES) -> dict[str, str]:
    """One run over every tenant table. Never raises; returns state per table."""
    if MODE == "off":
        return {}
    if _lock.locked():
        return {}
    async with _lock:
        try:
            c = await _admin_client()
            await _ensure_progress_table(c)
        except Exception as exc:
            log.warning("ClickHouse tenant rebuild skipped: %s", exc)
            return {}
        out: dict[str, str] = {}
        for table in tables:
            try:
                out[table] = await rebuild_table(c, table)
            except RebuildRefused as exc:
                out[table] = f"refused: {exc}"
                log.warning("ClickHouse rebuild of %s refused: %s", table, exc)
            except Exception as exc:
                out[table] = f"error: {exc}"
                log.warning("ClickHouse rebuild of %s failed, retried next run", table, exc_info=True)
        return out


async def status(c=None) -> dict[str, str]:
    """Per table: ``rebuilt`` (sort key leads with tenant_id, nothing pending),
    ``grace`` / ``attention`` (exchanged, old table kept), ``copying``
    (work table present) or ``pending``."""
    c = c or await ch.get_client()  # metadata reads only: the normal timeout
    out: dict[str, str] = {}
    for table in ch.TENANT_TABLES:
        info = await table_info(c, table)
        if info is None:
            out[table] = "missing"
        elif leads_with_tenant(info["sorting_key"]):
            if await table_info(c, table + OLD_SUFFIX) is None:
                out[table] = "rebuilt"
            else:
                state, _, _ = await get_state(c, table)
                out[table] = "attention" if state == "attention" else "grace"
        else:
            out[table] = "copying" if await table_info(c, table + WORK_SUFFIX) else "pending"
    return out


async def complete(c=None) -> bool:
    """Every tenant table sorts by tenant first — what ee must check before it
    creates a second tenant."""
    return all(s in ("rebuilt", "grace", "missing") for s in (await status(c)).values())
