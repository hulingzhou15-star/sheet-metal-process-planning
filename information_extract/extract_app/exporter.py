"""Versioned, facts-only Excel interchange for the local annotation tool.

The editor keeps its source bindings and stable internal IDs in its project file.
Only the existing Sheet1--Sheet5 contract is written to the standard workbook.
Formal validation reads this new workbook; it never reads an expert plan.
"""
from __future__ import annotations

from copy import deepcopy
import csv
from datetime import datetime, timezone
from io import BytesIO, StringIO
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from urllib.parse import quote
from uuid import uuid4

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from .operation_fields import parse_bend_angle


MODULE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = MODULE_ROOT.parent
HEADERS = {
    "Sheet1": ("part_id", "operation_id", "source_feature_ref", "operation_type", "surface_side",
               "required_predecessor_ids", "cad_parent_operation_id", "process_group_id", "geometry_id"),
    "Sheet2": ("part_id", "geometry_id", "seq", "segment_type", "start_x", "start_y", "end_x", "end_y",
               "center_x", "center_y", "radius", "sweep_angle"),
    "Sheet3": ("part_id", "station_id", "order", "allowed_operation_types"),
    "Sheet4": ("operation_id", "fixed_station"),
    "Sheet5": ("bend_operation_id", "start_x", "start_y", "end_x", "end_y", "bend_axis",
               "far_boundary_coord", "affected_depth", "affected_side", "bend_length", "affected_area_proxy",
               "influence_region_id", "parent_region_id"),
}
SOLVER_SETTINGS = {"max_time_seconds": 10.0, "random_seed": 0, "num_search_workers": 1}


def _plain(value):
    """Keep Python numeric values untouched; lists are contract token columns."""
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value)
    if isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Excel facts must contain finite numbers")
    return value


def _json_default(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(f"Not JSON serializable: {type(value).__name__}")


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False,
                               default=_json_default) + "\n", encoding="utf-8")


def _rows(project, report):
    from .process_config import planning_operations, expanded_capabilities, excludes_trim, tokens
    part = project.get("part_id")
    result = {sheet: [] for sheet in HEADERS}
    geometries = report.get("geometries", {})
    active_operations = planning_operations(project)
    for operation in active_operations:
        uid = operation.get("uid")
        segments = geometries.get(uid, [])
        # A stable identity follows the editor UID, never the editable O-number.
        from .business_geometry import active_business_geometry
        geometry_id = (operation['business_geometry']['geometry_id'] if active_business_geometry(operation) else f"G_{uid}") if segments else None
        result["Sheet1"].append({
            "part_id": part, "operation_id": operation.get("operation_id"),
            "source_feature_ref": operation.get("name"),
            "operation_type": operation.get("operation_type"),
            "surface_side": operation.get("surface_side"),
            "required_predecessor_ids": operation.get("required_predecessor_ids"),
            "cad_parent_operation_id": operation.get("cad_parent_operation_id"),
            "process_group_id": operation.get("process_group_id"), "geometry_id": geometry_id,
        })
        for index, segment in enumerate(segments, 1):
            # Accept the public model's flat coordinates, or its explicit coords
            # mapping; do not round numbers or change signed sweep angles.
            record = {**segment.get("coords", {}), **segment}
            record.update(part_id=part, geometry_id=geometry_id)
            record.setdefault("seq", index)
            result["Sheet2"].append(record)
    for station in project.get("station_rows", []):
        row = {"part_id": part, **station}
        if excludes_trim(project) or "PUNCH" in tokens(station.get("allowed_operation_types")):
            row["allowed_operation_types"] = ", ".join(expanded_capabilities(station.get("allowed_operation_types"), omit_trim=excludes_trim(project)))
        result["Sheet3"].append(row)
    excluded_ids = {op['operation_id'] for op in project.get('operations', []) if op not in active_operations}
    result["Sheet4"] = [deepcopy(row) for row in project.get("fixed_rows", []) if row.get('operation_id') not in excluded_ids]
    result["Sheet5"] = deepcopy([r for r in report.get("regions", []) if r.get('bend_operation_id') not in excluded_ids])
    return result


