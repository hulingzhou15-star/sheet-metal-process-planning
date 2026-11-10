"""Build the independent all-operation geometry evidence review."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.inference.input import load_inference_context
from src.proximity_review.analysis import build_evidence
from src.visualization.config import load_visualization_config
from src.visualization.geometry_adapter import primitive_shape
from src.visualization.outline_adapter import load_display_outline
from src.visualization.svg_renderer import shape_markup, outline_markup


def write_report(data, output):
    summary = data["summary"]
    source = data["source"]
    lines = ["# P001 二维图与几何审查交付报告", "", "日期：2026-10-16。", "",
             "## 完成内容", "",
             f"为{summary['operations']}个Operation记录覆盖状态；{summary['geometry_operations']}个有geometry，{summary['all_geometric_pairs']}组两两几何证据。",
             "每组带真实曲线上的最近位置、图元索引、原始边界距离和已有工艺关系。页面可按任一Operation查询，包含全部59个折弯和38个异形孔。",
             "主动规则尚不完整；未将有距离、曲线接触或缺少几何解释为已确认的工艺干涉/无需邻居。", "",
             "## 组合覆盖", "", "| 组合 | 无向组合数 | 当前方法 |", "|---|---:|---|"]
    descriptions = {"BEND_CIRCULAR": "圆心到有限线段，孔径2/3/4倍对照；保留旧轮廓口径",
                    "IRREGULAR_CIRCULAR": "轮廓最近点；使用圆形动作记录直径作尺度",
                    "BEND_IRREGULAR": "解析最近点与双向轮廓采样距离分布；长度阈值待定",
                    "IRREGULAR_IRREGULAR": "同上；长度阈值待定",
                    "BEND_BEND": "线段距离、夹角、双侧投影重叠及已有先后；阈值待定",
                    "CIRCULAR_CIRCULAR": "翻孔为中心的尺寸试验；无翻孔的组合仅保留证据",
                    "PENDING_TYPE_POLICY": "有距离与最近点，主动类型政策待定"}
    for family, count in summary["pair_family_counts"].items():
        lines.append(f"| {family} | {count} | {descriptions[family]} |")
    lines += ["", "## 四个局部图的核对", "",
              "1. O025—O066：边界距离1.5，圆心到有限折弯段3.4，最近投影在段内；在翻孔外圆直径3.8的3倍范围内。",
              "2. O031—O196：轮廓距离4，在3倍记录外径范围内；没有因自身预孔距离过小而截断。",
              "3. O025—O196：展开曲线有交点。红点标出一个交点，不是所有交点清单；沿整条长折弯的采样中位距离约142，说明最短距离与全轮廓距离分布是不同信息。实际干涉仍待判断。",
              "4. O009—O010：两条长度8的折弯平行，距离9.6，两侧投影重叠均为8；已有O010→O009先后关系保留。未据此新增约束。", "",
              "页面有全部原始几何上下文、背景外轮廓、可选Sheet5 region、局部/完整特征聚焦、270°显示、缩放平移和最近位置标注。已核对上述四个典型局部图，没有声称逐张视觉判断全部22155组。", "",
              "## 内部与外部", "",
              f"{summary['internal_pre_punch_flanging_pairs']}组明确的自身预孔—翻孔单列，不参与外部断层排序；CAD parent不自动当作内部组。",
              "外部完整排序可切换显示全部，前20名只是界面显示限制。相对断层不自动截断。", "",
              "## 方法差异与尚未确定", "",
              f"孔中心到折弯段与旧轮廓口径在3倍尺度下有{summary['center_vs_legacy_bend_gate_changes_at_primary']}组结果不同；本轮仅对照，不回写历史试验。",
              f"按主试验口径，范围内{summary['pair_status_counts'].get('WITHIN_TRIAL_RANGE',0)}组（含内部工艺组），范围外{summary['pair_status_counts'].get('OUTSIDE_TRIAL_RANGE',0)}组，未决{summary['pair_status_counts'].get('UNRESOLVED',0)}组。这些不是工艺质量标签。",
              "三个无孔径关系的长度阈值均为null。UI允许临时输入观察，但不保存到配置、JSON或生产逻辑。",
              "仍需确定：无孔径组合的有效尺度、不同动作类型的工艺意义、相对断层如何使用，以及更多零件上的适用性。", "",
              "## 校验", "",
              f"最近点长度与M2权威距离的构建期最大偏差为{summary['numeric_max_witness_residual']:.3g}（纯数值误差）。",
              "独立源曲线核验见verification.json，测试明细见test_results.txt，真实Edge页面检查见browser_qa.json。",
              "所有原有文件的SHA核验见verification.json和baseline_manifest.json；Office临时锁文件的排除记录另存。", "",
              "## 新增文件", "",
              "- configs/proximity_geometry_review.json",
              "- src/proximity_review/__init__.py、witness.py、analysis.py",
              "- src/visualization/proximity_geometry_review.html",
              "- scripts/build_proximity_geometry_review.py、verify_proximity_geometry_review.py",
              "- tests/test_proximity_geometry_review.py、proximity_geometry_browser_qa.cjs",
              "- docs/PROXIMITY_GEOMETRY_REVIEW.md",
              "- outputs/proximity_geometry_review/（证据、页面、四张局部预览及验证记录）", "",
              "## 输入与边界", "", f"原始Excel：`{source['workbook']}`。",
              f"SHA-256：`{source['source_sha256']}`。",
              "InferenceContext不暴露专家分配。候选、评分、训练、Graph、约束、Excel和历史盲化包均未接入或改写。",
              "方法与重跑入口见docs/PROXIMITY_GEOMETRY_REVIEW.md。", ""]
    (output / "REVIEW_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def build(workbook, audit, output, config):
    context = load_inference_context(workbook)
    previous = json.loads((audit / "analysis_report.json").read_text(encoding="utf-8"))
    if previous["source_sha256"] != context.part_data.source_sha256 or previous["facts_fingerprint"] != context.facts_fingerprint:
        raise ValueError("Distance audit does not match current facts; no stale reuse allowed")
    matrix = json.loads((audit / "distance_matrix.json").read_text(encoding="utf-8"))
    data, primitives = build_evidence(context, matrix, config, progress=lambda x: print(json.dumps(x), flush=True))
    data["source"] = {"workbook": str(workbook.resolve()), "source_sha256": context.part_data.source_sha256,
                      "facts_fingerprint": context.facts_fingerprint, "matrix_sha256": hashlib.sha256((audit / "distance_matrix.json").read_bytes()).hexdigest()}
    visual = load_visualization_config()
    outline, outline_audit = load_display_outline(visual, ROOT, context.part_data.operations[0].part_id)
    data["outline_audit"] = outline_audit
    output.mkdir(parents=True, exist_ok=True)
    (output / "geometry_evidence.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    write_report(data, output)
    geometries = {op: "".join(shape_markup(primitive_shape(p)) for p in shapes) for op, shapes in primitives.items()}
    payload = json.dumps({"evidence": data, "geometries": geometries,
                          "outline_markup": outline_markup({"display_outline": outline}, visual)}, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace("&", "\\u0026")
    template = (ROOT / "src/visualization/proximity_geometry_review.html").read_text(encoding="utf-8")
    (output / "geometry_review.html").write_text(template.replace("__PAYLOAD__", payload), encoding="utf-8")
    print(json.dumps(data["summary"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=ROOT / "data/parts/P001/钣金加工数据表_P001.xlsx")
    parser.add_argument("--distance-audit", type=Path, default=ROOT / "outputs/relative_proximity_audit")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/proximity_geometry_review")
    parser.add_argument("--config", type=Path, default=ROOT / "configs/proximity_geometry_review.json")
    args = parser.parse_args()
    build(args.workbook, args.distance_audit, args.output, json.loads(args.config.read_text(encoding="utf-8")))
