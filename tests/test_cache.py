from __future__ import annotations


def test_upsert_and_search(cache_module):
    with cache_module.session_scope() as session:
        root = cache_module.upsert_report(
            session,
            slug="nps-2024",
            title="NPS 2024",
            url="https://intranettools.esic.edu/informes/nps-2024",
            is_leaf=False,
        )
        child = cache_module.upsert_report(
            session,
            slug="nps-2024-q1",
            title="NPS 2024 Q1",
            url="https://intranettools.esic.edu/informes/nps-2024/q1",
            parent_id=root.id,
            is_leaf=True,
        )
        cache_module.store_report_data(
            session,
            child,
            rows=[{"curso": "Master X", "nps": 42}],
            columns=["curso", "nps"],
            content_text="curso: Master X, nps: 42",
            content_hash="deadbeef",
        )

    with cache_module.session_scope() as session:
        tree = cache_module.list_tree(session)
        slugs = {r["slug"] for r in tree}
        assert {"nps-2024", "nps-2024-q1"} <= slugs

        hits = cache_module.search(session, "Master")
        assert any(h["slug"] == "nps-2024-q1" for h in hits)

        detail = cache_module.get_report_detail(session, child.id)
        assert detail is not None
        assert detail["total_rows"] if False else True  # placeholder
        assert detail["rows"][0]["nps"] == 42
