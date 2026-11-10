"""Exact SVG geometry from M2 analytic primitives; D3 membership is reused."""
from collections import Counter
import math

from src.constraints.region_assignment import influence_rectangle, local_region_distribution
from src.graph.geometry_primitives import Arc, Line, geometry_primitives, primitives_bbox
from src.review.plan_diff import type_name


def material_to_svg(point):
    """Only invert SVG's screen y-axis. No engineering rotation or reflection."""
    return (point[0], -point[1])


def primitive_shape(primitive):
    if isinstance(primitive, Line):
        return {"kind": "line", "start": material_to_svg(primitive.start), "end": material_to_svg(primitive.end)}
    if isinstance(primitive, Arc):
        if primitive.full_circle:
            return {"kind": "circle", "center": material_to_svg(primitive.center), "radius": primitive.radius}
        start, end = primitive.endpoints
        return {"kind": "arc", "start": material_to_svg(start), "end": material_to_svg(end),
                "center": material_to_svg(primitive.center), "radius": primitive.radius,
                "large_arc_flag": int(abs(primitive.sweep) > math.pi),
                "sweep_flag": int(primitive.sweep < 0),
                "start_angle": primitive.start_angle, "sweep": primitive.sweep}
    raise ValueError("Unknown frozen primitive")


def build_geometry_catalog(context, config, *, display_outline=None, outline_audit=None,
                           region_assignments=None):
    operations, boxes, issues = {}, [], []
    for op in sorted(context.part_data.operations, key=lambda item: item.operation_id):
        shapes, box = [], None
        if op.geometry_id:
            try:
                primitives = geometry_primitives(context.part_data.geometry_for(op))
                shapes = [primitive_shape(p) for p in primitives]
                box = primitives_bbox(primitives)
                boxes.append(box)
            except ValueError as exc:
                issues.append({"operation_id": op.operation_id, "reason": str(exc)})
        operations[op.operation_id] = {"operation_id": op.operation_id, "operation_type": type_name(op.operation_type),
                                      "has_geometry": bool(op.geometry_id), "renderable": bool(shapes),
                                      "bbox": box, "shapes": shapes}
    if not boxes:
        raise ValueError("No reliable Geometry for a 2D viewport")
    bbox = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    geometry_bbox=bbox
    if display_outline:
        a,b,c,d=display_outline["native_svg_bbox"]
        bbox=(min(bbox[0],a),min(bbox[1],-d),max(bbox[2],c),max(bbox[3],-b))
    x0, y0, x1, y1 = bbox
    padding = max(x1-x0, y1-y0, config["svg"]["minimum_extent"]) * config["svg"]["padding_ratio"]
    width = max(x1-x0, config["svg"]["minimum_extent"])
    height = max(y1-y0, config["svg"]["minimum_extent"])
    view_box = ((x0+x1-width)/2-padding, -(y0+y1+height)/2-padding, width+2*padding, height+2*padding)
    assignments = (region_assignments if region_assignments is not None else
                   {a.operation_id: a.station_id for a in context.expert_reference_plan.assignments})
    d3 = local_region_distribution(context, assignments)
    memberships = {op: "NO_GEOMETRY" if not row["has_geometry"] else "OUTSIDE_REGION" for op, row in operations.items()}
    if d3["status"] == "unavailable":
        memberships.update({op: "UNRESOLVED_REGION" for op, row in operations.items() if row["has_geometry"]})
    for row in d3.get("assigned_operations", []):
        memberships[row["operation_id"]] = row["assigned_region_id"]
    for row in (*d3.get("ambiguous_smallest_regions", []), *d3.get("invalid_representative_points", [])):
        memberships[row["operation_id"]] = "UNRESOLVED_REGION"
    regions = {}
    bend_mapping_issues = []
    for r in context.bend_influences:
        box = influence_rectangle(r)
        regions[r.influence_region_id] = {"region_id": r.influence_region_id, "bend_operation_id": r.bend_operation_id,
                                          "parent_region_id": r.parent_region_id,
                                          "region_level": len(context.region_ancestors[r.influence_region_id]),
                                          "bbox": box, "ancestor_region_ids": sorted(context.region_ancestors[r.influence_region_id])}
        shapes = operations[r.bend_operation_id]["shapes"]
        recorded_endpoints = {material_to_svg((r.start_x,r.start_y)),material_to_svg((r.end_x,r.end_y))}
        if len(shapes) != 1 or shapes[0]["kind"] != "line" or {shapes[0]["start"],shapes[0]["end"]} != recorded_endpoints:
            bend_mapping_issues.append({"operation_id":r.bend_operation_id,"region_id":r.influence_region_id,
                                        "reason":"Sheet5 bend endpoints differ from the recorded M2 line; neither source was altered"})
    coverage = {kind: {"operation_count": sum(row["operation_type"] == kind for row in operations.values()),
                       "rendered_count": sum(row["operation_type"] == kind and row["renderable"] for row in operations.values())}
                for kind in sorted({row["operation_type"] for row in operations.values()})}
    return {"bbox": bbox, "view_box": view_box, "coordinate_direction": "CARTESIAN_Y_UP","display_outline":display_outline,
            "operations": operations, "regions": regions, "membership": memberships,
            "audit": {"geometry_type_coverage": coverage,"geometry_only_bbox":geometry_bbox,
                      "outline":outline_audit or {"status":"not_requested","display_only":True},
                      "renderable_count": sum(row["renderable"] for row in operations.values()),
                      "no_geometry_ids": [op for op, row in operations.items() if not row["has_geometry"]],
                      "unrenderable_geometry": issues, "bend_geometry_mapping_issues":bend_mapping_issues,
                      "region_group_counts": dict(sorted(Counter(memberships.values()).items())),
                      "d3": {key: value for key, value in d3.items() if key not in ("regions", "assigned_operations")}}}
