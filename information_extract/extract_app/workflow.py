"""Two-stage annotation and explicit, type-driven geometry compilation.

Only the editor contract lives here. Frozen Sheet1--5 consumers are not extended.
Multiple paths retain their own identities; no contour is silently flattened.
"""
from copy import deepcopy
import hashlib
import json
import math

from .geometry import (_primitive, _point, _reverse, _signed_area, _contour,
                       _bend, _bridge, derive_region)
from .pairing import synchronize_pairing


FEATURE_TYPES = [
    {"key": "CIRCLE_PUNCH", "label": "冲孔—圆形", "operation_type": "CIRCLE_PUNCH", "shape": "CIRCULAR"},
    {"key": "IRREGULAR_PUNCH", "label": "冲孔—异形", "operation_type": "IRREGULAR_PUNCH", "shape": "NONCIRCULAR"},
    {"key": "PRE_PUNCH_CIRCULAR", "label": "预孔—圆形", "operation_type": "PRE_PUNCH", "shape": "CIRCULAR"},
    {"key": "PRE_PUNCH_NONCIRCULAR", "label": "预孔—异形", "operation_type": "PRE_PUNCH", "shape": "NONCIRCULAR"},
    {"key": "FLANGING_CIRCULAR", "label": "翻孔—圆形", "operation_type": "FLANGING", "shape": "CIRCULAR"},
    {"key": "FLANGING_NONCIRCULAR", "label": "翻孔—异形", "operation_type": "FLANGING", "shape": "NONCIRCULAR"},
    {"key": "BEND_STRAIGHT", "label": "一般折弯", "operation_type": "BEND", "shape": "STRAIGHT"},
    {"key": "BEND_CURVED", "label": "曲线折弯", "operation_type": "BEND", "shape": "CURVED"},
    {"key": "BEND_Z", "label": "Z折（组合）", "operation_type": "BEND", "shape": "COMPOSITE_Z"},
    {"key": "DIMPLE", "label": "凸米", "operation_type": "DIMPLE", "shape": "UNSPECIFIED"},
    {"key": "DRAW_FORM", "label": "拉包（凸包）", "operation_type": "DRAW_FORM", "shape": "MULTI_CONTOUR"},
    {"key": "BRIDGE", "label": "折桥", "operation_type": "BRIDGE", "shape": "UNSPECIFIED"},
    {"key": "TRIM", "label": "切边", "operation_type": "TRIM", "shape": "UNSPECIFIED"},
    {"key": "MARKING", "label": "刻线", "operation_type": "MARKING", "shape": "UNSPECIFIED"},
]
FEATURE_BY_KEY = {item["key"]: item for item in FEATURE_TYPES}


def annotation_project(project):
    """Upgrade in memory, preserving all old fields and source bindings."""
    result = deepcopy(project)
    result["workflow_version"] = 2
    from .process_config import PLANNING_SCOPE, grouped_capabilities
    result["planning_scope"] = PLANNING_SCOPE
    for row in result.get("station_rows", []):
        row["allowed_operation_types"] = ", ".join(grouped_capabilities(row.get("allowed_operation_types"), omit_trim=True))
    for op in result["operations"]:
        if not op.get("feature_type"):
            kind = op.get("operation_type")
            key = {"PRE_PUNCH": "PRE_PUNCH_CIRCULAR", "FLANGING": "FLANGING_CIRCULAR",
                   "BEND": "BEND_STRAIGHT"}.get(kind, kind)
            # Legacy PRE/FLANGE supported circles only; this is a format migration.
            if key in FEATURE_BY_KEY and not (kind in {"PRE_PUNCH", "FLANGING", "BEND"} and op.get("geometry_mode") == "unbound"):
                op["feature_type"] = key
                op["geometry_shape"] = FEATURE_BY_KEY[key]["shape"]
        elif op["feature_type"] in FEATURE_BY_KEY:
            feature = FEATURE_BY_KEY[op["feature_type"]]
            if not op.get("geometry_shape"):
                op["geometry_shape"] = feature["shape"]
            if not op.get("operation_type"):
                op["operation_type"] = feature["operation_type"]
        if op.get("geometry_mode") == "none":
            op["no_geometry"] = True
        if op.get("geometry_mode") == "bend_direct":
            op.setdefault("bend_source_is_centerline", True)
    synchronize_pairing(result)
    return result


