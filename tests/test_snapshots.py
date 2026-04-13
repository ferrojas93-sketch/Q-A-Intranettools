"""Tests for Snapshot CRUD + FTS + the loader pipeline."""
from __future__ import annotations

import json
from pathlib import Path


def _make_sample_body(tmp_path, *, filename, url):
    """Write a minimal DSR-encoded QES response to tmp_path/filename and return its path."""
    payload = {
        "results": [{
            "result": {
                "data": {
                    "descriptor": {
                        "Select": [
                            {"Name": "datos.Programa"},
                            {"Name": "datos.NPS"},
                        ],
                    },
                    "dsr": {
                        "DS": [{
                            "PH": [{
                                "DM0": [
                                    {
                                        "S": [
                                            {"N": "G0", "T": 1, "DN": "D0"},
                                            {"N": "G1", "T": 1, "DN": "D1"},
                                        ],
                                        "C": ["MBA Marketing", 72],
                                    },
                                    {"C": ["Grado ADE", 65]},
                                    {"C": [0, 80]},  # reuse MBA Marketing
                                ],
                            }],
                        }],
                    },
                },
            },
        }],
    }
    path = tmp_path / filename
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_snapshot_insert_and_search(cache_module, tmp_path):
    # Prepare a bodies dir with one sample response + _index.json
    bodies_dir = tmp_path / "bodies"
    bodies_dir.mkdir()
    _make_sample_body(
        bodies_dir,
        filename="0001_test.json",
        url="https://wabi-north-europe.example/explore/reports/abcd-1234/query",
    )
    (bodies_dir / "_index.json").write_text(
        json.dumps([{
            "seq": 1,
            "method": "POST",
            "url": "https://wabi-north-europe.example/explore/reports/abcd-1234/query",
            "status": 200,
            "body_file": str((bodies_dir / "0001_test.json").resolve()),
            "body_bytes": 100,
            "request_body": "dax-payload",
        }]),
        encoding="utf-8",
    )

    from qa_intranet.loader import load_bodies_dir
    summary = load_bodies_dir(bodies_dir)
    assert summary["snapshots_inserted"] == 1

    with cache_module.session_scope() as session:
        listed = cache_module.list_snapshots(session)
        assert len(listed) == 1
        snap = listed[0]
        assert snap["row_count"] == 3
        assert "datos.NPS" in snap["columns"]

        detail = cache_module.get_snapshot_rows(session, snap["id"])
        assert detail["total_rows"] == 3
        assert detail["rows"][0]["datos.Programa"] == "MBA Marketing"
        assert detail["rows"][2]["datos.Programa"] == "MBA Marketing"  # index reuse

        hits = cache_module.search_snapshot_rows(session, "Grado")
        assert any("Grado" in h["snippet"] or "Grado ADE" in h["snippet"]
                   for h in hits)


def test_loader_is_idempotent(cache_module, tmp_path):
    bodies_dir = tmp_path / "bodies"
    bodies_dir.mkdir()
    _make_sample_body(
        bodies_dir, filename="0001_test.json",
        url="https://example/explore/reports/abcd-1234/query",
    )

    from qa_intranet.loader import load_bodies_dir
    first = load_bodies_dir(bodies_dir)
    assert first["snapshots_inserted"] == 1

    second = load_bodies_dir(bodies_dir)
    assert second["snapshots_skipped_duplicate"] == 1
    assert second["snapshots_inserted"] == 0


def test_reset_clears_snapshots(cache_module, tmp_path):
    bodies_dir = tmp_path / "bodies"
    bodies_dir.mkdir()
    _make_sample_body(
        bodies_dir, filename="0001_test.json", url="https://example/query",
    )

    from qa_intranet.loader import load_bodies_dir
    load_bodies_dir(bodies_dir)
    with cache_module.session_scope() as session:
        assert len(cache_module.list_snapshots(session)) == 1

    # Reset and re-run with a different file → single snapshot only.
    _make_sample_body(
        bodies_dir, filename="0002_test.json", url="https://example/query",
    )
    summary = load_bodies_dir(bodies_dir, reset=True)
    assert summary["snapshots_inserted"] >= 1
    with cache_module.session_scope() as session:
        snaps = cache_module.list_snapshots(session)
    # After reset + reload, exactly the files present → 2 snapshots.
    assert len(snaps) == 2
