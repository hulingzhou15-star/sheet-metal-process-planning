"""Post-selection observations. Nothing in this module influences selection."""
from collections import Counter
from itertools import combinations
import math
import statistics
import json

from src.constraints.diagnostics import hierarchy_distribution
from src.constraints.region_assignment import local_region_distribution
from src.constraints.soft_priors import bend_ordering_statistics
from .config import BUCKET_ORDER


def distribution(values):
    values = list(values)
    if not values:
        return {"count": 0, "minimum": None, "median": None, "mean": None, "maximum": None}
    return {"count": len(values), "minimum": min(values), "median": statistics.median(values),
            "mean": math.fsum(values)/len(values), "maximum": max(values)}


def candidate_statistics(generator, pool, selected, run):
    context, distance, expert = generator.context, generator.distance, generator.expert
    bucket_stats = {}
    for bucket in BUCKET_ORDER:
        candidates = [c for c in selected if c["expert_distance"]["bucket"] == bucket]
        bucket_stats[bucket] = {"integer_distance_range": run["distance_buckets"][bucket],
                                **run["bucket_generation"][bucket], "selected_count": len(candidates),
                                "distance_to_expert_raw": distribution(c["expert_distance"]["raw"] for c in candidates),
                                "distance_to_expert_normalized": distribution(c["expert_distance"]["normalized"] for c in candidates),
                                "total_soft_penalty": distribution(c["soft"]["total_soft_penalty"] for c in candidates),
                                "components": {name: distribution(c["soft"]["components"][name]["raw_component"] for c in candidates)
                                               for name in generator.constraint_config["soft_weights"]}}
    pair_records = [{"plan_ids": [a["plan_id"], b["plan_id"]],
                     "buckets": [a["expert_distance"]["bucket"], b["expert_distance"]["bucket"]],
                     "raw": distance.raw(a["assignments"], b["assignments"]),
                     "normalized": distance.normalized(a["assignments"], b["assignments"])}
                    for a, b in combinations(selected, 2)]
    same_bucket = [r for r in pair_records if r["buckets"][0] == r["buckets"][1]]
    cross_bucket = [r for r in pair_records if r["buckets"][0] != r["buckets"][1]]
    diversity = {"all_pairs": distribution(r["normalized"] for r in pair_records),
                 "within_bucket": distribution(r["normalized"] for r in same_bucket),
                 "cross_bucket": distribution(r["normalized"] for r in cross_bucket),
                 "within_each_bucket": {b: distribution(r["normalized"] for r in same_bucket if r["buckets"][0] == b) for b in BUCKET_ORDER},
                 "between_bucket_pairs": {f"{a}/{b}": distribution(r["normalized"] for r in cross_bucket if set(r["buckets"]) == {a, b})
                                          for a, b in combinations(BUCKET_ORDER, 2)}, "pairs": pair_records}
    expert_guard = generator.guard.evaluate(expert)
    station_loads = []
    for record in expert_guard["stations"]:
        station = record["station_id"]
        station_loads.append({"station_id": station, "expert_load": record["expert_load"],
                              "guardrail_upper_bound": record["guardrail_upper_bound"],
                              "selected_load": distribution(sum(s == station for s in c["assignments"].values()) for c in selected)})
    movements, type_stats = [], {}
    for op_id in distance.movable_operation_ids:
        changed = sum(c["assignments"][op_id] != expert[op_id] for c in selected)
        kind = context.operations[op_id].operation_type
        movements.append({"operation_id": op_id, "operation_type": getattr(kind, "value", kind),
                          "expert_station": expert[op_id], "changed_count": changed,
                          "changed_fraction": changed/len(selected) if selected else None,
                          "selected_station_distribution": dict(sorted(Counter(c["assignments"][op_id] for c in selected).items()))})
    movements.sort(key=lambda r: (-r["changed_count"], r["operation_id"]))
    for kind in sorted({getattr(op.operation_type, "value", op.operation_type) for op in context.part_data.operations}):
        ids = [op.operation_id for op in context.part_data.operations if op.operation_type == kind]
        transitions = Counter(f"{expert[op]} -> {c['assignments'][op]}" for c in selected for op in ids
                              if expert[op] != c["assignments"][op])
        type_stats[kind] = {"operation_count": len(ids), "movable_count": sum(op in distance.movable_operation_ids for op in ids),
                            "changed_assignment_count": sum(transitions.values()),
                            "station_transitions": dict(sorted(transitions.items(), key=lambda item: (-item[1], item[0])))}
    totals = {key: sum(s[key] for s in run["bucket_generation"].values()) for key in
              ("attempts", "solve_attempts", "feasible_solutions", "hard_validation_failures", "guardrail_rejections",
               "duplicate_rejections", "exact_infeasible_count", "unknown_count", "fallback_solve_count")}
    totals["maximum_distance_solver_calls"] = 1
    totals["total_solver_calls_including_maximum"] = totals["solve_attempts"]+1
    all_soft = {"total_soft_penalty": distribution(c["soft"]["total_soft_penalty"] for c in selected),
                "components": {name: distribution(c["soft"]["components"][name]["raw_component"] for c in selected)
                               for name in generator.constraint_config["soft_weights"]}}
    return {"solver_pool_totals": totals, "buckets": bucket_stats, "selected_count": len(selected),
            "soft_distributions": all_soft, "diversity": diversity, "station_loads": station_loads,
            "operation_movement_frequency": movements,
            "top_operation_movements": movements[:generator.config["diagnostics"]["top_movement_count"]],
            "operation_type_movement": type_stats,
            "candidate_diagnostics": selected_diagnostics(generator, selected)}


