"""M3 v0.4 presentation layer, consuming frozen v0.3 IDs/pairs verbatim."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from scripts.build_human_review_pack import read_source_batch
from scripts.generate_candidate_plans import check_integrity
from src.constraints import HardConstraintEngine, load_constraint_context
from src.data import validate
from src.review.blinding import blank_comparison_form, leakage_findings
from src.review.config import load_review_config
from src.review.report import safe_write, write_csv
from src.visualization import (build_diff_view_model, build_geometry_catalog, build_plan_view_model,
                               load_visualization_config)
from src.visualization.config import DEFAULT_CONFIG_PATH
from src.visualization.preview import preview_png, preview_svg
from src.visualization.review_visualization import audit_visual_pack, render_visual_page
from src.visualization.outline_adapter import load_display_outline
from src.visualization.display_transform import display_box,display_matrix
from src.visualization.svg_renderer import SVG_NS,outline_markup


def capture_visual_manifest(root):
    root=Path(root)
    exclude={"build_visual_review_pack.py","m3_visualization.yaml","MILESTONE_3_VISUALIZATION.md","visual_fixtures.py"}
    paths=[root/name for name in ("PROJECT_SPEC.md","README.md","requirements.txt","requirements-m3.txt") if (root/name).exists()]
    for directory in ("src","tests","scripts","configs","docs","data/parts/P001","outputs/m3_candidates","outputs/m3_diagnostics","outputs/m3_human_review"):
        for path in (root/directory).rglob("*"):
            if (path.is_file() and "__pycache__" not in path.parts and path.name not in exclude
                    and not path.name.startswith("test_m3_visual") and not path.is_relative_to(root/"src/visualization")):
                paths.append(path)
    return {path.relative_to(root).as_posix():hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(set(paths))}


def check_visual_integrity(root, manifest, acknowledged_changes=()):
    """Retain the original baseline; accept only the explicitly confirmed copy removal."""
    observed=check_integrity(root,manifest)
    accepted=[]
    canonical="outputs/m3_human_review/expert_facing/review_pack.html"
    for change in acknowledged_changes:
        path=change.get("path")
        if (path!="data/raw/review_pack.html" or change.get("change_type")!="USER_REMOVED_DUPLICATE_REVIEW_COPY"
                or change.get("before_sha256")!=manifest.get(path) or manifest.get(path)!=manifest.get(canonical)):
            raise ValueError("Acknowledgment does not describe the confirmed duplicate review copy")
        record=next(row for row in observed["files"] if row["path"]==path)
        if record["after_sha256"] is None:
            accepted.append(change)
        elif not record["unchanged"]:
            raise ValueError("Only deletion of the confirmed copy was acknowledged")
    excluded={row["path"] for row in accepted}
    effective={path:digest for path,digest in manifest.items() if path not in excluded}
    frozen=check_integrity(root,effective)
    return {**frozen,"observed_baseline_file_count":len(manifest),
            "observed_baseline_all_unchanged":observed["all_unchanged"],"user_acknowledged_changes":accepted}


def capture_v05_baseline(root):
    root=Path(root);paths=[]
    for directory in ("src","tests","scripts","configs","docs","data/parts/P001","outputs/m3_diagnostics","outputs/m3_candidates","outputs/m3_human_review","outputs/m3_visual_review"):
        paths.extend(p for p in (root/directory).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    paths.extend(root/name for name in ("PROJECT_SPEC.md","README.md","requirements.txt","requirements-m3.txt") if (root/name).exists())
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths))}


def is_authorized_visual_path(path):
    return (path.startswith("src/visualization/") or path.startswith("tests/test_m3_visual") or path in ("tests/visual_fixtures.py","tests/visual_ui_dom_harness.js","tests/visual_review_answers_harness.js")
            or path in ("configs/m3_visualization.yaml","scripts/build_visual_review_pack.py","docs/MILESTONE_3_VISUALIZATION_V05.md"))


def check_v05_integrity(root,manifest):
    protected={path:digest for path,digest in manifest.items() if not is_authorized_visual_path(path)}
    frozen=check_integrity(root,protected)
    observed=check_integrity(root,manifest)
    changes=[row for row in observed["files"] if not row["unchanged"] and is_authorized_visual_path(row["path"])]
    return {**frozen,"observed_baseline_file_count":len(manifest),"authorized_visual_changes":changes,
            "all_outside_visualization_unchanged":frozen["all_unchanged"],"user_acknowledged_changes":[]}


def load_visual_inputs(context, candidate_dir, review_dir):
    candidates, expert,_=read_source_batch(context,candidate_dir,load_review_config())
    folder=Path(review_dir)
    read=lambda name:json.loads((folder/name).read_text(encoding="utf-8"))
    mapping=read("internal/blind_plan_mapping.json")
    reps=read("internal/representative_selection.json")["representatives"]
    pairs=read("internal/comparison_pairs_internal.json")["pairs"]
    if len(reps)!=12 or len({r["internal_id"] for r in reps})!=12:
        raise ValueError("Frozen representatives are not twelve unique Plans")
    if Counter(r["bucket"] for r in reps)!={"NEAR":4,"MEDIUM":4,"FAR":4}:
        raise ValueError("Frozen representative bucket count differs")
    expected={r["internal_id"] for r in reps}|{"EXPERT_REFERENCE"}
    if len(mapping["blind_to_internal"])!=13 or set(mapping["blind_to_internal"].values())!=expected:
        raise ValueError("Frozen blind mapping differs from representatives")
    if {v:k for k,v in mapping["blind_to_internal"].items()}!=mapping["internal_to_blind"]:
        raise ValueError("Frozen blind mapping is not reversible")
    by_id={c["plan_id"]:c for c in candidates}
    plans={name:by_id[name] for name in expected if name!="EXPERT_REFERENCE"}
    plans["EXPERT_REFERENCE"]={"plan_id":"EXPERT_REFERENCE","assignments":expert}
    if len(pairs)!=24 or len({tuple(sorted((p["blind_plan_A"],p["blind_plan_B"]))) for p in pairs})!=24:
        raise ValueError("Frozen comparisons are incomplete or duplicate")
    if [p["comparison_id"] for p in pairs]!=[f"CMP_{i:03d}" for i in range(1,25)]:
        raise ValueError("Frozen comparison order differs")
    for pair in pairs:
        for side in ("A","B"):
            if mapping["blind_to_internal"].get(pair["blind_plan_"+side])!=pair["internal_plan_"+side]:
                raise ValueError("Frozen A/B orientation differs from mapping")
    with (folder/"expert_facing/comparison_pairs.csv").open(encoding="utf-8-sig",newline="") as handle:
        form=list(csv.DictReader(handle))
    if len(form)!=24 or any((r["comparison_id"],r["plan_A"],r["plan_B"])!=(p["comparison_id"],p["blind_plan_A"],p["blind_plan_B"]) for r,p in zip(form,pairs)):
        raise ValueError("Frozen public CSV differs from comparison mapping")
    for blind,internal in mapping["blind_to_internal"].items():
        with (folder/f"expert_facing/plan_cards/{blind}.csv").open(encoding="utf-8-sig",newline="") as handle:
            rows=list(csv.DictReader(handle))
        if len(rows)!=len(context.operations) or {r["operation_id"]:r["assigned_station"] for r in rows}!=plans[internal]["assignments"]:
            raise ValueError("Frozen public plan card differs from assignments")
    for rep in reps:
        if rep["plan_hash"]!=by_id[rep["internal_id"]]["plan_hash"]:
            raise ValueError("Frozen representative hash differs from candidate")
    return plans,reps,pairs,mapping


def prepare_visual_data(context, plans, reps, pairs, mapping, config):
    part_id=next(iter(context.operations.values())).part_id
    outline,outline_audit=load_display_outline(config,ROOT,part_id)
    catalog=build_geometry_catalog(context,config,display_outline=outline,outline_audit=outline_audit)
    blind_views={blind:build_plan_view_model(context,plans[internal]["assignments"],catalog,blind)
                 for blind,internal in sorted(mapping["blind_to_internal"].items())}
    comparisons=[]
    for pair in pairs:
        a=plans[pair["internal_plan_A"]]["assignments"]; b=plans[pair["internal_plan_B"]]["assignments"]
        comparisons.append({"comparison_id":pair["comparison_id"],"blind_plan_A":pair["blind_plan_A"],"blind_plan_B":pair["blind_plan_B"],
                            "diff":build_diff_view_model(context,a,b,catalog)})
    rep_by_id={r["internal_id"]:r for r in reps}
    debug={}
    for blind,internal in mapping["blind_to_internal"].items():
        rep=rep_by_id.get(internal)
        debug[blind]={"internal_id":internal,"bucket":rep["bucket"] if rep else None,
                      "role":rep["selection_role"] if rep else "EXPERT_REFERENCE",
                      "distance_to_expert":rep["distance_to_expert_raw"] if rep else 0,
                      "SoftPenalty":rep["total_soft_penalty"] if rep else None,
                      "P1/P2/P3":rep["soft_components"] if rep else "See frozen M3 v0.1 reference report"}
    return catalog,blind_views,comparisons,debug


def validate_destination(output, root):
    output,root=Path(output).resolve(),Path(root).resolve()
    if output==root/"outputs" or not output.is_relative_to(root/"outputs"):
        raise ValueError("Visual output must be a dedicated folder below outputs/")
    protected=[root/"outputs"/name for name in ("m3_candidates","m3_diagnostics","m3_human_review","m3_visual_review")]
    if any(output.is_relative_to(path) or path.is_relative_to(output) for path in protected):
        raise ValueError("Visual output aliases a frozen milestone directory")
    return output


def preflight_answers(folder):
    path=Path(folder)/"expert_facing/comparison_pairs.csv"
    if path.exists():
        with path.open(encoding="utf-8-sig",newline="") as handle:
            if any(row.get(key,"").strip() for row in csv.DictReader(handle) for key in ("answer","confidence","reason_note")):
                raise ValueError("Refusing to overwrite human-filled comparison answers")


def write_visual_pack(folder, catalog, views, comparisons, debug, config, internal_ids):
    folder=Path(folder); public=folder/"expert_facing"; private=folder/"internal"
    preflight_answers(folder)
    safe_write(public/"review_pack.html",render_visual_page(catalog,views,comparisons,config),root=folder)
    safe_write(private/"review_pack_internal.html",render_visual_page(catalog,views,comparisons,config,debug_by_blind=debug),root=folder)
    for blind in sorted(views):
        safe_write(public/f"plan_cards/{blind}.html",render_visual_page(catalog,{blind:views[blind]},[],config,single_plan=blind),root=folder)
        safe_write(private/f"plan_cards/{blind}.html",render_visual_page(catalog,{blind:views[blind]},[],config,single_plan=blind,debug_by_blind=debug),root=folder)
    forms=blank_comparison_form(comparisons)
    write_csv(public/"comparison_pairs.csv",("comparison_id","plan_A","plan_B","answer","confidence","reason_note"),forms,root=folder)
    safe_write(private/"geometry_audit.json",json.dumps(catalog["audit"],ensure_ascii=False,indent=2),root=folder)
    safe_write(private/"outline_extraction_report.json",json.dumps(catalog["audit"]["outline"],ensure_ascii=False,indent=2),root=folder)
    if catalog.get("display_outline"):
        safe_write(private/"assets/outline_display_asset.json",json.dumps(catalog["display_outline"],ensure_ascii=False),root=folder)
        for rotation in (0,270):
            box=" ".join(str(v) for v in display_box(catalog["view_box"],rotation))
            asset_svg=f'<svg xmlns="{SVG_NS}" viewBox="{box}"><g transform="{display_matrix(rotation)}">{outline_markup(catalog,config)}</g></svg>'
            safe_write(private/f"assets/outline_{rotation}.svg",asset_svg,root=folder)
    safe_write(private/"visual_view_models.json",json.dumps({"plans":views,"comparisons":comparisons},ensure_ascii=False,indent=2),root=folder)
    previews=[]; png_issues=[]
    p=config["previews"]; comparison=next((row for row in comparisons if row["comparison_id"]==p["comparison_id"]),None)
    if p["plan_id"] not in views or comparison is None or any(s not in next(iter(views.values()))["station_ids"] for s in p["stations"]):
        raise ValueError("Configured preview Plan/comparison/Station is absent")
    a,b=views[comparison["blind_plan_A"]],views[comparison["blind_plan_B"]]
    preview_jobs=[]
    for station in p["stations"]:
        preview_jobs.append((f"single_{station}",[views[p["plan_id"]]],station,{},None))
        preview_jobs.append((f"comparison_{station}",[a,b],station,{},set(comparison["diff"]["changed_ids"])))
    preview_jobs.append(("comparison_difference_only",[a,b],p["stations"][0],{"difference_only":True},set(comparison["diff"]["changed_ids"])))
    preview_jobs.append(("comparison_influence_regions",[a,b],p["stations"][-1],{"show_influence":True},set(comparison["diff"]["changed_ids"])))
    rid=next(iter(sorted(catalog["regions"])),None)
    if rid: preview_jobs.append(("comparison_region_filter",[a,b],p["stations"][-1],{"region_filter":rid,"show_influence":True},set(comparison["diff"]["changed_ids"])))
    preview_jobs.append(("comparison_original_orientation",[a,b],p["stations"][0],{"rotation_degrees":0},set(comparison["diff"]["changed_ids"])))
    for name,selected_views,station,state,changed in preview_jobs:
        label="单方案工序视图" if len(selected_views)==1 else "A/B 同步工序比较"
        if state.get("difference_only"): label="只看 A/B 差异"
        elif state.get("region_filter"): label="局部区域 "+state["region_filter"]
        elif state.get("show_influence"): label="显示影响区域"
        title=(comparison["comparison_id"] if len(selected_views)==2 else p["plan_id"])+" · "+label
        svg_path=private/f"previews/{name}.svg"
        safe_write(svg_path,preview_svg(catalog,selected_views,station,config,title=title,changed_ids=changed,**state),root=folder)
        previews.append({"name":name,"svg":str(svg_path.resolve()),"station":station,"state":state})
        try:
            png_path=svg_path.with_suffix(".png")
            if png_path.is_symlink() or (png_path.exists() and png_path.stat().st_nlink>1): raise ValueError("Unsafe PNG alias")
            preview_png(catalog,selected_views,station,config,title=title,changed_ids=changed,**state).save(png_path)
            previews[-1]["png"]=str(png_path.resolve())
        except (ImportError,OSError) as exc:
            png_issues.append({"name":name,"reason":str(exc)})
    audit=audit_visual_pack(public,internal_ids,len(comparisons),len(views))
    if not audit["passed"]: raise ValueError(f'Blind leakage: {audit["findings"]}')
    archive=folder/"plan_comparison_visual_pack.zip"
    if archive.is_symlink() or (archive.exists() and archive.stat().st_nlink>1): raise ValueError("Unsafe ZIP alias")
    with zipfile.ZipFile(archive,"w",compression=zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(public.rglob("*")):
            if path.is_file(): handle.write(path,path.relative_to(public).as_posix())
    with zipfile.ZipFile(archive) as handle:
        zip_findings=[{"file":name,**row} for name in handle.namelist() for row in leakage_findings(handle.read(name).decode("utf-8-sig"),internal_ids)]
        if zip_findings: raise ValueError("ZIP blind leakage")
    return {"blinding_audit":audit,"preview_files":previews,"png_preview_issues":png_issues,
            "all_answers_blank":True,"archive":str(archive.resolve()),"archive_contains_only_blind_material":True}


def report_markdown(report):
    audit=report["geometry_audit"]
    lines=["# M3 v0.5 visual review 可读性增强（内部报告）","",f'渲染 {report["plan_count"]} 个盲化 Plan、{report["comparison_count"]} 个冻结 comparison。无 preference label。',"",
           f'Frozen integrity：{report["frozen_integrity"]["all_unchanged"]}；检查 {report["frozen_integrity"]["checked_file_count"]} 文件。',"",
           "| OperationType | Total | Rendered |","| --- | ---: | ---: |"]
    lines += [f'| {kind} | {row["operation_count"]} | {row["rendered_count"]} |' for kind,row in audit["geometry_type_coverage"].items()]
    lines += ["",f'无 Geometry：{len(audit["no_geometry_ids"])}，{", ".join(audit["no_geometry_ids"])}。只显示在动作列表，不伪造位置。',"",
              "坐标：原工程 Geometry 不变。默认工程 CCW 270°，对应原生 SVG 的 matrix(0 1 -1 0 0 0)：工程(x,y)显示为(y,x)，与源HTML已有朝向一致。统一父层变换覆盖外轮廓、Operation、region和hit geometry。可切回原始朝向。", "",
              f'归区：{audit["d3"]["status"]}，{audit["d3"].get("assigned_operation_count",0)} 个归区；{audit["d3"].get("outside_region_count",0)} 个 OUTSIDE_REGION。',"",
              f'对外盲化扫描 {report["blinding_audit"]["file_count"]} 文件，findings={len(report["blinding_audit"]["findings"])}。所有判断项空白。',"",
              "SVG 为权威矢量预览，PNG 为同一冻结 primitive 和显示状态的光栅便利副本；不是 HTML 截图。", "",
              "交互仅在本地运行，没有网络、后端或自动提交。人工判断尝试在当前浏览器本地暂存，可从 HTML 一键导出 CSV；浏览器禁止存储时明确提示及时导出。Station/zoom/pan 共用一个状态，两面板同一 viewBox。", "",
              "预览："]
    lines += [f'- `{row["svg"]}`'+(f' / `{row["png"]}`' if "png" in row else "") for row in report["preview_files"]]
    lines += ["", "边界：外轮廓来自源HTML的多条已闭合外部边界，保留原间隙，没有拼接成虚构的单一实体外环。当前无geometry的TRIM/MARKING仍无动作空间位置；轮廓背景不替它们补造geometry。", "",
              "下一步建议：先由人工核对方向与高亮；可增强动作分组、可点击区域聚类、局部 zoom 导航和人工 annotation，不赋予工艺评分。完成到此停止。"]
    outline=audit["outline"]
    lines += ["",f'轮廓提取：{outline["status"]}；源文件 {outline.get("source_path","—")}；方法：{outline.get("method",outline.get("reason","—"))}。',
              f'外边界 {outline.get("exterior_contour_count",0)} 条，复用源段 {outline.get("selected_source_segment_count",0)} 条；max endpoint roundtrip error={outline.get("max_local_svg_endpoint_error")}。',
              "配色：当前序#2E6DAE（克制工程蓝），非当前序#748496/opacity0.55，选中#C56A1F（暖橙）；外轮廓#707A84/淡灰填充，影响区域#829AB2/opacity0.06。提高结构参考的可见性，保留当前序与选中态的层次。"]
    if report.get("human_answer_ui"):
        lines += ["", "人工评审可直接在 HTML 填写。填写时尝试本地暂存，显示已判断进度；完成后导出 comparison_pairs_completed.csv 返回。原 comparison_pairs.csv 保持空白，导出的24行顺序、A/B盲号和字段定义沿用冻结表。没有自动提交，未填写项不推断答案。"]
    if report["frozen_integrity"]["user_acknowledged_changes"]:
        lines += ["", "原 baseline 观察到190文件；用户确认删除了 data/raw/review_pack.html 临时副本。原 hash 和说明保留，正式冻结文件189/189不变。程序未恢复、删除或改写该副本。"]
    if report.get("tests"):
        t=report["tests"]
        lines += ["",f'测试：{t["tests_run"]}，failures={t["failures"]}，errors={t["errors"]}，skipped={t["skipped"]}。']
    return "\n".join(lines)+"\n"


def main(argv=None):
    parser=argparse.ArgumentParser(description="Build visual review from frozen v0.3 plans/pairs")
    parser.add_argument("--input",type=Path,default=ROOT/"data/parts/P001/钣金加工数据表_P001.xlsx")
    parser.add_argument("--candidate-dir",type=Path,default=ROOT/"outputs/m3_candidates")
    parser.add_argument("--review-dir",type=Path,default=ROOT/"outputs/m3_human_review")
    parser.add_argument("--config",type=Path,default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output",type=Path,default=ROOT/"outputs/m3_visual_review_v05")
    args=parser.parse_args(argv); folder=validate_destination(args.output,ROOT)
    preflight_answers(folder)
    manifest_path=folder/"internal/input_baseline_manifest.json"
    manifest=json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else capture_v05_baseline(ROOT)
    before=check_v05_integrity(ROOT,manifest)
    if not before["all_unchanged"]: raise ValueError("Frozen source or review material changed since baseline")
    safe_write(manifest_path,json.dumps(manifest,ensure_ascii=False,indent=2),root=folder)
    context=load_constraint_context(args.input)
    if not validate(context.part_data).passed: raise ValueError("Frozen M1 validation failed")
    HardConstraintEngine(context).require_feasible(context.expert_reference_plan)
    config=load_visualization_config(args.config)
    plans,reps,pairs,mapping=load_visual_inputs(context,args.candidate_dir,args.review_dir)
    catalog,views,comparisons,debug=prepare_visual_data(context,plans,reps,pairs,mapping,config)
    written=write_visual_pack(folder,catalog,views,comparisons,debug,config,plans)
    integrity=check_v05_integrity(ROOT,manifest)
    if not integrity["all_unchanged"]: raise ValueError("Visualization changed a frozen file")
    report={"schema_version":"m3.v0.5","plan_count":len(views),"comparison_count":len(comparisons),
            "geometry_audit":catalog["audit"],"bbox":catalog["bbox"],"view_box":catalog["view_box"],
            **written,"frozen_integrity":integrity,"labels_generated":False,"default_rotation_degrees":config["display"]["default_rotation_degrees"],
            "human_answer_ui":{"direct_html_entry":True,"local_browser_drafts":True,"csv_download":True,"automatic_submission":False},
            "outputs":{"expert_facing_html":str((folder/"expert_facing/review_pack.html").resolve()),"internal_html":str((folder/"internal/review_pack_internal.html").resolve())},
            "provenance":{"workbook_sha256":context.metadata_sha256,"config_sha256":hashlib.sha256(args.config.read_bytes()).hexdigest(),
                          "mapping_sha256":hashlib.sha256((args.review_dir/"internal/blind_plan_mapping.json").read_bytes()).hexdigest(),
                          "comparison_mapping_sha256":hashlib.sha256((args.review_dir/"internal/comparison_pairs_internal.json").read_bytes()).hexdigest()}}
    test_path=folder/"internal/test_results.json"
    if test_path.exists():
        tests=json.loads(test_path.read_text(encoding="utf-8"))
        report["tests"]={key:value for key,value in tests.items() if key!="test_ids"}
    for name,value in (("visual_review_pack_report.json",report),("internal/frozen_integrity_check.json",integrity),("internal/blinding_leakage_check.json",written["blinding_audit"])):
        safe_write(folder/name,json.dumps(value,ensure_ascii=False,indent=2),root=folder)
    safe_write(folder/"visual_review_pack_report.md",report_markdown(report),root=folder)
    print(json.dumps({"plans":len(views),"comparisons":len(comparisons),"renderable_operations":catalog["audit"]["renderable_count"],
                      "no_geometry":len(catalog["audit"]["no_geometry_ids"]),"previews":len(written["preview_files"]),"blinding":written["blinding_audit"]["passed"],
                      "frozen_integrity":integrity["all_unchanged"],"outputs":report["outputs"]},ensure_ascii=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
