"""D3 only. Reuse M2 representative points; never create graph/process rules."""
from collections import Counter
import math

from src.graph.geometry_features import GeometryFeatureExtractorV1


def influence_rectangle(region):
    """Recorded axial bend line extruded to its recorded far boundary."""
    if region.bend_axis == "VERTICAL" and region.start_x == region.end_x:
        if ((region.affected_side == "NEGATIVE_X" and region.far_boundary_coord <= region.start_x)
                or (region.affected_side == "POSITIVE_X" and region.far_boundary_coord >= region.start_x)):
            x0, x1 = sorted((region.start_x, region.far_boundary_coord))
            y0, y1 = sorted((region.start_y, region.end_y))
            return (x0, y0, x1, y1)
    elif region.bend_axis == "HORIZONTAL" and region.start_y == region.end_y:
        if ((region.affected_side == "NEGATIVE_Y" and region.far_boundary_coord <= region.start_y)
                or (region.affected_side == "POSITIVE_Y" and region.far_boundary_coord >= region.start_y)):
            x0, x1 = sorted((region.start_x, region.end_x))
            y0, y1 = sorted((region.start_y, region.far_boundary_coord))
            return (x0, y0, x1, y1)
    return None


def local_region_distribution(context, assignments):
    rectangles, invalid_regions = {}, []
    for region in context.bend_influences:
        box = influence_rectangle(region)
        if box is None or box[0] == box[2] or box[1] == box[3]:
            invalid_regions.append({"region_id": region.influence_region_id,
                                    "reason": "Recorded axis, endpoints, side or boundary cannot define a positive-area rectangle"})
        else:
            rectangles[region.influence_region_id] = box
    base = {"exploratory_only": True, "valid_region_count": len(rectangles), "invalid_regions": invalid_regions,
            "representative_point_source": "M2 GeometryFeatureExtractorV1, converted back to material coordinates",
            "boundary_convention": "closed rectangle; shared-boundary membership is reported",
            "equal_smallest_area_policy": "unresolved; no region assigned without a unique smallest region"}
    try:
        extractor = GeometryFeatureExtractorV1(context.part_data)
    except ValueError as exc:
        return {**base, "status": "unavailable", "reason": str(exc), "assigned_operation_count": 0, "regions": []}
    norm = extractor.normalization
    normalized_boxes = {region_id: ((box[0]-norm.center[0])/norm.length_scale,
                                    (box[1]-norm.center[1])/norm.length_scale,
                                    (box[2]-norm.center[0])/norm.length_scale,
                                    (box[3]-norm.center[1])/norm.length_scale)
                        for region_id, box in rectangles.items()}
    members = {region_id: [] for region_id in rectangles}
    assignments_audit, invalid_points, ambiguous = [], [], []
    missing_geometry = valid_points = outside = 0
    for op in sorted(context.part_data.operations, key=lambda op: op.operation_id):
        try:
            feature = extractor.extract(op)
        except ValueError as exc:
            invalid_points.append({"operation_id": op.operation_id, "reason": str(exc)})
            continue
        if not feature.has_geometry:
            missing_geometry += 1
            continue
        normalized_point = (feature.geom_center_x_norm, feature.geom_center_y_norm)
        point = (feature.geom_center_x_norm*norm.length_scale+norm.center[0],
                 feature.geom_center_y_norm*norm.length_scale+norm.center[1])
        if not all(math.isfinite(v) for v in point):
            invalid_points.append({"operation_id": op.operation_id, "reason": "Nonfinite M2 representative point"})
            continue
        valid_points += 1
        # Compare in M2's normalized coordinates to avoid inverse-transform
        # roundoff moving a representative point off a recorded boundary.
        containing = [region_id for region_id, (x0, y0, x1, y1) in normalized_boxes.items()
                      if x0 <= normalized_point[0] <= x1 and y0 <= normalized_point[1] <= y1]
        if not containing:
            outside += 1
            continue
        areas = {region_id: (rectangles[region_id][2]-rectangles[region_id][0])
                           *(rectangles[region_id][3]-rectangles[region_id][1]) for region_id in containing}
        smallest = min(areas.values())
        selected = [region_id for region_id in containing if areas[region_id] == smallest]
        if len(selected) != 1:
            ambiguous.append({"operation_id": op.operation_id, "point": point, "tied_region_ids": sorted(selected)})
            continue
        region_id = selected[0]
        members[region_id].append(op.operation_id)
        assignments_audit.append({"operation_id": op.operation_id, "representative_point": point,
                                  "containing_region_ids": sorted(containing), "assigned_region_id": region_id,
                                  "on_boundary": any(normalized_point[0] in (normalized_boxes[r][0], normalized_boxes[r][2])
                                                     or normalized_point[1] in (normalized_boxes[r][1], normalized_boxes[r][3]) for r in containing)})
    ops = context.operations
    stats = []
    for region_id in sorted(rectangles):
        ids = members[region_id]
        distribution = dict(sorted(Counter(assignments[op_id] for op_id in ids).items()))
        stats.append({"region_id": region_id, "bend_operation_id": context.regions[region_id].bend_operation_id,
                      "rectangle": rectangles[region_id], "operation_count": len(ids), "operation_ids": ids,
                      "operation_type_distribution": dict(sorted(Counter(ops[op_id].operation_type for op_id in ids).items())),
                      "station_count": len(distribution), "station_distribution": distribution})
    return {**base, "status": "partial" if invalid_points or invalid_regions or ambiguous else "available",
            "valid_representative_point_count": valid_points, "no_geometry_count": missing_geometry,
            "outside_region_count": outside, "invalid_representative_points": invalid_points,
            "ambiguous_smallest_regions": ambiguous, "assigned_operation_count": len(assignments_audit),
            "assigned_operations": assignments_audit, "nonempty_region_count": sum(bool(ids) for ids in members.values()),
            "regions": stats}
