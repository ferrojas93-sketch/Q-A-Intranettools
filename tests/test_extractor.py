from __future__ import annotations

import pandas as pd

from qa_intranet.extractor import parse_export


def test_parse_csv(tmp_path):
    csv_path = tmp_path / "satisfaccion.csv"
    pd.DataFrame(
        {"curso": ["MBA", "Master Marketing"], "nps": [42, 55]}
    ).to_csv(csv_path, index=False)

    result = parse_export(csv_path)
    assert result.columns == ["curso", "nps"]
    assert len(result.rows) == 2
    assert result.rows[0]["curso"] == "MBA"
    assert "MBA" in result.content_text
    assert len(result.content_hash) == 64


def test_parse_xlsx(tmp_path):
    xlsx_path = tmp_path / "nps.xlsx"
    pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}).to_excel(xlsx_path, index=False)
    result = parse_export(xlsx_path)
    assert result.columns == ["a", "b"]
    assert len(result.rows) == 2
