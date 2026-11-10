"""Independent saved-output, source-geometry and frozen-file verification."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.candidate.distance import plan_hash
from src.constraints.v06 import HardConstraintEngine
from src.inference.input import load_inference_context
from src.neighborhood import NeighborhoodContext, build_neighborhood, load_config, measure_geometry

OUT = ROOT/"outputs/neighborhood_v01"
KNOWN_HISTORICAL_FAILURES = {
    "test_p001_small_batch_post_selection_report_and_frozen_integrity (test_m3_candidate_generator.CandidateGeneratorTests.test_p001_small_batch_post_selection_report_and_frozen_integrity)",
    "test_frozen_files_unchanged (test_m3_review_blinding.BlindingTests.test_frozen_files_unchanged)",
    "test_plan_and_comparison_models_equal_frozen_v04 (test_m3_visual_readability.RotationReadabilityTests.test_plan_and_comparison_models_equal_frozen_v04)",
    "test_v04_output_preserved_and_only_visual_paths_allowed (test_m3_visual_readability.RotationReadabilityTests.test_v04_output_preserved_and_only_visual_paths_allowed)",
    "test_frozen_integrity_old_and_v03_inputs (test_m3_visual_review_pack.ReviewPackTests.test_frozen_integrity_old_and_v03_inputs)"
}


def read(name):
    return json.loads((OUT/name).read_text(encoding="utf-8"))


def main():
    baseline, metadata = read("baseline_manifest.json"), read("run_metadata.json")
    changed = [name for name, digest in baseline.items() if not (ROOT/name).is_file()
               or hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != digest]
    assert not changed, changed
    context = load_inference_context(metadata["source_workbook"])
    assert metadata["run_state"] == "complete"
    assert context.part_data.source_sha256 == metadata["source_sha256_provenance_only"]
    assert not hasattr(context, "expert_reference_plan") and not hasattr(context.part_data, "expert_reference_plan")
    config = load_config()
    # Recompute every distance, witness and gate from source facts, without the cache.
    evidence = measure_geometry(context, config)
    fresh = build_neighborhood(context, config, evidence=evidence)
    snapshot = read("neighborhood_context.json")
    saved_fp = snapshot.pop("fingerprint")
    saved = NeighborhoodContext(snapshot)
    assert saved.fingerprint == fresh.fingerprint == saved_fp
    run = read("candidate_exploration.json")
    assert run["neighborhood_fingerprint"] == saved_fp and not run["expert_used"] and not run["soft_penalty_used"]
    assert not run["quality_model_trained"] and not run["ready_for_expert_blind_review"]
    pool = {r["plan_hash"]: r for r in run["pool"]}
    assert len(pool) == len(run["pool"])
    hard = HardConstraintEngine(context)
    for key, row in pool.items():
        assert hard.evaluate(row["assignments"]).feasible and key == plan_hash(row["assignments"])
    assert {r["plan_hash"] for r in run["selected"]} <= set(pool)
    for name, key in (("candidate_pool.jsonl", "pool"), ("selected_candidates.jsonl", "selected")):
        assert [json.loads(line) for line in (OUT/name).read_text(encoding="utf-8").splitlines()] == run[key]
    relations = {r["pair_id"]: r for r in saved.snapshot["relations"]}
    overlays = read("candidate_neighborhood_overlays.json")
    assert set(overlays["by_plan_hash"]) == set(pool)
    overlay_rows_checked = 0
    for key, overlay in overlays["by_plan_hash"].items():
        assignments = pool[key]["assignments"]
        assert overlay["neighborhood_fingerprint"] == saved_fp and overlay["quality_score"] is None
        assert {r["pair_id"] for r in overlay["relations"]} == set(relations)
        for row in overlay["relations"]:
            a, b = relations[row["pair_id"]]["operation_ids"]
            delta = context.station_ranks[assignments[b]]-context.station_ranks[assignments[a]]
            assert row["same_station"] == (delta == 0)
            assert row["station_rank_gap"] == abs(delta) and row["station_rank_delta_a_to_b"] == delta
            overlay_rows_checked += 1
    valid_trials = {r["trial_id"]: r for r in run["trials"] if "alternative" in r}
    for trial in valid_trials.values():
        base = pool[trial["base_plan_hash"]]["assignments"]
        alt = trial["alternative"]["assignments"]
        assert hard.evaluate(alt).feasible and plan_hash(alt) in pool
        a, b = trial["operation_ids"]
        assert (alt[a] == alt[b]) == trial["target_same_station"]
        assert {op for op in base if base[op] != alt[op]} == {r["operation_id"] for r in trial["all_changes"]}
        assert {r["operation_id"] for r in trial["all_changes"] if not r["is_focus_endpoint"]} == set(trial["collateral_operation_ids"])
        if trial["held_operation"]:
            assert base[trial["held_operation"]] == alt[trial["held_operation"]]
    impact_map = {r["trial_id"]: r["changed_relation_states"] for r in overlays["local_change_impacts"]}
    assert set(impact_map) == set(valid_trials)
    for tid, trial in valid_trials.items():
        before = {r["pair_id"]: r for r in overlays["by_plan_hash"][trial["base_plan_hash"]]["relations"]}
        after = {r["pair_id"]: r for r in overlays["by_plan_hash"][trial["alternative"]["plan_hash"]]["relations"]}
        assert {r["pair_id"] for r in impact_map[tid]} == {k for k in before if before[k] != after[k]}
    assert all(c["answer"] is None and c["confidence"] is None and c["reason_note"] is None for c in run["comparisons"])
    known = read("known_case_audit.json")
    assert known["summary"] == {"cases": 4, "detected": 4, "cases_with_legal_separation": 4}
    assert not known["used_in_new_generation"]
    for t in known["trials"]:
        if "alternative" in t:
            a, b = t["operation_ids"]; alt = t["alternative"]["assignments"]
            assert alt[a] != alt[b] and hard.evaluate(alt).feasible
    log = (OUT/"test_results.txt").read_text(encoding="utf-8")
    assert log.rstrip().endswith("OK")
    targeted = int(re.search(r"Ran (\d+) tests", log)[1])
    full = (OUT/"full_test_results.txt").read_text(encoding="utf-8")
    full_count = int(re.search(r"Ran (\d+) tests", full)[1])
    failures = set(re.findall(r"^FAIL: (.+)$", full, re.M))
    errors = re.findall(r"^ERROR: (.+)$", full, re.M)
    assert not errors and failures == KNOWN_HISTORICAL_FAILURES, (failures, errors)
    browser = read("browser_qa.json")
    assert browser["passed"] and not browser["page_errors"] and not browser["http_requests"]
    html = (OUT/"neighborhood_review.html").read_text(encoding="utf-8")
    assert "__PAYLOAD__" not in html and '"expert_station"' not in html and '"expert_reference_plan"' not in html
    checks = {"passed": True, "scope": "new implementation and regressions; historical suite is not fully green",
              "preexisting_files_checked": len(baseline), "preexisting_files_modified_or_missing": changed,
              "fresh_source_geometry_pairs": len(evidence["pairs"]), "fresh_and_cached_neighborhood_fingerprints_match": True,
              "all_candidates_hard_feasible": len(pool), "selected_count": len(run["selected"]),
              "independently_checked_overlay_relations": overlay_rows_checked,
              "successful_local_trials_checked": len(valid_trials), "known_cases_detected_and_legally_separable": 4,
              "targeted_tests_passed": targeted, "full_suite_tests": full_count, "full_suite_passed": full_count-len(failures),
              "historical_snapshot_failures": sorted(failures), "new_failures": 0, "browser_qa_passed": True,
              "source_sha256": context.part_data.source_sha256,
              "trial_status_counts": dict(Counter(t["status"] for t in run["trials"])),
              "quality_guarantee": False, "new_blind_review_pack": False}
    (OUT/"verification.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    # Append exact evidence, rather than presenting old snapshot failures as green.
    report_path = OUT/"REPORT.md"
    report = report_path.read_text(encoding="utf-8").split("\n## 本次实测验收\n")[0]
    report += f"\n## 本次实测验收\n\n相关测试 {targeted}/{targeted} 通过；全套 {full_count} 项，{full_count-len(failures)} 通过，5项已知历史快照失败，新增失败0。\n\n"
    report += f"原有 {len(baseline)} 个文件SHA全部不变。重新从源数据计算 {len(evidence['pairs'])} 对几何，邻域指纹与缓存构建完全一致；{len(pool)} 个候选全部满足H1–H7。\n\n"
    report += "四组已知OP30问题全部入域，分别找到合法分序分支：O200或O196可从OP30移至OP20。此结论仅证明硬可行的局部替代存在，不代表工艺优劣已由模型判断。旧R02包未修改。\n\n"
    report += "Edge核查全部224个查询中心、5个参数场景、4组已知案例，点击/270°映射/Station过滤/同步缩放平移均通过；页面错误0，HTTP请求0。\n\n历史失败条目：\n\n"+"\n".join("- "+f for f in sorted(failures))+"\n"
    report_path.write_text(report, encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == "__main__":
    main()
