from __future__ import annotations

from qa_intranet.crawler import flatten, parse_menu_html

SAMPLE_HTML = """
<html>
<body>
  <nav aria-label="Informes">
    <a href="/informes/encuestas/nps">NPS</a>
    <a href="/informes/encuestas/nps/q1">NPS Q1</a>
    <a href="/informes/encuestas/nps/q2">NPS Q2</a>
    <a href="/informes/encuestas/satisfaccion">Satisfacción</a>
    <a href="/informes/encuestas/satisfaccion/general">General</a>
  </nav>
</body>
</html>
"""


def test_parse_menu_builds_hierarchy():
    roots = parse_menu_html(SAMPLE_HTML, page_url="https://intranettools.esic.edu/informes/encuestas")
    all_nodes = flatten(roots)

    titles = {n.title for n in all_nodes}
    assert {"NPS", "NPS Q1", "NPS Q2", "Satisfacción", "General"} <= titles

    root_titles = {r.title for r in roots}
    assert {"NPS", "Satisfacción"} <= root_titles

    nps_root = next(r for r in roots if r.title == "NPS")
    child_titles = {c.title for c in nps_root.children}
    assert child_titles == {"NPS Q1", "NPS Q2"}
    assert nps_root.is_leaf is False
    assert all(c.is_leaf for c in nps_root.children)


def test_unique_slugs():
    roots = parse_menu_html(SAMPLE_HTML)
    slugs = [n.slug for n in flatten(roots)]
    assert len(slugs) == len(set(slugs))
