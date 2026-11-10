"""Separate geometry experiment; never changes production graphs or plans."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.analyze_relative_proximity import adjacent_gap
from src.graph.builders import DISTANCE_SORT_DECIMALS, build_part_graph
from src.graph.geometry_primitives import Arc, geometry_primitives
from src.inference.input import load_inference_context
from src.visualization.geometry_adapter import primitive_shape
from src.visualization.svg_renderer import shape_markup


def circular_profile(primitives):
    """Exact recorded concentric circles only; no inferred centers or diameters."""
    if not primitives or not all(isinstance(p, Arc) and p.full_circle for p in primitives):
        return None
    center = primitives[0].center
    if any(p.center != center for p in primitives):
        return None
    diameter = 2 * max(p.radius for p in primitives)
    if diameter <= 0:
        return None
    return {"center": list(center), "diameter": diameter,
            "recorded_radii": sorted({p.radius for p in primitives})}


def measure_pair(anchor, target, boundary_distance):
    """Mixed metrics are explicit: circle centers vs recorded noncircle curves."""
    if boundary_distance is None:
        return {"metric": "UNAVAILABLE", "measurement": None, "normalized_distance": None}
    if target is not None:
        measurement = math.dist(anchor["center"], target["center"])
        metric = "CIRCLE_CENTER_DISTANCE"
    else:
        measurement, metric = boundary_distance, "NONCIRCULAR_BOUNDARY_DISTANCE"
    return {"metric": metric, "measurement": measurement,
            "normalized_distance": measurement / anchor["diameter"]}


def within_gate(measurement, diameter, multiple):
    if not math.isfinite(diameter) or diameter <= 0 or not math.isfinite(multiple) or multiple <= 0:
        raise ValueError("A positive finite diameter and multiple are required")
    if measurement is None:
        return False
    if not math.isfinite(measurement) or measurement < 0:
        raise ValueError("A finite nonnegative distance is required")
    # Frozen M2 numerical buckets, not an added manufacturing clearance.
    return round(measurement, DISTANCE_SORT_DECIMALS) <= round(multiple * diameter, DISTANCE_SORT_DECIMALS)


def relative_candidates(rows, ratio):
    """All jump locations in the absolute gate, no automatic truncation."""
    cuts, ordered = [], sorted(rows, key=lambda r: (round(r["boundary_distance"], DISTANCE_SORT_DECIMALS), r["target_id"]))
    for rank, (a, b) in enumerate(zip(ordered, ordered[1:]), 1):
        value, status = adjacent_gap(a["boundary_distance"], b["boundary_distance"])
        if value is not None and value >= ratio:
            cuts.append({"after_rank": rank, "left_id": a["target_id"], "right_id": b["target_id"],
                         "left_boundary_distance": a["boundary_distance"], "right_boundary_distance": b["boundary_distance"],
                         "ratio": value, "status": status})
    return {"ordered_ids": [r["target_id"] for r in ordered], "candidate_cuts": cuts,
            "final_relative_cut_selected": False}


def build_trial(context, matrix, config):
    if config["anchor_types"] != ["FLANGING"]:
        raise ValueError("This trial only supports the explicitly scoped FLANGING anchors")
    supported_metrics = {"circle_diameter": "largest_recorded_concentric_circle",
                         "circular_target_metric": "center_distance",
                         "noncircular_target_metric": "recorded_curve_boundary_distance"}
    if any(config[key] != value for key, value in supported_metrics.items()):
        raise ValueError("Unsupported metric configuration: this trial cannot silently change distance definitions")
    multiples = config["comparison_size_multiples"]
    if config["primary_size_multiple"] not in multiples or not multiples or any(not math.isfinite(m) or m <= 0 for m in multiples):
        raise ValueError("Primary size multiple must be among positive finite comparison multiples")
    if not math.isfinite(config["relative_jump_ratio"]) or config["relative_jump_ratio"] <= 1:
        raise ValueError("Relative jump ratio must exceed one")
    ids = matrix["operation_ids"]
    if ids != sorted(context.operations):
        raise ValueError("Distance matrix operation ids differ from current facts")
    distances = matrix["distances"]
    ops, primitives, profiles, catalogue = context.operations, {}, {}, []
    for op in ids:
        obj = ops[op]
        geometry = context.part_data.geometry_for(obj)
        if geometry is not None:
            primitives[op] = geometry_primitives(geometry)
            profiles[op] = circular_profile(primitives[op])
        catalogue.append({"operation_id": op, "operation_type": obj.operation_type,
                          "source_feature_ref": obj.source_feature_ref,
                          "geometry_id": obj.geometry_id, "circular_profile": profiles.get(op),
                          "has_geometry": op in primitives,
                          "active_anchor": obj.operation_type in config["anchor_types"]})
    anchors, records = [], []
    for i, anchor_id in enumerate(ids):
        obj = ops[anchor_id]
        if obj.operation_type not in config["anchor_types"]:
            continue
        profile = profiles.get(anchor_id)
        if profile is None:
            anchors.append({"anchor_id": anchor_id, "status": "UNSUPPORTED_ANCHOR_GEOMETRY"})
            continue
        group = []
        neighbor_indices = sorted((j for j in range(len(ids)) if j != i and distances[i][j] is not None),
                                  key=lambda j: (round(distances[i][j], DISTANCE_SORT_DECIMALS), ids[j]))
        ranks = {ids[j]: rank for rank, j in enumerate(neighbor_indices, 1)}
        for j, target_id in enumerate(ids):
            if target_id == anchor_id:
                continue
            target_obj = ops[target_id]
            measurement = measure_pair(profile, profiles.get(target_id), distances[i][j])
            same_group = bool(obj.process_group_id and obj.process_group_id == target_obj.process_group_id)
            row = {"anchor_id": anchor_id, "target_id": target_id, "target_type": target_obj.operation_type,
                   "target_feature_ref": target_obj.source_feature_ref,
                   "anchor_diameter": profile["diameter"], "boundary_distance": distances[i][j],
                   "full_boundary_rank": ranks.get(target_id), **measurement,
                   "same_process_group": same_group,
                   "own_pre_punch": same_group and target_obj.operation_type == "PRE_PUNCH",
                   "passes": {f"{m:g}": within_gate(measurement["measurement"], profile["diameter"], m) for m in multiples}}
            records.append(row)
            group.append(row)
        scenarios = []
        for multiple in multiples:
            passed = [r for r in group if r["passes"][f"{multiple:g}"]]
            relative = relative_candidates(passed, config["relative_jump_ratio"])
            scenarios.append({"size_multiple": multiple, "absolute_limit": profile["diameter"] * multiple,
                              "passed_count": len(passed), "passed_ids": relative["ordered_ids"],
                              "passed_type_counts": dict(Counter(r["target_type"] for r in passed)),
                              "only_own_pre_punch": bool(passed) and all(r["own_pre_punch"] for r in passed),
                              "beyond_baseline_rank_count": sum(r["full_boundary_rank"] > config["baseline_neighbor_count"] for r in passed),
                              "relative_boundary_analysis": relative})
        anchors.append({"anchor_id": anchor_id, "status": "SUPPORTED", **profile, "scenarios": scenarios})
    summary = []
    for multiple in multiples:
        scenarios = [s for a in anchors if a["status"] == "SUPPORTED" for s in a["scenarios"] if s["size_multiple"] == multiple]
        summary.append({"size_multiple": multiple, "supported_anchors": len(scenarios),
                        "admitted_directed_pairs": sum(s["passed_count"] for s in scenarios),
                        "count_histogram": dict(sorted(Counter(s["passed_count"] for s in scenarios).items())),
                        "only_own_pre_punch_anchors": sum(s["only_own_pre_punch"] for s in scenarios),
                        "zero_neighbor_anchors": sum(s["passed_count"] == 0 for s in scenarios),
                        "multiple_relative_cut_anchors": sum(len(s["relative_boundary_analysis"]["candidate_cuts"]) > 1 for s in scenarios)})
    return {"config": config, "anchors": anchors, "pair_records": records,
            "operation_catalogue": catalogue, "summary": summary}, primitives


def write_report(output, trial):
    cfg, primary = trial["config"], trial["config"]["primary_size_multiple"]
    lines = ["# 翻孔尺寸范围与相对邻近试验", "",
             "本轮为独立探索。范围内只表示几何接近，不是冲突真值、评分项或禁止同序规则。最终相对断层尚未选择。", "",
             "## 试验口径", "",
             "- 主动中心只启用FLANGING，不给CIRCLE_PUNCH强制找邻居；其它有几何动作均可作为被检查对象。",
             "- D取翻孔记录的最大同心圆直径，不取自身预孔直径；不推断模具直径或实际工艺影响半径。",
             "- 圆形几何对象按圆心距离 <= mD。其它对象按既有M2曲线边界距离 <= mD。",
             "- 非圆对象暂试相同m，属未经标定的试验参数。两种距离口径明确分开，不声明同一个物理影响圆。",
             "- m=2/3/4为敏感性对比，主展示m=3。邻域有方向，不自动对称化、不传递合并。",
             "- 通过尺寸范围后，以原始边界距离排序并列出3倍相对跳变；不自动取首断层，也不凑满5个。",
             "- 缺失geometry不代入0，非同心/非圆翻孔不猜中心，0距离不作除法或epsilon替代。", "",
             "阈值比较沿用M2的9位小数数值桶，仅处理浮点边界，不代表工艺容差。", "",
             "## 数量对比", "", "| 尺寸倍数 | 几何通过pair | 只含自身预孔的翻孔数 | 邻居数分布（数量:翻孔数） |",
             "|---:|---:|---:|---|"]
    for s in trial["summary"]:
        lines.append(f"| {s['size_multiple']:g} | {s['admitted_directed_pairs']} | {s['only_own_pre_punch_anchors']} | {s['count_histogram']} |")
    lines += ["", "## 主参数重点结果", "", "| 翻孔 | 记录外径D | 3D长度 | 范围内Operation（按边界距离排序） |", "|---|---:|---:|---|"]
    for op in cfg["examples"]:
        a = next(a for a in trial["anchors"] if a["anchor_id"] == op)
        s = next(s for s in a["scenarios"] if s["size_multiple"] == primary)
        lines.append(f"| {op} | {a['diameter']:g} | {s['absolute_limit']:g} | {', '.join(s['passed_ids']) or '无'} |")
    hole = cfg["simple_hole_example"]
    incoming = [r["anchor_id"] for r in trial["pair_records"] if r["target_id"] == hole and r["passes"][f"{primary:g}"]]
    lines += ["", f"{hole} 不作为主动中心；主参数下被哪些翻孔纳入：{', '.join(incoming) or '无'}。", "",
              "## 观察与未决项", "",
              "- O184仅保留O185；O066保留O067、O025、O200、O065，符合用户这两组已描述的局部结构。",
              "- O015/O031/O035都保留O196，未被早期相对倍数截断误删。O031范围内仍有自身预孔到O196的3倍以上跳变，故不能自动再删掉O196。",
              "- O180还纳入O008（DIMPLE），说明尺寸范围并未自动解决动作类型的工艺意义，需要单独判断。",
              "- 本试验记录的是当前展开几何。没有模具包络、材料运动、三维成形干涉数据，不能称为已验证干涉检测。",
              "- 仍需决定动作类型作用、非圆对象的尺度、相对断层是否继续筛选，以及更多零件上的适用性。",
              "- 不修改既有Graph0/1/2、约束、评分、候选、reference、盲化或历史评审包。", "",
              "## 来源", "", f"原始工作簿：`{trial['source']['workbook']}`。",
              f"SHA-256：`{trial['source']['sha256']}`。",
              "完整边界距离复用上一轮audit的矩阵，先核对事实指纹与工作簿SHA；本轮不读取专家分配。", "",
              "参数入口：`configs/relative_proximity_trial.json`。",
              "运行：`.venv-m3/Scripts/python.exe -B scripts/try_scale_aware_proximity.py`。", ""]
    (output / "TRIAL_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def run(workbook, audit, output, config_path):
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    context = load_inference_context(workbook)
    audit_report = json.loads((audit / "analysis_report.json").read_text(encoding="utf-8"))
    if context.facts_fingerprint != audit_report["facts_fingerprint"] or context.part_data.source_sha256 != audit_report["source_sha256"]:
        raise ValueError("Complete distance audit is stale: regenerate it explicitly first")
    matrix = json.loads((audit / "distance_matrix.json").read_text(encoding="utf-8"))
    trial, primitives = build_trial(context, matrix, cfg)
    trial.update(purpose="exploratory_geometry_only", production_integration=False,
                 final_relative_neighbors_selected=False,
                 source={"workbook": str(workbook.resolve()), "sha256": context.part_data.source_sha256,
                         "facts_fingerprint": context.facts_fingerprint,
                         "distance_audit": str(audit.resolve()),
                         "matrix_sha256": hashlib.sha256((audit / "distance_matrix.json").read_bytes()).hexdigest()})
    static = build_part_graph(context.part_data)
    edges = {(static.operation_ids[e.source_index], static.operation_ids[e.target_index]) for e in static.geometric_edges}
    primary = f"{cfg['primary_size_multiple']:g}"
    passed = [r for r in trial["pair_records"] if r["passes"][primary]]
    trial["existing_graph_coverage"] = {"frozen_graph_k": static.knn_diagnostics.k,
                                        "passed_pairs": len(passed),
                                        "missing_directed_pairs": [[r["anchor_id"], r["target_id"]] for r in passed if (r["anchor_id"], r["target_id"]) not in edges]}
    output.mkdir(parents=True, exist_ok=True)
    (output / "trial_results.json").write_text(json.dumps(trial, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    write_report(output, trial)
    geometries = {op: {"markup": "".join(shape_markup(primitive_shape(p)) for p in values)} for op, values in primitives.items()}
    payload = json.dumps({"trial": trial, "geometries": geometries}, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    template = (ROOT / "src/visualization/proximity_trial.html").read_text(encoding="utf-8")
    (output / "proximity_trial.html").write_text(template.replace("__PAYLOAD__", payload), encoding="utf-8")
    print(json.dumps({"summary": trial["summary"], "existing_graph_coverage": trial["existing_graph_coverage"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=ROOT / "data/parts/P001/钣金加工数据表_P001.xlsx")
    parser.add_argument("--distance-audit", type=Path, default=ROOT / "outputs/relative_proximity_audit")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/relative_proximity_trial")
    parser.add_argument("--config", type=Path, default=ROOT / "configs/relative_proximity_trial.json")
    args = parser.parse_args()
    run(args.workbook, args.distance_audit, args.output, args.config)
