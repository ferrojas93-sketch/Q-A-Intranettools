"""HAR (HTTP Archive) parsing.

When live capture via CDP/route fails (Power BI's out-of-process iframes
drop response bodies from Playwright's reach), the pragmatic path is to
have the user record traffic with Chrome DevTools → Network → Save as HAR.
The HAR file is a plain JSON document containing every request and the
full response body, so we can pull the Power BI data out without any
browser automation.
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


DATA_URL_MARKERS = (
    "/queryexecutionservice/",
    "/querydata",
    "/explore/reports/",
    "/metadata/v",
)


def _is_data_url(url: str) -> bool:
    low = url.lower()
    return any(m in low for m in DATA_URL_MARKERS)


@dataclass
class HarEntry:
    method: str
    url: str
    status: int
    mime_type: str
    request_body: Optional[str]
    response_bytes: Optional[bytes]

    @property
    def is_data(self) -> bool:
        return _is_data_url(self.url)


def _decode_response_content(content: dict) -> Optional[bytes]:
    text = content.get("text")
    if text is None:
        return None
    encoding = content.get("encoding")
    if encoding == "base64":
        try:
            return base64.b64decode(text)
        except Exception:
            return text.encode("utf-8", errors="replace")
    return text.encode("utf-8")


def iter_entries(har_path: Path) -> Iterable[HarEntry]:
    with har_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    for raw in data.get("log", {}).get("entries", []):
        request = raw.get("request", {}) or {}
        response = raw.get("response", {}) or {}
        content = response.get("content", {}) or {}
        post = request.get("postData", {}) or {}
        yield HarEntry(
            method=request.get("method", "GET"),
            url=request.get("url", ""),
            status=response.get("status", 0),
            mime_type=content.get("mimeType", ""),
            request_body=post.get("text"),
            response_bytes=_decode_response_content(content),
        )


def extract_to_bodies_dir(
    har_path: Path, bodies_dir: Path, *, only_data: bool = True
) -> dict:
    """Read a HAR file and dump each (data) response body to disk.

    Returns a summary dict with counts and a list of unique data URLs.
    """
    bodies_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    saved = 0
    skipped = 0
    unique_urls: set[str] = set()
    index: list[dict] = []

    for i, entry in enumerate(iter_entries(har_path), 1):
        total += 1
        if only_data and not entry.is_data:
            continue
        if not entry.response_bytes:
            skipped += 1
            continue
        unique_urls.add(entry.url.split("?")[0])
        digest = hashlib.sha1(
            entry.url.encode("utf-8") + str(i).encode()
        ).hexdigest()[:12]
        head = entry.response_bytes[:1].decode("latin-1", errors="ignore")
        ext = ".json" if head in ("{", "[") else ".bin"
        fname = f"{i:04d}_har_{digest}{ext}"
        (bodies_dir / fname).write_bytes(entry.response_bytes)
        index.append({
            "seq": i,
            "method": entry.method,
            "url": entry.url,
            "status": entry.status,
            "mime_type": entry.mime_type,
            "body_file": str((bodies_dir / fname).resolve()),
            "body_bytes": len(entry.response_bytes),
            "request_body": entry.request_body,
        })
        saved += 1

    (bodies_dir / "_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "total_entries": total,
        "saved": saved,
        "skipped_without_body": skipped,
        "unique_data_urls": sorted(unique_urls),
        "bodies_dir": str(bodies_dir.resolve()),
        "index_file": str((bodies_dir / "_index.json").resolve()),
    }