def batch_fingerprint(project, config):
    project = deepcopy(project)
    for op in project.get("operations", []):
        op.pop("expert_station", None)  # Reference edits cannot invalidate geometry.
        if isinstance(op.get('source_business_row'), dict):
            op['source_business_row'].pop('expert_station', None)
    if project.get("workflow_version") == 2:
        synchronize_pairing(project)
    facts = {key: project.get(key) for key in
             ("source_sha256", "part_id", "operations", "ignored_entities",
              "inventory_confirmed", "expected_operation_count")}
    if "planning_scope" in project:
        facts["planning_scope"] = project["planning_scope"]
    facts["geometry_config"] = {key: config.get(key) for key in
                                ("geometry_tolerance", "parallel_tolerance")}
    return hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def current_batch(project, config):
    batch = project.get("geometry_batch")
    return batch if isinstance(batch, dict) and batch.get("input_fingerprint") == batch_fingerprint(project, config) else None


def _paths(segments, tolerance):
    """Split exact primitives into closed connected paths, without bridging gaps."""
    paths, edges, vertices, adjacency = [], [], [], {}
    for source in sorted(segments, key=lambda s: s["id"]):
        row = _primitive(source, tolerance)
        if row["segment_type"] == "CIRCLE" or (row["segment_type"] == "ARC" and abs(row["sweep_angle"]) == 360):
            paths.append({"source_segment_ids": [source["id"]], "segments": [row], "closed": True})
            continue
        pair = []
        for end in ("start", "end"):
            point = _point(row, end)
            matches = [i for i, v in enumerate(vertices) if math.dist(point, v) <= tolerance]
            if len(matches) > 1:
                raise ValueError("轮廓端点过近，连接不唯一；请检查重复或重叠图元。")
            if not matches:
                vertices.append(point)
            pair.append(matches[0] if matches else len(vertices)-1)
        index = len(edges)
        edges.append((pair, row, source["id"]))
        for vertex in pair:
            adjacency.setdefault(vertex, []).append(index)
    if any(len(neighbours) != 2 for neighbours in adjacency.values()):
        raise ValueError("所选轮廓有断口、分叉或重叠；请核对绑定图元，剖面/说明线应单独标记为背景。")
    remaining = set(range(len(edges)))
    while remaining:
        first = min(remaining)
        origin = current = edges[first][0][0]
        ordered, source_ids = [], []
        while True:
            choices = [i for i in adjacency[current] if i in remaining]
            if not choices:
                raise ValueError("轮廓未闭合；不会自动补线。")
            i = min(choices)
            remaining.remove(i)
            pair, row, sid = edges[i]
            forward = pair[0] == current
            ordered.append(deepcopy(row) if forward else _reverse(row))
            source_ids.append(sid)
            current = pair[1 if forward else 0]
            if current == origin:
                break
        area = _signed_area(ordered)
        if abs(area) <= tolerance*tolerance:
            raise ValueError("轮廓面积为零，不能作为闭合边界。")
        if area < 0:
            ordered = [_reverse(row) for row in reversed(ordered)]
            source_ids.reverse()
        paths.append({"source_segment_ids": source_ids, "segments": ordered, "closed": True})
    for i, path in enumerate(paths, 1):
        path.update(path_id=f"PATH_{i:03d}", role="UNSPECIFIED_CONTOUR")
        for seq, row in enumerate(path["segments"], 1):
            row["seq"] = seq
    return paths


