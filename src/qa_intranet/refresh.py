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


def discover_menu() -> list[MenuNode]:
    """Log in with the saved session and return the parsed menu tree."""
    from playwright.sync_api import sync_playwright

    state = _require_state()
    with tempfile.TemporaryDirectory() as td:
        with sync_playwright() as p:
            browser, context = _launch_context(p, state, Path(td))
            page = context.new_page()
            page.goto(BASE_URL, wait_until="networkidle")
            html = page.content()
            current_url = page.url
            browser.close()

    if "login.microsoftonline.com" in current_url:
        raise NotAuthenticatedError(
            "Session expired. Run `python -m qa_intranet login` again."
        )
    return parse_menu_html(html, page_url=BASE_URL)


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

            page.goto(BASE_URL, wait_until="networkidle")
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
                    page.goto(node.url, wait_until="networkidle")
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