def _write_workbook(project, report, target):
    book = openpyxl.Workbook()
    book.remove(book.active)
    for sheet, records in _rows(project, report).items():
        tab = book.create_sheet(sheet)
        header = HEADERS[sheet]
        tab.append(header)
        for record in records:
            tab.append([_plain(record.get(key)) for key in header])
        tab.freeze_panes = "A2"
        tab.auto_filter.ref = tab.dimensions
        for row in tab:
            for cell in row:
                # User names are literal text, including names beginning '='.
                # Never create formulas from annotation text or imported values.
                if isinstance(cell.value, str):
                    cell.data_type = "s"
                elif isinstance(cell.value, float):
                    cell.number_format = "0.000000"
                cell.alignment = Alignment(vertical="top")
        for cell in tab[1]:
            cell.font = Font(color="FFFFFF", bold=True)
            cell.fill = PatternFill("solid", fgColor="355C80")
        for index, key in enumerate(header, 1):
            tab.column_dimensions[openpyxl.utils.get_column_letter(index)].width = min(max(len(key) + 2, 15), 34)
    book.save(target)
    book.close()


def _load_context(path):
    # Root code is reused read-only. Prevent __pycache__ creation in frozen src/.
    sys.dont_write_bytecode = True
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from src.inference.input import load_inference_context
    return load_inference_context(path)


def _check_hard_feasibility(context, settings, relations=()):
    """One witness solve, with no objectives, candidate pool, or expert inputs."""
    from ortools.sat.python import cp_model
    from src.constraints.v06.feasibility import build_feasibility_model, make_solver
    from src.constraints.v06.hard_constraints import HardConstraintEngine

    model, ranks = build_feasibility_model(context)
    from .allocation_constraints import add_not_later_constraints, check_not_later_constraints
    add_not_later_constraints(model,ranks,relations)
    solver = make_solver(settings)
    status = solver.solve(model)
    label = solver.status_name(status)
    result = {"status": label, "feasible": None, "settings": settings,
              "purpose": "single H1-H7 feasibility witness; no candidate generation"}
    if relations:
        result.update(purpose='single H1-H7 plus explicit not-later constraints witness; no candidate generation',
                      not_later_relation_count=len(relations))
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        witness = {operation_id: context.ordered_stations[solver.value(rank)].station_id
                   for operation_id, rank in ranks.items()}
        checked = HardConstraintEngine(context).evaluate(witness).to_dict()
        relation_check=check_not_later_constraints(witness,{s.station_id:i for i,s in enumerate(context.ordered_stations)},relations)
        result.update(feasible=checked["feasible"], witness_operation_count=len(witness),
                      hard_constraint_check=checked)
        if relations:
            result.update(additional_relation_check=relation_check,feasible=checked['feasible'] and relation_check['feasible'])
        if not result["feasible"]:
            result["status"] = "WITNESS_CHECK_FAILED"
    elif status == cp_model.INFEASIBLE:
        result["feasible"] = False
    return result


def _diagnostic_exception(exc):
    if callable(getattr(exc, "to_dict", None)):
        return exc.to_dict()
    if hasattr(exc, "report") and callable(getattr(exc.report, "to_dict", None)):
        return exc.report.to_dict()
    return {"exception_type": type(exc).__name__, "message": str(exc)}


