"""Autonomous navigation of the Power BI dashboard.

The agent uses these primitives to explore filters and trigger new queries
on the user's open Chrome without the user touching anything:

* ``inspect_dashboard_filters()`` — enumerate the slicers the current page
  is showing, each with its visible label and (a sample of) options.
* ``apply_filters_and_capture(filters, duration_s)`` — click through the
  slicers to set the requested values, then record everything Power BI
  fetches during ``duration_s`` seconds and decode the DSR responses.

These helpers rely on Playwright semantic locators (``get_by_role``,
``get_by_text``) against the Power BI iframe. They're intentionally
tolerant to DOM changes, but the first run against a given portal will
often surface selector adjustments — logs are verbose so the agent /
user can see exactly what failed.
"""
from __future__ import annotations

import json
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from qa_intranet.auth import get_context
from qa_intranet.dsr_parser import parse_qes_json
from qa_intranet.live import _extract_bodies_from_trace, _is_data_url


POWER_BI_FRAME_MARKERS = ("powerbi", "wabi-", "analysis.windows.net", "pbilive")


def _find_intranet_page(context):
    for page in context.pages:
        try:
            if "intranettools" in (page.url or ""):
                return page
        except Exception:
            continue
    return context.pages[0] if context.pages else None


def _find_powerbi_frames(page) -> list:
    out = []
    try:
        frames = page.frames
    except Exception:
        frames = []
    for frame in frames:
        url = (frame.url or "").lower()
        if any(m in url for m in POWER_BI_FRAME_MARKERS):
            out.append(frame)
    # If none matched, fall back to the deepest frames.
    if not out:
        out = [f for f in frames if f != page.main_frame]
    return out


def _slicer_locators(frame):
    """Return locator candidates that usually represent a Power BI slicer."""
    candidates = []
    try:
        candidates.append(frame.locator(
            "div.slicer-container, div.slicer, .visual-slicer"
        ))
    except Exception:
        pass
    try:
        candidates.append(frame.get_by_role("combobox"))
    except Exception:
        pass
    try:
        candidates.append(frame.get_by_role("listbox"))
    except Exception:
        pass
    return candidates


def _describe_slicer(slicer_el) -> dict:
    """Best-effort description of a slicer element."""
    info = {"label": "", "options_sample": [], "selector": ""}
    try:
        info["label"] = slicer_el.get_attribute("aria-label") or slicer_el.inner_text()[:80]
    except Exception:
        pass
    try:
        # Power BI often renders options as role=option once opened; we peek
        # at the visible text inside the slicer box to gather labels.
        info["options_sample"] = [
            t for t in slicer_el.inner_text().splitlines() if t.strip()
        ][:20]
    except Exception:
        pass
    return info


def _shell_dropdowns(page) -> list[dict]:
    """Find dropdowns in the Angular shell (outside the Power BI iframe).

    The portal's TITULACIÓN selector lives there: it changes which Power BI
    report is embedded, then the slicers inside the iframe become available.
    """
    main = page.main_frame
    out: list[dict] = []
    selectors = (
        "mat-select",
        "select",
        "[role='combobox']",
        ".dropdown-toggle",
        "ng-select",
    )
    seen_labels: set[str] = set()
    for sel in selectors:
        try:
            loc = main.locator(sel)
            count = loc.count()
        except Exception:
            continue
        for i in range(min(count, 30)):
            try:
                el = loc.nth(i)
                label = (
                    el.get_attribute("aria-label")
                    or el.get_attribute("placeholder")
                    or el.inner_text()
                )
                label = (label or "").strip()
                if not label:
                    continue
                key = label[:80]
                if key in seen_labels:
                    continue
                seen_labels.add(key)
                out.append({
                    "label": key,
                    "css_selector": sel,
                    "index": i,
                    "kind": "shell",
                })
            except Exception:
                continue
    return out


def inspect_filters(duration_hint_s: int = 0) -> dict:
    """List shell dropdowns AND Power BI slicers visible on the page."""
    with get_context(headless=False) as (_, context):
        page = _find_intranet_page(context)
        if page is None:
            raise RuntimeError(
                "No hay pestañas abiertas en Chrome CDP. Abre el portal con "
                "`python -m qa_intranet open-chrome`."
            )
        shell = _shell_dropdowns(page)
        frames = _find_powerbi_frames(page)
        slicers: list[dict] = []
        for frame in frames:
            for candidate in _slicer_locators(frame):
                try:
                    count = candidate.count()
                except Exception:
                    count = 0
                for i in range(min(count, 20)):
                    try:
                        el = candidate.nth(i)
                        info = _describe_slicer(el)
                        info["frame_url"] = frame.url
                        info["index"] = i
                        info["kind"] = "slicer"
                        if info["label"] or info["options_sample"]:
                            slicers.append(info)
                    except Exception:
                        continue

        return {
            "page_url": page.url,
            "frame_count": len(frames),
            "shell_filters": shell,
            "slicers": slicers,
        }


def _match_slicer(frame, label_query: str):
    """Find the slicer element whose label best matches ``label_query``."""
    rx = re.compile(re.escape(label_query), re.IGNORECASE)
    # Try aria-label match first.
    try:
        by_aria = frame.locator(f"[aria-label*=\"{label_query}\" i]")
        if by_aria.count() > 0:
            return by_aria.first
    except Exception:
        pass
    # Fall back to text-content match on slicer containers.
    for candidate in _slicer_locators(frame):
        try:
            count = candidate.count()
        except Exception:
            continue
        for i in range(min(count, 40)):
            try:
                el = candidate.nth(i)
                text = el.inner_text()
                if rx.search(text or ""):
                    return el
            except Exception:
                continue
    return None