def selected_diagnostics(generator, selected):
    context, expert, ranks = generator.context, generator.expert, generator.context.station_ranks
    # The forest and D3 membership are static; obtain each once from frozen v0.1.
    hierarchy = hierarchy_distribution(context, expert)
    regions = {r["region_id"]: r for r in hierarchy["hierarchy"]}
    d1_records, per_plan = [], []
    for candidate in selected:
        assignments = candidate["assignments"]
        depth = bend_ordering_statistics(context, assignments, metric="affected_depth",
                                         significance_ratio=generator.constraint_config["depth_diagnostic"]["significance_ratio"])
        record = {"plan_id": candidate["plan_id"], "comparable_pair_count": depth["comparable_pair_count"],
                  "inversion_count": depth["inversion_count"], "inversion_rate": depth["inversion_rate"],
                  "inversion_pairs": depth["inversion_pairs"]}
        d1_records.append(record)
        per_plan.append({"plan_id": candidate["plan_id"], "D1": {key: record[key] for key in
                         ("comparable_pair_count", "inversion_count", "inversion_rate")}})
    region_stations, gap_stats = [], []
    for region in hierarchy["hierarchy"]:
        op_id = region["bend_operation_id"]
        region_stations.append({"region_id": region["region_id"], "bend_operation_id": op_id, "level": region["level"],
                                "station_distribution": dict(sorted(Counter(c["assignments"][op_id] for c in selected).items()))})
        if region["parent_region_id"] is not None:
            parent = regions[region["parent_region_id"]]["bend_operation_id"]
            gaps = [ranks[c["assignments"][parent]]-ranks[c["assignments"][op_id]] for c in selected]
            gap_stats.append({"child_region_id": region["region_id"], "parent_region_id": region["parent_region_id"],
                              "gap_distribution": dict(sorted(Counter(gaps).items())), "gap_statistics": distribution(gaps)})
    d2 = {"root_region_count": hierarchy["root_region_count"], "leaf_region_count": hierarchy["leaf_region_count"],
          "max_hierarchy_depth": hierarchy["max_hierarchy_depth"], "hierarchy_constructed_once": True,
          "forest": hierarchy["hierarchy"], "region_station_distributions": region_stations,
          "levels": {level: {"bend_count_per_plan": value["bend_count"],
                             "selected_station_distribution": dict(sorted(Counter(
                                 c["assignments"][r["bend_operation_id"]] for c in selected
                                 for r in hierarchy["hierarchy"] if r["level"] == level).items()))}
                     for level, value in hierarchy["levels"].items()},
          "parent_child_gaps": gap_stats,
          "all_parent_child_gap_distribution": dict(sorted(sum((Counter(row["gap_distribution"]) for row in gap_stats), Counter()).items()))}
    d3 = {"status": "disabled_by_generator_configuration"}
    if generator.config["diagnostics"]["include_d3"]:
        local = local_region_distribution(context, expert)
        region_summaries = []
        for region in local["regions"]:
            ids = region["operation_ids"]
            by_plan = [{"plan_id": c["plan_id"], "station_distribution": dict(sorted(Counter(c["assignments"][op] for op in ids).items()))}
                       for c in selected]
            region_summaries.append({"region_id": region["region_id"], "operation_count": len(ids), "operation_ids": ids,
                                     "operation_type_distribution": region["operation_type_distribution"],
                                     "station_count_distribution": distribution(len(row["station_distribution"]) for row in by_plan),
                                     "aggregate_station_distribution": dict(sorted(sum((Counter(row["station_distribution"]) for row in by_plan), Counter()).items())),
                                     "per_plan_station_distribution": by_plan})
        d3 = {"status": local["status"], "membership_computed_once": True, "valid_region_count": local["valid_region_count"],
              "assigned_operation_count_per_plan": local["assigned_operation_count"],
              "no_geometry_count": local.get("no_geometry_count"), "outside_region_count": local.get("outside_region_count"),
              "invalid_regions": local["invalid_regions"], "ambiguous_smallest_regions": local.get("ambiguous_smallest_regions", []),
              "reason": local.get("reason"), "regions": region_summaries}
    return {"affects_selection": False, "D1": {"weight": 0, "inversion_rate_distribution": distribution(r["inversion_rate"] for r in d1_records),
                                               "per_plan": d1_records}, "D2": d2, "D3": d3, "per_plan_summary": per_plan}