def export_workbook(project, report, output_dir: Path, *, mode="draft", module_root=None):
    """Write a new version directory; formal failures remain diagnostic drafts.

    Return download files plus a complete validation report. Only a successful
    formal request has ``ready=True``. A failed/unknown solver status never
    becomes an infeasibility claim, and no rejected export overwrites a source.
    """
    if mode not in {"draft", "formal", "validated"}:
        raise ValueError("mode must be 'draft' or 'validated'")
    formal_requested = mode in {"formal", "validated"}
    if project.get("workflow_version") == 2:
        from .pairing import synchronize_pairing
        project = deepcopy(project)
        synchronize_pairing(project)
    if project.get("workflow_version") == 2 and (not project.get("inventory_confirmed") or report.get("geometry_state") != "CURRENT"):
        raise ValueError("请先完成整图标注并点击“整理几何”，再导出数据表；未整理的标注可以保存项目备份。")
    root = Path(module_root or MODULE_ROOT).resolve()
    # Overriding root is useful for isolated tests, not an escape from the module.
    if not root.is_relative_to(MODULE_ROOT):
        raise ValueError("module_root must remain inside information_extract")
    destination = Path(output_dir).resolve()
    if not destination.is_relative_to(root / "outputs"):
        raise ValueError("Exports must remain below information_extract/outputs")
    part_slug = re.sub(r"[^A-Za-z0-9_.-]", "_", str(project.get("part_id") or "unnamed"))[:80]
    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ") + "_" + uuid4().hex[:8]
    folder = destination / f"{part_slug}_{version}"
    folder.mkdir(parents=True, exist_ok=False)
    combined = deepcopy(report)
    combined.setdefault("errors", [])
    combined.setdefault("warnings", [])
    combined["requested_mode"] = mode
    combined["annotation_ready"] = bool(report.get("ready"))
    combined["ready"] = False
    combined["data_contract"] = {"status": "NOT_CHECKED"}
    combined["hard_feasibility"] = {"status": "NOT_CHECKED", "feasible": None}
    book_path = folder / f"{part_slug}_facts_draft.xlsx"
    _write_workbook(project, report, book_path)
    if formal_requested:
        if report.get("errors") or not report.get("ready"):
            combined["errors"].append({"code": "ANNOTATION_NOT_READY",
                                        "message": "标注仍有错误或未完成项，已保存诊断草稿。"})
        else:
            try:
                context = _load_context(book_path)
                combined["data_contract"] = {"status": "PASSED",
                                               "facts_fingerprint": context.facts_fingerprint,
                                               "expert_reference_required": False}
            except Exception as exc:
                combined["data_contract"] = {"status": "FAILED", "diagnostics": _diagnostic_exception(exc)}
                combined["errors"].append({"code": "DATA_CONTRACT_FAILED", "message": str(exc)})
            else:
                try:
                    settings = {**SOLVER_SETTINGS, **project.get("solver_settings", {}),
                                **project.get("export_solver_settings", {})}
                    from .process_config import not_later_relations
                    relations=not_later_relations(project)
                    hard = _check_hard_feasibility(context, settings, relations) if relations else _check_hard_feasibility(context, settings)
                    combined["hard_feasibility"] = hard
                    if hard.get("feasible") is True:
                        combined["ready"] = True
                    else:
                        code = "HARD_INFEASIBLE" if hard.get("status") == "INFEASIBLE" else "HARD_FEASIBILITY_UNRESOLVED"
                        combined["errors"].append({"code": code,
                                                    "message": f"H1–H7 检查状态：{hard.get('status')}，已保存诊断草稿。"})
                except Exception as exc:
                    combined["hard_feasibility"] = {"status": "ERROR", "feasible": None,
                                                     "diagnostics": _diagnostic_exception(exc)}
                    combined["errors"].append({"code": "HARD_FEASIBILITY_ERROR", "message": str(exc)})
    if combined["ready"]:
        formal_path = folder / f"{part_slug}_facts_formal.xlsx"
        book_path.rename(formal_path)
        book_path = formal_path
    combined["exported_mode"] = "formal" if combined["ready"] else "draft"
    combined["workbook_name"] = book_path.name
    exported_geometry_ids = {r['operation_id']: r['geometry_id'] for r in _rows(project, report)['Sheet1']}
    manifest = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "part_id": project.get("part_id"), "source_sha256": project.get("source_sha256"),
        "source_file_name": project.get("source_file_name"),
        "coordinate_system": project.get("coordinate_system"),
        "workbook_name": book_path.name, "workbook_sha256": hashlib.sha256(book_path.read_bytes()).hexdigest(),
        "export_mode": combined["exported_mode"], "expert_reference_required": False,
        "planning_scope": project.get("planning_scope", "legacy"),
        "operations": [{"uid": op.get("uid"), "operation_id": op.get("operation_id"),
                        "geometry_id": exported_geometry_ids.get(op.get('operation_id')),
                        "geometry_mode": op.get("geometry_mode"),
                        "source_segment_ids": op.get("source_segment_ids", []),
                        "no_geometry_reason": op.get("no_geometry_reason")}
                       for op in project.get("operations", [])],
        "station_import_source": project.get("station_import_source"),
        "sheet4_confirmed": bool(project.get("sheet4_confirmed")),
        "note": "The facts workbook excludes reference assignments and candidate identities. Expert annotations are exported separately in expert_assignments.json and are not inference inputs.",
    }
    report_path, manifest_path = folder / "validation_report.json", folder / "source_manifest.json"
    if report.get("drawing_source_supplement"):
        manifest["drawing_source_supplement"] = deepcopy(report["drawing_source_supplement"])
    _write_json(report_path, combined)
    _write_json(manifest_path, manifest)
    artifacts = [book_path, report_path, manifest_path]
    from .process_config import not_later_relations
    relations=not_later_relations(project)
    if relations:
        relations_path=folder/'allocation_relations.json'
        _write_json(relations_path,{'part_id':project['part_id'],'schema_version':1,
                                   'required_for_allocation':True,'not_later_than':relations,
                                   'semantics':'station_order(operation_id) <= station_order(not_later_than); same station allowed',
                                   'expert_reference_included':False})
        artifacts.append(relations_path)
        manifest['required_allocation_relations']='allocation_relations.json'
        manifest['workbook_alone_is_complete_allocation_input']=False
        _write_json(manifest_path,manifest)
    # Reference assignments remain a separate artifact, never Sheet1 / Sheet4.
    from .process_config import inspect_process, planning_operations
    expert_path = folder / "expert_assignments.json"
    _write_json(expert_path, {"part_id": project.get("part_id"),
                            "scope": "expert reference only; excluded from facts workbook and inference",
                            "assignments": [{"operation_id": op.get("operation_id"),
                                             "expert_station": op.get("expert_station") or None}
                                            for op in planning_operations(project)],
                            "diagnostics": inspect_process(project)})
    artifacts.append(expert_path)
    metadata_path = folder / "operation_metadata.json"
    _write_json(metadata_path, {"part_id": project.get("part_id"), "angle_unit": "deg",
                                "scope": "annotation metadata; not used for constraints, scoring, or geometry inference",
                                "operations": [{"operation_id": op.get("operation_id"), "bend_angle_deg": op.get("bend_angle_deg")}
                                               for op in project.get("operations", []) if op.get("operation_type") == "BEND"]})
    artifacts.append(metadata_path)
    if project.get("workflow_version") == 2:
        review_path = folder / "geometry_review.json"
        _write_json(review_path, {"part_id": project["part_id"], "source_sha256": project["source_sha256"],
                                 "coordinate_system": project.get("coordinate_system"),
                                 "records": report.get("geometry_records", {}),
                                 "note": "Multiple paths retain independent identities. Roles are not inferred. Pending rules are not formal geometry."})
        annotation_path = folder / "annotation_project.json"
        _write_json(annotation_path, project)
        artifacts += [review_path, annotation_path]
    files = [{"name": item.name, "url": "/" + "/".join(quote(p) for p in item.relative_to(root).parts)}
             for item in artifacts]
    return {"files": files, "report": combined, "ready": combined["ready"],
            "mode": combined["exported_mode"], "directory": str(folder)}


