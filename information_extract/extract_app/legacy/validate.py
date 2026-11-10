"""Geometry-only checks. No clustering, process identification or matching."""
from __future__ import annotations

from collections import defaultdict, Counter
import math


def distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def validate(raw: list[dict], candidates: list[dict], tolerance: float) -> dict:
    failures = []
    checks = Counter()
    by_source = {r["entity_id"]: r for r in raw}
    seen_source = set()
    groups = defaultdict(list)
    for row in candidates:
        groups[row["geometry_id"]].append(row)
        source_id = row["source_entity_id"]
        source = by_source.get(source_id)
        if source is None or source_id in seen_source:
            failures.append({"code": "SOURCE_LINK", "entity_id": source_id, "message": "源实体链接不存在或重复。"})
            continue
        seen_source.add(source_id)
        if row["segment_type"] == "LINE" and not source["is_from_polyline"]:
            prefix = "local_" if row.get("local_start_x") is not None else ""
            sx, sy, ex, ey = (row[prefix + k] for k in ("start_x", "start_y", "end_x", "end_y"))
            checks["independent_line_direction_local" if prefix else "independent_line_direction_wcs"] += 1
            if abs(sy - ey) < tolerance:
                checks["independent_horizontal_lines"] += 1
                correct = sx <= ex
            elif abs(sx - ex) < tolerance:
                checks["independent_vertical_lines"] += 1
                correct = sy <= ey
            else:
                checks["independent_diagonal_lines"] += 1
                correct = (sx, sy) <= (ex, ey)
            if not correct:
                failures.append({"code": "LINE_DIRECTION", "entity_id": source_id, "message": "独立 LINE 方向不符合规范。"})
        if row["segment_type"] == "ARC":
            checks["arcs_checked"] += 1
            start = row["start_x"], row["start_y"]
            end = row["end_x"], row["end_y"]
            center = row["center_x"], row["center_y"]
            radius = row["radius"]
            a = math.atan2(start[1] - center[1], start[0] - center[0]) + math.radians(row["sweep_angle"])
            reconstructed = center[0] + radius * math.cos(a), center[1] + radius * math.sin(a)
            errors = [abs(distance(start, center) - radius), abs(distance(end, center) - radius), distance(reconstructed, end)]
            if max(errors) > tolerance:
                failures.append({"code": "ARC_RECONSTRUCTION", "entity_id": source_id,
                                 "message": f"圆弧起点/半径/有向角重建误差 {max(errors):.17g}。"})
        if row["segment_type"] == "CIRCLE":
            checks["circles_checked"] += 1
            if any(row.get(k) is not None for k in ("start_x", "start_y", "end_x", "end_y", "sweep_angle",
                                                   "local_start_x", "local_start_y", "local_end_x", "local_end_y")):
                failures.append({"code": "CIRCLE_BLANKS", "entity_id": source_id, "message": "CIRCLE 起终点和 sweep_angle 应为空。"})
    for candidate_id, segments in groups.items():
        if [s["seq"] for s in segments] != list(range(1, len(segments) + 1)):
            failures.append({"code": "SEQ", "entity_id": candidate_id, "message": "seq 不连续。"})
        first = by_source[segments[0]["source_entity_id"]]
        if not first["is_from_polyline"]:
            if len(segments) != 1:
                failures.append({"code": "INDEPENDENT_GROUP", "entity_id": candidate_id, "message": "独立实体被跨实体合并。"})
            continue
        checks["polylines_checked"] += 1
        closed = first["closed"]
        if closed:
            checks["closed_polylines_checked"] += 1
        pairs = list(zip(segments, segments[1:]))
        if closed:
            pairs.append((segments[-1], segments[0]))
        max_gap = max((distance((s["end_x"], s["end_y"]), (t["start_x"], t["start_y"])) for s, t in pairs), default=0)
        checks["polyline_connections_checked"] += len(pairs)
        if max_gap > tolerance:
            failures.append({"code": "POLYLINE_CONTINUITY", "entity_id": candidate_id,
                             "message": f"多段线连续/闭合误差 {max_gap:.17g}。"})
        if segments[0].get("local_start_x") is not None:
            local_gap = max((distance((s["local_end_x"], s["local_end_y"]),
                                      (t["local_start_x"], t["local_start_y"])) for s, t in pairs), default=0)
            checks["local_polyline_connections_checked"] += len(pairs)
            if closed:
                checks["local_closed_polylines_checked"] += 1
            if local_gap > tolerance:
                failures.append({"code": "LOCAL_POLYLINE_CONTINUITY", "entity_id": candidate_id,
                                 "message": f"局部多段线连续/闭合误差 {local_gap:.17g}。"})
    if seen_source != set(by_source):
        failures.append({"code": "UNUSED_RAW_ENTITY", "entity_id": "", "message": "存在未关联的 raw 实体。"})
    return {"passed": not failures, "checks": dict(checks), "failures": failures}
