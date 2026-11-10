"""Independent source-curve, frozen-file and saved-artifact verification."""
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.graph.geometry_distance import (primitives_distance, point_line_distance, point_arc_distance)
from src.graph.geometry_primitives import Line, geometry_primitives
from src.inference.input import load_inference_context

OUT = ROOT / "outputs/proximity_geometry_review"


def main():
    data = json.loads((OUT / "geometry_evidence.json").read_text(encoding="utf-8"))
    baseline = json.loads((OUT / "baseline_manifest.json").read_text(encoding="utf-8"))
    changed = [name for name, digest in baseline.items() if not (ROOT / name).is_file()
               or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
    assert not changed, changed
    context = load_inference_context(data["source"]["workbook"])
    assert context.part_data.source_sha256 == data["source"]["source_sha256"]
    assert context.facts_fingerprint == data["source"]["facts_fingerprint"]
    shapes = {op: geometry_primitives(g) for op, o in context.operations.items() if (g := context.part_data.geometry_for(o)) is not None}
    tolerance = data["config"]["numeric_witness_tolerance"]
    max_distance_residual = max_membership_residual = 0
    pair_keys, projections, profiles = set(), 0, 0
    for row in data["pairs"]:
        a, b = row["a"], row["b"]
        assert a < b and (a, b) not in pair_keys
        pair_keys.add((a, b))
        actual = primitives_distance(shapes[a], shapes[b])
        residual = abs(actual - row["boundary_distance"])
        max_distance_residual = max(max_distance_residual, residual)
        assert residual <= tolerance
        witness = row["boundary_witness"]
        assert abs(math.dist(witness["point_a"], witness["point_b"]) - actual) <= tolerance
        for side, op in (("a", a), ("b", b)):
            p = shapes[op][witness[f"primitive_index_{side}"]]
            point = witness[f"point_{side}"]
            distance = point_line_distance(point, p) if isinstance(p, Line) else point_arc_distance(point, p)
            assert distance <= tolerance, (row["pair_id"], side, distance)
            max_membership_residual = max(max_membership_residual, distance)
        for gate in row["gates"]:
            if gate["metric"] == "CENTER_TO_FINITE_BEND":
                bend = b if gate["scale_operation"] == a else a
                actual_center_distance = point_line_distance(gate["center"], shapes[bend][0])
                assert abs(actual_center_distance - gate["measurement"]) <= tolerance
                projections += 1
        for side in ("a", "b"):
            profile = row.get(f"profile_{side}_to_{'b' if side == 'a' else 'a'}")
            if profile:
                assert actual - tolerance <= profile["sampled_min"] <= profile["q10"] <= profile["q50"] <= profile["q90"] <= profile["sampled_max"]
                profiles += 1
    assert len(pair_keys) == len(shapes)*(len(shapes)-1)//2
    assert len(data["operations"]) == len(context.operations)
    assert not data["production_integration"] and not data["all_operations_have_final_neighbor_policy"]
    test_log = (OUT / "test_results.txt").read_text(encoding="utf-8")
    match = re.search(r"Ran (\d+) tests", test_log)
    assert match and test_log.rstrip().endswith("OK")
    browser = json.loads((OUT / "browser_qa.json").read_text(encoding="utf-8"))
    assert browser["passed"] and browser["page_errors"] == [] and browser["http_requests"] == []
    html = (OUT / "geometry_review.html").read_text(encoding="utf-8")
    assert "__PAYLOAD__" not in html and '"assigned_station"' not in html and '"expert_station"' not in html
    verification = {"passed": True, "preexisting_files_checked": len(baseline), "preexisting_files_modified_or_missing": changed,
                    "all_unordered_pairs_recomputed_from_source": len(pair_keys), "closest_points_checked": len(pair_keys)*2,
                    "max_authoritative_distance_residual": max_distance_residual,
                    "max_point_on_source_curve_residual": max_membership_residual,
                    "finite_bend_center_projections_checked": projections, "diagnostic_profiles_checked": profiles,
                    "targeted_tests_passed": int(match.group(1)), "browser_qa_passed": browser["passed"],
                    "source_sha256": context.part_data.source_sha256,
                    "production_integration": False, "all_operations_have_final_neighbor_policy": False}
    (OUT / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(verification, ensure_ascii=False))


if __name__ == "__main__":
    main()
