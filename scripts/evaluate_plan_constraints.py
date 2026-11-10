"""Evaluate one supplied Plan; expert feasibility is always checked first."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.constraints import ContextValidationError, HardConstraintEngine, load_constraint_context
from src.constraints.config import DEFAULT_CONFIG_PATH, load_config
from src.constraints.diagnostics import DiagnosticEngine
from src.constraints.guardrails import StationOverloadGuardrail
from src.constraints.soft_priors import SoftPriorEngine
from src.data import validate


def build_report(context, plan, *, config, plan_source="expert"):
    hard = HardConstraintEngine(context)
    expert_result = hard.evaluate(context.expert_reference_plan)
    report = {"schema_version": "m3.v0.1", "plan_source": plan_source,
              "interpretation": "SoftPenalty is only a candidate generation heuristic; it is neither process quality nor a GNN/preference label.",
              "expert_hard_constraints": expert_result.to_dict(), "configuration": config,
              "metadata": {"operation_count": len(context.operations), "station_count": len(context.stations),
                           "fixed_trim_count": len(context.fixed_assignments), "region_count": len(context.regions),
                           "fixed_assignments": [asdict(a) for a in context.fixed_assignments],
                           "bend_influences": [asdict(r) for r in context.bend_influences]}}
    # No scoring, diagnostics, or guardrail inference follows an expert conflict.
    if not expert_result.feasible:
        return {**report, "status": "stopped_expert_infeasible", "hard_constraints": expert_result.to_dict()}
    result = hard.evaluate(plan)
    report["hard_constraints"] = result.to_dict()
    if not result.feasible:
        return {**report, "status": "stopped_plan_infeasible"}
    try:
        report["soft_priors"] = SoftPriorEngine(context, config).evaluate(plan)
    except ValueError as exc:
        return {**report, "status": "stopped_definition_or_bound_issue", "issue": str(exc)}
    report["diagnostics"] = DiagnosticEngine(context, config).evaluate(plan)
    report["guardrail"] = StationOverloadGuardrail(context, config).evaluate(plan)
    report["status"] = "evaluated"
    return report


def markdown_report(report):
    lines = ["# Milestone 3 v0.1 Plan 约束报告", "", f"Plan: `{report.get('plan_source', 'expert')}`",
             "", f"状态：`{report['status']}`", "",
             "SoftPenalty 仅为未来候选生成启发式，不是真实工艺质量、GNN label 或 preference label。", ""]

    def table(headers, rows):
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for row in rows:
            lines.append("| " + " | ".join(str(value) for value in row) + " |")
        lines.append("")

    def compact(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    if "provenance" in report:
        lines += ["## 输入完整性", "", "```json", json.dumps(report["provenance"], ensure_ascii=False, indent=2), "```", ""]
    if "hard_constraints" not in report:
        lines += ["```json", json.dumps(report, ensure_ascii=False, indent=2), "```", ""]
        return "\n".join(lines)
    hard = report["hard_constraints"]
    lines += ["## H1–H6", "", f"Hard feasibility: **{hard['feasible']}**", ""]
    table(["Constraint", "Violation count"], hard["violation_breakdown"].items())
    if hard["violations"]:
        lines += ["```json", json.dumps(hard["violations"], ensure_ascii=False, indent=2), "```", ""]
    if report["status"] != "evaluated":
        lines += ["已停止后续结论。", "", report.get("issue", ""), ""]
        return "\n".join(lines)
    soft = report["soft_priors"]
    lines += ["## P1–P3", ""]
    table(["Component", "Raw", "Weight", "Weighted contribution"],
          [(name, f"{c['raw_component']:.12f}", c["weight"], f"{c['weighted_contribution']:.12f}")
           for name, c in soft["components"].items()])
    lines += [f"Total SoftPenalty: **{soft['total_soft_penalty']:.12f}**", ""]
    area = soft["components"]["bend_area_ordering"]["audit"]
    lines += ["### P1 面积排序", "",
              f"全部 pair={area['total_pair_count']}；comparable={area['comparable_pair_count']}；"
              f"excluded hierarchy={area['excluded_hierarchy_pair_count']}；insignificant={area['insignificant_pair_count']}；"
              f"inversion={area['inversion_count']}。同 Station pair 仍在 comparable 分母中，不记 inversion。", "",
              "全部 comparable pair、值、Station.order 及筛选记录见同名 JSON 的 `comparable_pairs` / `insignificant_pairs`。", ""]
    table(["Excluded region pair", "BEND pair"],
          [(" / ".join(r["region_ids"]), " / ".join(r["operation_ids"])) for r in area["excluded_hierarchy_pairs"]])
    table(["Inversion larger BEND", "Smaller BEND", "Larger area", "Smaller area", "Stations (larger / smaller)"],
          [(r["larger_operation_id"], r["smaller_operation_id"], r["larger_value"], r["smaller_value"],
            " / ".join(r["station_ids"])) for r in area["inversion_pairs"]])
    gap = soft["components"]["precedence_extra_gap"]["audit"]
    lines += ["### P2 额外间隔", "", f"有效 relation count={gap['relation_count']}；gap 使用连续 rank。", ""]
    table(["Source", "Count", "g_min distribution", "g_actual distribution", "g_max distribution", "Mean penalty", "c_extra_gap contribution"],
          [(source, r["relation_count"], compact(r["g_min_distribution"]), compact(r["g_actual_distribution"]),
            compact(r["g_max_distribution"]), r["extra_gap_mean"], r["component_contribution"])
           for source, r in gap["sources"].items()])
    table(["A → B", "Source", "Allowed A", "Allowed B", "g_min", "g_actual", "g_max", "p_e", "Component contribution"],
          [(" → ".join(r["operation_ids"]), r["relation_source"], ", ".join(r["allowed_predecessor_stations"]),
            ", ".join(r["allowed_successor_stations"]), r["g_min"], r["g_actual"], r["g_max"], r["extra_gap_penalty"],
            r["component_contribution"]) for r in gap["relations"]])
    fragment = soft["components"]["type_fragmentation"]["audit"]
    lines += ["### P3 类型分散", ""]
    table(["Enabled type", "Operation count", "Allowed Stations", "Used Stations", "K_allowed", "K_actual", "Penalty", "Participating"],
          [(r["operation_type"], r["operation_count"], ", ".join(r["allowed_stations"]),
            ", ".join(r["actual_used_stations"]), r["K_allowed"], r["K_actual"], r["fragmentation_penalty"], r["participating"])
           for r in fragment["types"]])
    diag = report["diagnostics"]
    depth, hierarchy, local = diag["D1"], diag["D2"], diag["D3"]
    lines += ["## D1–D3（不评分）", "", "### D1 affected-depth", "",
              f"comparable={depth['comparable_pair_count']}；excluded={depth['excluded_hierarchy_pair_count']}；"
              f"inversion={depth['inversion_count']}；rate={depth['inversion_rate']:.12f}；weight=0。", ""]
    table(["Larger-depth BEND", "Smaller-depth BEND", "Depths", "Stations"],
          [(r["larger_operation_id"], r["smaller_operation_id"], f"{r['larger_value']} / {r['smaller_value']}",
            " / ".join(r["station_ids"])) for r in depth["inversion_pairs"]])
    lines += ["### D2 region hierarchy", "",
              f"roots={hierarchy['root_region_count']}；leaves={hierarchy['leaf_region_count']}；"
              f"max depth={hierarchy['max_hierarchy_depth']}；level count={hierarchy['hierarchy_level_count']}；"
              f"multilevel chain={hierarchy['has_multilevel_chain']}。root level=0，depth 按 parent 链接数计。", "",
              f"parent-child gap distribution: {compact(hierarchy['parent_child_gap_distribution'])}", ""]
    table(["Level", "BEND count", "Plan Station distribution"],
          [(level, r["bend_count"], compact(r["station_distribution"])) for level, r in hierarchy["levels"].items()])
    table(["Region", "BEND", "Parent", "Level", "Children", "Station"],
          [(r["region_id"], r["bend_operation_id"], r["parent_region_id"] or "root", r["level"],
            ", ".join(r["child_region_ids"]), r["station_id"]) for r in hierarchy["hierarchy"]])
    lines += ["### D3 local region distribution", "", f"状态：{local['status']}；valid regions={local['valid_region_count']}；"
              f"assigned Operations={local['assigned_operation_count']}。", "",
              "代表点复用 M2；rectangle 含边界；使用唯一最小 containing rectangle；等面积并列仅报告，不自行分配。", ""]
    if local["status"] != "unavailable":
        lines += [f"valid points={local['valid_representative_point_count']}；no geometry={local['no_geometry_count']}；"
                  f"outside={local['outside_region_count']}；nonempty regions={local['nonempty_region_count']}。", ""]
    for issue_key in ("invalid_regions", "invalid_representative_points", "ambiguous_smallest_regions"):
        if local.get(issue_key):
            lines += [f"{issue_key}: {compact(local[issue_key])}", ""]
    if local.get("reason"):
        lines += [local["reason"], ""]
    table(["Region", "BEND", "Operation count", "Types", "Station count", "Station distribution", "Operations"],
          [(r["region_id"], r["bend_operation_id"], r["operation_count"], compact(r["operation_type_distribution"]),
            r["station_count"], compact(r["station_distribution"]), ", ".join(r["operation_ids"])) for r in local["regions"]])
    lines += ["## G1（不评分）", "", "阈值仅为未来候选生成的宽松异常过滤范围，不是真实 Station capacity。", ""]
    table(["Station", "Expert load", "Upper bound", "Plan load", "Violation"],
          [(r["station_id"], r["expert_load"], r["guardrail_upper_bound"], r["plan_load"], r["guardrail_violation"])
           for r in report["guardrail"]["stations"]])
    lines += ["## 参数快照", "", "```json", json.dumps(report["configuration"], ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


def write_reports(report, output_dir, *, project_root=ROOT):
    output_dir, project_root = Path(output_dir).resolve(), Path(project_root).resolve()
    if not output_dir.is_relative_to((project_root / "outputs").resolve()):
        raise ValueError("M3 reports may only be written below project outputs/")
    stem = "expert_plan_constraint_report" if report.get("plan_source", "expert") == "expert" else "supplied_plan_constraint_report"
    targets = [output_dir / (stem+suffix) for suffix in (".json", ".md")]
    raw = project_root / "data/parts/P001"
    for target in targets:
        if target.is_symlink() or (target.exists() and any(target.samefile(p) for p in raw.rglob("*")
                                                          if p.is_file() and not p.name.startswith("~$"))):
            raise ValueError("Report output aliases a protected source")
    output_dir.mkdir(parents=True, exist_ok=True)
    targets[0].write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    targets[1].write_text(markdown_report(report), encoding="utf-8")
    return targets


def main(argv=None):
    parser = argparse.ArgumentParser(description="M3 v0.1 constraint audit; no candidate generation")
    parser.add_argument("--input", type=Path, default=ROOT / "data/parts/P001/钣金加工数据表_P001.xlsx")
    parser.add_argument("--plan", default="expert", help="expert or one JSON Operation-to-Station Plan")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/m3_diagnostics")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    try:
        context = load_constraint_context(args.input)
    except ContextValidationError as exc:
        report = {"status": "stopped_invalid_context", "plan_source": "expert", "context_validation": exc.to_dict()}
        write_reports(report, args.output)
        print(json.dumps(report, ensure_ascii=True))
        return 1
    # Expert gate before opening/evaluating any other Plan or emitting soft results.
    expert_result = HardConstraintEngine(context).evaluate(context.expert_reference_plan)
    validation = validate(context.part_data)
    if not expert_result.feasible:
        report = build_report(context, context.expert_reference_plan, config=config)
    elif not validation.passed:
        report = {"status": "stopped_milestone1_validation", "plan_source": "expert",
                  "hard_constraints": expert_result.to_dict(), "milestone1_validation": validation.to_dict()}
    else:
        plan = (context.expert_reference_plan if args.plan == "expert"
                else json.loads(Path(args.plan).read_text(encoding="utf-8"), object_pairs_hook=lambda pairs: pairs))
        report = build_report(context, plan, config=config, plan_source=args.plan)
    after_digest = hashlib.sha256(args.input.read_bytes()).hexdigest()
    report["provenance"] = {"source_path": str(context.part_data.source_path),
                            "source_sha256_before": context.part_data.source_sha256,
                            "metadata_snapshot_sha256": context.metadata_sha256,
                            "source_sha256_after": after_digest,
                            "raw_unchanged": after_digest == context.part_data.source_sha256,
                            "config_path": str(args.config.resolve()),
                            "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
                            "milestone1_validation_counts": validation.counts}
    if not report["provenance"]["raw_unchanged"]:
        raise RuntimeError("Source workbook changed during evaluation")
    paths = write_reports(report, args.output)
    summary = {"status": report["status"], "hard_constraints": report["hard_constraints"],
               "reports": [str(p) for p in paths]}
    if "soft_priors" in report:
        summary["soft_components"] = {k: {field: v[field] for field in ("raw_component", "weight", "weighted_contribution")}
                                      for k, v in report["soft_priors"]["components"].items()}
        summary["total_soft_penalty"] = report["soft_priors"]["total_soft_penalty"]
    print(json.dumps(summary, ensure_ascii=True))
    return 0 if report["status"] == "evaluated" else 1


if __name__ == "__main__":
    raise SystemExit(main())
