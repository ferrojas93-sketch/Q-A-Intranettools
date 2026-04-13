"""Parser for Power BI "DSR" (Data Shape Result) responses.

The QueryExecutionService returns JSON that looks like::

    {
      "results": [{
        "result": {
          "data": {
            "descriptor": {"Select": [{"Name": "datos.Asignatura"}, ...]},
            "dsr": {
              "DS": [{
                "PH": [{
                  "DM0": [
                    {"S": [{"N": "G0", "T": 1, "DN": "D0"}, ...], "C": [0, 0, ...]},
                    {"C": [1, 1, 1, 1, 1]},
                    {"C": [3, 3, 3], "R": 20},        # repeat bitmask
                    {"C": [65, "Un gran prof...", 11, 74, 9]},
                    ...
                  ],
                  "ValueDicts": {"D0": [...], "D1": [...]}  # sometimes present
                }]
              }]
            }
          }
        }
      }]
    }

Each row ``{"C": [...]}`` contains values for the columns. Values can be:

* an integer — index into the per-column dictionary (``ValueDicts[DN]`` or the
  inline-built one).
* a literal string/number — the value itself, and it is also appended to the
  column dictionary for later reuse.

Two optional fields compress rows further:

* ``R`` (int bitmask) — a set bit at position ``i`` means column ``i`` is
  *not* in ``C``; its value is copied from the previous row. That also means
  ``len(C)`` equals the number of 0 bits in ``R`` below the total column count.
* ``Ø`` (bitmask) — set bit at position ``i`` means that column is null.

This parser is defensive: unknown structures yield ``None`` cells rather than
raising, and a warning list captures anomalies for later inspection.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class ParsedTable:
    columns: list[str]
    rows: list[list[Any]]
    warnings: list[str] = field(default_factory=list)

    def to_records(self) -> list[dict]:
        return [dict(zip(self.columns, r)) for r in self.rows]


def parse_file(path: Path) -> list[ParsedTable]:
    with path.open("r", encoding="utf-8") as f:
        obj = json.load(f)
    return parse_qes_json(obj)


def parse_qes_json(obj: dict) -> list[ParsedTable]:
    tables: list[ParsedTable] = []
    for result in obj.get("results", []) or []:
        data = (result.get("result") or {}).get("data") or {}
        if not data:
            continue
        parsed = _parse_data(data)
        if parsed is not None:
            tables.append(parsed)
    return tables


def _column_names(descriptor: dict) -> list[str]:
    names = []
    for item in descriptor.get("Select", []) or []:
        name = item.get("Name")
        if not name:
            name = (item.get("Value") or f"col{len(names)}")
        names.append(name)
    return names


def _parse_data(data: dict) -> Optional[ParsedTable]:
    descriptor = data.get("descriptor", {}) or {}
    dsr = data.get("dsr", {}) or {}
    columns = _column_names(descriptor)
    if not columns:
        return None

    ds_list = dsr.get("DS") or []
    if not ds_list:
        return None

    warnings: list[str] = []
    rows: list[list[Any]] = []

    # A DS may contain several PH (primary holders); iterate all.
    for ds in ds_list:
        ph_list = ds.get("PH") or []
        # Dictionaries declared up-front, if any.
        explicit_dicts: dict[str, list[Any]] = {}
        for dictname, values in (ds.get("ValueDicts") or {}).items():
            if isinstance(values, list):
                explicit_dicts[dictname] = list(values)

        # Mapping from column index → dictionary name (from S entries).
        col_to_dict: dict[int, str] = {}

        # Per-column accumulated dictionaries (grow as literal values appear).
        acc_dicts: dict[str, list[Any]] = {}

        for ph in ph_list:
            ph_dicts = ph.get("ValueDicts") or {}
            for dictname, values in ph_dicts.items():
                if isinstance(values, list):
                    explicit_dicts[dictname] = list(values)

            previous_row: Optional[list[Any]] = None
            dm_entries = ph.get("DM0") or []

            for entry in dm_entries:
                # Schema: maps column index → dict name.
                if "S" in entry:
                    for idx, sitem in enumerate(entry["S"] or []):
                        dn = sitem.get("DN")
                        if dn:
                            col_to_dict[idx] = dn
                            acc_dicts.setdefault(dn, [])

                c_vals = entry.get("C") or []
                repeat_mask = int(entry.get("R") or 0)
                null_mask = int(entry.get("Ø") or 0)

                row: list[Any] = [None] * len(columns)
                c_idx = 0
                for col_idx in range(len(columns)):
                    bit = 1 << col_idx
                    if null_mask & bit:
                        row[col_idx] = None
                        continue
                    if repeat_mask & bit:
                        # Copy from previous row.
                        if previous_row is not None:
                            row[col_idx] = previous_row[col_idx]
                        else:
                            row[col_idx] = None
                            warnings.append(
                                f"R bit set on first row for col {col_idx}"
                            )
                        continue
                    if c_idx >= len(c_vals):
                        # Implicit repeat (short C list).
                        if previous_row is not None:
                            row[col_idx] = previous_row[col_idx]
                        continue
                    val = c_vals[c_idx]
                    c_idx += 1
                    if isinstance(val, bool):
                        # bool is a subclass of int; handle explicitly.
                        row[col_idx] = val
                    elif isinstance(val, int):
                        dn = col_to_dict.get(col_idx)
                        dict_values: list[Any] = []
                        if dn:
                            if dn in explicit_dicts:
                                dict_values = explicit_dicts[dn]
                            elif dn in acc_dicts:
                                dict_values = acc_dicts[dn]
                        if 0 <= val < len(dict_values):
                            row[col_idx] = dict_values[val]
                        else:
                            # Fall back to raw index — likely a numeric measure
                            # rather than a dictionary key.
                            row[col_idx] = val
                    else:
                        # Literal value. Also append to accumulated dict so
                        # later rows can reference it by index.
                        row[col_idx] = val
                        dn = col_to_dict.get(col_idx)
                        if dn is not None:
                            acc_dicts.setdefault(dn, []).append(val)

                previous_row = list(row)
                rows.append(row)

    return ParsedTable(columns=columns, rows=rows, warnings=warnings)


def summarise_directory(bodies_dir: Path) -> list[dict]:
    """Walk *.json files in bodies_dir that look like QES responses and return
    a list of dicts with basic info for each table found.
    """
    out: list[dict] = []
    for path in sorted(bodies_dir.glob("*.json")):
        if path.name == "_index.json":
            continue
        try:
            tables = parse_file(path)
        except Exception as exc:  # noqa: BLE001
            out.append({"file": path.name, "error": str(exc)})
            continue
        for i, t in enumerate(tables):
            out.append({
                "file": path.name,
                "result_index": i,
                "columns": t.columns,
                "row_count": len(t.rows),
                "warnings": len(t.warnings),
                "sample": t.rows[:2],
            })
    return out
