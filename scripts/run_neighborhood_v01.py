"""Build fact-only neighborhoods and independently explore local candidate moves."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.inference.input import load_inference_context
from src.graph.geometry_primitives import geometry_primitives
from src.neighborhood import (build_neighborhood, build_neighborhood_overlay, explore_neighborhood_candidates,
                              load_config, measure_geometry)
from src.visualization.config import load_visualization_config
from src.visualization.geometry_adapter import primitive_shape
from src.visualization.outline_adapter import load_display_outline
from src.visualization.svg_renderer import shape_markup, outline_markup


def write_json(folder, name, data):
    target = folder/name
    if target.exists() and (target.is_symlink() or target.stat().st_nlink > 1):
        raise ValueError("Refuse aliased output")
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def sensitivity_scenarios(context, config, evidence):
    rules = config["rules"]
    values = [("primary", config)]
    for m in rules["size_sensitivity"]:
        if m != rules["size_multiple"]:
            c = deepcopy(config); c["rules"]["size_multiple"] = m
            values.append((f"size_{m:g}", c))
    for threshold in rules["absolute_sensitivity"]:
        if not all(threshold == x for x in rules["absolute_thresholds"].values()):
            c = deepcopy(config); c["rules"]["absolute_thresholds"] = {k: threshold for k in rules["absolute_thresholds"]}
            values.append((f"absolute_{threshold:g}", c))
    result = []
    for name, c in values:
        n = build_neighborhood(context, c, evidence=evidence)
        result.append({"scenario_id": name, "fingerprint": n.fingerprint, "rules": n.snapshot["rules"],
                       "summary": n.snapshot["summary"], "operations": n.snapshot["operations"],
                       "relations": n.snapshot["relations"], "relative_groups": n.snapshot["relative_groups"]})
    primary = {r["pair_id"] for r in result[0]["relations"] if r["arcs"]}
    for r in result:
        actual = {p["pair_id"] for p in r["relations"] if p["arcs"]}
        r["added_external_pairs_vs_primary"] = sorted(actual-primary)
        r["removed_external_pairs_vs_primary"] = sorted(primary-actual)
    return result


def build_page(context, config, scenarios, run, output):
    hit_shapes = {op: [primitive_shape(p) for p in geometry_primitives(g)]
                  for op, o in context.operations.items() if (g := context.part_data.geometry_for(o)) is not None}
    geometries = {op: "".join(shape_markup(p) for p in shapes) for op, shapes in hit_shapes.items()}
    visual = load_visualization_config()
    part_id = context.part_data.operations[0].part_id if context.part_data.operations else ""
    outline, audit = load_display_outline(visual, ROOT, part_id)
    payload = {"scenarios": scenarios, "geometries": geometries, "hit_shapes": hit_shapes,
               "outline_markup": outline_markup({"display_outline": outline}, load_visualization_config()),
               "view": config["view"], "candidates": run,
               "stations": [{"station_id": s.station_id, "order": s.order} for s in context.ordered_stations]}
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace("&", "\\u0026")
    template = (ROOT/"src/visualization/neighborhood_review.html").read_text(encoding="utf-8")
    (output/"neighborhood_review.html").write_text(template.replace("__PAYLOAD__", encoded), encoding="utf-8")
    return audit


def report(output, context, neighborhood, scenarios, run):
    s, g = neighborhood.snapshot["summary"], run["summary"]
    lines = ["# 几何邻域 v0.1 与候选局部调整", "", "独立试验版本；不属于可直接发送的专家盲评包。", "",
             "## 规则与结果", "",
             "主动类型：FLANGING、BEND、IRREGULAR_PUNCH。自身 PRE_PUNCH—FLANGING 内部关系单列。",
             "圆形相关关系采用3倍记录直径；圆心到有限折弯段、圆心距离、轮廓距离分别标识。",
             "无孔径三种组合暂用10源坐标单位，5/10/15对照。P001按既有坐标毫米口径观察；新零件须确认相同单位，系统不猜测或自动换算。",
             "这些参数均为试验值，不是已验证安全间距。相对断层仅在同anchor/family/metric的范围内排序，使用9位数值桶；首个有限≥3倍断层分优先/扩展，不删后者。零到正数不计算倍率，不拆相同距离。",
             "两翻孔尺度分别判断；任一侧通过即保留关系，触发侧及方向完整保留。简单孔不主动凑邻居；待定类型及缺少几何有独立状态。", "",
             f"Operation {s['operations']}，有效几何 {s['geometry_operations']}，完整几何组合 {s['all_geometry_pairs']}。",
             f"内部关系 {s['internal_pairs']}；外部邻域 {s['external_pairs']}；主动方向 {s['directed_external_arcs']}。",
             f"层级计数（方向）：{s['tier_counts']}。外部组合：{s['external_family_counts']}。", "",
             "## 参数对照", "", "| 场景 | 外部关系 | 相比主场景新增 | 移除 |", "|---|---:|---:|---:|"]
    for scene in scenarios:
        lines.append(f"| {scene['scenario_id']} | {scene['summary']['external_pairs']} | {len(scene['added_external_pairs_vs_primary'])} | {len(scene['removed_external_pairs_vs_primary'])} |")
    lines += ["", "## 候选探索", "",
              f"基础池 {g['base_pool_count']}，选取基础方案 {g['base_plans_explored']}；局部尝试 {g['local_trials']}，成功 {g['successful_trials']}。",
              f"合并去重后 {g['pool_count']} 个候选，多样性选取 {g['selected_count']} 个，未标注局部对照 {g['unlabeled_comparisons']} 对。",
              "同序时尝试分序、分序时尝试同序；分别移动一端并固定另一端，允许其它动作按硬依赖连带移动；若两分支均未找到解，再试两端均可动。",
              "目标仅为该分支最少移动数。OPTIMAL才声称最小性；INFEASIBLE/UNKNOWN分别报告。全部移动、受影响的其它邻域关系、求解种子和预算可追踪。",
              "先后已强制分序的关系不采样无意义的同序对照，记录排除原因。采样按组合/邻域层级/当前同分序分层，有预算遗漏清单。",
              "最终仍用候选间farthest-first；没有邻域质量分、没有SoftPenalty预筛，没有训练或偏好标签。选中的方案仍可能包含专家不认可的局部安排。", "",
              "## 页面与文件", "",
              "neighborhood_review.html：全部动作、内部/外部邻域、优先/扩展、五种参数场景、真实轮廓/几何、270°视角、点击、缩放与聚焦。",
              "候选模式可选择一次局部尝试，对照基础/变体与Station过滤；这是内部可解释页面，含构造角色，不作为盲评材料。",
              "neighborhood_context.json：完整范围判断与关系证据；sensitivity.json：参数对照。",
              "candidate_exploration.json：生成审计、所有变体及未标注对照；candidate_pool.jsonl / selected_candidates.jsonl：新版本候选。",
              "candidate_neighborhood_overlays.json：每方案同序、Station rank方向/间隔及局部移动造成的其它关系变化。其统计不作分数。", "",
              "## 冻结与限制", "",
              f"事实指纹 {context.facts_fingerprint}。推理事实对象不提供专家方案，来源文件hash仅审计。",
              "已有Graph0/1/2、H1–H7、P1–P3、Excel、候选池、盲号及比较顺序不修改。旧R02包仍暂停；本页未修复或替换旧包。",
              "DIMPLE/BRIDGE主动类型未定，缺少geometry不代表无需邻域；本版本不声明所有类型最终规则齐备。",
              "10源单位及相对断层策略仍需更多零件和真实反馈验证。没有把轮廓接触直接解释为实际干涉，没有新增硬/软规则，没有训练模型。",
              "完整测试与冻结文件校验另见verification.json、test_results.txt及browser_qa.json。历史全套测试的已知快照失败须单独披露。", ""]
    (output/"REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT/"data/parts/P001/钣金加工数据表_P001.xlsx")
    parser.add_argument("--config", type=Path, default=ROOT/"configs/neighborhood_v01.json")
    parser.add_argument("--output", type=Path, default=ROOT/"outputs/neighborhood_v01")
    parser.add_argument("--evidence", type=Path, help="Optional compatible fact-only geometry cache; not required on new parts")
    args = parser.parse_args()
    folder = args.output.resolve()
    if not folder.is_relative_to((ROOT/"outputs").resolve()) or folder == (ROOT/"outputs").resolve():
        raise ValueError("Use a dedicated directory under outputs")
    folder.mkdir(parents=True, exist_ok=True)
    owner = folder/"run_metadata.json"
    if any(p.name not in {"baseline_manifest.json", "baseline_exclusions.json", "build.log", "build_errors.log"} for p in folder.iterdir()) and not owner.exists():
        raise ValueError("Refuse to overwrite unrelated artifacts")
    if owner.exists() and json.loads(owner.read_text(encoding="utf-8"))["schema_version"] != "neighborhood.v0.1":
        raise ValueError("Output owner mismatch")
    config = load_config(args.config)
    context = load_inference_context(args.input)
    write_json(folder, "run_metadata.json", {"schema_version": config["schema_version"], "run_state": "building"})
    progress = lambda e: print(json.dumps(e, ensure_ascii=False), flush=True)
    evidence = json.loads(args.evidence.read_text(encoding="utf-8")) if args.evidence else measure_geometry(context, config, progress=progress)
    neighborhood = build_neighborhood(context, config, evidence=evidence)
    scenes = sensitivity_scenarios(context, config, evidence)
    run = explore_neighborhood_candidates(context, neighborhood, config, progress=progress)
    write_json(folder, "neighborhood_context.json", {"fingerprint": neighborhood.fingerprint, **neighborhood.to_dict()})
    write_json(folder, "sensitivity.json", scenes)
    write_json(folder, "candidate_exploration.json", run)
    for name, rows in (("candidate_pool.jsonl", run["pool"]), ("selected_candidates.jsonl", run["selected"])):
        target = folder/name
        if target.exists() and (target.is_symlink() or target.stat().st_nlink > 1):
            raise ValueError("Refuse aliased output")
        target.write_text("".join(json.dumps(r, ensure_ascii=False, separators=(",", ":"))+"\n" for r in rows), encoding="utf-8")
    overlays = {r["plan_hash"]: build_neighborhood_overlay(context, neighborhood, r["assignments"]) for r in run["pool"]}
    changes = []
    for trial in run["trials"]:
        if "alternative" in trial:
            before = {r["pair_id"]: r for r in overlays[trial["base_plan_hash"]]["relations"]}
            after = overlays[trial["alternative"]["plan_hash"]]["relations"]
            changes.append({"trial_id": trial["trial_id"], "changed_relation_states": [
                {"pair_id": r["pair_id"], "before": before[r["pair_id"]], "after": r} for r in after if r != before[r["pair_id"]]]})
    write_json(folder, "candidate_neighborhood_overlays.json", {"by_plan_hash": overlays, "local_change_impacts": changes})
    outline_audit = build_page(context, config, scenes, run, folder)
    write_json(folder, "run_metadata.json", {"schema_version": config["schema_version"], "run_state": "complete", "source_workbook": str(args.input.resolve()),
        "source_sha256_provenance_only": context.part_data.source_sha256, "facts_fingerprint": context.facts_fingerprint,
        "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
        "evidence_cache": {"path": str(args.evidence.resolve()), "sha256": hashlib.sha256(args.evidence.read_bytes()).hexdigest()} if args.evidence else None,
        "outline_audit_display_only": outline_audit, "expert_used": False})
    report(folder, context, neighborhood, scenes, run)
    progress({"neighborhood": neighborhood.snapshot["summary"], "candidates": run["summary"]})


if __name__ == "__main__":
    main()