def _circle(path, tolerance):
    rows = path["segments"]
    first = rows[0]
    if len(rows) == 1 and first["segment_type"] == "CIRCLE":
        return deepcopy(first)
    if any(row["segment_type"] != "ARC" or
           math.dist(_point(first, "center"), _point(row, "center")) > tolerance or
           abs(first["radius"]-row["radius"]) > tolerance for row in rows):
        raise ValueError("圆形动作绑定的图元未构成同圆心、同半径的完整圆。")
    if abs(math.radians(abs(sum(row["sweep_angle"] for row in rows)))-2*math.pi)*first["radius"] > tolerance:
        raise ValueError("圆弧未构成一整圈；请核对是否缺选或重复。")
    return {**first, "segment_type": "CIRCLE", "start_x": None, "start_y": None,
            "end_x": None, "end_y": None, "sweep_angle": None, "seq": 1}


def compile_operation(op, selected, config):
    """Called only by the explicit batch command, never by select/save/validate."""
    from .business_geometry import business_record
    imported = business_record(op)
    if imported is not None:
        return imported
    tolerance = config["geometry_tolerance"]
    kind, shape = op["operation_type"], FEATURE_BY_KEY[op["feature_type"]]["shape"]
    record = {"operation_id": op["operation_id"], "feature_type": op["feature_type"],
              "source_segment_ids": list(op.get("source_segment_ids", [])),
              "status": "READY", "paths": [], "legacy_geometry": [],
              "legacy_supported": True, "region": None, "issues": []}
    if op.get("no_geometry"):
        if kind == "DRAW_FORM" or (kind in {"PRE_PUNCH", "FLANGING"} and shape == "NONCIRCULAR"):
            record.update(legacy_supported=False, issues=["无适用几何的说明已保存；此新类型/形状仍需旧下游适配，不能借此绕过正式检查。"])
        return record
    if kind == 'BEND' and shape == 'COMPOSITE_Z':
        record.update(status='RULE_PENDING', legacy_supported=False,
                      component_operation_ids=list(op.get('composite_operation_ids', [])),
                      paths=[{'path_id':'Z_SOURCES','role':'COMPOSITE_BEND_SOURCES','closed':False,
                              'source_segment_ids':record['source_segment_ids'],
                              'segments':[_primitive(s,tolerance) for s in selected]}],
                      issues=['Z折按一个动作分配，保留两处折弯来源；多折弯几何/Sheet5适配待完成，不压成一条中线。'])
        return record
    if kind == "BEND" and shape == "CURVED":
        record.update(status="RULE_PENDING", legacy_supported=False,
                      paths=[{"path_id": "RAW", "role": "UNRESOLVED_BEND_SOURCES", "closed": None,
                              "source_segment_ids": record["source_segment_ids"],
                              "segments": [_primitive(s, tolerance) for s in selected]}],
                      issues=["曲线折弯表示及影响区规则尚未确定；已保留全部原始线形，未生成直线中线或 Sheet5。"])
        return record
    if kind == "BEND":
        geometry = _bend(selected, op.get("geometry_options", {}), config,
                         op.get("bend_source_is_centerline", False), preview=True)
        record["paths"] = [{"path_id": "CENTERLINE", "role": "BEND_REFERENCE", "closed": False,
                            "segments": geometry, "source_segment_ids": record["source_segment_ids"]}]
        if not op.get("geometry_options", {}).get("confirmed_span"):
            record.update(status="NEEDS_CONFIRMATION", issues=["已计算一般折弯中线，请核对整体跨度并确认，再重新整理。"])
        else:
            record["legacy_geometry"] = geometry
        if not op.get("region"):
            record["issues"].append("一般折弯影响区尚未填写；Sheet5 未完成。")
            record["status"] = "NEEDS_CONFIRMATION"
        elif record["status"] == "READY":
            try:
                record["region"] = derive_region(op, geometry, op["region"])
            except (ValueError, TypeError, KeyError) as exc:
                record.update(status="NEEDS_CONFIRMATION", issues=[str(exc)])
        return record
    if kind == "BRIDGE":
        geometry = _bridge(selected, op.get("geometry_options", {}), config, preview=True)
        record["paths"] = [{"path_id": "FOOTPRINT", "role": "BRIDGE_FOOTPRINT", "closed": True,
                            "segments": geometry, "source_segment_ids": record["source_segment_ids"]}]
        if not op.get("geometry_options", {}).get("confirmed_footprint"):
            record.update(status="NEEDS_CONFIRMATION", issues=["已计算折桥外围范围，请核对并确认，再重新整理。"])
        else:
            record["legacy_geometry"] = geometry
        return record
    if kind in {"CIRCLE_PUNCH", "IRREGULAR_PUNCH", "PRE_PUNCH", "FLANGING", "DRAW_FORM"}:
        paths = _paths(selected, tolerance)
        record["paths"] = paths
        if op.get('composite_policy')=='INTEGRATED_DRAW_FORM':
            primary=set(op.get('primary_source_segment_ids', []))
            record['component_operation_ids']=list(op.get('composite_operation_ids', []))
            record['composite_policy']='INTEGRATED_DRAW_FORM'
            for path in paths:
                path['role']='DRAW_FORM_PROFILE' if set(path['source_segment_ids'])<=primary else 'INTEGRATED_HOLE'
        if shape == "CIRCULAR":
            circles = [_circle(path, tolerance) for path in paths]
            if kind != "FLANGING" and len(circles) != 1:
                raise ValueError("一个圆形冲孔/预孔应形成一整圈；当前包含多个独立圈，请核对动作绑定。")
            if kind == "FLANGING" and any(math.dist(_point(circles[0], "center"), _point(c, "center")) > tolerance for c in circles):
                raise ValueError("圆形翻孔的多圈不同心，请核对是否混入附近的孔。")
            record["legacy_geometry"] = sorted(circles, key=lambda c: c["radius"])
            for seq, circle in enumerate(record["legacy_geometry"], 1):
                circle["seq"] = seq
        elif kind == "IRREGULAR_PUNCH" and len(paths) == 1:
            try:
                record["legacy_geometry"] = _contour(selected, op.get("geometry_options", {}), tolerance)
            except ValueError as exc:
                record.update(legacy_supported=False, issues=[f"闭合轮廓已记录；旧 Sheet2 起始协议未满足：{exc}"])
        else:
            # The root contract cannot encode path identity or new feature roles.
            record.update(legacy_supported=False,
                          issues=["各闭合轮廓已分别记录；内外/顶部/底部工艺含义未自动推断。现有 Sheet2 消费端尚无此多轮廓/类型适配。"])
        return record
    geometry = [_primitive(s, tolerance) for s in selected]
    for seq, row in enumerate(geometry, 1):
        row["seq"] = seq
    record.update(legacy_geometry=geometry,
                  paths=[{"path_id": "SOURCES", "role": "SOURCE_GEOMETRY", "closed": None,
                          "segments": geometry, "source_segment_ids": record["source_segment_ids"]}])
    return record


