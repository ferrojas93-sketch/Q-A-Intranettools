"""Capture Power BI data on demand from the user's live Chrome session.

Uses Playwright's tracing API (``context.tracing.start/stop``) rather than
event listeners or CDP getResponseBody. Tracing records the raw response
bodies for every request made during the window — including responses
from cross-origin iframes (Power BI's OOPIF) that Playwright's live
listeners can't access. Afterwards we open the trace zip, pull out the
bodies that match Power BI data URLs, and decode them with
``dsr_parser``.

The typical flow is:

1. Agent tool decides it needs fresh data.
2. Tool tells the user: "voy a capturar los próximos 30s, cambia los
   filtros en Chrome ahora".
3. Tool starts tracing, sleeps, stops tracing.
4. Parses the trace and returns decoded tables.
"""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Optional

from qa_intranet.auth import get_context
from qa_intranet.dsr_parser import parse_qes_json


DATA_URL_MARKERS = (
    "/queryexecutionservice/",
    "/querydata",
    "/explore/reports/",
)


def _is_data_url(url: str) -> bool:
    low = url.lower()
    return any(m in low for m in DATA_URL_MARKERS)


def _extract_bodies_from_trace(trace_zip: Path) -> list[dict]:
    """Parse a Playwright trace.zip and yield response body dicts for data URLs.

    Returns a list of dicts: ``{"url", "method", "body_bytes", "status"}``.
    """
    entries: dict[str, dict] = {}  # requestId -> partial info
    bodies: dict[str, bytes] = {}  # sha1 -> body
    out: list[dict] = []

    with zipfile.ZipFile(trace_zip) as zf:
        # Gather every resource body that could be a response body.
        for name in zf.namelist():
            if name.startswith("resources/"):
                # resources/<sha1>[ext]
                sha1 = Path(name).stem
                try:
                    bodies[sha1] = zf.read(name)
                except Exception:
                    pass
            elif name.endswith(".network"):
                # trace.network is a jsonl file with entries like
                # {"type": "http@1", "url": ..., "requestSha1": ..., "responseSha1": ...}
                for line in zf.read(name).splitlines():
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("type") not in ("http", "http@1", "network"):
                        continue
                    url = rec.get("url", "")
                    if not _is_data_url(url):
                        continue
                    resp_sha1 = rec.get("responseSha1") or rec.get("body")
                    if not resp_sha1:
                        continue
                    entries[rec.get("id") or url + str(len(entries))] = {
                        "url": url,
                        "method": rec.get("method", "GET"),
                        "status": rec.get("status", 0),
                        "response_sha1": resp_sha1,
                        "content_type": (rec.get("headers") or {}).get("content-type", ""),
                    }

    for info in entries.values():
        body = bodies.get(info["response_sha1"])
        if body:
            out.append({
                "url": info["url"],
                "method": info["method"],
                "status": info["status"],
                "body_bytes": body,
                "content_type": info.get("content_type", ""),
            })
    return out


def capture_live_view(
    duration_s: int = 30, on_progress=None
) -> dict:
    """Record the user's Chrome traffic for ``duration_s`` seconds and decode
    every Power BI data response captured during that window.

    ``on_progress(seconds_left)`` is invoked once per second if provided.
    """
    with get_context(headless=True) as (_, context):
        with tempfile.TemporaryDirectory() as td:
            trace_path = Path(td) / "trace.zip"
            context.tracing.start(
                screenshots=False, snapshots=False, sources=False
            )
            started_at = time.monotonic()
            try:
                for remaining in range(duration_s, 0, -1):
                    if on_progress:
                        on_progress(remaining)
                    time.sleep(1)
            finally:
                context.tracing.stop(path=str(trace_path))

            entries = _extract_bodies_from_trace(trace_path)

    tables = []
    for entry in entries:
        body = entry["body_bytes"]
        # Attempt JSON parse
        try:
            obj = json.loads(body)
        except Exception:
            continue
        decoded = parse_qes_json(obj) if isinstance(obj, dict) else []
        for t in decoded:
            if t.rows:
                tables.append({
                    "url": entry["url"],
                    "columns": t.columns,
                    "row_count": len(t.rows),
                    "rows": t.rows,
                })
    return {
        "duration_s": duration_s,
        "responses_seen": len(entries),
        "tables": tables,
        "elapsed_s": time.monotonic() - started_at,
    }
