"""Orchestrator: load the authenticated session, crawl the menu, download each
leaf report and persist everything to the SQLite cache.

All Playwright launches use the persistent Chromium profile defined in
``qa_intranet.auth`` so that authenticated API calls work the same way
they do in the user's visible browser.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from qa_intranet.auth import has_profile, persistent_context
from qa_intranet.cache import (
    get_engine,
    get_report_by_slug,
    session_scope,
    store_report_data,
    upsert_report,
)
from qa_intranet.config import BASE_URL
from qa_intranet.crawler import MenuNode, flatten, parse_menu_html
from qa_intranet.extractor import download_report, parse_export


class NotAuthenticatedError(RuntimeError):
    pass


def _require_profile() -> None:
    if not has_profile():
        raise NotAuthenticatedError(
            "No saved session. Run `python -m qa_intranet login` first."
        )


def fetch_page_html(
    url: Optional[str] = None, timeout_ms: int = 60_000
) -> tuple[str, str]:
    """Load a page with the persistent profile and return (html, final_url)."""
    _require_profile()
    target = url or BASE_URL

    with persistent_context(headless=True) as (_, context):
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(target, wait_until="load", timeout=timeout_ms)
        # Give Angular time to bootstrap and fire its initial API calls.
        page.wait_for_timeout(3000)
        html = page.content()
        current_url = page.url

    if "login.microsoftonline.com" in current_url:
        raise NotAuthenticatedError(
            "Session expired. Run `python -m qa_intranet login` again."
        )
    return html, current_url


def discover_menu() -> list[MenuNode]:
    html, _ = fetch_page_html(BASE_URL)
    return parse_menu_html(html, page_url=BASE_URL)


def capture_network(output_path: Path) -> dict:
    """Open a visible browser with the persistent profile and record traffic.

    The user drives the browser manually. When they come back to the terminal
    and press Enter, the browser closes and a JSONL file is written.
    """
    import json

    _require_profile()
    captured: list[dict] = []
    seen_api_urls: set[str] = set()

    def _is_interesting(url: str) -> bool:
        lower = url.lower().split("?")[0]
        boring_ext = (".js", ".css", ".woff", ".woff2", ".ttf", ".svg", ".png",
                      ".jpg", ".jpeg", ".gif", ".ico", ".map", ".html")
        if any(lower.endswith(ext) for ext in boring_ext):
            return False
        # Keep any host — tokens often come from microsoftonline.com too.
        return True

    with persistent_context(headless=False) as (_, context):
        page = context.pages[0] if context.pages else context.new_page()

        def on_request(request):
            if _is_interesting(request.url):
                captured.append({
                    "kind": "request",
                    "method": request.method,
                    "url": request.url,
                    "resource_type": request.resource_type,
                    "headers": {
                        k: v for k, v in request.headers.items()
                        if k.lower() in ("content-type", "accept", "authorization")
                    },
                })

        def on_response(response):
            if not _is_interesting(response.url):
                return
            ct = response.headers.get("content-type", "")
            body_preview = None
            if "json" in ct.lower():
                try:
                    body_preview = response.text()[:2000]
                except Exception:
                    pass
                seen_api_urls.add(response.url.split("?")[0])
            captured.append({
                "kind": "response",
                "status": response.status,
                "url": response.url,
                "content_type": ct,
                "body_preview": body_preview,
            })

        page.on("request", on_request)
        page.on("response", on_response)

        page.goto(BASE_URL, wait_until="load", timeout=60_000)

        print()
        print("=" * 70)
        print(" Browser abierto. NAVEGA POR LOS INFORMES que te interesan:")
        print("   - Cambia de titulación en el dropdown.")
        print("   - Abre varias pestañas inferiores (CUANTI, CUALI, etc.).")
        print("   - Cambia año y campus si puedes.")
        print(" Cuando termines, vuelve aquí y pulsa ENTER.")
        print("=" * 70)
        input()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for entry in captured:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return {
        "events": len(captured),
        "unique_api_urls": sorted(seen_api_urls),
        "output_path": str(output_path.resolve()),
    }


def refresh_all(only_slug: Optional[str] = None) -> dict:
    """Refresh the cache (or a single report by slug). Returns a summary dict."""
    _require_profile()
    get_engine()

    summary = {"discovered": 0, "fetched": 0, "failed": 0, "errors": []}

    with persistent_context(headless=True) as (_, context):
        page = context.pages[0] if context.pages else context.new_page()

        page.goto(BASE_URL, wait_until="load", timeout=60_000)
        page.wait_for_timeout(3000)
        if "login.microsoftonline.com" in page.url:
            raise NotAuthenticatedError(
                "Session expired. Run `python -m qa_intranet login` again."
            )

        roots = parse_menu_html(page.content(), page_url=BASE_URL)
        all_nodes = flatten(roots)
        summary["discovered"] = len(all_nodes)

        slug_to_id: dict[str, int] = {}
        with session_scope() as session:
            for node in sorted(all_nodes, key=lambda n: n.parent is not None):
                parent_id = (
                    slug_to_id.get(node.parent.slug) if node.parent else None
                )
                row = upsert_report(
                    session,
                    slug=node.slug,
                    title=node.title,
                    url=node.url,
                    parent_id=parent_id,
                    is_leaf=node.is_leaf,
                )
                slug_to_id[node.slug] = row.id

        targets = [n for n in all_nodes if n.is_leaf]
        if only_slug:
            targets = [n for n in targets if n.slug == only_slug]
            if not targets:
                raise ValueError(f"No leaf report with slug '{only_slug}'")

        for node in targets:
            try:
                page.goto(node.url, wait_until="load", timeout=60_000)
                page.wait_for_timeout(2000)
                path = download_report(page, node.slug)
                if path is None:
                    summary["failed"] += 1
                    summary["errors"].append(
                        f"{node.slug}: no export control found"
                    )
                    continue
                extracted = parse_export(path)
                with session_scope() as session:
                    report = get_report_by_slug(session, node.slug)
                    if report is None:
                        continue
                    store_report_data(
                        session,
                        report,
                        rows=extracted.rows,
                        columns=extracted.columns,
                        content_text=extracted.content_text,
                        content_hash=extracted.content_hash,
                    )
                summary["fetched"] += 1
            except Exception as exc:  # noqa: BLE001
                summary["failed"] += 1
                summary["errors"].append(f"{node.slug}: {exc}")

    return summary
