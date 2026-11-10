"""Build a separate blind, offline human review batch from v0.7 results."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.inference import load_inference_context
from src.review.reference_free_pack import (DEFAULT_CONFIG, load_pack_config, load_sampling_batch,
                                           design_comparisons, prepare_views)
from src.review.blinding import blank_comparison_form, leakage_findings
from src.review.report import safe_write, write_csv
from src.visualization.review_visualization import render_visual_page, audit_visual_pack
from src.visualization.preview import preview_svg, preview_png


def write_json(path, value, root):
    safe_write(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", root=root)


def preflight_destination(folder, root):
    folder, root = Path(folder).resolve(), Path(root).resolve()
    if not folder.is_relative_to(root/"outputs") or folder == root/"outputs":
        raise ValueError("Use a dedicated output folder")
    protected = ("m3_candidates", "m3_human_review", "m3_visual_review", "m3_visual_review_v05", "m3_reference_free_v07")
    if any(folder.is_relative_to(root/"outputs"/name) or (root/"outputs"/name).is_relative_to(folder) for name in protected):
        raise ValueError("Output aliases frozen artifacts")
    public = folder/"expert_facing"
    form = public/"comparison_pairs.csv"
    if form.exists():
        with form.open(encoding="utf-8-sig", newline="") as stream:
            if any(row.get(k, "").strip() for row in csv.DictReader(stream) for k in ("answer", "confidence", "reason_note")):
                raise ValueError("Refuse to overwrite completed human review answers")
    if public.exists():
        # Same source batch can be rebuilt only while the original form is blank.
        allowed = {"review_pack.html", "comparison_pairs.csv", "使用说明.md", "plan_cards", "previews"}
        if any(p.name not in allowed for p in public.iterdir()):
            raise ValueError("Unrecognized or downloaded human files found; preserve this batch")
    return folder


def instructions(batch, count, plans):
    return f"""# {batch} 方案评审使用说明

本包共 {count} 对比较，涉及 {plans} 个方案。请先解压整个 ZIP，再用 Chrome 或 Edge 打开 review_pack.html；保留 plan_cards 和 previews 文件夹的位置。

1. 点击比较编号切换方案对，按 OP10、OP20 等工序查看 A/B。两侧工序、缩放和平移同步。
2. 可勾选“只看 A/B 分配不同的 Operation”，或按区域筛选；点击动作可查看编号和两侧分配。页面默认旋转270°，可切回原始视角。
3. 判断完整方案的合理性。差异少不代表更好；如果两者都有明显问题，请选D。不要为了凑出优劣而强选A/B。
4. 点击“填写本对判断”，选择答案和把握程度。备注可填写涉及的Operation编号、工序及原因，也可留空。
5. 随时点击“导出评审 CSV”。页面会下载 comparison_pairs_completed.csv；请将其改名为 {batch}_completed.csv 后返回。建议分段导出，最后返回最新完整版本。

| 答案 | 含义 |
| --- | --- |
| A | A更合理 |
| B | B更合理 |
| C | 两者基本等价 |
| D | 两者都存在明显问题 |
| E | 当前信息不足，无法判断 |

把握程度1–5：数字越大，表示对本次判断越有把握。未完成项保留空白即可，不要填猜测答案。

页面尝试在当前浏览器本地暂存；它不会自动改写随包的 comparison_pairs.csv，也不会联网提交。关闭或更换浏览器前务必导出CSV。原CSV也可作为备用填写表，方案编号和左右方向必须保持原样。

previews 中的SVG/PNG用于快速预览，完整比较和填写请使用HTML。plan_cards可单独查看方案，主页面也提供相应链接。