def compile_project(project, drawing, annotation_report):
    if annotation_report["errors"]:
        raise ValueError("请先修正动作标注检查中的错误，再整理几何。")
    if not project.get("inventory_confirmed"):
        raise ValueError("请先确认整图动作解释完成，再执行整理几何。")
    index = {s["id"]: s for s in drawing["segments"]}
    records = {}
    for op in project["operations"]:
        from .process_config import excludes_trim
        if op.get('superseded_by_operation_id') or (excludes_trim(project) and op.get("operation_type") == "TRIM"):
            records[op["uid"]] = {"operation_id": op["operation_id"], "status": "READY",
                                   "paths": [], "legacy_geometry": [], "legacy_supported": True,
                                   "issues": [], "excluded_from_allocation": True}
            continue
        try:
            records[op["uid"]] = compile_operation(op, [index[sid] for sid in op.get("source_segment_ids", [])], drawing["config"])
        except (ValueError, KeyError, TypeError) as exc:
            records[op["uid"]] = {"operation_id": op["operation_id"], "feature_type": op.get("feature_type"),
                                  "status": "NEEDS_REVIEW", "paths": [], "legacy_geometry": [],
                                  "legacy_supported": False, "issues": [str(exc)]}
    return {"input_fingerprint": batch_fingerprint(project, drawing["config"]), "records": records}
