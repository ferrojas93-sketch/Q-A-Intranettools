"""Ingest parsed DSR tables into SQLite as Snapshots.

Input: a directory of JSON bodies written by ``parse-har`` (usually
``network_bodies/``). For each body that is a QES response, decode it via
``dsr_parser.parse_file`` and persist the resulting tables as
``Snapshot`` / ``SnapshotRow`` rows. Uses a sha1 of (source filename +
columns + row count + rows) as the ``content_hash`` so re-running the
loader is idempotent (existing snapshots skipped).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from qa_intranet.cache import (
    reset_snapshots,
    session_scope,
    upsert_snapshot,
)
from qa_intranet.dsr_parser import parse_file


def _content_hash(source_file: str, columns: list[str], rows: list[list]) -> str:
    h = hashlib.sha1()
    h.update(source_file.encode("utf-8"))
    h.update(b"|")
    h.update("||".join(columns).encode("utf-8"))
    h.update(b"|")
    h.update(str(len(rows)).encode("utf-8"))
    h.update(b"|")
    for row in rows[:10]:
        h.update(json.dumps(row, ensure_ascii=False, default=str).encode("utf-8"))
    return h.hexdigest()


def load_bodies_dir(
    bodies_dir: Path, *, reset: bool = False, limit: Optional[int] = None
) -> dict:
    """Load every QES response body from ``bodies_dir`` into SQLite."""
    bodies_dir = Path(bodies_dir)
    index_path = bodies_dir / "_index.json"
    url_by_file: dict[str, str] = {}
    req_by_file: dict[str, Optional[str]] = {}
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            for entry in index:
                name = Path(entry.get("body_file", "")).name
                if name:
                    url_by_file[name] = entry.get("url", "")
                    req_by_file[name] = entry.get("request_body")
        except Exception:
            pass

    summary = {
        "files_seen": 0,
        "snapshots_inserted": 0,
        "snapshots_skipped_duplicate": 0,
        "files_without_tables": 0,
        "files_errored": 0,
    }

    paths = sorted(p for p in bodies_dir.glob("*.json") if p.name != "_index.json")
    if limit:
        paths = paths[:limit]

    with session_scope() as session:
        if reset:
            reset_snapshots(session)

        for path in paths:
            summary["files_seen"] += 1
            try:
                tables = parse_file(path)
            except Exception:
                summary["files_errored"] += 1
                continue

            tables = [t for t in tables if t.rows]
            if not tables:
                summary["files_without_tables"] += 1
                continue

            url = url_by_file.get(path.name, "")
            request_body = req_by_file.get(path.name)

            for t in tables:
                ch = _content_hash(path.name, t.columns, t.rows)
                inserted = upsert_snapshot(
                    session,
                    source_file=path.name,
                    url=url,
                    columns=t.columns,
                    rows=t.rows,
                    title=None,
                    request_body=request_body,
                    content_hash=ch,
                )
                if inserted is None:
                    summary["snapshots_skipped_duplicate"] += 1
                else:
                    summary["snapshots_inserted"] += 1

    return summary
