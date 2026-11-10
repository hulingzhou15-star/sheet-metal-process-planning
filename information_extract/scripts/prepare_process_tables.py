"""Read-only collection snapshot for human-editable process workbooks.

No inventory confirmation, geometry batch, CAD relation or project is saved.
The standard Sheet2 only receives geometry from an already-current batch.
Uncompiled DXF sources remain in a separate, explicitly raw binding table.
"""
from collections import Counter
from copy import deepcopy
import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT)]

from extract_app.drawing import load_drawing
from extract_app.model import validate_project
from extract_app.workflow import FEATURE_BY_KEY, compile_operation


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected_files():
    paths = set(ROOT.glob("projects/**/*.json")) | set(ROOT.glob("configs/*.json")) | set(ROOT.glob("*.dxf"))
    paths |= set((ROOT.parent / "src").rglob("*.py"))
    paths |= set((ROOT.parent / "data/parts/P001").glob("*.xlsx"))
    paths.add(ROOT.parent / "PROJECT_SPEC.md")
    return {str(p.resolve()): sha(p) for p in sorted(paths) if p.is_file()}


def headers():
    tree = ast.parse((ROOT / "extract_app/exporter.py").read_text(encoding="utf-8"))
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "HEADERS" for t in n.targets))


def plain(value):
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value) or None
    return None if value == "" else value


