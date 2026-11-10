"""Instance-aware recursive traversal; no explode, inference or cross-entity grouping."""
from __future__ import annotations

from collections import Counter
import json
import math
from ezdxf.math import Matrix44, Vec3
from ezdxf.xclip import XClip
from .entity_parser import parse_primitive, parse_polyline
from .transform import GeometryError, normalize_line
from .validate import validate
from .candidate_coordinates import block_local_candidates

SUPPORTED = {"LINE", "ARC", "CIRCLE", "LWPOLYLINE"}
GEOMETRY_COLUMNS = [
    "geometry_id", "seq", "segment_type", "start_x", "start_y", "end_x", "end_y",
    "center_x", "center_y", "local_start_x", "local_start_y", "local_end_x", "local_end_y",
    "local_center_x", "local_center_y", "radius", "sweep_angle", "source_entity_id",
]
RAW_COLUMNS = [
    "entity_id", "entity_type", "source_handle", "block_name", "parent_block", "layer",
    "color", "linetype", "is_from_polyline", "polyline_id", "polyline_seq", "closed",
    "start_x", "start_y", "end_x", "end_y", "center_x", "center_y", "radius",
    "start_angle", "end_angle", "sweep_angle", "start_z", "end_z", "center_z",
    "source_entity_type", "source_object_id", "block_path", "insert_path", "instance_key",
    "source_layer", "source_color", "source_linetype", "true_color", "is_visible",
    "extrusion_x", "extrusion_y", "extrusion_z", "bulge", "source_start_width",
    "source_end_width", "candidate_line_reversed", "candidate_line_kind",
    "transform_matrix_json", "source_dxf_json",
]
SOURCE_COLUMNS = ["source_object_id", "source_handle", "source_entity_type", "block_name",
                  "block_path", "insert_path", "instance_key", "status", "segment_count",
                  "candidate_id", "closed", "source_dxf_json", "vertices_json"]
ISSUE_COLUMNS = ["severity", "code", "source_handle", "entity_type", "block_path", "insert_path", "message"]
INSERT_COLUMNS = ["source_handle", "block_name", "parent_block", "block_path", "insert_path",
                  "array_index", "insert_x", "insert_y", "insert_z", "rotation",
                  "xscale", "yscale", "zscale", "extrusion_x", "extrusion_y", "extrusion_z",
                  "base_x", "base_y", "base_z", "transform_matrix_json"]


def json_value(value):
    if isinstance(value, Vec3):
        return list(value)
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(f"Unsupported JSON value: {type(value)}")


