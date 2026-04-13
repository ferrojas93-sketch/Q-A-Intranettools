"""Orchestrator: load the authenticated session, crawl the menu, download each
leaf report and persist everything to the SQLite cache."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from qa_intranet.auth import (
    load_storage_state,
    storage_state_to_tempfile,
)
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


def _require_state() -> dict:
    state = load_storage_state()
    if state is None:
        raise NotAuthenticatedError(
            "No saved session. Run `python -m qa_intranet login` first."
        )
    return state


def _launch_context(p, state: dict, tmp_dir: Path, headless: bool = True):
    storage_path = storage_state_to_tempfile(state, tmp_dir)
    browser = p.chromium.launch(headless=headless)
    context = browser.new_context(storage_state=str(storage_path))
    return browser, context


def fetch_page_html(
    url: Optional[str] = None, timeout_ms: int = 60_000
) -> tuple[str, str]:
    """Load a page with the saved session and return (html, final_url).

    Raises NotAuthenticatedError if the session redirects to the Microsoft
    login page, which indicates cookies expired.
    """
    from playwright.sync_api import sync_playwright

    target = url or BASE_URL
    state = _require_state()
    with tempfile.TemporaryDirectory() as td:
        with sync_playwright() as p:
            browser, context = _launch_context(p, state, Path(td))
            page = context.new_page()
            page.goto(target, wait_until="load", timeout=timeout_ms)
            # Give any lazy JS a moment to populate the menu/content.
            page.wait_for_timeout(2000)
            html = page.content()
            current_url = page.url
            browser.close()

    if "login.microsoftonline.com" in current_url:
        raise NotAuthenticatedError(
            "Session expired. Run `python -m qa_intranet login` again."
        )
    return html, current_url


def discover_menu() -> list[MenuNode]:
    """Log in with the saved session and return the parsed menu tree."""
    html, _ = fetch_page_html(BASE_URL)
    return parse_menu_html(html, page_url=BASE_URL)


def capture_network(output_path: Path) -> dict:
    """Open a visible browser with the saved session and record every request
    the user's navigation triggers on the intranettools host.

    The user drives the browser manually (clicks, filters, reports). When they
    come back to the terminal and press Enter, the browser closes and a JSONL
    file is written with one event per line. Returns a summary dict.
    """
    import json

    from playwright.sync_api import sync_playwright

    state = _require_state()
    captured: list[dict] = []
    seen_api_urls: set[str] = set()

    def _is_interesting(url: str) -> bool:
        if "intranettools.esic.edu" not in url:
            return False
        # Skip static assets (noise in the log).
        lower = url.lower()
        boring = (".js", ".css", ".woff", ".woff2", ".ttf", ".svg", ".png",
                  ".jpg", ".jpeg", ".gif", ".ico", ".map")
        return not any(lower.split("?")[0].endswith(ext) for ext in boring)

    with tempfile.TemporaryDirectory() as td:
        with sync_playwright() as p:
            browser, context = _launch_context(p, state, Path(td), headless=False)
            page = context.new_page()

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
                        text = response.text()
                        body_preview = text[:2000]
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

            browser.close()

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
    from playwright.sync_api import sync_playwright

    get_engine()
    state = _require_state()

    summary = {"discovered": 0, "fetched": 0, "failed": 0, "errors": []}

    with tempfile.TemporaryDirectory() as td:
        with sync_playwright() as p:
            browser, context = _launch_context(p, state, Path(td))
            page = context.new_page()

            page.goto(BASE_URL, wait_until="load", timeout=60_000)
            page.wait_for_timeout(2000)
            if "login.microsoftonline.com" in page.url:
                browser.close()
                raise NotAuthenticatedError(
                    "Session expired. Run `python -m qa_intranet login` again."
                )

            roots = parse_menu_html(page.content(), page_url=BASE_URL)
            all_nodes = flatten(roots)
            summary["discovered"] = len(all_nodes)

            # Persist the menu tree first so parent_ids resolve correctly.
            slug_to_id: dict[str, int] = {}
            with session_scope() as session:
                # Two passes: roots first, then children.
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
                    browser.close()
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

            browser.close()

    return summary