def generation_markdown(report):
    lines = ["# Milestone 3 v0.2 CandidatePlanGenerator", "", f"Status: `{report['status']}`", "",
             "本批输出为候选方案；SoftPenalty 仅为弱启发式，不是真实工艺质量、训练标签或专家偏好。", ""]

    def table(headers, rows):
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        lines.extend("| " + " | ".join(str(v) for v in row) + " |" for row in rows)
        lines.append("")

    def fmt(stat):
        return " / ".join(f"{stat[key]:.6f}" if stat[key] is not None else "n/a" for key in ("minimum", "median", "maximum"))

    if report["status"] != "generated":
        lines.extend(["```json", json.dumps(report, ensure_ascii=False, indent=2), "```", ""])
        return "\n".join(lines)
    run, stats = report["run"], report["statistics"]
    maximum = run["maximum_distance"]
    lines += ["## Solver 与 pool", "", f"OR-Tools: {report['runtime']['ortools_version']}；workers=1；"
              f"|M|={run['movable_operation_count']}。", "",
              f"Maximum-distance solver={maximum['solver_status']}；proven optimal={maximum['proven_optimal']}；"
              f"D_max={maximum['D_max']}；best-known={maximum['best_known_D_max']}；upper bound={maximum['solver_upper_bound']}。", "",
              "若 solver 只返回 FEASIBLE，D_max 留空，bucket 仅以 best-known 值构造。", ""]
    table(["Statistic", "Count"], [(key, value) for key, value in stats["solver_pool_totals"].items()])
    lines += [f"Unique pool={run['unique_pool_size']}；selected={stats['selected_count']}；"
              f"unfilled pool={run['unfilled_pool_total']}；unfilled selected={run['selection']['unfilled_total']}。", ""]
    table(["Bucket", "Distance range", "Attempts", "Solver calls", "Unique pool", "Prefilter", "Selected", "Redistributed selected"],
          [(b, f"{r['integer_distance_range']['lower']}–{r['integer_distance_range']['upper']}" if not r['integer_distance_range']['empty'] else "empty",
            r["attempts"], r["solve_attempts"], r["unique_pool_size"], run["selection"]["buckets"][b]["prefilter_count"],
            r["selected_count"], run["selection"]["buckets"][b]["redistributed_extra_count"]) for b, r in stats["buckets"].items()])
    lines += ["## Selected 分布", "", "下表为 min / median / max。", ""]
    table(["Bucket", "Expert raw distance", "Expert norm distance", "SoftPenalty", "P1", "P2", "P3"],
          [(b, fmt(r["distance_to_expert_raw"]), fmt(r["distance_to_expert_normalized"]), fmt(r["total_soft_penalty"]),
            *(fmt(r["components"][name]) for name in ("bend_area_ordering", "precedence_extra_gap", "type_fragmentation")))
           for b, r in stats["buckets"].items()])
    table(["All selected component", "Min / median / max"],
          [("Total SoftPenalty", fmt(stats["soft_distributions"]["total_soft_penalty"]))]
          +[(name, fmt(value)) for name, value in stats["soft_distributions"]["components"].items()])
    lines += ["## Pairwise diversity", ""]
    diversity = stats["diversity"]
    rows = [(name, r["count"], fmt(r), r["mean"]) for name, r in
            [(key, diversity[key]) for key in ("all_pairs", "within_bucket", "cross_bucket")]
            +list(diversity["within_each_bucket"].items())+list(diversity["between_bucket_pairs"].items())]
    table(["Group", "Pair count", "Norm min / median / max", "Mean"], rows)
    lines += ["## Station load", ""]
    table(["Station", "Expert load", "G1 upper", "Selected min / median / max"],
          [(r["station_id"], r["expert_load"], r["guardrail_upper_bound"], fmt(r["selected_load"])) for r in stats["station_loads"]])
    lines += ["## Operation movement", ""]
    table(["Operation", "Type", "Expert", "Changed count", "Changed fraction"],
          [(r["operation_id"], r["operation_type"], r["expert_station"], r["changed_count"], r["changed_fraction"])
           for r in stats["top_operation_movements"]])
    table(["OperationType", "Movable", "Changed assignments", "Transitions"],
          [(kind, r["movable_count"], r["changed_assignment_count"], str(r["station_transitions"]))
           for kind, r in stats["operation_type_movement"].items()])
    diagnostics = stats["candidate_diagnostics"]
    lines += ["## D1–D3（不影响 score 或 selection）", "",
              f"D1 depth inversion rate min/median/max={fmt(diagnostics['D1']['inversion_rate_distribution'])}。", "",
              f"D2 roots={diagnostics['D2']['root_region_count']}；leaves={diagnostics['D2']['leaf_region_count']}；"
              f"max depth={diagnostics['D2']['max_hierarchy_depth']}；forest 构造一次。", "",
              f"D2 selected parent-child gap distribution: {diagnostics['D2']['all_parent_child_gap_distribution']}", ""]
    table(["Level", "BEND per Plan", "Selected aggregate Station distribution"],
          [(level, value["bend_count_per_plan"], str(value["selected_station_distribution"]))
           for level, value in diagnostics["D2"]["levels"].items()])
    d3 = diagnostics["D3"]
    lines += [f"D3 status={d3['status']}；valid regions={d3.get('valid_region_count', 'n/a')}；"
              f"assigned Operations per Plan={d3.get('assigned_operation_count_per_plan', 'n/a')}。固定 membership 复用一次。", ""]
    if "regions" in d3:
        table(["Region", "Operation count", "Station count min / median / max", "Aggregate Station distribution"],
              [(r["region_id"], r["operation_count"], fmt(r["station_count_distribution"]), str(r["aggregate_station_distribution"])) for r in d3["regions"]])
    lines += ["## 可审计边界", "",
              "H1–H6 和 G1 通过冻结实现二次验证；P1/P2/P3 只在求解之后计算。", "",
              "70% 预筛选按 ceil 保留，之后逐 bucket 进行 max-min farthest-first；并列按 penalty、hash。无隐藏综合权重。", "",
              f"Frozen integrity: {report['frozen_integrity']['all_unchanged']}，files={report['frozen_integrity']['checked_file_count']}。", "",
              "所有逐 candidate、逐 pair、movable ID、attempt、region Station 分布和参数见 JSON/JSONL/CSV。", "",
              "本轮仅为候选探索。可进入人工 expert comparison 的审阅准备，尚不能把候选或 penalty 自动转成 preference labels。", ""]
    return "\n".join(lines)
