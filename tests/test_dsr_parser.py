from qa_intranet.dsr_parser import parse_qes_json


def test_minimal_dsr_with_dict_index_and_literals():
    payload = {
        "results": [{
            "result": {
                "data": {
                    "descriptor": {
                        "Select": [
                            {"Name": "datos.Asignatura"},
                            {"Name": "datos.Comentario"},
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
                                        "C": ["Marketing", "Muy buena"],
                                    },
                                    {"C": ["Finanzas", "Excelente"]},
                                    # Row reusing "Marketing" via index, new comment.
                                    {"C": [0, "Regular"]},
                                    # Row with R mask: col 0 repeats from previous.
                                    {"C": ["Buenisima"], "R": 1},
                                ],
                            }],
                        }],
                    },
                },
            },
        }],
    }

    tables = parse_qes_json(payload)
    assert len(tables) == 1
    t = tables[0]
    assert t.columns == ["datos.Asignatura", "datos.Comentario"]
    assert len(t.rows) == 4
    assert t.rows[0] == ["Marketing", "Muy buena"]
    assert t.rows[1] == ["Finanzas", "Excelente"]
    assert t.rows[2] == ["Marketing", "Regular"]
    assert t.rows[3] == ["Marketing", "Buenisima"]


def test_null_mask():
    payload = {
        "results": [{
            "result": {
                "data": {
                    "descriptor": {"Select": [{"Name": "a"}, {"Name": "b"}]},
                    "dsr": {
                        "DS": [{
                            "PH": [{
                                "DM0": [
                                    {
                                        "S": [
                                            {"N": "G0", "T": 1, "DN": "D0"},
                                            {"N": "G1", "T": 1, "DN": "D1"},
                                        ],
                                        "C": ["x", "y"],
                                    },
                                    # col 1 null
                                    {"C": ["z"], "\u00d8": 2},
                                ],
                            }],
                        }],
                    },
                },
            },
        }],
    }
    tables = parse_qes_json(payload)
    assert tables[0].rows == [["x", "y"], ["z", None]]


def test_explicit_value_dicts():
    payload = {
        "results": [{
            "result": {
                "data": {
                    "descriptor": {"Select": [{"Name": "curso"}]},
                    "dsr": {
                        "DS": [{
                            "ValueDicts": {"D0": ["MBA", "Master Marketing", "Grado"]},
                            "PH": [{
                                "DM0": [
                                    {
                                        "S": [{"N": "G0", "T": 1, "DN": "D0"}],
                                        "C": [0],
                                    },
                                    {"C": [2]},
                                    {"C": [1]},
                                ],
                            }],
                        }],
                    },
                },
            },
        }],
    }
    tables = parse_qes_json(payload)
    assert tables[0].rows == [["MBA"], ["Grado"], ["Master Marketing"]]