def json_text(value):
    return json.dumps(value, default=json_value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class Extractor:
    def __init__(self, document, tolerance=1e-6, relative_tolerance=1e-10, max_depth=64, local_block_name="Plotview 1"):
        if not math.isfinite(tolerance) or tolerance <= 0:
            raise ValueError("tolerance 必须为有限正数。")
        if not math.isfinite(relative_tolerance) or not 0 < relative_tolerance <= 1e-6:
            raise ValueError("relative_tolerance 必须在 (0, 1e-6]。")
        if max_depth < 1:
            raise ValueError("max_depth 必须大于零。")
        self.doc = document
        self.tolerance, self.relative_tolerance, self.max_depth = tolerance, relative_tolerance, max_depth
        self.raw, self.candidates, self.sources, self.inserts, self.issues = [], [], [], [], []
        self.encountered, self.accepted, self.poly_segments = Counter(), Counter(), Counter()
        self.used_blocks = set()
        self.candidate_number = self.polyline_number = 0
        self.local_block_name = local_block_name

    def issue(self, severity, code, entity, blocks, path, message):
        self.issues.append(dict(severity=severity, code=code, source_handle=entity.dxf.get("handle", ""),
                                entity_type=entity.dxftype(), block_path=" / ".join(blocks),
                                insert_path=" / ".join(path), message=message))

    def style(self, entity, parent):
        source_layer = entity.dxf.get("layer", "0")
        layer = parent["layer"] if source_layer == "0" and parent else source_layer
        table = self.doc.layers.get(layer) if layer in self.doc.layers else None
        aci = int(entity.dxf.get("color", 256))
        own_truecolor = entity.dxf.get("true_color")
        if aci == 0:
            color = parent["color"] if parent else 7
            true_color = own_truecolor if own_truecolor is not None else (parent.get("true_color") if parent else None)
        elif aci == 256:
            color = abs(table.dxf.color) if table else 7
            true_color = own_truecolor if own_truecolor is not None else (table.dxf.get("true_color") if table else None)
        else:
            color, true_color = abs(aci), own_truecolor
        linetype = entity.dxf.get("linetype", "BYLAYER")
        if linetype.upper() == "BYBLOCK":
            linetype = parent["linetype"] if parent else "Continuous"
        elif linetype.upper() == "BYLAYER":
            linetype = table.dxf.linetype if table else "Continuous"
        visible = not bool(entity.dxf.get("invisible", 0))
        if table:
            visible = visible and not table.is_off() and not table.is_frozen()
        if parent:
            visible = visible and parent["is_visible"]
        return {"layer": layer, "color": color, "linetype": linetype,
                "true_color": true_color, "is_visible": visible}

    def walk(self, entities, matrix, blocks=(), path=(), parent_style=None):
        for source_index, entity in enumerate(entities, 1):
            kind = entity.dxftype()
            self.encountered[kind] += 1
            if kind == "INSERT":
                self.walk_insert(entity, matrix, blocks, path, parent_style)
                continue
            if kind not in SUPPORTED:
                self.issue("warning", "UNSUPPORTED_ENTITY", entity, blocks, path,
                           f"第一版不支持 {kind}；未输出几何。")
                continue
            source_id = f"S{len(self.sources) + 1:06d}"
            handle = entity.dxf.get("handle", "")
            instance_key = "/".join((*path, str(handle or f"index{source_index}")))
            source = dict(source_object_id=source_id, source_handle=handle, source_entity_type=kind,
                          block_name=blocks[-1] if blocks else "", block_path=" / ".join(blocks),
                          insert_path=" / ".join(path), instance_key=instance_key,
                          status="rejected", segment_count=0, candidate_id=None,
                          closed=bool(entity.closed) if kind == "LWPOLYLINE" else None,
                          source_dxf_json="", vertices_json=None)
            self.sources.append(source)
            try:
                source["source_dxf_json"] = json_text(entity.dxf.all_existing_dxf_attribs())
                if kind == "LWPOLYLINE":
                    source["vertices_json"] = json_text(entity.get_points("xyseb"))
                    parsed = parse_polyline(entity, matrix, self.tolerance, self.relative_tolerance)
                else:
                    parsed = parse_primitive(entity, matrix, self.tolerance, self.relative_tolerance)
                # The whole polyline is accepted or rejected together, never partial.
                style = self.style(entity, parent_style)
            except (GeometryError, ValueError, ZeroDivisionError, OverflowError) as error:
                self.issue("error", getattr(error, "code", "INVALID_GEOMETRY"), entity, blocks, path, str(error))
                continue
            self.accepted[kind] += 1
            self.candidate_number += 1
            candidate_id = f"CAND{self.candidate_number:06d}"
            poly_id = None
            if kind == "LWPOLYLINE":
                self.polyline_number += 1
                poly_id = f"P{self.polyline_number:06d}"
            source.update(status="accepted", segment_count=len(parsed), candidate_id=candidate_id)
            extrusion = entity.dxf.get("extrusion", Vec3(0, 0, 1))
            if kind == "LWPOLYLINE" and entity.has_width:
                self.issue("warning", "POLYLINE_WIDTH", entity, blocks, path,
                           "多段线有宽度：输出中心路径，宽度保留在原始属性中；未生成外边界。")
            if entity.dxf.get("thickness", 0):
                self.issue("warning", "ENTITY_THICKNESS", entity, blocks, path,
                           "实体含 thickness：仅提取基准路径/圆，未生成三维厚度。")
            for seq, geometry in enumerate(parsed, 1):
                row = {col: None for col in RAW_COLUMNS}
                row.update(geometry)
                row.update(entity_id=f"E{len(self.raw) + 1:06d}", source_handle=handle,
                           block_name=source["block_name"], parent_block=blocks[-2] if len(blocks) > 1 else "",
                           **style, is_from_polyline=kind == "LWPOLYLINE", polyline_id=poly_id,
                           polyline_seq=seq if poly_id else None, closed=source["closed"],
                           source_entity_type=kind, source_object_id=source_id,
                           block_path=source["block_path"], insert_path=source["insert_path"], instance_key=instance_key,
                           source_layer=entity.dxf.get("layer", "0"), source_color=entity.dxf.get("color", 256),
                           source_linetype=entity.dxf.get("linetype", "BYLAYER"),
                           extrusion_x=extrusion.x, extrusion_y=extrusion.y, extrusion_z=extrusion.z,
                           candidate_line_reversed=False, candidate_line_kind=None,
                           transform_matrix_json=json_text(list(matrix)), source_dxf_json=source["source_dxf_json"])
                candidate = {col: row.get(col) for col in GEOMETRY_COLUMNS}
                candidate.update(geometry_id=candidate_id, seq=seq, segment_type=row["entity_type"],
                                 source_entity_id=row["entity_id"])
                if row["entity_type"] == "LINE" and not poly_id:
                    s = Vec3(row["start_x"], row["start_y"], row["start_z"])
                    e = Vec3(row["end_x"], row["end_y"], row["end_z"])
                    s, e, line_kind, reversed_line = normalize_line(s, e, self.tolerance)
                    candidate.update(start_x=s.x, start_y=s.y, end_x=e.x, end_y=e.y)
                    row.update(candidate_line_reversed=reversed_line, candidate_line_kind=line_kind)
                if poly_id:
                    self.poly_segments[row["entity_type"]] += 1
                self.raw.append(row)
                self.candidates.append(candidate)
            z_values = [r[k] for r in parsed for k in ("start_z", "end_z", "center_z") if r.get(k) is not None]
            if any(abs(z) > self.tolerance for z in z_values):
                self.issue("warning", "NONZERO_ELEVATION", entity, blocks, path,
                           "几何平行于 XY 但标高非零；raw 保留 Z，geometry 仅有 XY。")

    def walk_insert(self, entity, parent_matrix, blocks, path, parent_style):
        name = entity.dxf.name
        new_blocks = (*blocks, name)
        if len(blocks) >= self.max_depth:
            self.issue("error", "MAX_BLOCK_DEPTH", entity, new_blocks, path, "块嵌套深度超过限制。")
            return
        if name.casefold() in {b.casefold() for b in blocks}:
            self.issue("error", "CYCLIC_BLOCK", entity, new_blocks, path, "循环 Block 引用；停止该分支。")
            return
        block = entity.block()
        if block is None:
            self.issue("error", "MISSING_BLOCK", entity, new_blocks, path, "引用的 Block 不存在。")
            return
        if block.block.dxf.flags & 4:
            self.issue("error", "EXTERNAL_REFERENCE", entity, new_blocks, path, "外部参照未解析，请先在 CAD 绑定外部参照。")
            return
        clip = XClip(entity)
        if clip.has_clipping_path and clip.is_clipping_enabled:
            self.issue("error", "XCLIP_NOT_SUPPORTED", entity, new_blocks, path, "启用 XCLIP，未实现裁剪；停止该块以避免导出不可见延伸部分。")
            return
        self.used_blocks.add(name)
        references = entity.multi_insert() if entity.mcount > 1 else [entity]
        for array_index, reference in enumerate(references):
            new_path = (*path, f"{entity.dxf.get('handle', 'INSERT')}[{array_index}]")
            try:
                values = [reference.dxf.xscale, reference.dxf.yscale, reference.dxf.zscale,
                          reference.dxf.rotation, *reference.dxf.insert, *reference.dxf.extrusion]
                if not all(math.isfinite(v) for v in values) or any(v == 0 for v in values[:3]):
                    raise GeometryError("INVALID_INSERT_TRANSFORM", "INSERT 变换包含非有限参数或零缩放。")
                if reference.dxf.extrusion.magnitude == 0:
                    raise GeometryError("INVALID_EXTRUSION", "INSERT extrusion 为零向量。")
                # Matrix44 uses row vectors: local @ parent applies local first.
                matrix = reference.matrix44() @ parent_matrix
                style = self.style(reference, parent_style)
            except (GeometryError, ValueError, ZeroDivisionError) as error:
                self.issue("error", getattr(error, "code", "INVALID_INSERT_TRANSFORM"), entity, new_blocks, new_path, str(error))
                continue
            insert = reference.dxf.insert
            extrusion = reference.dxf.extrusion
            base = block.block.dxf.base_point
            self.inserts.append(dict(source_handle=entity.dxf.handle, block_name=name,
                                     parent_block=blocks[-1] if blocks else "", block_path=" / ".join(new_blocks),
                                     insert_path=" / ".join(new_path), array_index=array_index,
                                     insert_x=insert.x, insert_y=insert.y, insert_z=insert.z,
                                     rotation=reference.dxf.rotation, xscale=reference.dxf.xscale,
                                     yscale=reference.dxf.yscale, zscale=reference.dxf.zscale,
                                     extrusion_x=extrusion.x, extrusion_y=extrusion.y, extrusion_z=extrusion.z,
                                     base_x=base.x, base_y=base.y, base_z=base.z, transform_matrix_json=json_text(list(matrix))))
            self.walk(block, matrix, new_blocks, new_path, style)

    def extract(self) -> dict:
        self.walk(self.doc.modelspace(), Matrix44())
        self.candidates, block_local = block_local_candidates(
            self.raw, self.candidates, self.inserts, self.local_block_name, self.tolerance,
        )
        validation = validate(self.raw, self.candidates, self.tolerance)
        for error in validation["failures"]:
            row = next((r for r in self.raw if r["entity_id"] == error["entity_id"]), {})
            self.issues.append(dict(severity="error", code=error["code"], source_handle=row.get("source_handle", ""),
                                    entity_type=row.get("source_entity_type", ""), block_path=row.get("block_path", ""),
                                    insert_path=row.get("insert_path", ""), message=error["message"]))
        definitions = [b.name for b in self.doc.blocks if not b.name.upper().startswith(("*MODEL_SPACE", "*PAPER_SPACE"))]
        stats = {
            "top_level_insert_count": sum(e.dxftype() == "INSERT" for e in self.doc.modelspace()),
            "block_count_including_layouts": len(self.doc.blocks),
            "block_definition_count": len(definitions), "referenced_block_count": len(self.used_blocks),
            "unused_block_definitions": [b for b in definitions if b not in self.used_blocks],
            "insert_instance_count": len(self.inserts), "encountered_entity_counts": dict(self.encountered),
            "accepted_source_entity_counts": dict(self.accepted), "polyline_segment_counts": dict(self.poly_segments),
            "raw_segment_counts": dict(Counter(r["entity_type"] for r in self.raw)),
            "raw_row_count": len(self.raw), "candidate_count": self.candidate_number,
            "geometry_row_count": len(self.candidates), "rejected_source_count": sum(s["status"] == "rejected" for s in self.sources),
            "closed_polyline_count": sum(s["source_entity_type"] == "LWPOLYLINE" and s["closed"] for s in self.sources),
            "hidden_raw_row_count": sum(not r["is_visible"] for r in self.raw),
            "issue_counts": dict(Counter(i["severity"] for i in self.issues)),
        }
        return {"raw_entities": self.raw, "geometry": self.candidates, "source_entities": self.sources,
                "insert_instances": self.inserts, "issues": self.issues, "stats": stats, "validation": validation,
                "block_local_coordinates": block_local,
                "columns": {"raw_entities": RAW_COLUMNS, "geometry": GEOMETRY_COLUMNS,
                            "source_entities": SOURCE_COLUMNS, "insert_instances": INSERT_COLUMNS, "issues": ISSUE_COLUMNS}}