def snapshot(part, standard_headers, as_of):
    source = ROOT / "projects" / part / "project.json"
    project_bytes = source.read_bytes()
    project = json.loads(project_bytes)
    original = deepcopy(project)
    config = json.loads((ROOT / "configs" / f"{part}.json").read_text(encoding="utf-8"))
    drawing = load_drawing(ROOT / config["source_file"], config)
    report = validate_project(project, drawing)
    sources = {s["id"]: s for s in drawing["segments"]}
    records = {s: [] for s in standard_headers}
    extra, raw, issues = [], [], []
    status_names = {"READY": "已试算，未正式整理", "NEEDS_CONFIRMATION": "待确认",
                    "RULE_PENDING": "规则待定", "NEEDS_REVIEW": "待复核"}
    for op in project["operations"]:
        uid = op["uid"]
        geometry = report["geometries"].get(uid, [])
        geometry_id = f"G_{uid}" if geometry else None
        row = {"part_id": part, "operation_id": op["operation_id"], "source_feature_ref": op.get("name"),
               "operation_type": op.get("operation_type"), "surface_side": op.get("surface_side"),
               "required_predecessor_ids": op.get("required_predecessor_ids"),
               "cad_parent_operation_id": op.get("cad_parent_operation_id"),
               "process_group_id": op.get("process_group_id"), "geometry_id": geometry_id}
        records["Sheet1"].append([plain(row.get(k)) for k in standard_headers["Sheet1"]])
        for i, seg in enumerate(geometry, 1):
            row = {**seg.get("coords", {}), **seg, "part_id": part, "geometry_id": geometry_id}
            row.setdefault("seq", i)
            records["Sheet2"].append([plain(row.get(k)) for k in standard_headers["Sheet2"]])
        try:
            probe = compile_operation(deepcopy(op), [sources[s] for s in op.get("source_segment_ids", [])], config)
        except (ValueError, TypeError, KeyError) as exc:
            probe = {"status": "NEEDS_REVIEW", "legacy_supported": False, "issues": [str(exc)]}
        state = status_names.get(probe["status"], probe["status"])
        if probe["status"] == "READY" and not probe["legacy_supported"]:
            state = "待下游适配"
        details = "；".join(probe.get("issues", []))
        is_bend = op.get("operation_type") == "BEND"
        feature = FEATURE_BY_KEY.get(op.get("feature_type"), {})
        extra.append([part, op["operation_id"], op.get("name"), op.get("feature_type"), feature.get("label"),
                      op.get("geometry_shape"), op.get("bend_angle_deg") if is_bend else None,
                      "按已批准默认值填入，非模型测量" if is_bend and op.get("bend_angle_deg") == 90 else None,
                      op.get("protrusion_side"), len(op.get("source_segment_ids", [])),
                      "是" if op.get("no_geometry") else "否", op.get("no_geometry_reason") or None,
                      state, "可表达" if probe["legacy_supported"] else "待适配或复核",
                      details or None, op.get("notes") or None, uid])
        if probe["status"] != "READY" or not probe["legacy_supported"]:
            action = "程序适配 / 核对轮廓" if state == "待下游适配" else "核对绑定图元" if state == "待复核" else "确认表示规则" if state == "规则待定" else "确认中线跨度及影响区"
            issues.append([part, op["operation_id"], op.get("name"), "几何试算", state, details, action, None])
        for order, sid in enumerate(op.get("source_segment_ids", []), 1):
            seg = sources[sid]
            raw.append([part, op["operation_id"], order, sid, seg.get("source_handle"), seg.get("segment_type"),
                        *[seg.get(k) for k in ("start_x", "start_y", "end_x", "end_y", "center_x", "center_y", "radius", "sweep_angle")]])
    for station in project.get("station_rows", []):
        row = {"part_id": part, **station}
        records["Sheet3"].append([plain(row.get(k)) for k in standard_headers["Sheet3"]])
    records["Sheet4"] = [[plain(row.get(k)) for k in standard_headers["Sheet4"]] for row in project.get("fixed_rows", [])]
    regions = {r["bend_operation_id"]: r for r in report.get("regions", [])}
    for op in project["operations"]:
        if op.get("operation_type") == "BEND":
            row = regions.get(op["operation_id"], {"bend_operation_id": op["operation_id"]})
            records["Sheet5"].append([plain(row.get(k)) for k in standard_headers["Sheet5"]])
    for severity in ("errors", "warnings"):
        for issue in report[severity]:
            issues.append([part, issue.get("operation_id"), None, issue["code"],
                           "错误" if severity == "errors" else "待完成", issue["message"],
                           "核对对应动作" if issue.get("operation_id") else "按填表说明完成", None])
    if project != original or source.read_bytes() != project_bytes:
        raise RuntimeError("标注在读取期间发生变化，未生成该快照。")
    return {"part_id": part, "as_of": as_of, "revision": project["revision"], "source_file": project["source_file_name"],
            "source_sha256": project["source_sha256"], "project_sha256": sha(source), "units": drawing["source"]["units"],
            "coordinate_system": project.get("coordinate_system"), "headers": standard_headers, "records": records,
            "extra": extra, "raw": raw, "issues": issues, "statistics": report["statistics"],
            "types": dict(Counter(op.get("feature_type") for op in project["operations"])),
            "inventory_confirmed": project.get("inventory_confirmed", False),
            "sheet4_confirmed": project.get("sheet4_confirmed", False), "geometry_state": report["geometry_state"],
            "ready": report["ready"], "root_supported_types": ["CIRCLE_PUNCH", "IRREGULAR_PUNCH", "PRE_PUNCH", "FLANGING", "BEND", "TRIM", "DIMPLE", "BRIDGE", "MARKING"],
            "annotation_types": list(dict.fromkeys(v["operation_type"] for v in FEATURE_BY_KEY.values()))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--as-of", required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if not out.is_relative_to(ROOT / "outputs"):
        raise ValueError("输出必须保存在 information_extract/outputs 下。")
    before = protected_files()
    values = [snapshot(p, headers(), args.as_of) for p in ("P002", "P003")]
    if protected_files() != before:
        raise RuntimeError("读取期间受保护文件发生变化，请重新生成。")
    out.mkdir(parents=True, exist_ok=True)
    (out / "workbook_payload.json").write_text(json.dumps(values, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (out / "source_manifest.json").write_text(json.dumps({"protected_files": before, "parts": [
        {k: v[k] for k in ("part_id", "revision", "source_file", "source_sha256", "project_sha256", "as_of")}
        for v in values]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps([{p["part_id"]: {"operations": len(p["extra"]), "source_bindings": len(p["raw"]),
                       "bends": len(p["records"]["Sheet5"]), "ready": p["ready"]}} for p in values], ensure_ascii=False))


if __name__ == "__main__":
    main()
