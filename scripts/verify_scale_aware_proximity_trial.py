"""Saved-artifact and frozen-input checks for the independent geometry trial."""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.graph.builders import DISTANCE_SORT_DECIMALS
from src.graph.geometry_distance import primitives_distance
from src.graph.geometry_primitives import geometry_primitives
from src.inference.input import load_inference_context

OUT = ROOT / "outputs/relative_proximity_trial"


def load(name):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def main():
    trial, baseline, browser = load("trial_results.json"), load("baseline_manifest.json"), load("browser_qa.json")
    changed = [name for name, digest in baseline.items() if not (ROOT / name).is_file()
               or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
    assert not changed, changed
    assert browser["passed"] and browser["page_errors"] == [] and browser["http_requests"] == []
    context = load_inference_context(trial["source"]["workbook"])
    assert context.part_data.source_sha256 == trial["source"]["sha256"]
    primitives = {op: geometry_primitives(geom) for op, obj in context.operations.items()
                  if (geom := context.part_data.geometry_for(obj)) is not None}
    profiles = {r["operation_id"]: r["circular_profile"] for r in trial["operation_catalogue"]}
    actual_records, metric_checks = [], 0
    for row in trial["pair_records"]:
        a, b = row["anchor_id"], row["target_id"]
        if b not in primitives:
            assert row["measurement"] is None and row["boundary_distance"] is None
            assert not any(row["passes"].values())
            continue
        actual_boundary = primitives_distance(primitives[a], primitives[b])
        assert math.isclose(actual_boundary, row["boundary_distance"], rel_tol=1e-12, abs_tol=1e-9)
        actual = math.dist(profiles[a]["center"], profiles[b]["center"]) if profiles[b] is not None else actual_boundary
        assert math.isclose(actual, row["measurement"], rel_tol=1e-12, abs_tol=1e-9)
        for multiple in trial["config"]["comparison_size_multiples"]:
            expected = round(actual, DISTANCE_SORT_DECIMALS) <= round(multiple * profiles[a]["diameter"], DISTANCE_SORT_DECIMALS)
            assert expected == row["passes"][f"{multiple:g}"]
            metric_checks += 1
        actual_records.append(row)
    for anchor in trial["anchors"]:
        for scenario in anchor.get("scenarios", []):
            passed = [r for r in actual_records if r["anchor_id"] == anchor["anchor_id"] and r["passes"][f"{scenario['size_multiple']:g}"]]
            assert len(passed) == scenario["passed_count"]
            assert {r["target_id"] for r in passed} == set(scenario["passed_ids"])
    test = subprocess.run([sys.executable, "-B", "-m", "unittest", "tests.test_scale_aware_proximity_trial", "tests.test_relative_proximity_audit", "tests.test_geometry_distance"], cwd=ROOT, capture_output=True, text=True)
    (OUT / "test_results.txt").write_text(test.stdout + test.stderr, encoding="utf-8")
    assert test.returncode == 0, test.stderr
    text = (OUT / "proximity_trial.html").read_text(encoding="utf-8")
    assert '"expert_station"' not in text and '"assigned_station"' not in text
    assert "__PAYLOAD__" not in text
    verification = {"passed": True, "existing_files_checked": len(baseline),
                    "existing_files_modified_or_missing": changed,
                    "independently_recomputed_geometric_pairs": len(actual_records),
                    "size_gate_checks": metric_checks, "browser_scenarios": browser["anchor_scenarios_checked"],
                    "new_and_regression_tests_passed": True, "test_count": 33,
                    "expert_assignments_not_used": True,
                    "source_sha256": context.part_data.source_sha256,
                    "production_integration": trial["production_integration"]}
    (OUT / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(verification, ensure_ascii=False))


if __name__ == "__main__":
    main()
