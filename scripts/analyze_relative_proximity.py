"""All-pairs geometry audit. No neighbor selection or production graph changes."""
import argparse
from collections import Counter
import csv
from itertools import combinations
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.graph.builders import DISTANCE_SORT_DECIMALS, build_part_graph
from src.graph.geometry_distance import primitives_distance
from src.graph.geometry_primitives import geometry_primitives
from src.inference.input import load_inference_context


def adjacent_gap(current, following):
    """Never divide by zero or split a frozen M2 numerical tie bucket."""
    if current == 0:
        return None, "BOTH_ZERO" if following == 0 else "ZERO_TO_POSITIVE"
    if round(current, DISTANCE_SORT_DECIMALS) == round(following, DISTANCE_SORT_DECIMALS):
        return None, "EQUAL_DISTANCE_BUCKET"
    return following / current, "FINITE"


def ranked_neighbors(ids, matrix):
    rows = []
    for i, op in enumerate(ids):
        if matrix[i][i] is None:
            continue
        indices = sorted((j for j in range(len(ids)) if j != i and matrix[i][j] is not None),
                         key=lambda j: (round(matrix[i][j], DISTANCE_SORT_DECIMALS), ids[j]))
        for rank, j in enumerate(indices, 1):
            following = matrix[i][indices[rank]] if rank < len(indices) else None
            ratio, status = adjacent_gap(matrix[i][j], following) if following is not None else (None, "LAST_NEIGHBOR")
            rows.append({"operation_id": op, "rank": rank, "neighbor_id": ids[j],
                         "distance": matrix[i][j], "next_distance": following,
                         "next_ratio": ratio, "gap_status": status})
    return rows


def threshold_candidates(rows, ratios, baseline):
    """Report every boundary, not an inferred final neighborhood."""
    by_op = {}
    for row in rows:
        by_op.setdefault(row["operation_id"], []).append(row)
    results = []
    for op, group in by_op.items():
        for threshold in ratios:
            cuts = [r["rank"] for r in group if r["next_ratio"] is not None and r["next_ratio"] >= threshold]
            # Diagnostic only: this illustrates what a first-break policy would do.
            results.append({"operation_id": op, "threshold": threshold,
                            "candidate_cut_ranks": cuts,
                            "first_candidate_cut": cuts[0] if cuts else None,
                            "cuts_before_5": sum(k < baseline for k in cuts),
                            "cuts_at_5": sum(k == baseline for k in cuts),
                            "cuts_after_5": sum(k > baseline for k in cuts),
                            "zero_to_positive_ranks": [r["rank"] for r in group if r["gap_status"] == "ZERO_TO_POSITIVE"],
                            "baseline_splits_tie": any(r["rank"] == baseline and r["gap_status"] in {"BOTH_ZERO", "EQUAL_DISTANCE_BUCKET"} for r in group)})
    return results


def write_csv(path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                          for k, v in row.items() if k in fields} for row in rows)


