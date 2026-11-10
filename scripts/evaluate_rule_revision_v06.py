"""Offline v0.6 evaluation and historical candidate audit; never generate Plans."""
import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.constraints import ConstraintContext, HardConstraintEngine as LegacyHard
from src.constraints.soft_priors import SoftPriorEngine as LegacySoft
from src.constraints.v06 import load_rule_context, HardConstraintEngine, SoftPriorEngine
from src.constraints.v06.config import DEFAULT_CONFIG_PATH, load_config
from src.constraints.v06.context import RULE_VERSION
from src.constraints.v06.proximity import proximity_statistics
from src.data.validator import validate


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def evaluate_expert(context, config):
    validation = validate(context.part_data)
    hard = HardConstraintEngine(context).evaluate(context.expert_reference_plan)
    report = {"rule_version": RULE_VERSION, "status": "stopped_expert_or_data_issue",
              "milestone1_validation": validation.to_dict(), "expert_hard_constraints": hard.to_dict(),
              "configuration": config,
              "interpretation": "Heuristics only, not process quality, GNN labels, or expert preference labels"}
    if not validation.passed or not hard.feasible:
        return report
    relations = context.precedence_relations
    unique = {(r.predecessor_id, r.operation_id) for r in relations}
    legacy = ConstraintContext(context.part_data, context.fixed_assignments, context.bend_influences, context.metadata_sha256)
    legacy_pairs = {(r.predecessor_id, r.operation_id) for r in legacy.precedence_relations}
    feature_pairs = {(r.predecessor_id, r.operation_id) for r in context.feature_tree_relations}
    report["precedence"] = {"raw_relation_count": len(relations), "unique_pair_count": len(unique),
                            "source_counts": dict(Counter(r.relation_source for r in relations)),
                            "feature_tree_count": len(feature_pairs), "feature_tree_overlap_with_H1_H6": len(feature_pairs & legacy_pairs),
                            "additional_feature_tree_pairs": len(feature_pairs-legacy_pairs),
                            "relations": [asdict(r) for r in relations]}
    try:
        report["soft_priors"] = SoftPriorEngine(context, config).evaluate(context.expert_reference_plan)
    except ValueError as exc:
        report.update(status="stopped_soft_definition_or_bound_issue", issue=str(exc))
        return report
    report["legacy_recomputed_on_current_input"] = LegacySoft(legacy).evaluate(legacy.expert_reference_plan)
    report["proximity_diagnostic"] = proximity_statistics(context, context.expert_reference_plan,
                                                          k=config["proximity_diagnostic"]["k"])
    report["status"] = "evaluated"
    return report


def revalidate_candidates(context, config, files):
    hard, scorer = HardConstraintEngine(context), SoftPriorEngine(context, config)
    legacy = ConstraintContext(context.part_data, context.fixed_assignments, context.bend_influences, context.metadata_sha256)
    result = {"rule_version": RULE_VERSION, "action": "read_only_revalidation_no_generation", "collections": {}}
    for name, path in files.items():
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            plan = record["assignments"]
            evaluated = hard.evaluate(plan)
            row = {"plan_id": record["plan_id"], "original_plan_hash": record["plan_hash"],
                   "legacy_H1_H6_feasible": LegacyHard(legacy).evaluate(plan).feasible,
                   "hard_constraints": evaluated.to_dict()}
            if evaluated.feasible:
                # New versioned result only; never replace an original plan's score.
                soft = scorer.evaluate(plan)
                row["v06_soft_priors"] = {key: value for key, value in soft.items() if key != "components"}
                row["v06_soft_priors"]["components"] = {
                    name: {key: value for key, value in component.items() if key != "audit"}
                    for name, component in soft["components"].items()}
                area = soft["components"]["bend_area_ordering"]["audit"]
                row["area_audit_summary"] = {key: area[key] for key in (
                    "comparable_pair_count", "excluded_hierarchy_pair_count", "excluded_additional_hard_precedence_pair_count",
                    "insignificant_pair_count", "inversion_count", "inversion_rate")}
                row["inversion_operation_pairs"] = [[r["larger_operation_id"], r["smaller_operation_id"]]
                                                     for r in area["inversion_pairs"]]
            rows.append(row)
        failures = Counter(v["constraint_type"] for row in rows for v in row["hard_constraints"]["violations"])
        result["collections"][name] = {"source_path": str(path), "source_sha256": file_hash(path), "count": len(rows),
                                        "feasible_count": sum(row["hard_constraints"]["feasible"] for row in rows),
                                        "infeasible_count": sum(not row["hard_constraints"]["feasible"] for row in rows),
                                        "violation_counts": dict(failures), "plans": rows}
    return result