def _set_slicer_value(frame, label_query: str, value: str, timeout_ms: int = 5000):
    """Open the matching slicer and click the option whose text contains
    ``value``. Raises RuntimeError with a descriptive message on failure."""
    slicer = _match_slicer(frame, label_query)
    if slicer is None:
        raise RuntimeError(
            f"No encontré un slicer con etiqueta '{label_query}'."
        )
    try:
        slicer.click(timeout=timeout_ms)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"No pude abrir el slicer '{label_query}': {exc}"
        ) from exc

    # Try selecting the option via role=option / text match.
    option = None
    try:
        option = frame.get_by_role("option", name=re.compile(re.escape(value), re.I)).first
    except Exception:
        option = None
    if option is None or option.count() == 0:
        try:
            option = frame.get_by_text(value, exact=False).first
        except Exception:
            option = None
    if option is None or option.count() == 0:
        raise RuntimeError(
            f"Abrí '{label_query}' pero no encontré la opción '{value}'."
        )
    option.click(timeout=timeout_ms)

    # Click somewhere neutral to close.
    try:
        frame.locator("body").click(position={"x": 5, "y": 5}, timeout=1000)
    except Exception:
        pass


def _set_shell_dropdown(page, label_query: str, value: str, timeout_ms: int = 5000):
    """Open a shell-level dropdown (Angular) and select a value by substring."""
    rx = re.compile(re.escape(label_query), re.IGNORECASE)
    main = page.main_frame
    selectors = (
        "mat-select", "select", "[role='combobox']", ".dropdown-toggle",
        "ng-select",
    )
    target = None
    for sel in selectors:
        try:
            candidates = main.locator(sel)
            count = candidates.count()
        except Exception:
            continue
        for i in range(min(count, 30)):
            try:
                el = candidates.nth(i)
                txt = (el.get_attribute("aria-label")
                       or el.get_attribute("placeholder")
                       or el.inner_text() or "")
                if rx.search(txt):
                    target = el
                    break
            except Exception:
                continue
        if target is not None:
            break
    if target is None:
        raise RuntimeError(
            f"No encontré un dropdown del shell con etiqueta '{label_query}'."
        )

    # Native <select> has a special API.
    try:
        tag = target.evaluate("el => el.tagName.toLowerCase()")
    except Exception:
        tag = ""
    if tag == "select":
        target.select_option(label=value, timeout=timeout_ms)
        return

    target.click(timeout=timeout_ms)
    # Try to find an option with the value
    option = None
    for sel in ("[role='option']", "mat-option", ".dropdown-item",
                "ng-option", "li[role='option']"):
        try:
            opts = main.locator(sel)
            n = opts.count()
        except Exception:
            continue
        for j in range(min(n, 200)):
            try:
                o = opts.nth(j)
                t = (o.inner_text() or "").strip()
                if value.lower() in t.lower():
                    option = o
                    break
            except Exception:
                continue
        if option is not None:
            break
    if option is None:
        raise RuntimeError(
            f"Abrí el dropdown shell '{label_query}' pero no encontré "
            f"una opción que contenga '{value}'."
        )
    option.click(timeout=timeout_ms)


def apply_and_capture(
    filters: dict[str, str], duration_s: int = 20
) -> dict[str, Any]:
    """Apply the given filters on the live Chrome page and capture responses.

    For each filter we first try to apply it as a shell dropdown (Angular),
    and if that fails fall back to a Power BI slicer inside the iframe.
    Capture stops early once 5s pass without new data responses.
    """
    applied: list[str] = []
    errors: list[str] = []
    response_count = {"n": 0}

    with get_context(headless=False) as (_, context):
        page = _find_intranet_page(context)
        if page is None:
            raise RuntimeError(
                "No hay pestaña de intranettools abierta en el Chrome CDP."
            )

        # Count interesting responses so we can stop early.
        def on_response(response):
            try:
                if _is_data_url(response.url):
                    response_count["n"] += 1
            except Exception:
                pass
        context.on("response", on_response)

        context.tracing.start(screenshots=False, snapshots=False, sources=False)
        try:
            for name, value in (filters or {}).items():
                shell_err = None
                try:
                    _set_shell_dropdown(page, name, value)
                    applied.append(f"shell:{name} = {value}")
                    time.sleep(2)  # let the iframe re-load
                    continue
                except Exception as exc:  # noqa: BLE001
                    shell_err = exc

                # Fall back to slicers inside the Power BI iframe.
                frames = _find_powerbi_frames(page)
                target_frame = frames[0] if frames else page.main_frame
                try:
                    _set_slicer_value(target_frame, name, value)
                    applied.append(f"slicer:{name} = {value}")
                    time.sleep(1)
                except Exception as exc:  # noqa: BLE001
                    errors.append(
                        f"{name}={value}: shell({shell_err}); slicer({exc})"
                    )

            # Wait for queries with early stop: if no new response in 5s,
            # consider the dashboard finished refreshing.
            deadline = time.monotonic() + max(duration_s, 5)
            quiet_since = time.monotonic()
            last_count = response_count["n"]
            while time.monotonic() < deadline:
                time.sleep(0.5)
                if response_count["n"] != last_count:
                    last_count = response_count["n"]
                    quiet_since = time.monotonic()
                elif time.monotonic() - quiet_since > 5:
                    break

            with tempfile.TemporaryDirectory() as td:
                trace_path = Path(td) / "trace.zip"
                context.tracing.stop(path=str(trace_path))
                entries = _extract_bodies_from_trace(trace_path)
        except Exception:
            try:
                context.tracing.stop(path=None)
            except Exception:
                pass
            raise

    tables = []
    for entry in entries:
        body = entry["body_bytes"]
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
        "applied_filters": applied,
        "errors": errors,
        "responses_seen": len(entries),
        "tables": tables,
    }