def run(workbook, output, config_path):
    output.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    context = load_inference_context(workbook)
    ops = context.operations
    ids = sorted(ops)
    primitives, catalog = {}, []
    for op in ids:
        obj = ops[op]
        geom = context.part_data.geometry_for(obj)
        if geom is not None:
            primitives[op] = geometry_primitives(geom)
        catalog.append({"operation_id": op, "operation_type": obj.operation_type,
                        "source_feature_ref": obj.source_feature_ref, "geometry_id": obj.geometry_id,
                        "process_group_id": obj.process_group_id, "cad_parent_operation_id": obj.cad_parent_operation_id,
                        "geometry_status": "AVAILABLE" if op in primitives else "MISSING",
                        "source_sheet": obj.source.sheet, "source_row": obj.source.row})
    matrix = [[None] * len(ids) for _ in ids]
    for i, op in enumerate(ids):
        if op in primitives:
            matrix[i][i] = 0.0
    pairs = []
    for i, j in combinations(range(len(ids)), 2):
        a, b = ids[i], ids[j]
        value = primitives_distance(primitives[a], primitives[b]) if a in primitives and b in primitives else None
        matrix[i][j] = matrix[j][i] = value
        pairs.append({"operation_a": a, "operation_b": b, "type_a": ops[a].operation_type,
                      "type_b": ops[b].operation_type, "distance": value,
                      "status": "AVAILABLE" if value is not None else "MISSING_GEOMETRY"})
    ranked = ranked_neighbors(ids, matrix)
    for row in ranked:
        row.update(operation_type=ops[row["operation_id"]].operation_type,
                   neighbor_type=ops[row["neighbor_id"]].operation_type,
                   neighbor_feature_ref=ops[row["neighbor_id"]].source_feature_ref)
        for threshold in cfg["candidate_ratios"]:
            row[f"jump_ge_{threshold:g}"] = row["next_ratio"] is not None and row["next_ratio"] >= threshold
    candidates = threshold_candidates(ranked, cfg["candidate_ratios"], cfg["baseline_neighbor_count"])
    summary = []
    for threshold in cfg["candidate_ratios"]:
        rows = [r for r in candidates if r["threshold"] == threshold]
        counts = Counter("none" if r["first_candidate_cut"] is None else
                         "below_5" if r["first_candidate_cut"] < cfg["baseline_neighbor_count"] else
                         "equal_5" if r["first_candidate_cut"] == cfg["baseline_neighbor_count"] else "above_5" for r in rows)
        summary.append({"threshold": threshold, "geometry_operation_count": len(rows),
                        **{k: counts[k] for k in ("below_5", "equal_5", "above_5", "none")},
                        "multiple_cut_operations": sum(len(r["candidate_cut_ranks"]) > 1 for r in rows)})
    static = build_part_graph(context.part_data)
    index = {op: i for i, op in enumerate(ids)}
    residuals = [abs(e.distance_norm * static.normalization.length_scale -
                     matrix[index[static.operation_ids[e.source_index]]][index[static.operation_ids[e.target_index]]])
                 for e in static.geometric_edges]
    graph_ranks_match = all([r["neighbor_id"] for r in ranked if r["operation_id"] == static.operation_ids[i]][:static.knn_diagnostics.k]
                           == [static.operation_ids[j] for j in neighbors]
                           for i, neighbors in static.knn_diagnostics.selected_neighbors)
    checks = {"matrix_symmetric": all(matrix[i][j] == matrix[j][i] for i in range(len(ids)) for j in range(len(ids))),
              "available_distances_finite_nonnegative": all(r["distance"] is None or math.isfinite(r["distance"]) and r["distance"] >= 0 for r in pairs),
              "directed_rows_equal_twice_available_pairs": len(ranked) == 2 * sum(r["distance"] is not None for r in pairs),
              "m2_neighbor_order_matches": graph_ranks_match,
              "m2_max_distance_residual": max(residuals, default=0),
              "m2_directed_edges_checked": len(residuals)}
    if not all(checks[k] for k in ("matrix_symmetric", "available_distances_finite_nonnegative", "directed_rows_equal_twice_available_pairs", "m2_neighbor_order_matches")) or checks["m2_max_distance_residual"] > 1e-9:
        raise AssertionError(checks)
    report = {"purpose": "exploratory_geometry_audit_only", "final_neighbors_selected": False,
              "source_workbook": str(workbook.resolve()), "source_sha256": context.part_data.source_sha256,
              "facts_fingerprint": context.facts_fingerprint, "config": cfg,
              "distance_method": "Existing M2 analytic shortest recorded curve-boundary distance; not filled-area distance or centroid distance",
              "units": "source coordinate units (no unit conversion)",
              "pair_semantics": "Operation pairs, including separate Operations of the same CAD feature; no merging",
              "sort": f"distance rounded to {DISTANCE_SORT_DECIMALS} decimals then operation_id; raw distances retained",
              "zero_policy": "0/0 and positive/0 undefined: null ratio plus explicit status, never epsilon",
              "operation_count": len(ids), "geometry_operation_count": len(primitives),
              "missing_geometry_operations": [op for op in ids if op not in primitives],
              "all_unordered_pairs": len(pairs), "available_unordered_pairs": sum(r["distance"] is not None for r in pairs),
              "zero_distance_unordered_pairs": sum(r["distance"] == 0 for r in pairs),
              "directed_ranked_rows": len(ranked), "gap_status_counts": dict(Counter(r["gap_status"] for r in ranked)),
              "threshold_summary": summary, "verification": checks,
              "example_rows": [r for op in cfg["example_operation_ids"] for r in ranked
                               if r["operation_id"] == op and r["rank"] <= cfg["example_rank_count"]]}
    report["flanging_first_boundary_counts"] = {
        str(threshold): dict(Counter("none" if r["first_candidate_cut"] is None else str(r["first_candidate_cut"])
                                    for r in candidates if r["threshold"] == threshold
                                    and ops[r["operation_id"]].operation_type == "FLANGING"))
        for threshold in cfg["candidate_ratios"]}
    report["operations_with_zero_to_positive_transition"] = len({r["operation_id"] for r in ranked if r["gap_status"] == "ZERO_TO_POSITIVE"})
    report["operations_with_baseline_tie"] = len({r["operation_id"] for r in candidates if r["baseline_splits_tie"]})
    for name, content in (("distance_matrix.json", {"operation_ids": ids, "distances": matrix}),
                          ("operation_catalogue.json", catalog), ("ranked_neighbors.json", ranked),
                          ("threshold_candidates.json", candidates), ("analysis_report.json", report)):
        (output / name).write_text(json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    write_csv(output / "all_operation_pairs.csv", pairs, list(pairs[0]))
    write_csv(output / "ranked_neighbors.csv", ranked, list(ranked[0]))
    write_csv(output / "threshold_candidates.csv", candidates, list(candidates[0]))
    table = ["| 倍数 | 首个断层<5 | =5 | >5 | 无有限比值断层 | 存在多个候选断层 |", "|---:|---:|---:|---:|---:|---:|"]
    table += [f"| {s['threshold']:g} | {s['below_5']} | {s['equal_5']} | {s['above_5']} | {s['none']} | {s['multiple_cut_operations']} |" for s in summary]
    lines = ["# P001 完整距离与相对邻近审计", "", "仅作几何探索，未选定最终邻域、未启用工艺约束或候选过滤。", "",
             f"来源：`{report['source_workbook']}`；SHA-256 `{report['source_sha256']}`。",
             f"共 {len(ids)} 个 Operation，{len(primitives)} 个有 geometry；缺失：{', '.join(report['missing_geometry_operations']) or '无'}。",
             f"可计算无向 pair {report['available_unordered_pairs']}，有向排序 {len(ranked)}，零距离 pair {report['zero_distance_unordered_pairs']}。", "",
             "## 倍数对比", "", "下表只展示假设采用‘首个有限比值断层’时的数量，不代表最终保留数量。未规定如何围绕5选择多个断层。", "", *table, "",
             "## 全表读取后的判断", "",
             "- 暂保留3倍为断层检测的主候选，2倍和4倍为对照；尚不足以确定正式邻域。",
             "- 46个FLANGING中，按首个3倍断层直接截断，39个会仅保留1个邻居。自身预孔的很小间距常导致过早截断。",
             "- O066在2倍下候选切点为1、3、4；3倍和4倍均为4，验证用户给出的四邻居例子。",
             "- O031在3倍下切点为1、2：若取第一个会漏掉距离4的O196。O015在3倍下有2、4、5三个切点，第2位切断会漏掉O196。",
             "- O196在3倍下仅有第9位切点，说明需要允许超过5；这只是候选边界，不等于正式取9。",
             f"- {report['operations_with_zero_to_positive_transition']}个动作存在0到正距离转换；{report['operations_with_baseline_tie']}个动作的第5位落在并列距离组内。",
             "- O025在3倍下没有有限比值断层，显示长折弯的处理仍需单独核查。",
             "- 因此不能将‘首个3倍跳变’接入生产。下一步需确定围绕5选择断层的办法、零距离及无断层策略，并在其它零件复核。", "",
             "## 数据解释", "", "- 全部排序包含所有可计算邻居，未限制为k=5/k=8。",
             "- 零距离表示记录曲线相交或相切等，不自动等于工艺冲突。0到正值单独报告，不当作无限倍，也未加epsilon。",
             "- 相同9位小数距离桶不拆分；baseline落在并列组内会单独标记。",
             "- 缺少geometry的pair留空，不以0代替。原始坐标单位未转换。",
             "- Operation是表格单位；同CAD特征的PRE_PUNCH与FLANGING仍为不同Operation。",
             "- 长折弯可能同时靠近许多动作，邻域为有向关系，不能推导同组成员两两紧邻或自动取传递闭包。",
             "- 原始数据、Graph0/1/2、约束、评分、候选、盲化包均未修改。", "",
             "## 待定", "", "3倍仍只是初始候选。最终规则需要明确：多个断层如何选、无断层如何处理、零距离组如何处理、绝对/局部尺度是否需要、是否双向对称化。不得据本表自动禁止同序。", ""]
    (output / "ANALYSIS.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("operation_count", "geometry_operation_count", "available_unordered_pairs", "directed_ranked_rows", "threshold_summary", "verification")}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=ROOT / "data/parts/P001/钣金加工数据表_P001.xlsx")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/relative_proximity_audit")
    parser.add_argument("--config", type=Path, default=ROOT / "configs/relative_proximity_audit.json")
    args = parser.parse_args()
    run(args.workbook, args.output, args.config)
