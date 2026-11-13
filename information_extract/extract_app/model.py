"""Annotation validation, reversible coverage accounting and project persistence."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import uuid

from .geometry import derive_geometry, derive_region, fully_contained
from .operation_fields import validate_bend_angle, preserve_bend_angles

OPERATION_TYPES = ["CIRCLE_PUNCH", "IRREGULAR_PUNCH", "PRE_PUNCH", "FLANGING", "BEND", "TRIM", "DIMPLE", "DRAW_FORM", "BRIDGE", "MARKING"]
SURFACE_SIDES = ["SMOOTH", "ROUGH", "NOT_APPLICABLE"]


def new_project(drawing, part_id="P001"):
    return {"schema_version": 1, "part_id": part_id,
            "source_sha256": drawing["source"]["sha256"], "revision": 0,
            "source_file_name": drawing["source"].get("file_name"),
            "coordinate_system": drawing["source"].get("coordinate_system", "block-local material XY"),
            "source_units": drawing["source"].get("units"),
            "expected_operation_count": None, "inventory_confirmed": False,
            "operations": [], "ignored_entities": {}, "station_rows": [],
            "fixed_rows": [], "sheet4_confirmed": False}


def basic_check(project, drawing):
    if not isinstance(project, dict) or project.get("schema_version") != 1:
        raise ValueError("不支持的标注项目格式。")
    if project.get("source_sha256") != drawing["source"]["sha256"]:
        raise ValueError("源 DXF 指纹与标注项目不一致，未自动重新绑定。")
    if not isinstance(project.get("part_id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]+", project["part_id"]):
        raise ValueError("零件编号只能使用字母、数字、下划线或短横线。")
    if not isinstance(project.get("operations"), list) or not isinstance(project.get("ignored_entities", {}), dict):
        raise ValueError("动作列表或忽略实体格式错误。")
    uids = [op.get("uid") for op in project["operations"] if isinstance(op, dict)]
    if len(uids) != len(project["operations"]) or any(not isinstance(x, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", x) for x in uids) or len(set(uids)) != len(uids):
        raise ValueError("动作内部 ID 必须非空、唯一并使用安全字符。")
    for op in project["operations"]:
        if not isinstance(op.get("source_segment_ids", []), list) or not isinstance(op.get("required_predecessor_ids", []), list):
            raise ValueError("几何来源和前置动作必须保存为列表。")
        for field in ('not_later_than_operation_ids','composite_operation_ids','primary_source_segment_ids'):
            value=op.get(field,[])
            if not isinstance(value,list) or any(not isinstance(x,str) or not x.strip() for x in value) or len(value)!=len(set(value)):
                raise ValueError('动作关系必须为不重复的编号列表：'+field)


def coverage(project, drawing, geometries):
    segments = {s["id"]: s for s in drawing["segments"]}
    bound = {s for op in project["operations"] for s in op.get("source_segment_ids", []) if s in segments}
    absorbed_by = {}
    tolerance = drawing["config"]["geometry_tolerance"]
    for op in project["operations"]:
        geometry = geometries.get(op["uid"], [])
        if op.get("operation_type") != "BRIDGE" or not op.get("absorb_interior", True) or not geometry:
            continue
        box = geometry[0]
        rectangle = [box["start_x"], box["start_y"], box["end_x"], box["end_y"]]
        excluded = set(op.get("absorption_exclusions", []))
        for sid, segment in segments.items():
            if sid in bound or sid in excluded or segment["segment_type"] not in {"LINE", "ARC"}:
                continue
            if fully_contained(segment, rectangle, tolerance):
                absorbed_by.setdefault(sid, []).append(op["operation_id"])
    statuses, details = {}, {}
    background_sources = set(drawing.get("background_source_ids", []))
    business_reference = project.get('business_import', {}).get('geometry_mode') == 'approved_workbook_not_dxf_binding'
    for sid in segments:
        status = "bound" if sid in bound else "absorbed" if sid in absorbed_by else "ignored" if project.get("ignored_entities", {}).get(sid) or sid in background_sources else "reference" if business_reference else "unhandled"
        statuses[sid] = status
        details[sid] = {"status": status, "absorbed_by": absorbed_by.get(sid, [])}
    counts = Counter(statuses.values())
    statistics = {"total": len(segments), "operation_count": len(project["operations"]),
                  "unsupported": len(drawing.get("issues", [])),
                  **{k: counts[k] for k in ("bound", "absorbed", "ignored", "unhandled", "reference")}}
    return statuses, statistics, details


def validate_project(project, drawing, *, annotation_only=False):
    basic_check(project, drawing)
    from .workflow import FEATURE_BY_KEY, current_batch
    staged = project.get("workflow_version") == 2
    pairing = None
    if staged:
        from .pairing import synchronize_pairing
        project = deepcopy(project)
        pairing = synchronize_pairing(project)
    errors, warnings, geometries, regions = [], [], {}, []
    from .business_geometry import business_record
    imported_records = {}
    def issue(code, message, op=None, warning=False):
        (warnings if warning else errors).append({"code": code, "message": message,
                                                 **({"operation_id": op.get("operation_id", "")} if op else {})})
    for entry in pairing["issues"] if pairing else []:
        issue(entry["code"], entry["message"])
    segment_map = {s["id"]: s for s in drawing["segments"]}
    operations = project["operations"]
    from .process_config import excludes_trim, planning_operations
    trim_excluded = excludes_trim(project)
    ids = [op.get("operation_id", "") for op in operations]
    counts = Counter(ids)
    by_id = {op.get("operation_id"): op for op in operations}
    if not operations:
        issue("EMPTY_OPERATIONS", "尚未创建加工动作。")
    for op in operations:
        oid = op.get("operation_id", "")
        if not isinstance(oid, str) or not oid.strip() or counts[oid] != 1:
            issue("OPERATION_ID", "动作编号不能为空或重复。", op)
        if not str(op.get("name", "")).strip():
            issue("NAME_MISSING", "请填写动作名称。", op)
        if op.get("operation_type") not in OPERATION_TYPES or op.get("surface_side") not in SURFACE_SIDES:
            issue("TYPE_OR_SIDE", "请确认动作类型和表面侧。", op)
        if op.get("operation_type") == "BEND":
            try:
                validate_bend_angle(op.get("bend_angle_deg"))
            except ValueError as error:
                issue("BEND_ANGLE_VALUE", str(error), op)
        for ref in [op.get("cad_parent_operation_id"), *op.get("required_predecessor_ids", [])]:
            if ref and (ref not in by_id or ref == oid):
                issue("REFERENCE_INVALID", f"动作引用不存在或引用自身：{ref}", op)
        selected = op.get("source_segment_ids", [])
        if len(selected) != len(set(selected)) or any(s not in segment_map for s in selected):
            issue("SOURCE_REFERENCE", "几何来源重复或不属于当前图纸。", op)
            continue
        if op.get('superseded_by_operation_id') or (trim_excluded and op.get("operation_type") == "TRIM"):
            continue
        try:
            imported = business_record(op)
        except (ValueError, KeyError, TypeError) as exc:
            issue("BUSINESS_GEOMETRY_INVALID", str(exc), op)
            continue
        if imported is not None:
            imported_records[op['uid']] = imported
            geometries[op['uid']] = imported['legacy_geometry']
            if imported['region']:
                regions.append(imported['region'])
            continue
        if staged:
            feature = FEATURE_BY_KEY.get(op.get("feature_type"))
            if not feature or feature["operation_type"] != op.get("operation_type") or feature["shape"] != op.get("geometry_shape"):
                issue("FEATURE_TYPE", "请确认细分动作类型（圆形/异形、一般/曲线折弯）。", op)
            if op.get("no_geometry"):
                if selected or not str(op.get("no_geometry_reason", "")).strip():
                    issue("NO_GEOMETRY_REASON", "无适用几何需填写原因，且不能同时绑定图元。", op)
                if op.get("operation_type") == "BEND":
                    issue("BEND_GEOMETRY_MISSING", "折弯需保留对应图元；曲线规则未定也可以先绑定。", op)
            elif not selected:
                issue("SOURCES_MISSING", "尚未绑定动作图元。", op)
            continue
        if op.get("geometry_mode") == "none":
            if selected:
                issue("NONE_HAS_SOURCES", "无几何动作不能同时绑定实体。", op)
            if not str(op.get("no_geometry_reason", "")).strip():
                issue("NO_GEOMETRY_REASON", "请说明无适用几何的原因。", op)
            if op.get("operation_type") == "BEND":
                issue("BEND_REGION_UNAVAILABLE", "折弯缺少中线，不能完成影响区。", op)
            continue
        try:
            geometry = derive_geometry(op, [segment_map[s] for s in selected], drawing["config"])
            geometries[op["uid"]] = geometry
            if op.get("operation_type") == "BEND":
                if not op.get("region"):
                    issue("REGION_MISSING", "请设置并确认折弯影响区。", op)
                else:
                    regions.append(derive_region(op, geometry, op["region"]))
        except (ValueError, KeyError, TypeError) as exc:
            issue("GEOMETRY_OR_REGION", str(exc), op)
    batch = current_batch(project, drawing["config"]) if staged else None
    if staged:
        owners = {}
        for op in planning_operations(project):
            for sid in op.get("source_segment_ids", []):
                owners.setdefault(sid, []).append(op)
        for sid, members in owners.items():
            if len(members) > 1 and any(sid not in op.get("shared_source_segment_ids", []) for op in members):
                issue("SHARED_SOURCE_UNCONFIRMED", f"图元 {sid} 同时绑定 {', '.join(op['operation_id'] for op in members)}，请明确确认共用或移除误选。")
        if not annotation_only:
            if not batch:
                issue("GEOMETRY_NOT_COMPILED", "几何尚未统一整理，或标注已变化；确认整图解释完成后点击“整理几何”。", warning=True)
            else:
                for op in operations:
                    if op['uid'] in imported_records:
                        continue
                    record = batch["records"].get(op["uid"], {})
                    if record.get("status") != "READY":
                        issue("GEOMETRY_PENDING", "；".join(record.get("issues", ["几何尚待处理"])), op)
                    elif not record.get("legacy_supported"):
                        issue("LEGACY_ADAPTER_PENDING", "；".join(record.get("issues", [])), op, warning=True)
                    if record.get("status") == "READY" and record.get("legacy_supported"):
                        geometries[op["uid"]] = record.get("legacy_geometry", [])
                    if record.get("region"):
                        regions.append(record["region"])
    # Both CAD and manufacturing dependencies must be explicit acyclic relations.
    def cycle_check(edges, code):
        visited, active = set(), set()
        def visit(node):
            if node in active:
                raise ValueError(node)
            if node in visited:
                return
            active.add(node)
            for nxt in edges.get(node, []):
                if nxt in by_id:
                    visit(nxt)
            active.remove(node)
            visited.add(node)
        try:
            for node in by_id:
                visit(node)
        except ValueError as exc:
            issue(code, f"动作关系存在循环：{exc}")
    cycle_check({o.get("operation_id"): [o["cad_parent_operation_id"]] if o.get("cad_parent_operation_id") else [] for o in operations}, "CAD_CYCLE")
    cycle_check({o.get("operation_id"): o.get("required_predecessor_ids", []) for o in operations}, "PRECEDENCE_CYCLE")
    groups = {}
    for op in operations:
        if op.get("process_group_id"):
            groups.setdefault(op["process_group_id"], []).append(op)
        elif op.get("operation_type") in {"FLANGING", "PRE_PUNCH"}:
            issue("PAIR_UNCONFIRMED", "尚未自动确定预孔/翻孔加工组，请补齐明确 CAD 对应关系或核对配对提示。" if staged else "预孔/翻孔需要明确配对的加工组。", op)
    for gid, members in groups.items():
        kinds = Counter(o.get("operation_type") for o in members)
        if kinds.keys() & {"PRE_PUNCH", "FLANGING"} and kinds != Counter({"PRE_PUNCH": 1, "FLANGING": 1}):
            issue("PROCESS_GROUP", f"加工组 {gid} 必须恰好包含一个预孔和一个翻孔。")
    region_ids = Counter(r["influence_region_id"] for r in regions)
    for region in regions:
        if region_ids[region["influence_region_id"]] != 1:
            issue("REGION_ID_DUPLICATE", "影响区编号重复。")
        if region.get("parent_region_id") and region["parent_region_id"] not in region_ids:
            issue("REGION_PARENT_MISSING", f"父影响区不存在：{region['parent_region_id']}")
    for start in regions:
        current, visited = start, set()
        lookup = {r["influence_region_id"]: r for r in regions}
        while current:
            rid = current["influence_region_id"]
            if rid in visited:
                issue("REGION_CYCLE", "影响区父子关系存在循环。")
                break
            visited.add(rid)
            current = lookup.get(current.get("parent_region_id"))
    statuses, statistics, details = coverage(project, drawing, geometries)
    statistics['business_geometry_operations'] = len(imported_records)
    statistics['missing_action_geometry'] = sum(not op.get('source_segment_ids') and op['uid'] not in imported_records
                                                and not op.get('no_geometry') and not op.get('superseded_by_operation_id') and not (trim_excluded and op.get('operation_type') == 'TRIM') for op in operations)
    if statistics["unhandled"]:
        issue("UNHANDLED", f"仍有 {statistics['unhandled']} 个几何片段尚未处理。", warning=True)
    if drawing.get("issues"):
        issue("DXF_ISSUES", "源 DXF 存在未支持/异常实体，请先解决源图问题。")
    expected = project.get("expected_operation_count")
    if expected is not None and (type(expected) is not int or expected != len(operations)):
        issue("EXPECTED_COUNT", "已建动作数量与预期动作数不一致。")
    for sid, reason in project.get("ignored_entities", {}).items():
        if sid not in segment_map or not isinstance(reason, str) or not reason.strip():
            issue("IGNORE_INVALID", "背景/忽略实体必须来自本图并记录原因。")
    for key, message in (("inventory_confirmed", "尚未确认动作清单完整。"),
                         ("sheet4_confirmed", "尚未确认 Sheet4 固定 TRIM。")):
        if key == "sheet4_confirmed" and trim_excluded:
            continue
        if not project.get(key):
            issue("COMPLETION_PENDING", message, warning=True)
    if not project.get("station_rows"):
        issue("STATIONS_PENDING", "尚未在工序能力窗口指定工序。", warning=True)
    from .process_config import inspect_process
    process_report = inspect_process(project)
    for message in process_report["station_errors"]:
        issue("STATION_CONFIG", message)
    for message in process_report['relation_capacity_issues']:
        issue('NOT_LATER_CAPABILITY_CONFLICT',message,warning=True)
    geometry_complete = not staged or bool(batch and all(r.get("status") == "READY" for r in batch["records"].values()) and len(batch["records"]) == len(operations))
    legacy_compatible = not staged or bool(batch and all(r.get("legacy_supported") for r in batch["records"].values()))
    ready = not errors and not process_report['relation_capacity_issues'] and geometry_complete and legacy_compatible and not statistics["unhandled"] and project.get("inventory_confirmed", False) and (trim_excluded or project.get("sheet4_confirmed", False)) and bool(project.get("station_rows"))
    return {"errors": errors, "warnings": warnings, "geometries": geometries, "regions": regions,
            "statuses": statuses, "statistics": statistics, "status_details": details, "ready": bool(ready),
            "geometry_complete": geometry_complete, "legacy_compatible": legacy_compatible,
            "process_config": process_report,
            "geometry_state": ("CURRENT" if batch else "NOT_COMPILED_OR_STALE") if staged else "LEGACY",
            "geometry_records": {**(batch["records"] if batch and not annotation_only else {}), **imported_records},
            "pairing": pairing, "drawing_source_supplement": deepcopy(drawing.get("source_supplement"))}


class ProjectStore:
    def __init__(self, root, drawing, part_id):
        self.root, self.drawing = Path(root), drawing
        self.directory = self.root / "projects" / part_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "project.json"
        self.empty = new_project(drawing, part_id)

    def load(self):
        project = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else deepcopy(self.empty)
        basic_check(project, self.drawing)
        for key in ("source_file_name", "coordinate_system", "source_units"):
            project.setdefault(key, self.empty[key])
        return project

    def save(self, project, expected_revision):
        basic_check(project, self.drawing)
        if project["part_id"] != self.empty["part_id"]:
            raise ValueError("当前图纸的零件编号由 configs/app.json 确定；新零件请配置独立来源并重新启动，避免覆盖当前项目。")
        previous = self.load()
        if type(expected_revision) is not int or expected_revision != previous["revision"]:
            raise ValueError("保存版本冲突，请先备份当前标注并重新读取服务器项目。")
        project = deepcopy(project)
        old = {op["uid"]: op for op in previous["operations"]}
        preserve_bend_angles(project, previous)
        renamed = {old[op["uid"]]["operation_id"]: op["operation_id"] for op in project["operations"]
                   if op["uid"] in old and old[op["uid"]]["operation_id"] != op["operation_id"]}
        for op in project["operations"]:
            op["cad_parent_operation_id"] = renamed.get(op.get("cad_parent_operation_id"), op.get("cad_parent_operation_id"))
            op["required_predecessor_ids"] = [renamed.get(x, x) for x in op.get("required_predecessor_ids", [])]
            for field in ('not_later_than_operation_ids','composite_operation_ids'):
                if field in op:op[field]=[renamed.get(x,x) for x in op[field]]
            if op.get('superseded_by_operation_id'):
                op['superseded_by_operation_id']=renamed.get(op['superseded_by_operation_id'],op['superseded_by_operation_id'])
            previous_op = old.get(op["uid"])
            if previous_op and previous_op.get("source_segment_ids") != op.get("source_segment_ids") and previous_op.get("region") == op.get("region") and op.get("region"):
                op["region"]["confirmed"] = False
        for fixed in project.get("fixed_rows", []):
            fixed["operation_id"] = renamed.get(fixed.get("operation_id"), fixed.get("operation_id"))
        if project.get("workflow_version") == 2:
            from .pairing import synchronize_pairing
            synchronize_pairing(project)
        report = validate_project(project, self.drawing)
        project["revision"] = previous["revision"] + 1
        project["saved_at"] = datetime.now(timezone.utc).isoformat()
        revisions = self.directory / "revisions"
        revisions.mkdir(exist_ok=True)
        encoded = json.dumps(project, ensure_ascii=False, indent=2, allow_nan=False)
        temporary = self.directory / (uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_text(encoded, encoding="utf-8")
            # Prepare the immutable revision before committing canonical state.
            # If history cannot be written, the existing project stays intact.
            (revisions / f"revision_{project['revision']:06d}_{uuid.uuid4().hex[:8]}.json").write_text(encoded, encoding="utf-8")
            temporary.replace(self.path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return {"project": project, "report": report, "path": str(self.path.relative_to(self.root))}
