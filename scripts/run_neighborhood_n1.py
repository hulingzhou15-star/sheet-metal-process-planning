"""N1 trial: baseline audit, joint feasibility, from-scratch generation, internal review."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.candidate.distance import plan_hash
from src.inference.input import load_inference_context
from src.inference.generation import select_diverse
from src.neighborhood import build_neighborhood, load_config as load_neighborhood_config, measure_geometry
from src.neighborhood_n1 import (audit_cohort, collect_policy, compare_candidates,
                                 generate_candidates, joint_feasibility, load_config)
from src.neighborhood_n1.analysis import known_case_comparisons
from src.visualization.config import load_visualization_config
from src.visualization.geometry_adapter import primitive_shape
from src.visualization.outline_adapter import load_display_outline
from src.visualization.svg_renderer import shape_markup, outline_markup
from src.graph.geometry_primitives import geometry_primitives
from scripts.run_neighborhood_v01 import write_json


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def baseline_inputs(folder, facts_fingerprint):
    if folder is None:
        return [], [], [], {}
    meta = json.loads((folder/"run_metadata.json").read_text(encoding="utf-8"))
    if meta["facts_fingerprint"] != facts_fingerprint:
        raise ValueError("Baseline candidates are from different facts")
    paths = [folder/"candidate_pool.jsonl", folder/"selected_candidates.jsonl"]
    pool, selected = (read_jsonl(p) for p in paths)
    known_path = folder/"known_case_audit.json"
    known = json.loads(known_path.read_text(encoding="utf-8"))["cases"] if known_path.exists() else []
    if known_path.exists():
        paths.append(known_path)
    for rows in (pool, selected):
        if len({r["plan_hash"] for r in rows}) != len(rows) or any(plan_hash(r["assignments"]) != r["plan_hash"] for r in rows):
            raise ValueError("Invalid/duplicate baseline plan hash")
    if not {r["plan_hash"] for r in selected} <= {r["plan_hash"] for r in pool}:
        raise ValueError("Baseline selected candidates not in baseline pool")
    return pool, selected, known, {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def write_csv(path, rows, fields):
    if path.exists() and (path.is_symlink() or path.stat().st_nlink > 1):
        raise ValueError("Refuse aliased CSV")
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(row.get(k), ensure_ascii=False) if isinstance(row.get(k), (list, dict)) else row.get(k) for k in fields})


def build_page(context, neighborhood, policy, cohorts, baseline, new, comparisons, joint, config, output):
    hit_shapes = {op: [primitive_shape(p) for p in geometry_primitives(g)]
                  for op, o in context.operations.items() if (g := context.part_data.geometry_for(o)) is not None}
    shapes = {op: "".join(shape_markup(s) for s in rows) for op, rows in hit_shapes.items()}
    visual = load_visualization_config()
    part_id = context.part_data.operations[0].part_id if context.part_data.operations else ""
    outline, audit = load_display_outline(visual, ROOT, part_id)
    baseline_audits = {r["plan_hash"]: r for r in cohorts["original_pool"]["plans"]}
    new_audits = {r["plan_hash"]: r for r in cohorts["new_pool"]["plans"]}
    payload = {"policy": policy, "joint": joint, "view": config["view"], "comparisons": comparisons,
               "operations": neighborhood.snapshot["operations"], "geometries": shapes, "hit_shapes": hit_shapes,
               "outline_markup": outline_markup({"display_outline": outline}, visual),
               "before": [{**r, "n1_audit": baseline_audits[r["plan_hash"]]} for r in baseline],
               "after": [{**r, "n1_audit": new_audits[r["plan_hash"]]} for r in new],
               "summary": {k: {a: v for a, v in c.items() if a != "plans"} for k, c in cohorts.items()},
               "stations": [{"station_id": s.station_id, "order": s.order} for s in context.ordered_stations]}
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace("&", "\\u0026")
    template = (ROOT/"src/visualization/neighborhood_n1_review.html").read_text(encoding="utf-8")
    (output/"n1_internal_review.html").write_text(template.replace("__PAYLOAD__", encoded), encoding="utf-8")
    return audit


def write_report(out, context, policy, joint, run, cohorts, known, config):
    lines = ["# N1 送审筛选策略试验", "", "## 结论", "",
             f"联合状态：{joint['state']}；N1适用关系 {len(policy['pairs'])} 对。",
             f"新候选生成状态：{run['state']}，实际 {run['actual_pool']} 个，目标 {config['generation']['pool_target']} 个。",
             "N1仅是本轮独立送审试验策略；H1–H7、P1–P3、Graph0/1/2及历史材料不变。不是H8、不是SoftPenalty，不制造偏好标签。", "",
             "## 固定范围", "",
             "仅FLANGING为anchor、IRREGULAR_PUNCH为neighbor、PRIORITY层、EXTERNAL_NEIGHBOR的关系。",
             "候选要求两者不同Station，不指定顺序或目标Station。反向IRREGULAR优先、FLANGING扩展不能触发；自身预孔、扩展层及其它组合均不参与。",
             "范围来自冻结的本轮主场景邻域（3倍记录孔径、同family/metric内相对3倍断层分层）。无孔径组合的10源单位不进入N1；它们只作为其它邻域观察。",
             "完整适用/排除理由、距离、尺度、层级及事实指纹见n1_policy.json和n1_pair_catalogue.csv。", "",
             "## 候选筛选与生成对照", "", "| 样本组 | 方案数 | H1–H7通过 | N1通过 | N1问题数 min/mean/max |", "|---|---:|---:|---:|---|"]
    for key, group in cohorts.items():
        d = group["n1_violation_distribution"]
        lines.append(f"| {key} | {group['plan_count']} | {group['hard_pass_count']} | {group['n1_pass_count']} | {d['min']} / {d['mean']} / {d['max']} |")
    lines += ["", "旧候选只读筛选，没有修补或回写原分配。新候选从零件事实和H1–H7＋全部N1条件直接求解，不用旧候选或联合可行见证warm start，不使用专家方案。",
              "采用固定预算、单worker、派生种子和随机分配目标；每个结果独立复核H1–H7和N1，no-good去重，再用farthest-first选取。",
              "同序问题数仅审计，不作为SoftPenalty或质量分；新方案仍需人工判断其它未建模工艺。", "",
              "## 多样性与Station分布", "", "| 样本组 | 方案对数 | Hamming min/mean/max |", "|---|---:|---|"]
    for key, group in cohorts.items():
        d = group["pairwise_raw_hamming"]
        lines.append(f"| {key} | {d['count']} | {d['min']} / {d['mean']} / {d['max']} |")
    lines += ["", "Hamming沿用冻结的基于合法Station选择的movable集合。原selected与新selected为等数量描述性对照；原75个pool与新pool样本量/生成方法不同，不能据此声称精确的可行域损失或N1的因果效应。",
              "旧池通过率也只是这批样本通过率，不是全部可行空间的保留比例。完整Station及OperationType负载分布、每Operation实际使用的Station见cohort_audit.json及station_load_statistics.csv。", "",
              "## 已知问题覆盖", ""]
    for row in known:
        lines.append(f"- {row['pair_id']}：N1覆盖={row['covered_by_n1']}；原pool同序={row['original_same_station_count']}；新pool同序={row['new_same_station_count']}。")
    lines += ["", "这些已知案例只做离线验收，不决定生成策略/种子，不据此新增标签。原reference、专家答案未进入生成流程。", "",
              "## 其它邻域变化与内部对照", "",
              "comparisons.json及comparison_changes.csv列出每个新selected与最近原selected的全部分配差异，另为已知案例从原pool选取确实同序的方案作专门对照。它们是生成后的展示配对，通常涉及多个Operation，不能当成只改变一条关系的因果实验。",
              "other_neighbor_changes.csv逐对列出新增/消除的其它同序邻域，仅观察，不自动判好坏。n1_internal_review.html支持完整零件/局部、Station、差异模式与点击几何；左右保持同一270°映射及同步视角。",
              "当前只交付内部验收材料；不创建新专家盲评包、不发布、不发送给专家。旧R02包继续暂停。", "",
              "## 求解与停止条件", "",
              "joint_feasibility.json保存独立H1–H7基线、全部N1联合可行状态及见证。若INFEASIBLE，提取N1 assumption冲突集并按预算做删除检验；删除最小不等于最少基数，整个H1–H7模型一直保留。UNKNOWN不当作无解。",
              "未建立联合可行性则停止新候选生成；候选不足时返回实际数量及原因，不放宽N1或硬约束。求解墙钟上限可能影响跨机器边界情况，不把固定seed等同于绝对确定性。", "",
              f"事实指纹：{context.facts_fingerprint}。N1指纹：{policy['fingerprint']}。",
              "测试、源几何重算及原文件SHA校验结果将在verification.json记录。", ""]
    (out/"REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT/"data/parts/P001/钣金加工数据表_P001.xlsx")
    parser.add_argument("--config", type=Path, default=ROOT/"configs/neighborhood_n1.json")
    parser.add_argument("--output", type=Path, default=ROOT/"outputs/neighborhood_n1")
    parser.add_argument("--baseline", type=Path, help="Optional historical cohort for post-generation offline comparison only")
    parser.add_argument("--evidence", type=Path, help="Optional compatible geometry-only cache")
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to((ROOT/"outputs").resolve()) or out == (ROOT/"outputs").resolve():
        raise ValueError("Use a dedicated output folder")
    out.mkdir(parents=True, exist_ok=True)
    owner = out/"run_metadata.json"
    permitted = {"baseline_manifest.json", "baseline_exclusions.json", "build.log", "build_errors.log"}
    if not owner.exists() and any(p.name not in permitted for p in out.iterdir()):
        raise ValueError("Refuse unrelated output directory")
    if owner.exists() and json.loads(owner.read_text(encoding="utf-8"))["schema_version"] != "neighborhood_n1.v0.1":
        raise ValueError("Output owner mismatch")
    cfg = load_config(args.config)
    context = load_inference_context(args.input)
    write_json(out, "run_metadata.json", {"schema_version": cfg["schema_version"], "run_state": "building"})
    progress = lambda e: print(json.dumps(e, ensure_ascii=False), flush=True)
    ncfg = load_neighborhood_config(ROOT/cfg["neighborhood_config"])
    evidence = json.loads(args.evidence.read_text(encoding="utf-8")) if args.evidence else measure_geometry(context, ncfg, progress=progress)
    neighborhood = build_neighborhood(context, ncfg, evidence=evidence)
    policy = collect_policy(context, neighborhood, cfg)
    write_json(out, "n1_policy.json", policy)
    joint = joint_feasibility(context, policy, cfg)
    write_json(out, "joint_feasibility.json", joint)
    progress({"stage": "joint_feasibility", "state": joint["state"], "n1_pairs": len(policy["pairs"])})
    pool, run = generate_candidates(context, policy, cfg, joint, progress=progress)
    selected, selection = select_diverse(context, pool, cfg["generation"]["selected_target"])
    write_json(out, "generation_report.json", {"run": run, "selection": selection, "config": cfg,
               "facts_fingerprint": context.facts_fingerprint, "n1_fingerprint": policy["fingerprint"],
               "expert_used": False, "reference_plan_used": False, "training_labels_generated": False})
    for name, plans in (("candidate_pool.jsonl", pool), ("selected_candidates.jsonl", selected)):
        target = out/name
        if target.exists() and (target.is_symlink() or target.stat().st_nlink > 1):
            raise ValueError("Refuse aliased candidates")
        target.write_text("".join(json.dumps(r, ensure_ascii=False, separators=(",", ":"))+"\n" for r in plans), encoding="utf-8")
    # Historical candidates and human-highlighted case IDs are first opened here,
    # after all new candidates and their selection have been fixed.
    before, before_selected, known_cases, hashes = baseline_inputs(args.baseline, context.facts_fingerprint)
    base_audit = audit_cohort(context, neighborhood, policy, before, "original_pool")
    pass_hashes = {r["plan_hash"] for r in base_audit["plans"] if r["trial_admitted"]}
    filtered = [r for r in before if r["plan_hash"] in pass_hashes]
    cohorts = {"original_pool": base_audit,
               "original_selected": audit_cohort(context, neighborhood, policy, before_selected, "original_selected"),
               "filtered_original_pool": audit_cohort(context, neighborhood, policy, filtered, "filtered_original_pool"),
               "new_pool": audit_cohort(context, neighborhood, policy, pool, "new_pool"),
               "new_selected": audit_cohort(context, neighborhood, policy, selected, "new_selected")}
    write_json(out, "cohort_audit.json", cohorts)
    comparisons = compare_candidates(context, neighborhood, policy, before_selected, selected)
    known = []
    policy_ids = {r["pair_id"] for r in policy["pairs"]}
    for case in known_cases:
        key = case["pair_id"]
        known.append({"pair_id": key, "covered_by_n1": key in policy_ids,
                      "original_same_station_count": cohorts["original_pool"]["per_pair_same_station_count"].get(key),
                      "new_same_station_count": cohorts["new_pool"]["per_pair_same_station_count"].get(key),
                      "used_for_generation": False})
    write_json(out, "known_case_coverage.json", known)
    comparisons.extend(known_case_comparisons(context, neighborhood, policy, before, selected, known_cases))
    write_json(out, "comparisons.json", comparisons)
    write_csv(out/"n1_pair_catalogue.csv", policy["pairs"], ["pair_id", "flanging_operation_id", "irregular_operation_id", "boundary_distance", "source_arc", "gates", "neighborhood_inclusion_reason"])
    write_csv(out/"plan_audit.csv", [{"cohort": name, **r} for name, group in cohorts.items() for r in group["plans"]],
              ["cohort", "plan_id", "plan_hash", "n1_evaluated", "n1_pass", "trial_admitted", "violation_count", "violations", "other_external_same_station_pair_ids"])
    write_csv(out/"station_load_statistics.csv", [{"cohort": key, "station_id": station, **stats} for key, group in cohorts.items() for station, stats in group["station_load_distribution"].items()],
              ["cohort", "station_id", "count", "min", "mean", "max"])
    write_csv(out/"comparison_changes.csv", [{"comparison_id": c["comparison_id"], **change} for c in comparisons for change in c["all_changes"]],
              ["comparison_id", "operation_id", "operation_type", "before_station", "after_station"])
    write_csv(out/"other_neighbor_changes.csv", [{"comparison_id": c["comparison_id"], **r} for c in comparisons for r in c["external_same_station_changes"] if not r["within_n1"]],
              ["comparison_id", "pair_id", "before_same_station", "after_same_station"])
    outline_audit = build_page(context, neighborhood, policy, cohorts, before, selected, comparisons, joint, cfg, out)
    write_report(out, context, policy, joint, run, cohorts, known, cfg)
    write_json(out, "run_metadata.json", {"schema_version": cfg["schema_version"], "run_state": "complete",
        "source_workbook": str(args.input.resolve()), "source_sha256_provenance_only": context.part_data.source_sha256,
        "facts_fingerprint": context.facts_fingerprint, "neighborhood_fingerprint": neighborhood.fingerprint,
        "n1_fingerprint": policy["fingerprint"], "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
        "baseline_files_read_after_generation_only": hashes, "outline_audit_display_only": outline_audit,
        "evidence_cache_sha256": hashlib.sha256(args.evidence.read_bytes()).hexdigest() if args.evidence else None,
        "new_expert_blind_pack": False, "expert_used": False})
    progress({"summary": {k: {a: g[a] for a in ("plan_count", "n1_pass_count", "n1_violation_distribution")} for k, g in cohorts.items()}})


if __name__ == "__main__":
    main()
