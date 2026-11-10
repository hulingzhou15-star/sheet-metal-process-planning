"""Remove only explicitly confirmed sources wholly covered by retained geometry."""
import math

from .geometry import _primitive


def _arc_ranges(segment):
    start = math.atan2(segment["start_y"]-segment["center_y"], segment["start_x"]-segment["center_x"])
    sweep = math.radians(segment["sweep_angle"])
    low = (start + min(0, sweep)) % math.tau
    high = low + abs(sweep)
    return [(low, min(high, math.tau))] + ([(0, high-math.tau)] if high > math.tau else [])


def wholly_covered(candidate, retained, tolerance):
    """Analytic coverage, including reversed lines/arcs; no proximity merging."""
    if candidate["segment_type"] != retained["segment_type"]:
        return False
    a, b = _primitive(candidate, tolerance), _primitive(retained, tolerance)
    kind = a["segment_type"]
    if kind == "LINE":
        origin = (b["start_x"], b["start_y"])
        v = (b["end_x"]-origin[0], b["end_y"]-origin[1])
        length = math.hypot(*v)
        for end in ("start", "end"):
            p = (a[end+"_x"]-origin[0], a[end+"_y"]-origin[1])
            projection = (p[0]*v[0] + p[1]*v[1]) / length
            perpendicular = abs(p[0]*v[1] - p[1]*v[0]) / length
            if perpendicular > tolerance or projection < -tolerance or projection > length+tolerance:
                return False
        return True
    if kind not in {"ARC", "CIRCLE"}:
        return False
    if math.dist((a["center_x"], a["center_y"]), (b["center_x"], b["center_y"])) + abs(a["radius"]-b["radius"]) > tolerance:
        return False
    if kind == "CIRCLE":
        return True
    a_ranges, b_ranges = _arc_ranges(a), _arc_ranges(b)
    covered = sum(max(0, min(v, z)-max(u, w)) for u, v in a_ranges for w, z in b_ranges)
    length = sum(v-u for u, v in a_ranges)
    return max(0, length-covered) * a["radius"] <= tolerance


def remove_confirmed_redundant_sources(segments, rules, tolerance):
    index = {s["id"]: s for s in segments}
    removed, retained_ids, audit = set(), set(), []
    for rule in rules:
        keep_id, remove_id = rule["keep_id"], rule["remove_id"]
        if (keep_id == remove_id or keep_id not in index or remove_id not in index
                or remove_id in removed):
            raise ValueError("重合图元清理清单的来源无效或重复。")
        relation = rule.get("relation")
        if relation not in {"COINCIDENT", "FULLY_COVERED"}:
            raise ValueError("清理只接受完全重合或整段覆盖关系。")
        retained, candidate = index[keep_id], index[remove_id]
        if not wholly_covered(candidate, retained, tolerance):
            raise ValueError("待移除图元没有被保留图元整段覆盖，拒绝删除。")
        if relation == "COINCIDENT" and not wholly_covered(retained, candidate, tolerance):
            raise ValueError("声明为完全重合的两个图元范围不一致。")
        removed.add(remove_id)
        retained_ids.add(keep_id)
        audit.append({"removed_source": candidate, "retained_source_id": keep_id,
                      "reason": "USER_CONFIRMED_REDUNDANT_GEOMETRY", "relation": relation})
    if removed & retained_ids:
        raise ValueError("清理不能移除其它规则要求保留的图元。")
    remaining = [s for s in segments if s["id"] not in removed]
    from .workflow import _paths
    affected_groups = {s.get("source_group") for s in segments if s["id"] in removed} - {None}
    for group in affected_groups:
        before = [s for s in segments if s.get("source_group") == group]
        after = [s for s in remaining if s.get("source_group") == group]
        if not after:
            continue  # Whole duplicate object removed; retained geometry covers it.
        try:
            _paths(before, tolerance)
        except ValueError:
            continue  # The original source group was an open construction object.
        try:
            _paths(after, tolerance)
        except ValueError as error:
            raise ValueError("去重会破坏原闭合轮廓，拒绝删除这些连接段。") from error
    return remaining, audit