def markdown_report(expert, candidates=None):
    lines = ["# M3 v0.6 规则修订验证", "", f"状态：{expert['status']}", "",
             "本报告使用已纠错 Excel 和独立 v0.6 入口。历史候选、分数、盲化产物保留。新分量不是训练标签。", ""]

    def table(head, rows):
        lines.extend(["| " + " | ".join(head) + " |", "| " + " | ".join("---" for _ in head) + " |"])
        lines.extend("| " + " | ".join(str(x) for x in row) + " |" for row in rows)
        lines.append("")

    table(["约束", "专家 violation 数"], expert["expert_hard_constraints"]["violation_breakdown"].items())
    if expert["status"] != "evaluated":
        lines.extend([expert.get("issue", "数据或专家约束不通过，已停止后续结论。"), ""])
        return "\n".join(lines)
    p = expert["precedence"]
    lines.extend([f"CAD child→parent={p['feature_tree_count']}；与原硬关系重叠={p['feature_tree_overlap_with_H1_H6']}；新增唯一 pair={p['additional_feature_tree_pairs']}。",
                  f"全部来源记录={p['raw_relation_count']}；P2 唯一有向 pair={p['unique_pair_count']}。", "", "## P1–P3", ""])
    soft = expert["soft_priors"]
    table(["分量", "raw", "weight", "weighted"], [(n, r["raw_component"], r["weight"], r["weighted_contribution"]) for n, r in soft["components"].items()])
    lines.extend([f"total_soft_penalty（仅新版 P1–P3）={soft['total_soft_penalty']:.12f}。旧版数字不能直接用于跨版本排名。", ""])
    area = soft["components"]["bend_area_ordering"]["audit"]
    lines.extend([f"P1：comparable={area['comparable_pair_count']}；hierarchy excluded={area['excluded_hierarchy_pair_count']}；其它 hard excluded={area['excluded_additional_hard_precedence_pair_count']}；inversions={area['inversion_count']}。", ""])
    gap = soft["components"]["precedence_extra_gap"]["audit"]
    lines.extend(["## P2 来源审计", "", "以下 source 集合有重叠，不能相加。互斥 source-set 可用于核账；逐 pair 上下界见 JSON。", ""])
    table(["来源", "唯一 pair 数", "g_min 分布", "g_actual 分布", "g_max 分布", "平均 penalty"],
          [(n, r["unique_pair_count"], r["g_min_distribution"], r["g_actual_distribution"], r["g_max_distribution"], r["extra_gap_mean"]) for n, r in gap["sources"].items()])
    table(["互斥来源组合", "pair 数", "component contribution"], [(n, r["unique_pair_count"], r["component_contribution"]) for n, r in gap["exclusive_source_sets"].items()])
    lines.extend(["## 折弯方向分量（无权重，不计 total）", ""])
    direction = soft["unweighted_components"]["bend_direction_fragmentation"]
    table(["方向", "数量", "K_min", "K_actual", "K_max", "used", "penalty", "参与平均"],
          [(r["direction"], r["operation_count"], r["K_min"], r["K_actual"], r["K_max"], r["actual_used_stations"], r["fragmentation_penalty"], r["participating"]) for r in direction["directions"]])
    lines.extend([f"方向分量={direction['value']}。上下界由 H1–H7 可行域证明，没有读取专家负载。", "", "## k=5 近邻诊断", ""])
    prox = expert["proximity_diagnostic"]
    lines.extend([f"Graph2 默认 k={prox['frozen_graph_k']}；分析 k={prox['analysis_k']}；有向={prox['directed_count']}；无向 union={prox['undirected_union_count']}；mutual={prox['mutual_pair_count']}。", "",
                  "未启用 PROXIMITY_CONFLICT。下表的已证明强制异序只计无共同合法 Station 或硬路径，属于下界，其余 pair 不代表已经证明能同序。", ""])
    table(["类型对", "pair 数", "同序", "异序", "异序比例", "已证明强制异序"],
          [(" / ".join(r["type_pair"]), r["pair_count"], r["same_station_count"], r["different_station_count"], f"{r['different_station_ratio']:.2%}", r["proven_forced_different_count"]) for r in prox["undirected_type_statistics"]])
    example = prox["expert_example_O131_O203"]
    if example:
        lines.extend([f"用户案例 O131/O203：ranks={example['ranks']}；boundary_distance={example['boundary_distance']}；expert Station={example['station_ids']}。",
                      "O203 原名为折弯异形孔11，但 CAD parent 为空。因此没有 BEND parent 不能用于排除这类孔；也不能自动创建翻孔与异形孔的配套关系。", ""])
    if candidates:
        lines.extend(["## 历史候选复核（未生成新候选）", ""])
        table(["集合", "总数", "新版合法", "新版不合法", "violations"],
              [(n, r["count"], r["feasible_count"], r["infeasible_count"], r["violation_counts"]) for n, r in candidates["collections"].items()])
        selected = candidates["collections"].get("selected", {})
        table(["selected ID", "冲突 Operation pair", "Station pair"],
              [(r["plan_id"], " / ".join(v["operation_ids"]), " / ".join(v["station_ids"])) for r in selected.get("plans", [])
               for v in r["hard_constraints"]["violations"]])
    lines.extend(["## 后续事项", "", "确认 proximity 类型集合、配套特征含义及新 soft 权重后，再安排新版候选和评审。正式无参考 CandidatePlanGenerator 和训练均未在本版实现。", ""])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data/parts/P001/钣金加工数据表_P001.xlsx")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/m3_rule_revision_v06")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / "outputs").resolve()):
        raise ValueError("Reports must be below project outputs/")
    files = {"pool": ROOT / "outputs/m3_candidates/candidate_pool.jsonl",
             "selected": ROOT / "outputs/m3_candidates/selected_candidates.jsonl"}
    input_hashes = {str(p): file_hash(p) for p in (args.input, args.config, *files.values())}
    context, config = load_rule_context(args.input), load_config(args.config)
    expert = evaluate_expert(context, config)
    candidate = revalidate_candidates(context, config, files) if expert["status"] == "evaluated" else None
    after = {p: file_hash(p) for p in input_hashes}
    if after != input_hashes:
        raise RuntimeError("Input changed during evaluation")
    expert["provenance"] = {"input_sha256": input_hashes, "inputs_unchanged": True}
    output.mkdir(parents=True, exist_ok=True)
    for name, value in (("expert_rule_revision_report.json", expert), ("candidate_rule_revalidation.json", candidate)):
        if value is not None:
            (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (output / "rule_revision_report.md").write_text(markdown_report(expert, candidate), encoding="utf-8")
    print(json.dumps({"status": expert["status"], "expert_feasible": expert["expert_hard_constraints"]["feasible"],
                      "candidate_counts": {n: {k: r[k] for k in ("count", "feasible_count", "infeasible_count")} for n, r in (candidate or {}).get("collections", {}).items()}}))
    return 0 if expert["status"] == "evaluated" else 1


if __name__ == "__main__":
    raise SystemExit(main())
