"""Express verified WCS geometry in one named ancestor block's local frame."""
from __future__ import annotations

import json
from ezdxf.math import Matrix44, Vec3
from .transform import GeometryError, finite_point, normalize_line

LOCAL_COORDINATE_FIELDS = (
    "local_start_x", "local_start_y", "local_end_x", "local_end_y", "local_center_x", "local_center_y",
)


def block_local_candidates(raw, candidates, insert_instances, target_block_name, tolerance):
    """Invert the named INSERT's cumulative matrix, never re-interpret entity OCS.

    A child block's transform stays inside the named block coordinate system.
    An INSERT above the named block is included in the matrix being inverted.
    """
    frames = []
    matrices = {}
    for instance in insert_instances:
        if instance["block_name"].casefold() != target_block_name.casefold():
            continue
        forward = Matrix44(json.loads(instance["transform_matrix_json"]))
        inverse = forward.copy()
        try:
            inverse.inverse()
        except (ZeroDivisionError, ValueError) as error:
            raise GeometryError("SINGULAR_BLOCK_TRANSFORM", f"块实例 {instance['insert_path']} 无法逆变换。") from error
        matrices[instance["insert_path"]] = forward, inverse
        frames.append({**instance, "inverse_transform_matrix_json": json.dumps(list(inverse))})

    by_source = {row["entity_id"]: row for row in raw}
    result = []
    frame_by_source = {}
    reversed_by_source = {}
    point_count = 0
    missing_frame_rows = 0
    max_roundtrip_error = 0.0
    max_local_abs_z = 0.0
    for old in candidates:
        row = dict(old)
        row.update({field: None for field in LOCAL_COORDINATE_FIELDS})
        source = by_source[row["source_entity_id"]]
        ancestors = [key for key in matrices
                     if source["insert_path"] == key or source["insert_path"].startswith(key + " / ")]
        if not ancestors:
            # Never claim unrelated modelspace geometry is in Plotview 1.
            missing_frame_rows += 1
            result.append(row)
            continue
        frame_path = max(ancestors, key=len)
        forward, inverse = matrices[frame_path]
        frame_by_source[source["entity_id"]] = frame_path
        world_points = {}
        local_points = {}
        for prefix in ("start", "end", "center"):
            if source.get(f"{prefix}_x") is None:
                continue
            world = Vec3(*(source[f"{prefix}_{axis}"] for axis in "xyz"))
            local = finite_point(inverse.transform(world))
            world_points[prefix], local_points[prefix] = world, local
            error = (forward.transform(local) - world).magnitude
            max_roundtrip_error = max(max_roundtrip_error, error)
            max_local_abs_z = max(max_local_abs_z, abs(local.z))
            point_count += 1
            if error > tolerance:
                raise GeometryError("BLOCK_INVERSE_ROUNDTRIP", f"{source['entity_id']} 正/逆变换误差 {error:.17g} 超出容差。")
        reverse = False
        if row["segment_type"] == "LINE" and not source["is_from_polyline"]:
            _, _, _, reverse = normalize_line(local_points["start"], local_points["end"], tolerance)
            if reverse:
                for points in (world_points, local_points):
                    points["start"], points["end"] = points["end"], points["start"]
        reversed_by_source[source["entity_id"]] = reverse
        for prefix, world in world_points.items():
            local = local_points[prefix]
            row[f"{prefix}_x"], row[f"{prefix}_y"] = world.x, world.y
            row[f"local_{prefix}_x"], row[f"local_{prefix}_y"] = local.x, local.y
        result.append(row)
    return result, {
        "target_block_name": target_block_name, "frames": frames,
        "frame_by_source_entity_id": frame_by_source,
        "candidate_line_reversed_from_raw": reversed_by_source,
        "rows_with_local_coordinates": len(result) - missing_frame_rows,
        "rows_without_target_block": missing_frame_rows,
        "inverse_point_checks": point_count,
        "max_wcs_roundtrip_error": max_roundtrip_error,
        "max_local_abs_z": max_local_abs_z,
        "radius_and_sweep_coordinate_system": "WCS; unchanged from the existing geometry schema",
    }
