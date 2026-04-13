"""Download and parse per-report exports (Excel/CSV) from intranettools.

The site is assumed to expose an 'Export' button on each leaf report. This
module tries several heuristics to find and click it via Playwright, handles
the resulting download, and returns the content as a pandas DataFrame plus a
Markdown rendering suitable for indexing in FTS5.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

from qa_intranet.config import DOWNLOADS_DIR

EXPORT_BUTTON_CANDIDATES = [
    "a:has-text('Exportar')",
    "button:has-text('Exportar')",
    "a:has-text('Descargar')",
    "button:has-text('Descargar')",
    "a[href$='.xlsx']",
    "a[href$='.csv']",
    "a[download]",
]


@dataclass
class ExtractedReport:
    path: Path
    rows: list[dict]
    columns: list[str]
    content_text: str
    content_hash: str


def _df_to_markdown(df: pd.DataFrame, max_rows: int = 200) -> str:
    """Render a DataFrame to a compact Markdown block for FTS indexing."""
    if df.empty:
        return ""
    preview = df.head(max_rows)
    try:
        return preview.to_markdown(index=False)
    except Exception:
        # to_markdown requires tabulate; fall back to CSV text.
        return preview.to_csv(index=False)


def parse_export(path: Path) -> ExtractedReport:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(path)
    elif suffix == ".csv":
        df = pd.read_csv(path)
    else:
        raise ValueError(f"Unsupported export format: {suffix}")

    df = df.fillna("")
    columns = [str(c) for c in df.columns]
    rows = df.to_dict(orient="records")
    content_text = f"# {path.stem}\n\n{_df_to_markdown(df)}"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return ExtractedReport(
        path=path,
        rows=rows,
        columns=columns,
        content_text=content_text,
        content_hash=digest,
    )


def download_report(page, slug: str, timeout_ms: int = 30_000) -> Optional[Path]:
    """Click the export button on the currently-loaded page and return the saved path.

    ``page`` is a Playwright ``Page``. Returns None if no export control is found.
    """
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    for selector in EXPORT_BUTTON_CANDIDATES:
        locator = page.locator(selector).first
        try:
            if locator.count() == 0:
                continue
        except Exception:
            continue

        try:
            with page.expect_download(timeout=timeout_ms) as download_info:
                locator.click()
            download = download_info.value
        except Exception:
            continue

        suggested = download.suggested_filename or f"{slug}.xlsx"
        suffix = Path(suggested).suffix or ".xlsx"
        target = DOWNLOADS_DIR / f"{slug}{suffix}"
        download.save_as(target)
        return target

    return None