def _read_table(book, name, required):
    if name not in book.sheetnames:
        raise ValueError(f"缺少 {name}")
    rows = book[name].iter_rows()
    header = next(rows, ())
    keys = tuple(str(c.value).strip() if c.value is not None else "" for c in header)
    for key in required:
        if keys.count(key) != 1:
            raise ValueError(f"{name} 必须且只能包含一个 {key} 列")
    indexes = {key: keys.index(key) for key in required}
    result = []
    for row_number, cells in enumerate(rows, 2):
        selected = {key: cells[index] for key, index in indexes.items()}
        if not any(cell.value is not None for cell in selected.values()):
            continue
        if any(cell.data_type in {"f", "e"} for cell in selected.values()):
            raise ValueError(f"{name} 第 {row_number} 行包含公式或错误值；请提供明确事实值")
        result.append({key: cell.value for key, cell in selected.items()})
    return result


def import_station_workbook(content: bytes):
    """Read user Sheet3/4 only; ignore all expert/assignment columns elsewhere."""
    book = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=False)
    try:
        stations = _read_table(book, "Sheet3", HEADERS["Sheet3"])
        fixed = _read_table(book, "Sheet4", HEADERS["Sheet4"])
    finally:
        book.close()
    return {"station_rows": stations, "fixed_rows": fixed, "sheet4_confirmed": True,
            "station_import_source": {"sha256": hashlib.sha256(content).hexdigest(),
                                      "sheets": ["Sheet3", "Sheet4"]}}


