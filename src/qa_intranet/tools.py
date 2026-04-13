"""Claude Agent SDK tools exposed to the chat agent.

Each tool returns structured text the model can reason about. Large payloads
are truncated to avoid blowing up the context window; the model can always
request a specific slice via ``get_report``.
"""
from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool

from qa_intranet import cache as cache_mod

MAX_ROWS_IN_RESPONSE = 50


def _truncate(rows: list[dict], limit: int = MAX_ROWS_IN_RESPONSE) -> tuple[list[dict], bool]:
    if len(rows) <= limit:
        return rows, False
    return rows[:limit], True


@tool(
    "list_reports",
    "List discovered reports and subreports. Pass parent_slug to list only the "
    "children of a given node; omit to get the full tree.",
    {"parent_slug": str},
)
async def list_reports(args: dict[str, Any]) -> dict[str, Any]:
    parent_slug = (args.get("parent_slug") or "").strip() or None
    with cache_mod.session_scope() as session:
        tree = cache_mod.list_tree(session)

    slug_to_id = {r["slug"]: r["id"] for r in tree}
    parent_id = slug_to_id.get(parent_slug) if parent_slug else None
    if parent_slug and parent_id is None:
        return {
            "content": [
                {"type": "text", "text": f"No report with slug '{parent_slug}'."}
            ]
        }

    filtered = [r for r in tree if r["parent_id"] == parent_id]
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "parent_slug": parent_slug,
                        "count": len(filtered),
                        "reports": filtered,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            }
        ]
    }


@tool(
    "search_reports",
    "Full-text search over report titles and contents. Returns a ranked list "
    "of slugs + snippets.",
    {"query": str, "limit": int},
)
async def search_reports(args: dict[str, Any]) -> dict[str, Any]:
    query = (args.get("query") or "").strip()
    limit = int(args.get("limit") or 10)
    if not query:
        return {"content": [{"type": "text", "text": "Provide a non-empty query."}]}
    with cache_mod.session_scope() as session:
        results = cache_mod.search(session, query, limit=limit)
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"query": query, "results": results},
                    ensure_ascii=False,
                    indent=2,
                ),
            }
        ]
    }


@tool(
    "get_report",
    "Fetch a single report's rows and metadata by slug. Returns up to "
    f"{MAX_ROWS_IN_RESPONSE} rows plus a truncated-flag.",
    {"slug": str, "offset": int, "limit": int},
)
async def get_report(args: dict[str, Any]) -> dict[str, Any]:
    slug = (args.get("slug") or "").strip()
    offset = int(args.get("offset") or 0)
    limit = int(args.get("limit") or MAX_ROWS_IN_RESPONSE)
    limit = min(limit, MAX_ROWS_IN_RESPONSE)

    if not slug:
        return {"content": [{"type": "text", "text": "Provide a non-empty slug."}]}

    with cache_mod.session_scope() as session:
        report = cache_mod.get_report_by_slug(session, slug)
        if report is None:
            return {
                "content": [
                    {"type": "text", "text": f"No report with slug '{slug}'."}
                ]
            }
        detail = cache_mod.get_report_detail(session, report.id)

    if detail is None:
        return {"content": [{"type": "text", "text": "Report not found."}]}

    all_rows = detail.get("rows") or []
    window = all_rows[offset : offset + limit]
    payload = {
        "slug": detail["slug"],
        "title": detail["title"],
        "url": detail["url"],
        "last_fetched": detail["last_fetched"],
        "columns": detail["columns"],
        "total_rows": len(all_rows),
        "offset": offset,
        "returned_rows": len(window),
        "rows": window,
    }
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            }
        ]
    }


@tool(
    "refresh_report",
    "Re-download and re-index a specific leaf report by slug. Use sparingly — "
    "it spawns a headless browser session.",
    {"slug": str},
)
async def refresh_report(args: dict[str, Any]) -> dict[str, Any]:
    from qa_intranet.refresh import refresh_all

    slug = (args.get("slug") or "").strip()
    if not slug:
        return {"content": [{"type": "text", "text": "Provide a non-empty slug."}]}
    try:
        summary = refresh_all(only_slug=slug)
    except Exception as exc:  # noqa: BLE001
        return {"content": [{"type": "text", "text": f"Refresh failed: {exc}"}]}
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(summary, ensure_ascii=False, indent=2),
            }
        ]
    }


ALL_TOOLS = [list_reports, search_reports, get_report, refresh_report]
