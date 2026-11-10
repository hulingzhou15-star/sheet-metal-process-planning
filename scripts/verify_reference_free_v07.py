"""Independent emitted-artifact verification, regression results and preservation."""
from dataclasses import replace
import hashlib
from io import StringIO
from itertools import combinations
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.inference import load_inference_context, generate_candidates, select_diverse
from src.inference.generation import load_config
from src.constraints.v06 import HardConstraintEngine
from src.candidate.distance import PlanDistance, plan_hash
from src.graph.builders import build_part_graph
from src.graph.plan_overlay import build_plan_overlay


def save(folder, name, value):
    (folder/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def main():
    folder = ROOT/"outputs/m3_reference_free_v07"
    context = load_inference_context(next((ROOT/"data/parts/P001").glob("*P001.xlsx")))
    hard = HardConstraintEngine(context)
    pool = [json.loads(line) for line in (folder/"candidate_pool.jsonl").read_text(encoding="utf-8").splitlines()]
    selected = [json.loads(line) for line in (folder/"selected_candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    contrasts = json.loads((folder/"local_contrasts.json").read_text(encoding="utf-8"))
    facts = {"pool_unique": len({r["plan_hash"] for r in pool}) == len(pool),
             "pool_hard_feasible": all(hard.evaluate(r["assignments"]).feasible for r in pool),
             "pool_hashes_valid": all(plan_hash(r["assignments"]) == r["plan_hash"] for r in pool),
             "selection_is_subset": {r["plan_hash"] for r in selected} <= {r["plan_hash"] for r in pool}}
    local_checks = []
    static = build_part_graph(context.part_data)
    before = static.fingerprint()
    edge_pairs = {(e.source_index, e.target_index) for e in static.geometric_edges}
    for row in contrasts["comparisons"]:
        a, b = row["focus_operation_ids"]
        same = row["same_station_plan"]["assignments"]
        different = row["different_station_plan"]["assignments"]
        actual_changes = sorted(op for op in same if same[op] != different[op])
        i, j = static.node_index[a], static.node_index[b]
        local_checks.append({"contrast_id": row["contrast_id"],
                             "both_hard_feasible": hard.evaluate(same).feasible and hard.evaluate(different).feasible,
                             "same_role_correct": same[a] == same[b],
                             "different_role_correct": different[a] != different[b],
                             "all_changes_disclosed": actual_changes == sorted(r["operation_id"] for r in row["all_changes"]),
                             "both_hashes_valid": all(plan_hash(row[k]["assignments"]) == row[k]["plan_hash"] for k in ("same_station_plan", "different_station_plan")),
                             "unlabeled": row["answer"] is None and row["reason_note"] is None,
                             "graph_edge_present": (i, j) in edge_pairs,
                             "overlay_same_correct": build_plan_overlay(static, same).edge_features(i, j).same_station == 1,
                             "overlay_different_correct": build_plan_overlay(static, different).edge_features(i, j).same_station == 0})
    facts["all_local_checks_passed"] = all(all(v is True for k, v in row.items() if k != "contrast_id") for row in local_checks)
    facts["static_graph_unchanged_after_overlays"] = before == static.fingerprint()
    # Repeat the full batch; raw file SHA and evaluation material are absent from decisions.
    repeated, _ = generate_candidates(context, load_config())
    repeat_selected, _ = select_diverse(context, repeated, len(selected))
    facts["full_pool_reproducible"] = [r["plan_hash"] for r in repeated] == [r["plan_hash"] for r in pool]
    facts["full_selection_reproducible"] = [r["plan_hash"] for r in repeat_selected] == [r["plan_hash"] for r in selected]
    distance = PlanDistance.from_context(context)
    gaps = [distance.raw(a["assignments"], b["assignments"]) for a, b in combinations(selected, 2)]
    save(folder, "artifact_verification.json", {"passed": all(facts.values()), "checks": facts, "local_checks": local_checks,
                                               "selected_pairwise_raw_distance": {"min": min(gaps, default=0), "max": max(gaps, default=0),
                                                                                  "mean": sum(gaps)/len(gaps) if gaps else 0},
                                               "movable_operations": len(distance.movable_operation_ids)})
    # Keep historical failing assertions intact and retain full tracebacks.
    stream = StringIO()
    suite = unittest.defaultTestLoader.discover(str(ROOT/"tests"))
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    (folder/"test_results.txt").write_text(stream.getvalue(), encoding="utf-8")
    tests = {"run": result.testsRun, "passed": result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),
             "failures": [{"test": test.id(), "traceback": trace} for test, trace in result.failures],
             "errors": [{"test": test.id(), "traceback": trace} for test, trace in result.errors],
             "skipped": len(result.skipped), "fully_green": result.wasSuccessful()}
    known_failures = {
        "test_m3_candidate_generator.CandidateGeneratorTests.test_p001_small_batch_post_selection_report_and_frozen_integrity",
        "test_m3_review_blinding.BlindingTests.test_frozen_files_unchanged",
        "test_m3_visual_readability.RotationReadabilityTests.test_plan_and_comparison_models_equal_frozen_v04",
        "test_m3_visual_readability.RotationReadabilityTests.test_v04_output_preserved_and_only_visual_paths_allowed",
        "test_m3_visual_review_pack.ReviewPackTests.test_frozen_integrity_old_and_v03_inputs"}
    tests["unexpected_failure_ids"] = sorted({test.id() for test, _ in result.failures}-known_failures)
    save(folder, "test_results.json", tests)
    print(json.dumps({k: v if k not in {"failures", "errors"} else [r["test"] for r in v] for k, v in tests.items()}), flush=True)
    manifest = json.loads((folder/"baseline_manifest.json").read_text(encoding="utf-8"))
    changed = []
    for index, (name, digest) in enumerate(manifest.items()):
        path = ROOT/name
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        if digest != actual:
            changed.append({"path": name, "before": digest, "after": actual})
        if index and index % 5000 == 0:
            print(json.dumps({"stage": "frozen_integrity", "checked": index, "total": len(manifest)}), flush=True)
    save(folder, "frozen_integrity.json", {"checked_existing_files": len(manifest), "all_unchanged": not changed, "changed": changed})
    print(json.dumps({"artifact_checks_passed": all(facts.values()), "existing_files_unchanged": not changed}), flush=True)
    return 0 if all(facts.values()) and not changed and not result.errors and not tests["unexpected_failure_ids"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
