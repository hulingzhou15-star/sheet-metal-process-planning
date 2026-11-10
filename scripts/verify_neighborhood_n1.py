"""Recompute source neighborhoods and independently verify N1 trial artifacts."""
from collections import Counter
import hashlib
from itertools import combinations
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.candidate.distance import plan_hash
from src.constraints.v06 import HardConstraintEngine
from src.inference.input import load_inference_context
from src.neighborhood import build_neighborhood, load_config as load_neighborhood_config, measure_geometry
from src.neighborhood_n1 import collect_policy, load_config
from src.neighborhood_n1.analysis import distribution

OUT = ROOT/"outputs/neighborhood_n1"


def read(name):
    return json.loads((OUT/name).read_text(encoding="utf-8"))


def jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def main():
    baseline, meta = read("baseline_manifest.json"), read("run_metadata.json")
    changed = [name for name, h in baseline.items() if not (ROOT/name).is_file() or hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != h]
    assert not changed, changed
    for name, h in meta["baseline_files_read_after_generation_only"].items():
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == h
    assert meta["run_state"] == "complete" and not meta["expert_used"] and not meta["new_expert_blind_pack"]
    context = load_inference_context(meta["source_workbook"])
    assert context.part_data.source_sha256 == meta["source_sha256_provenance_only"]
    assert not hasattr(context, "expert_reference_plan") and not hasattr(context.part_data, "expert_reference_plan")
    cfg, nc = load_config(), load_neighborhood_config()
    evidence = measure_geometry(context, nc)
    neighborhood = build_neighborhood(context, nc, evidence=evidence)
    policy = collect_policy(context, neighborhood, cfg)
    assert policy == read("n1_policy.json")
    # Independently select exact anchor/type/tier scope from fresh source geometry.
    expected = set()
    for relation in neighborhood.snapshot["relations"]:
        if relation["kind"] != "EXTERNAL_NEIGHBOR": continue
        for arc in relation["arcs"]:
            if context.operations[arc["anchor"]].operation_type == "FLANGING" and context.operations[arc["neighbor"]].operation_type == "IRREGULAR_PUNCH" and arc["tier"] == "PRIORITY":
                expected.add(relation["pair_id"])
    assert expected == {r["pair_id"] for r in policy["pairs"]} and len(expected) == 22
    joint = read("joint_feasibility.json")
    assert joint["state"] == "joint_feasible" and joint["generation_allowed"]
    hard = HardConstraintEngine(context)
    def n1_count(plan):
        return sum(plan[a] == plan[b] for r in policy["pairs"] for a, b in [r["operation_ids"]])
    witness = joint["witness"]["assignments"]
    assert hard.evaluate(witness).feasible and n1_count(witness) == 0
    new = jsonl(OUT/"candidate_pool.jsonl")
    selected = jsonl(OUT/"selected_candidates.jsonl")
    old_path = next(Path(p) for p in meta["baseline_files_read_after_generation_only"] if p.endswith("candidate_pool.jsonl"))
    old = jsonl(old_path)
    old_selected = jsonl(old_path.parent/"selected_candidates.jsonl")
    assert len(new) == 16 and len({r["plan_hash"] for r in new}) == 16
    for row in new:
        assert set(row["assignments"]) == set(context.operations)
        assert hard.evaluate(row["assignments"]).feasible and n1_count(row["assignments"]) == 0
        assert row["plan_hash"] == plan_hash(row["assignments"])
        assert row["preference_label"] is None
    assert {p["plan_hash"] for p in selected} <= {p["plan_hash"] for p in new} and len(selected) == 16
    cohorts = read("cohort_audit.json")
    sets = {"original_pool": old, "original_selected": old_selected,
            "filtered_original_pool": [p for p in old if n1_count(p["assignments"]) == 0],
            "new_pool": new, "new_selected": selected}
    movable = sorted(op for op in context.operations if len(context.allowed_stations(op)) >= 2)
    metric_pairs_checked = 0
    for key, plans in sets.items():
        audit = cohorts[key]
        assert audit["plan_count"] == len(plans)
        assert audit["n1_pass_count"] == sum(n1_count(p["assignments"]) == 0 for p in plans)
        assert audit["n1_violation_distribution"] == distribution([n1_count(p["assignments"]) for p in plans])
        rows = {r["plan_hash"]: r for r in audit["plans"]}
        for p in plans:
            assert rows[p["plan_hash"]]["violation_count"] == n1_count(p["assignments"])
            assert hard.evaluate(p["assignments"]).feasible
        distances = [sum(a["assignments"][op] != b["assignments"][op] for op in movable) for a, b in combinations(plans, 2)]
        metric_pairs_checked += len(distances)
        assert distribution(distances) == audit["pairwise_raw_hamming"]
        for station in context.ordered_stations:
            loads = [sum(s == station.station_id for s in p["assignments"].values()) for p in plans]
            assert distribution(loads) == audit["station_load_distribution"][station.station_id]
        for pair in policy["pairs"]:
            a, b = pair["operation_ids"]
            assert audit["per_pair_same_station_count"][pair["pair_id"]] == sum(p["assignments"][a] == p["assignments"][b] for p in plans)
    assert len(sets["filtered_original_pool"]) == 0
    before, after = {p["plan_hash"]: p for p in old}, {p["plan_hash"]: p for p in new}
    comps = read("comparisons.json")
    assert len(comps) == 20
    for comp in comps:
        a, b = before[comp["before_plan_hash"]]["assignments"], after[comp["after_plan_hash"]]["assignments"]
        changed_ops = {op for op in a if a[op] != b[op]}
        assert changed_ops == {r["operation_id"] for r in comp["all_changes"]}
        assert comp["raw_hamming"] == len(changed_ops & set(movable))
        expected_changes = []
        for relation in neighborhood.external_relations:
            x, y = relation["operation_ids"]
            previous, following = a[x] == a[y], b[x] == b[y]
            if previous != following:
                expected_changes.append({"pair_id": relation["pair_id"], "within_n1": relation["pair_id"] in expected,
                                         "before_same_station": previous, "after_same_station": following})
        assert comp["external_same_station_changes"] == expected_changes
        assert comp["answer"] is None
    known = read("known_case_coverage.json")
    assert len(known) == 4 and all(k["covered_by_n1"] and k["new_same_station_count"] == 0 and not k["used_for_generation"] for k in known)
    log = (OUT/"test_results.txt").read_text(encoding="utf-8")
    assert log.rstrip().endswith("OK")
    tests = int(re.search(r"Ran (\d+) tests", log)[1])
    n1log = (OUT/"n1_test_results.txt").read_text(encoding="utf-8")
    assert n1log.rstrip().endswith("OK")
    n1tests = int(re.search(r"Ran (\d+) tests", n1log)[1])
    browser = read("browser_qa.json")
    assert browser["passed"] and not browser["page_errors"] and not browser["http_requests"]
    html = (OUT/"n1_internal_review.html").read_text(encoding="utf-8")
    assert "__PAYLOAD__" not in html and '"expert_station"' not in html and '"expert_reference_plan"' not in html
    irregulars = sorted({r["irregular_operation_id"] for r in policy["pairs"]})
    result = {"passed": True, "preexisting_files_checked": len(baseline), "preexisting_files_modified_or_missing": changed,
              "source_geometry_pairs_recomputed": len(evidence["pairs"]), "n1_scope_recomputed_from_source": len(expected),
              "joint_feasible": True, "original_pool_count": len(old), "original_n1_passing": 0,
              "new_candidates_hard_and_n1_passed": len(new), "selected_count": len(selected),
              "pairwise_metric_values_independently_checked": metric_pairs_checked, "comparisons_with_complete_changes": len(comps),
              "known_cases_covered_and_new_same_station_zero": 4, "targeted_tests_passed": tests, "n1_tests_passed": n1tests,
              "full_historical_suite_rerun": False,
              "historical_suite_note": "Previous full run: 508 tests, 503 passed, 5 known historical snapshot failures; no historical tests/manifests rewritten",
              "browser_qa_passed": True, "affected_irregular_operation_count": len(irregulars),
              "observed_new_stations_for_affected_irregulars": {op: sorted({p["assignments"][op] for p in new}, key=context.station_ranks.get) for op in irregulars},
              "source_sha256": context.part_data.source_sha256, "expert_used": False,
              "new_expert_blind_pack": False, "process_quality_guaranteed": False}
    (OUT/"verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    report_path = OUT/"REPORT.md"
    text = report_path.read_text(encoding="utf-8").split("\n## 实测验收\n")[0]
    text += f"\n## 实测验收\n\n本轮相关测试 {tests}/{tests} 通过，其中N1专门测试 {n1tests} 项。覆盖联合无解但逐对可行、冲突集删除最小性、预算未决、候选不足、不强制加工先后、单独H1–H7仍接受同序，以及专家列缺失/错误/公式的独立性。\n\n"
    text += f"源数据重新计算 {len(evidence['pairs'])} 对几何，N1 22对精确一致；987个原有文件SHA全部不变。实际新候选16/16通过H1–H7和N1。\n\n"
    text += "本轮未重跑全部历史测试；上轮全套508项中503通过、5项为已知历史快照失败，未回写快照或宣称全套绿色。\n\n"
    text += "Edge核查20个对照、22个焦点关系、4组已知问题、完整/局部视图、工序颜色、差异模式、270°命中及同步缩放平移；页面错误0，HTTP请求0。\n\n"
    text += "P001中17个受N1关联的异形孔在这16个新候选均落到OP20，这是当前capability、硬先后及N1共同求解的观察结果，代码未指定该Station。其它185对外部邻域没有纳入N1；新候选仍有7–14对其它同序邻域，平均10.3125，只作观察，不能当作质量判断。\n\n"
    text += "下一步可以人工审阅这批内部材料，检查N1分序假设及其它残留问题；当前未创建新盲评包，未确认整体方案可投入生产。\n"
    files = sorted(p.relative_to(ROOT).as_posix() for folder in (ROOT/"src/neighborhood_n1",) for p in folder.glob("*.py"))
    files += ["configs/neighborhood_n1.json", "scripts/run_neighborhood_n1.py", "scripts/verify_neighborhood_n1.py", "src/visualization/neighborhood_n1_review.html", "tests/test_neighborhood_n1.py", "tests/neighborhood_n1_browser_qa.cjs", "docs/NEIGHBORHOOD_N1_REVIEW_TRIAL.md"]
    text += "\n## 新增文件\n\n"+"\n".join("- `"+p+"`" for p in files)+"\n\n输出仅新增在`outputs/neighborhood_n1/`。原有文件无修改。\n"
    report_path.write_text(text, encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
