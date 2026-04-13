"""Orchestrator: load the authenticated session, crawl the menu, download each
leaf report and persist everything to the SQLite cache.

All Playwright launches use the persistent Chromium profile defined in
``qa_intranet.auth`` so that authenticated API calls work the same way
they do in the user's visible browser.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from qa_intranet.auth import get_context, has_profile
from qa_intranet.config import CDP_URL
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

    with get_context(headless=True) as (_, context):
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

    Full response bodies for the Power BI QES endpoint (the one that returns
    the table data) are saved to a sibling ``*_bodies/`` directory so we can
    parse them offline without blowing up the JSONL.
    """
    import hashlib
    import json

    _require_profile()
    captured: list[dict] = []
    seen_api_urls: set[str] = set()
    bodies_dir = output_path.parent / (output_path.stem + "_bodies")
    body_counter = {"n": 0}

    def _is_interesting(url: str) -> bool:
        lower = url.lower().split("?")[0]
        boring_ext = (".js", ".css", ".woff", ".woff2", ".ttf", ".svg", ".png",
                      ".jpg", ".jpeg", ".gif", ".ico", ".map", ".html")
        if any(lower.endswith(ext) for ext in boring_ext):
            return False
        # Drop telemetry noise but keep everything else.
        if "applicationinsights.azure.com" in lower:
            return False
        return True

    def _is_data_endpoint(url: str) -> bool:
        """Power BI endpoints that return the actual dashboard data."""
        markers = (
            "/querydata",
            "/queryexecutionservice/",
            "/explore/reports/",
            "/metadata/v",
        )
        low = url.lower()
        return any(m in low for m in markers)

    # Collected data-endpoint requests to replay after the user finishes
    # navigating. Keyed by (method, url, post_data_hash) to dedupe.
    replay_queue: list[dict] = []
    replay_seen: set[str] = set()

    def on_request(request):
        if _is_interesting(request.url):
            # Save a full copy of every header (diagnostic purposes).
            all_headers = dict(request.headers)
            entry = {
                "kind": "request",
                "method": request.method,
                "url": request.url,
                "resource_type": request.resource_type,
                "headers": {
                    k: v for k, v in all_headers.items()
                    if k.lower() in (
                        "content-type", "accept", "authorization",
                        "x-powerbi-resourcekey", "activityid", "requestid",
                    )
                },
            }
            # Preserve the outgoing DAX/query payload so we can replay it.
            if _is_data_endpoint(request.url) and request.method in ("POST", "PUT"):
                try:
                    entry["post_data"] = request.post_data
                except Exception:
                    entry["post_data"] = None

                # Queue for later replay with the full header set.
                key = f"{request.method}|{request.url}|{hash(entry.get('post_data') or '')}"
                if key not in replay_seen:
                    replay_seen.add(key)
                    replay_queue.append({
                        "method": request.method,
                        "url": request.url,
                        "headers": all_headers,
                        "post_data": entry.get("post_data"),
                    })
            captured.append(entry)

    def on_response(response):
        if not _is_interesting(response.url):
            return
        ct = response.headers.get("content-type", "")
        entry = {
            "kind": "response",
            "status": response.status,
            "url": response.url,
            "content_type": ct,
        }

        is_data = _is_data_endpoint(response.url)
        if is_data or "json" in ct.lower():
            seen_api_urls.add(response.url.split("?")[0])

        if is_data:
            # Save the full body regardless of content-type; Power BI has mixed
            # responses (application/json, application/x-javascript, binary).
            body_bytes = None
            body_err = None
            try:
                # Block until the body is fully downloaded into Playwright's
                # cache. Without this the Power BI iframe often closes
                # itself before body() can read the bytes.
                try:
                    response.finished()
                except Exception:
                    pass
                body_bytes = response.body()
            except Exception as exc:  # noqa: BLE001
                body_err = f"{type(exc).__name__}: {exc}"
            if body_bytes:
                bodies_dir.mkdir(parents=True, exist_ok=True)
                body_counter["n"] += 1
                digest = hashlib.sha1(
                    response.url.encode("utf-8") + str(body_counter["n"]).encode()
                ).hexdigest()[:12]
                # Extension based on content-type heuristic.
                ext = ".json"
                if "json" not in ct.lower() and "javascript" not in ct.lower():
                    ext = ".bin"
                fname = f"{body_counter['n']:04d}_{digest}{ext}"
                (bodies_dir / fname).write_bytes(body_bytes)
                entry["body_file"] = str((bodies_dir / fname).resolve())
                entry["body_bytes"] = len(body_bytes)
                print(f"  [capture] saved body {fname} ({len(body_bytes):,}B) "
                      f"← {response.url.split('?')[0][-80:]}")
            else:
                entry["body_error"] = body_err or "empty"
                print(f"  [capture] could not read body for "
                      f"{response.url.split('?')[0][-80:]}: {entry['body_error']}")
        elif "json" in ct.lower():
            try:
                entry["body_preview"] = response.text()[:2000]
            except Exception:
                pass

        captured.append(entry)

    def handle_data_route(route):
        request = route.request
        try:
            # Playwright performs the request itself from its Node side,
            # then we hand the response back to the page. This gives us the
            # body even when the Power BI iframe closes immediately after.
            api_response = route.fetch()
        except Exception as exc:  # noqa: BLE001
            print(f"  [capture] route.fetch failed: {exc}")
            try:
                route.continue_()
            except Exception:
                pass
            return

        try:
            body_bytes = api_response.body()
        except Exception as exc:  # noqa: BLE001
            body_bytes = None
            print(f"  [capture] api_response.body failed: {exc}")

        if body_bytes:
            bodies_dir.mkdir(parents=True, exist_ok=True)
            body_counter["n"] += 1
            digest = hashlib.sha1(
                request.url.encode("utf-8") + str(body_counter["n"]).encode()
            ).hexdigest()[:12]
            ct = api_response.headers.get("content-type", "")
            ext = ".json" if "json" in ct.lower() else ".bin"
            fname = f"{body_counter['n']:04d}_route_{digest}{ext}"
            (bodies_dir / fname).write_bytes(body_bytes)
            short = request.url.split("?")[0][-80:]
            print(
                f"  [capture] route saved {fname} ({len(body_bytes):,}B) "
                f"← {request.method} {short}"
            )
            seen_api_urls.add(request.url.split("?")[0])
            captured.append({
                "kind": "route_response",
                "method": request.method,
                "url": request.url,
                "status": api_response.status,
                "content_type": ct,
                "body_file": str((bodies_dir / fname).resolve()),
                "body_bytes": len(body_bytes),
            })

        try:
            route.fulfill(response=api_response)
        except Exception as exc:  # noqa: BLE001
            # If fulfill fails (e.g. iframe already gone) we still kept the body.
            try:
                route.continue_()
            except Exception:
                pass

    with get_context(headless=False) as (_, context):
        # Collect every BrowserContext we can see.
        browser = getattr(context, "browser", None)
        all_contexts = list(browser.contexts) if browser is not None else [context]
        if context not in all_contexts:
            all_contexts.append(context)

        print(f"  [capture] attaching to {len(all_contexts)} context(s):")
        for idx, ctx in enumerate(all_contexts):
            # Still listen to events for URL discovery / request diagnostics.
            ctx.on("request", on_request)
            ctx.on("response", on_response)
            # Route-based interception for data endpoints: the only reliable
            # way to read Power BI QES bodies.
            ctx.route("**/QueryExecutionService/**", handle_data_route)
            ctx.route("**/querydata**", handle_data_route)
            ctx.route("**/explore/reports/**", handle_data_route)
            ctx.route("**/metadata/v*/**", handle_data_route)
            for page in ctx.pages:
                try:
                    print(f"    ctx#{idx} page: {page.url[:100]}")
                except Exception:
                    print(f"    ctx#{idx} page: <url unavailable>")

        if CDP_URL:
            print()
            print("=" * 70)
            print(" Conectado a tu Chrome via CDP.")
            print(" Ve a la pestaña de intranettools (o abre una nueva) y NAVEGA:")
            print("   - Cambia de titulación en el dropdown.")
            print("   - Abre varias pestañas inferiores (CUANTI, CUALI, etc.).")
            print("   - Cambia año y campus si puedes.")
            print(" Cuando termines, vuelve aquí y pulsa ENTER.")
            print("=" * 70)
        else:
            page = context.pages[0] if context.pages else context.new_page()
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

        # Replay the captured data requests using Playwright's server-side
        # HTTP client (context.request). Unlike on_response, this doesn't
        # depend on the Power BI iframe still being alive, and it shares
        # cookies with the user's active session.
        replay_summary = {"ok": 0, "fail": 0}
        if replay_queue:
            print(f"\n  [capture] replaying {len(replay_queue)} data requests…")
            for i, req in enumerate(replay_queue, 1):
                try:
                    kwargs = {"headers": req["headers"]}
                    if req.get("post_data"):
                        kwargs["data"] = req["post_data"]
                    # Try the same context first; fall back to any sibling
                    # context if that fails (cookie partitioning).
                    resp = None
                    last_err = None
                    for ctx in all_contexts:
                        try:
                            if req["method"] == "POST":
                                resp = ctx.request.post(req["url"], **kwargs)
                            else:
                                resp = ctx.request.fetch(
                                    req["url"], method=req["method"], **kwargs
                                )
                            if resp.ok or resp.status in (200, 206):
                                break
                        except Exception as exc:  # noqa: BLE001
                            last_err = exc
                            continue
                    if resp is None:
                        raise last_err or RuntimeError("no context could fulfil")
                    body_bytes = resp.body()
                    bodies_dir.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha1(
                        req["url"].encode("utf-8") + str(i).encode()
                    ).hexdigest()[:12]
                    ext = ".json"
                    fname = f"{i:04d}_replay_{digest}{ext}"
                    (bodies_dir / fname).write_bytes(body_bytes)
                    print(
                        f"    [replay] {i:02d} OK {resp.status} "
                        f"{len(body_bytes):>7,}B → {fname}"
                    )
                    replay_summary["ok"] += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"    [replay] {i:02d} FAIL: {exc}")
                    replay_summary["fail"] += 1
            print(
                f"  [capture] replay done: {replay_summary['ok']} ok / "
                f"{replay_summary['fail']} fail"
            )

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

    with get_context(headless=True) as (_, context):
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
