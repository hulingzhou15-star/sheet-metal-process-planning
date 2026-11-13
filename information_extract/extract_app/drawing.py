"""Read-only DXF adapter: every exported curve parameter is in material XY."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from ezdxf.math import Matrix44, Vec3

from .legacy.dxf_loader import load_dxf
from .legacy.extractor import Extractor


NUMERIC_FIELDS = ("start_x", "start_y", "end_x", "end_y", "center_x", "center_y", "radius", "sweep_angle")


def remove_confirmed_duplicate_lines(segments, rules, tolerance):
    """Apply only explicit, geometrically verified source removals; retain IDs."""
    index = {s["id"]: s for s in segments}
    removed, audit = set(), []
    for rule in rules:
        keep_id, remove_id = rule["keep_id"], rule["remove_id"]
        if keep_id == remove_id or keep_id not in index or remove_id not in index:
            raise ValueError("已确认重线删除清单的来源 ID 无效，请核对当前 DXF。")
        if remove_id in removed or keep_id in removed:
            raise ValueError("重线删除清单重复或引用了已删除来源。")
        keep, duplicate = index[keep_id], index[remove_id]
        if keep["segment_type"] != "LINE" or duplicate["segment_type"] != "LINE":
            raise ValueError("已确认重线删除只适用于两条直线。")
        a, b = ((keep[f"{end}_x"], keep[f"{end}_y"]) for end in ("start", "end"))
        c, d = ((duplicate[f"{end}_x"], duplicate[f"{end}_y"]) for end in ("start", "end"))
        if not ((math.dist(a, c) <= tolerance and math.dist(b, d) <= tolerance)
                or (math.dist(a, d) <= tolerance and math.dist(b, c) <= tolerance)):
            raise ValueError("待删除直线与保留直线不重合，拒绝删除。")
        removed.add(remove_id)
        audit.append({"removed_source": duplicate, "retained_source_id": keep_id,
                      "reason": "USER_CONFIRMED_DUPLICATE_LINE"})
    # A retained source must survive even if a later rule attempts to remove it.
    if any(item["retained_source_id"] in removed for item in audit):
        raise ValueError("重线删除清单不能删除其它规则保留的来源。")
    return [s for s in segments if s["id"] not in removed], audit


def load_drawing(path: Path, config: dict):
    from .geometry import segment_bbox
    from .source_cleanup import load_cleanup_manifest, apply_source_cleanup, apply_annotation_cleanup
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    cleanup = load_cleanup_manifest(path, config, before)
    doc, audit = load_dxf(path)
    declared_units = {0: "源单位（未指定）", 4: "mm", 1: "inch"}.get(doc.units, f"INSUNITS={doc.units}")
    confirmed_units = config.get("confirmed_source_units")
    if confirmed_units is not None:
        unit_codes = {"mm": 4, "inch": 1}
        if confirmed_units not in unit_codes or doc.units not in {0, unit_codes[confirmed_units]}:
            raise ValueError("人工确认单位无效或与 DXF 已声明单位冲突；未换算坐标。")
    resolved_units = confirmed_units if confirmed_units is not None else declared_units
    data = Extractor(doc, tolerance=config["geometry_tolerance"],
                     local_block_name=config["material_block"]).extract()
    if not data["validation"]["passed"]:
        raise ValueError("DXF 基础几何校验未通过，请查看原始解析问题。")
    mode = config.get("coordinate_mode", "block_local")
    if mode not in {"block_local", "world_xy"}:
        raise ValueError("coordinate_mode 仅支持 block_local 或 world_xy。")
    frames = data["block_local_coordinates"]["frames"]
    if mode == "world_xy":
        # Explicitly selected drawing coordinates; never assume a missing block
        # is permission to change a P001 project's frame.
        if any(abs(raw.get(key) or 0) > config["geometry_tolerance"]
               for raw in data["raw_entities"] for key in ("start_z", "end_z", "center_z")):
            raise ValueError("模型空间存在非零 Z 几何，不能作为平面 XY 标注图。")
        frame = {"coordinate_mode": mode, "inverse_transform_matrix_json": json.dumps(list(Matrix44()))}
    else:
        if len(frames) != 1:
            raise ValueError(f"需唯一材料坐标实例，当前找到 {len(frames)} 个；请明确材料块实例。")
        frame = frames[0]
    inverse = Matrix44(json.loads(frame["inverse_transform_matrix_json"]))
    u, v = inverse.transform_direction(Vec3(1, 0, 0)), inverse.transform_direction(Vec3(0, 1, 0))
    determinant = u.x * v.y - u.y * v.x
    candidates = {row["source_entity_id"]: row for row in data["geometry"]}
    segments = []
    for raw in data["raw_entities"]:
        row = candidates[raw["entity_id"]]
        if mode == "block_local" and row.get("local_center_x" if row["segment_type"] == "CIRCLE" else "local_start_x") is None:
            raise ValueError("存在目标材料块之外的几何，不能静默混入或丢弃；请先选择正确源图范围。")
        key = [before, raw["source_handle"], raw["insert_path"], raw["polyline_seq"] or 0]
        stable = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:24]
        segment = {"id": "S_" + stable, "segment_type": row["segment_type"],
                   "source_group": raw["instance_key"], "source_handle": raw["source_handle"],
                   "instance_path": raw["insert_path"], "polyline_seq": raw["polyline_seq"],
                   "layer": raw["layer"], "linetype": raw["linetype"],
                   "color": raw["color"], "is_visible": raw["is_visible"],
                   "raw_entity_id": raw["entity_id"], "source_entity_type": raw["source_entity_type"]}
        for name in NUMERIC_FIELDS:
            segment[name] = row.get("local_" + name) if mode == "block_local" and name not in {"radius", "sweep_angle"} else row.get(name)
        if segment["segment_type"] in {"ARC", "CIRCLE"}:
            if abs(u.magnitude - v.magnitude) > config["parallel_tolerance"] or abs(u.dot(v)) > config["parallel_tolerance"]:
                raise ValueError("材料坐标逆变换使圆成为椭圆，当前表示不支持。")
            segment["radius"] *= (u.magnitude + v.magnitude) / 2
            if segment["sweep_angle"] is not None:
                segment["sweep_angle"] *= 1 if determinant > 0 else -1
            if segment["segment_type"] == "ARC":
                a = math.atan2(segment["start_y"] - segment["center_y"], segment["start_x"] - segment["center_x"])
                a += math.radians(segment["sweep_angle"])
                end = (segment["center_x"] + segment["radius"] * math.cos(a),
                       segment["center_y"] + segment["radius"] * math.sin(a))
                if math.dist(end, (segment["end_x"], segment["end_y"])) > config["geometry_tolerance"]:
                    raise ValueError("材料坐标圆弧重建不一致。")
        segment["bbox"] = list(segment_bbox(segment))
        segments.append(segment)
    if len({s["id"] for s in segments}) != len(segments):
        raise ValueError("原始实体来源键重复，禁止不明确绑定。")
    if not segments:
        raise ValueError("没有可显示的受支持平面几何。")
    segments, removed_sources = remove_confirmed_duplicate_lines(
        segments, config.get("confirmed_duplicate_line_removals", []), config["geometry_tolerance"])
    from .source_supplement import append_confirmed_supplement
    source_bounds = [min(s["bbox"][0] for s in segments), min(s["bbox"][1] for s in segments),
                     max(s["bbox"][2] for s in segments), max(s["bbox"][3] for s in segments)]
    segments, supplement, source_dependencies = append_confirmed_supplement(
        path, config, before, segments, resolved_units, source_bounds)
    from .source_deduplication import remove_confirmed_redundant_sources
    segments, redundant_sources = remove_confirmed_redundant_sources(
        segments, config.get("confirmed_duplicate_source_removals", []), config["geometry_tolerance"])
    removed_sources.extend(redundant_sources)
    segments, cleanup_removed, background_source_ids = apply_source_cleanup(
        segments, cleanup, config["geometry_tolerance"])
    removed_sources.extend(cleanup_removed)
    from .source_correction import apply_confirmed_correction
    segments, correction, correction_dependencies = apply_confirmed_correction(path, config, before, segments)
    source_dependencies.extend(correction_dependencies)
    revision = None
    if config.get('confirmed_geometry_revision'):
        revision_config = dict(config, confirmed_geometry_correction=config['confirmed_geometry_revision'])
        segments, revision, revision_dependencies = apply_confirmed_correction(path, revision_config, before, segments)
        source_dependencies.extend(revision_dependencies)
    bounds = [min(s["bbox"][0] for s in segments), min(s["bbox"][1] for s in segments),
              max(s["bbox"][2] for s in segments), max(s["bbox"][3] for s in segments)]
    annotations, displayed_text_handles = [], set()
    if config.get("display_annotations", False):
        # Display-only TEXT, with a deliberately narrow supported placement.
        # Other placements remain visible as unsupported diagnostics.
        from .legacy.extractor import Extractor as StyleReader
        style_reader = StyleReader(doc)
        for entity in doc.modelspace().query("TEXT"):
            if (mode != "world_xy" or entity.dxf.halign or entity.dxf.valign
                    or entity.dxf.text_generation_flag or entity.dxf.extrusion != Vec3(0, 0, 1)):
                continue
            point = entity.dxf.insert
            if abs(point.z) > config["geometry_tolerance"]:
                continue
            annotations.append({"source_handle": entity.dxf.handle, "text": entity.plain_text(),
                                "x": point.x, "y": point.y, "height": entity.dxf.height,
                                "rotation": entity.dxf.rotation, "width_factor": entity.dxf.width,
                                **style_reader.style(entity, None)})
            displayed_text_handles.add(entity.dxf.handle)
        for entity in doc.modelspace().query("MTEXT"):
            # Narrow display-only subset: top-left, one line, no wrapping or
            # columns. Unsupported layouts stay in the explicit issue list.
            text = entity.plain_text()
            direction = entity.dxf.get("text_direction")
            if (mode != "world_xy" or entity.dxf.attachment_point != 1
                    or entity.has_columns or entity.dxf.width not in (None, 0) or "\n" in text
                    or entity.dxf.flow_direction != 1
                    or entity.dxf.extrusion != Vec3(0, 0, 1)
                    or (direction is not None and abs(direction.z) > config["geometry_tolerance"])):
                continue
            point = entity.dxf.insert
            if abs(point.z) > config["geometry_tolerance"]:
                continue
            annotations.append({"source_handle": entity.dxf.handle, "text": text,
                                "x": point.x, "y": point.y, "height": entity.dxf.char_height,
                                "rotation": entity.get_rotation(), "width_factor": 1,
                                "baseline": "text-before-edge", "source_entity_type": "MTEXT",
                                **style_reader.style(entity, None)})
            displayed_text_handles.add(entity.dxf.handle)
    remove_text = bool(cleanup and cleanup.get("remove_drawing_text"))
    removed_annotations = annotations if remove_text else []
    if remove_text:
        annotations = []
    issues = [issue for issue in data["issues"]
              if not (remove_text and issue["entity_type"] in {"TEXT", "MTEXT"})
              and not (issue["code"] == "UNSUPPORTED_ENTITY" and issue["entity_type"] in {"TEXT", "MTEXT"}
                      and issue["source_handle"] in displayed_text_handles and not issue["insert_path"])]
    issues, removed_annotation_issues = apply_annotation_cleanup(issues, cleanup)
    if hashlib.sha256(path.read_bytes()).hexdigest() != before:
        raise ValueError("读取期间源 DXF 已改变，请重新加载。")
    return {"source": {"sha256": before, "file_name": path.name,
                       "coordinate_system": "drawing WCS XY" if mode == "world_xy" else "block-local material XY",
                       "units": resolved_units, "declared_units": declared_units,
                       "unit_basis": "USER_CONFIRMED" if confirmed_units is not None else "DXF_INSUNITS" if doc.units else "UNDECLARED",
                       "insunits": doc.units, "material_block": config["material_block"],
                       "material_frame": frame, "dxf_audit": audit},
            "segments": segments, "annotations": annotations, "bounds": bounds, "issues": issues,
            "config": config, "raw_stats": data["stats"], "removed_sources": removed_sources,
            "background_source_ids": background_source_ids,
            "source_supplement": supplement, "source_dependencies": source_dependencies,
            "source_correction": correction,
            "source_revision": revision,
            "removed_annotations": removed_annotations,
            "removed_annotation_issues": removed_annotation_issues,
            "source_cleanup": {"manifest": config["confirmed_source_cleanup"],
                               "based_on_project_revision": cleanup["based_on_project_revision"],
                               "operation_source_count": len(cleanup["operation_source_ids"]),
                               "outline_source_count": len(background_source_ids),
                               "removed_geometry_count": len(cleanup_removed),
                               "removed_text_count": len(removed_annotations),
                               "removed_auxiliary_annotation_count": len(removed_annotation_issues)} if cleanup else None}