def _operation_records(rows):
    operations = []
    seen = set()
    for row in rows:
        identifier = str(row.get("operation_id") or "").strip()
        if not identifier:
            raise ValueError("每个动作必须填写 operation_id")
        if identifier in seen:
            raise ValueError(f"重复动作编号：{identifier}")
        seen.add(identifier)
        predecessors = row.get("required_predecessor_ids") or ""
        if isinstance(predecessors, str):
            predecessors = [x for x in re.split(r"[,;\s]+", predecessors) if x]
        operations.append({"uid": uuid4().hex, "operation_id": identifier,
                           "name": row.get("name") or row.get("source_feature_ref") or "",
                           "operation_type": row.get("operation_type") or "",
                           "feature_type": row.get("feature_type") or "",
                           "geometry_shape": row.get("geometry_shape") or "",
                           "surface_side": row.get("surface_side") or "",
                           "cad_parent_operation_id": row.get("cad_parent_operation_id") or None,
                           "required_predecessor_ids": predecessors,
                           "process_group_id": row.get("process_group_id") or None,
                           "bend_angle_deg": parse_bend_angle(row.get("bend_angle_deg")),
                           "source_segment_ids": [], "geometry_mode": "unbound",
                           "no_geometry_reason": ""})
    return {"operations": operations, "note": "仅导入显式关系；表格行顺序未转为加工先后。"}


def import_operation_workbook(content: bytes, *, filename=None):
    """Import a feature-tree list with explicit parents, never old geometry."""
    if filename and Path(filename).suffix.lower() in {".csv", ".tsv"}:
        return import_operation_csv(content)
    book = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=False)
    try:
        name = "Sheet1" if "Sheet1" in book.sheetnames else book.sheetnames[0]
        header = next(book[name].iter_rows(), ())
        available = {str(c.value).strip() for c in header if c.value is not None}
        keys = tuple(k for k in ("operation_id", "name", "source_feature_ref", "operation_type", "feature_type", "geometry_shape", "surface_side",
                                 "cad_parent_operation_id", "required_predecessor_ids", "process_group_id", "bend_angle_deg") if k in available)
        if "operation_id" not in keys:
            raise ValueError("特征树表缺少 operation_id 列")
        rows = _read_table(book, name, keys)
    finally:
        book.close()
    return _operation_records(rows)


def import_operation_csv(content: bytes):
    text = content.decode("utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(StringIO(text), dialect=dialect)
    if "operation_id" not in (reader.fieldnames or []):
        raise ValueError("特征树 CSV 缺少 operation_id 列")
    return _operation_records([row for row in reader if any(row.values())])