二维展开图用于辅助判断，未表达的三维姿态或工具条件请在备注说明，信息不足可选E。本包没有预填判断或把握程度。
"""


def build_pack(context, source, output, config, root=ROOT):
    output = preflight_destination(output, root)
    plans, selected, local = load_sampling_batch(context, source)
    mapping, comparisons, design = design_comparisons(context, plans, selected, local, config)
    catalog, views, rows, visual_config = prepare_views(context, plans, mapping, comparisons, root)
    public, private = output/"expert_facing", output/"internal"
    internal_ids = list(plans)
    safe_write(public/"review_pack.html", render_visual_page(catalog, views, rows, visual_config), root=output)
    for blind, view in sorted(views.items()):
        safe_write(public/f"plan_cards/{blind}.html", render_visual_page(catalog, {blind: view}, [], visual_config, single_plan=blind), root=output)
        write_csv(public/f"plan_cards/{blind}.csv", ("operation_id", "operation_type", "assigned_station"),
                  [{k: row[k] for k in ("operation_id", "operation_type", "assigned_station")} for row in view["operations"].values()], root=output)
    write_csv(public/"comparison_pairs.csv", ("comparison_id", "plan_A", "plan_B", "answer", "confidence", "reason_note"),
              blank_comparison_form(rows), root=output)
    safe_write(public/"使用说明.md", instructions(config["batch_id"], len(rows), len(views)), root=output)
    write_json(private/"blind_plan_mapping.json", mapping, output)
    write_json(private/"comparison_design.json", {"config": config, "audit": design, "comparisons": comparisons}, output)
    write_json(private/"source_plans.json", plans, output)
    write_json(private/"geometry_audit.json", catalog["audit"], output)
    write_json(private/"visual_view_models.json", {"plans": views, "comparisons": rows}, output)
    write_json(private/"source_provenance.json", {"facts_fingerprint": context.facts_fingerprint,
                                                 "source_sha256": context.metadata_sha256,
                                                 "input_artifacts": {name: hashlib.sha256((Path(source)/name).read_bytes()).hexdigest()
                                                                     for name in ("selected_candidates.jsonl", "local_contrasts.json", "generation_report.json")}}, output)
    preview_records = []
    public_by_id = {row["comparison_id"]: row for row in rows}
    for group in ("WHOLE", "LOCAL"):
        candidates = [r for r in comparisons if r["group"] == group]
        for comparison in candidates[:config["previews"][group.lower()+"_count"]]:
            row = public_by_id[comparison["comparison_id"]]
            pair_views = [views[row["blind_plan_"+side]] for side in ("A", "B")]
            for station in config["previews"]["stations"]:
                title = f"{row['comparison_id']} · {station}"
                name = f"previews/{row['comparison_id']}_{station}"
                safe_write(public/(name+".svg"), preview_svg(catalog, pair_views, station, visual_config, title=title,
                                                          changed_ids=set(row["diff"]["changed_ids"])), root=output)
                png = public/(name+".png")
                if png.exists() and (png.is_symlink() or png.stat().st_nlink > 1):
                    raise ValueError("Unsafe preview alias")
                preview_png(catalog, pair_views, station, visual_config, title=title,
                            changed_ids=set(row["diff"]["changed_ids"])).save(png)
                preview_records.append({"comparison_id": row["comparison_id"], "station": station,
                                        "svg": name+".svg", "png": name+".png"})
    audit = audit_visual_pack(public, internal_ids, len(rows), len(views))
    forbidden = ("same_station_plan", "different_station_plan", "focus_pairs", "source_contrast_id", "facts_fingerprint", "RF_", "LC_")
    for path in public.rglob("*"):
        if path.is_file() and path.suffix != ".png":
            content = path.read_text(encoding="utf-8-sig")
            if any(token in content for token in forbidden):
                raise ValueError(f"Construction metadata leaked: {path}")
    if not audit["passed"]:
        raise ValueError(f"Blind disclosure audit failed: {audit['findings']}")
    archive = output/(config["batch_id"]+"_review_pack.zip")
    if archive.exists() and (archive.is_symlink() or archive.stat().st_nlink > 1):
        raise ValueError("Unsafe archive alias")
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as zipfile:
        for path in sorted(public.rglob("*")):
            if path.is_file():
                zipfile.write(path, path.relative_to(public).as_posix())
    with ZipFile(archive) as zipfile:
        expected_files = {p.relative_to(public).as_posix() for p in public.rglob("*") if p.is_file()}
        if set(zipfile.namelist()) != expected_files or zipfile.testzip() is not None:
            raise ValueError("ZIP contents do not match public artifacts")
        for name in zipfile.namelist():
            if not name.endswith(".png"):
                findings = leakage_findings(zipfile.read(name).decode("utf-8-sig"), internal_ids)
                if findings:
                    raise ValueError(f"ZIP blind leakage: {name}")
    report = {"batch_id": config["batch_id"], "schema_version": config["schema_version"], "design": design,
              "comparison_count": len(rows), "plan_count": len(views), "all_answers_blank": True,
              "hard_constraints": "H1-H7 independently checked for every source plan", "source_plans_changed": False,
              "archive": str(archive.resolve()), "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
              "blinding_audit": audit, "previews": preview_records,
              "default_rotation": visual_config["display"]["default_rotation_degrees"],
              "outline_status": catalog["audit"]["outline"]["status"], "new_model_training": False}
    write_json(private/"build_report.json", report, output)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=ROOT/"data/parts/P001/钣金加工数据表_P001.xlsx")
    parser.add_argument("--source", type=Path, default=ROOT/"outputs/m3_reference_free_v07")
    parser.add_argument("--output", type=Path, default=ROOT/"outputs/m3_review_v08")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    report = build_pack(load_inference_context(args.input), args.source, args.output, load_pack_config(args.config))
    print(json.dumps({k: report[k] for k in ("batch_id", "comparison_count", "plan_count", "archive", "outline_status")}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
