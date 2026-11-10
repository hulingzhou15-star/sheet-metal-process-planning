"""Check saved workbook, source preservation and analytic distance tests."""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import zipfile

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/relative_proximity_audit"


def load(name):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def main():
    matrix = load("distance_matrix.json")
    report = load("analysis_report.json")
    ranked = load("ranked_neighbors.json")
    catalog = load("operation_catalogue.json")
    baseline = load("baseline_manifest.json")
    changes = [name for name, digest in baseline.items()
               if not (ROOT / name).is_file() or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
    assert not changes, changes
    with zipfile.ZipFile(OUT / "P001_complete_distances.xlsx") as archive:
        assert archive.testzip() is None
    wb = openpyxl.load_workbook(OUT / "P001_complete_distances.xlsx", read_only=True, data_only=True)
    try:
        ids = matrix["operation_ids"]
        data = list(wb["距离矩阵"].values)
        assert list(data[0][1:]) == ids
        assert [row[0] for row in data[1:]] == ids
        checked = 0
        for i, row in enumerate(data[1:]):
            for j, value in enumerate(row[1:]):
                expected = matrix["distances"][i][j]
                assert value == "n.a." if expected is None else isinstance(value, (float, int)) and math.isclose(value, expected, rel_tol=1e-12, abs_tol=1e-9)
                checked += 1
        displayed = [r for r in ranked if r["rank"] <= report["config"]["example_rank_count"]]
        saved_rows = list(wb["邻居前12"].values)[1:]
        assert len(saved_rows) == len(displayed)
        for saved, expected in zip(saved_rows, displayed):
            assert list(saved[:5]) == [expected[k] for k in ("operation_id", "operation_type", "rank", "neighbor_id", "neighbor_type")]
            assert math.isclose(saved[5], expected["distance"], abs_tol=1e-9)
            if expected["next_ratio"] is None:
                assert saved[7] in (None, "")
            else:
                assert isinstance(saved[7], (float, int)) and math.isclose(saved[7], expected["next_ratio"], rel_tol=1e-12)
            assert saved[9] == expected["neighbor_feature_ref"]
        assert len(list(wb["断层候选"].values)) == len(load("threshold_candidates.json")) + 1
        assert len(list(wb["动作目录"].values)) == len(catalog) + 1
        errors = []
        for sheet in wb:
            for row in sheet.values:
                for v in row:
                    if isinstance(v, str) and v in {"#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!", "#SPILL!", "#CALC!"}:
                        errors.append((sheet.title, v))
        assert not errors, errors
    finally:
        wb.close()
    test = subprocess.run([sys.executable, "-B", "-m", "unittest", "tests.test_relative_proximity_audit", "tests.test_geometry_distance"], cwd=ROOT, capture_output=True, text=True)
    (OUT / "test_results.txt").write_text(test.stdout + test.stderr, encoding="utf-8")
    assert test.returncode == 0, test.stderr
    result = {"passed": True, "existing_files_checked": len(baseline), "existing_files_modified_or_missing": changes,
              "matrix_cells_checked": checked, "ranked_excel_rows_checked": len(displayed),
              "all_directed_rank_rows": len(ranked), "matrix_includes_all_operations": True,
              "all_ordered_neighbors_in_csv_json": True, "excel_rank_view_limit": report["config"]["example_rank_count"],
              "xlsx_formula_errors": errors, "new_and_geometry_tests_passed": True,
              "source_workbook_sha256": report["source_sha256"],
              "workbook_sha256": hashlib.sha256((OUT / "P001_complete_distances.xlsx").read_bytes()).hexdigest()}
    (OUT / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
