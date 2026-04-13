"""Claude Agent SDK tools backed by the Snapshot tables.

The live portal is Power BI Embedded, so "reports" in the Q&A sense are
snapshots captured from the QueryExecutionService responses. Each
snapshot is one DAX query's rows, stored with its column list and the
dashboard URL it came from.
"""
from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool

from qa_intranet import cache as cache_mod

MAX_ROWS_IN_RESPONSE = 50


@tool(
    "list_snapshots",
    "List captured Power BI snapshots (one per DAX query). Optionally "
    "filter by a keyword that matches title, URL or column names.",
    {"keyword": str, "limit": int},
)
async def list_snapshots(args: dict[str, Any]) -> dict[str, Any]:
    keyword = (args.get("keyword") or "").strip() or None
    limit = int(args.get("limit") or 100)
    with cache_mod.session_scope() as session:
        items = cache_mod.list_snapshots(session, keyword=keyword, limit=limit)
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"count": len(items), "snapshots": items},
                    ensure_ascii=False, indent=2,
                ),
            }
        ]
    }


@tool(
    "get_snapshot",
    "Fetch rows from a specific snapshot. Returns up to ``limit`` rows "
    "starting at ``offset`` as dicts keyed by column name.",
    {"snapshot_id": int, "offset": int, "limit": int},
)
async def get_snapshot(args: dict[str, Any]) -> dict[str, Any]:
    snapshot_id = int(args.get("snapshot_id") or 0)
    offset = int(args.get("offset") or 0)
    limit = min(int(args.get("limit") or MAX_ROWS_IN_RESPONSE), MAX_ROWS_IN_RESPONSE)
    if snapshot_id <= 0:
        return {"content": [{"type": "text", "text": "Provide a positive snapshot_id."}]}
    with cache_mod.session_scope() as session:
        data = cache_mod.get_snapshot_rows(
            session, snapshot_id, offset=offset, limit=limit
        )
    if data is None:
        return {"content": [{"type": "text", "text": f"No snapshot {snapshot_id}."}]}
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(data, ensure_ascii=False, indent=2, default=str),
            }
        ]
    }


@tool(
    "search",
    "Full-text search over every snapshot row (FTS5). Returns snippets of "
    "matching rows with their snapshot_id and row_index so the caller can "
    "follow up with get_snapshot.",
    {"query": str, "limit": int},
)
async def search(args: dict[str, Any]) -> dict[str, Any]:
    query = (args.get("query") or "").strip()
    limit = int(args.get("limit") or 20)
    if not query:
        return {"content": [{"type": "text", "text": "Provide a non-empty query."}]}
    with cache_mod.session_scope() as session:
        hits = cache_mod.search_snapshot_rows(session, query, limit=limit)
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"query": query, "results": hits},
                    ensure_ascii=False, indent=2,
                ),
            }
        ]
    }


@tool(
    "fetch_live_view",
    "Capture Power BI responses for N seconds from the user's open Chrome "
    "(CDP). Use this when the user asks about a specific filter combination "
    "(titulación, año, campus…) that might not be in the cached snapshots. "
    "IMPORTANT: Tell the user to change the filters in their browser while "
    "this tool is recording. Returns the tables captured during the window.",
    {"duration_s": int, "note_to_user": str},
)
async def fetch_live_view(args: dict[str, Any]) -> dict[str, Any]:
    from qa_intranet.live import capture_live_view

    duration = min(max(int(args.get("duration_s") or 30), 5), 120)
    note = (args.get("note_to_user") or "").strip()

    try:
        summary = capture_live_view(duration_s=duration)
    except Exception as exc:  # noqa: BLE001
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Live capture failed: {exc}. Make sure Chrome is "
                        "open via `python -m qa_intranet open-chrome` and "
                        "QA_INTRANET_CDP_URL is set."
                    ),
                }
            ]
        }

    payload = {
        "duration_s": summary["duration_s"],
        "responses_seen": summary["responses_seen"],
        "table_count": len(summary["tables"]),
        "tables": [
            {
                "url": t["url"][-80:],
                "columns": t["columns"],
                "row_count": t["row_count"],
                "rows": t["rows"][:MAX_ROWS_IN_RESPONSE],
                "truncated": t["row_count"] > MAX_ROWS_IN_RESPONSE,
            }
            for t in summary["tables"]
        ],
        "note_to_user": note,
    }
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            }
        ]
    }


ALL_TOOLS = [list_snapshots, get_snapshot, search, fetch_live_view]
