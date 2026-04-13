"""Discovers the menu tree of reports on intranettools and hands each leaf to
the extractor.

The real site structure is only known once the user has logged in, so the
selectors here are written defensively and fall back to a generic
link-scraping strategy when the expected menu markup isn't found. Adjust the
CSS selectors in ``MENU_SELECTORS`` after inspecting the real DOM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from qa_intranet.config import BASE_URL

# Candidate selectors for the main menu — tweak with real DOM in hand.
MENU_SELECTORS = [
    "nav[aria-label*='informes' i] a",
    "aside a[href*='/informes']",
    "ul.menu a[href*='/informes']",
    "a[href*='/informes/']",
]


@dataclass
class MenuNode:
    slug: str
    title: str
    url: str
    parent: Optional["MenuNode"] = None
    children: list["MenuNode"] = field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        return not self.children


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    v = value.lower().strip()
    v = _SLUG_RE.sub("-", v).strip("-")
    return v or "report"


def _dedupe_slug(slug: str, seen: set[str]) -> str:
    base = slug
    i = 2
    while slug in seen:
        slug = f"{base}-{i}"
        i += 1
    seen.add(slug)
    return slug


def _is_intranet_url(url: str) -> bool:
    host = urlparse(url).netloc
    return host == "" or host.endswith("intranettools.esic.edu")


def parse_menu_html(html: str, page_url: str = BASE_URL) -> list[MenuNode]:
    """Parse the rendered menu into a list of top-level MenuNode roots.

    This is intentionally tolerant — it tries each selector until one returns
    links, then groups them into a simple two-level hierarchy based on URL
    path depth.
    """
    soup = BeautifulSoup(html, "html.parser")

    links: list[tuple[str, str]] = []  # (title, absolute_url)
    for selector in MENU_SELECTORS:
        found = soup.select(selector)
        if not found:
            continue
        for a in found:
            href = a.get("href")
            text = a.get_text(strip=True)
            if not href or not text:
                continue
            absolute = urljoin(page_url, href)
            if not _is_intranet_url(absolute):
                continue
            links.append((text, absolute))
        if links:
            break

    if not links:
        return []

    # Deduplicate by URL, preserving order.
    seen_urls: set[str] = set()
    unique: list[tuple[str, str]] = []
    for title, url in links:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        unique.append((title, url))

    # Group by path depth under /informes. Shortest paths become roots; longer
    # paths that share a prefix attach as children.
    nodes_by_url: dict[str, MenuNode] = {}
    roots: list[MenuNode] = []
    seen_slugs: set[str] = set()

    # Sort so that parents come before children.
    unique.sort(key=lambda t: len(urlparse(t[1]).path))

    for title, url in unique:
        slug = _dedupe_slug(_slugify(title), seen_slugs)
        node = MenuNode(slug=slug, title=title, url=url)
        nodes_by_url[url] = node

        parent_url = _find_parent_url(url, nodes_by_url.keys())
        if parent_url and parent_url != url:
            parent = nodes_by_url[parent_url]
            node.parent = parent
            parent.children.append(node)
        else:
            roots.append(node)

    return roots


def _find_parent_url(url: str, candidates: Iterable[str]) -> Optional[str]:
    path = urlparse(url).path.rstrip("/")
    segments = path.split("/")
    # Strip one segment at a time until we find an existing candidate URL.
    while len(segments) > 1:
        segments = segments[:-1]
        prefix = "/".join(segments) or "/"
        for c in candidates:
            c_path = urlparse(c).path.rstrip("/")
            if c_path == prefix and c != url:
                return c
    return None


def flatten(roots: list[MenuNode]) -> list[MenuNode]:
    out: list[MenuNode] = []

    def walk(node: MenuNode) -> None:
        out.append(node)
        for child in node.children:
            walk(child)

    for r in roots:
        walk(r)
    return out
