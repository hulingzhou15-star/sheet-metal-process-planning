"""Exact material-coordinate geometry derived from explicitly selected DXF segments.

The source primitives are never changed.  These helpers only construct the
existing Sheet2/Sheet5 representations; they do not infer manufacturing facts.
"""
from __future__ import annotations

import math
from collections import defaultdict


FIELDS = ("start_x", "start_y", "end_x", "end_y", "center_x", "center_y", "radius", "sweep_angle")
DEFAULT_TOLERANCE = 1e-6


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} 必须是有限数值")
    return float(value)


def _tolerance(config, key="geometry_tolerance"):
    result = _number((config or {}).get(key, DEFAULT_TOLERANCE), key)
    if result <= 0:
        raise ValueError(f"{key} 必须大于零")
    return result


def _point(segment, name):
    return (_number(segment.get(name + "_x"), name + "_x"),
            _number(segment.get(name + "_y"), name + "_y"))


def _row(kind, **values):
    return {"seq": 1, "segment_type": kind, **dict.fromkeys(FIELDS), **values}


def _primitive(segment, tolerance=DEFAULT_TOLERANCE):
    kind = segment.get("segment_type")
    if kind not in {"LINE", "BEND_LINE", "ARC", "CIRCLE", "BRIDGE"}:
        raise ValueError(f"不支持的几何片段类型：{kind}")
    row = _row(kind)
    keys = {"LINE": FIELDS[:4], "BEND_LINE": FIELDS[:4],
            "ARC": FIELDS, "CIRCLE": ("center_x", "center_y", "radius"),
            "BRIDGE": FIELDS[:6]}[kind]
    for key in keys:
        row[key] = _number(segment.get(key), key)
    if kind in {"LINE", "BEND_LINE"} and math.dist(_point(row, "start"), _point(row, "end")) <= tolerance:
        raise ValueError("所选直线长度为零或小于几何容差")
    if kind in {"ARC", "CIRCLE"} and row["radius"] <= 0:
        raise ValueError("圆和圆弧的半径必须大于零")
    if kind == "ARC":
        if not 0 < abs(row["sweep_angle"]) <= 360:
            raise ValueError("圆弧扫角必须非零且绝对值不超过 360°")
        start, end, center = (_point(row, p) for p in ("start", "end", "center"))
        angle = math.atan2(start[1] - center[1], start[0] - center[0]) + math.radians(row["sweep_angle"])
        recovered = (center[0] + row["radius"] * math.cos(angle), center[1] + row["radius"] * math.sin(angle))
        residual = max(abs(math.dist(start, center) - row["radius"]),
                       abs(math.dist(end, center) - row["radius"]), math.dist(end, recovered))
        if residual > tolerance:
            raise ValueError("圆弧起终点、圆心、半径与有符号扫角不一致")
    return row


def _angle_on_arc(angle, start, sweep):
    turn = 2 * math.pi
    delta = (angle - start) % turn if sweep > 0 else (start - angle) % turn
    return delta <= abs(sweep) + 1e-12


def segment_bbox(segment):
    """Return exact [min_x, min_y, max_x, max_y], including ARC extrema."""
    s = _primitive(segment)
    kind = s["segment_type"]
    if kind == "CIRCLE":
        x, y, r = s["center_x"], s["center_y"], s["radius"]
        return [x-r, y-r, x+r, y+r]
    points = [_point(s, "start"), _point(s, "end")]
    if kind == "ARC":
        cx, cy, r = s["center_x"], s["center_y"], s["radius"]
        start = math.atan2(s["start_y"]-cy, s["start_x"]-cx)
        sweep = math.radians(s["sweep_angle"])
        for angle in (0, math.pi/2, math.pi, 3*math.pi/2):
            if _angle_on_arc(angle, start, sweep):
                points.append((cx+r*math.cos(angle), cy+r*math.sin(angle)))
    return [min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points)]


def _bbox_values(bbox):
    if isinstance(bbox, dict):
        bbox = [bbox[key] for key in ("min_x", "min_y", "max_x", "max_y")]
    if not isinstance(bbox, (tuple, list)) or len(bbox) != 4:
        raise ValueError("包围范围必须包含 min_x、min_y、max_x、max_y")
    values = [_number(v, "包围范围坐标") for v in bbox]
    if values[0] > values[2] or values[1] > values[3]:
        raise ValueError("包围范围的最小值不能大于最大值")
    return values


def fully_contained(segment, bbox, tolerance=DEFAULT_TOLERANCE):
    """True only if the *entire* exact curve is within the confirmed rectangle."""
    tolerance = _number(tolerance, "包含判断容差")
    if tolerance < 0:
        raise ValueError("包含判断容差不能为负")
    outer = _bbox_values(bbox)
    inner = segment_bbox(segment)
    return (inner[0] >= outer[0]-tolerance and inner[1] >= outer[1]-tolerance
            and inner[2] <= outer[2]+tolerance and inner[3] <= outer[3]+tolerance)


def _reverse(row):
    result = dict(row)
    result["start_x"], result["start_y"], result["end_x"], result["end_y"] = (
        row["end_x"], row["end_y"], row["start_x"], row["start_y"])
    if row["segment_type"] == "ARC":
        result["sweep_angle"] = -row["sweep_angle"]
    return result


def _signed_area(rows):
    ox, oy = _point(rows[0], "start")
    result = 0.0
    for row in rows:
        sx, sy = row["start_x"]-ox, row["start_y"]-oy
        ex, ey = row["end_x"]-ox, row["end_y"]-oy
        result += (sx*ey-ex*sy)/2
        if row["segment_type"] == "ARC":
            theta = math.radians(row["sweep_angle"])
            result += row["radius"]**2*(theta-math.sin(theta))/2
    return result


def _contour(segments, options, tolerance):
    if not segments or any(s.get("segment_type") not in {"LINE", "ARC"} for s in segments):
        raise ValueError("异形孔必须选择由 LINE/ARC 组成的非空闭合轮廓")
    rows = [_primitive(s, tolerance) for s in segments]
    # Assign coincident endpoints to vertices. Do not round or move source data.
    vertices, edges, adjacency = [], [], defaultdict(list)
    for index, row in enumerate(rows):
        pair = []
        for name in ("start", "end"):
            point = _point(row, name)
            matches = [i for i, vertex in enumerate(vertices) if math.dist(point, vertex) <= tolerance]
            if len(matches) > 1:
                raise ValueError("轮廓端点合并存在歧义，请检查过近或重复的片段")
            if matches:
                vertex = matches[0]
            else:
                vertex = len(vertices)
                vertices.append(point)
            pair.append(vertex)
            adjacency[vertex].append(index)
        if pair[0] == pair[1]:
            raise ValueError("轮廓包含自闭合单段或退化片段，请检查选择")
        edges.append(pair)
    if any(len(neighbours) != 2 for neighbours in adjacency.values()):
        raise ValueError("轮廓存在断口、分叉或重复片段；不会自动补线")
    visited, ordered, current = set(), [], edges[0][0]
    while len(visited) < len(rows):
        choices = [i for i in adjacency[current] if i not in visited]
        if not choices:
            raise ValueError("选择中包含多个独立轮廓，请每次选择一个动作的闭合边界")
        index = min(choices)
        visited.add(index)
        forward = edges[index][0] == current
        row = dict(rows[index]) if forward else _reverse(rows[index])
        row["_source_id"] = segments[index].get("id", str(index))
        ordered.append(row)
        current = edges[index][1 if forward else 0]
    if current != edges[0][0]:
        raise ValueError("所选轮廓未闭合")
    area = _signed_area(ordered)
    if abs(area) <= tolerance**2:
        raise ValueError("轮廓面积为零，无法确定整体逆时针方向")
    if area < 0:
        ordered = [_reverse(row) for row in reversed(ordered)]
    requested = options.get("start_segment_id")
    if requested:
        choices = [i for i, row in enumerate(ordered) if row["_source_id"] == requested]
        if len(choices) != 1:
            raise ValueError("指定的起始片段不在当前轮廓中，或来源编号重复")
        index = choices[0]
        row = ordered[index]
        if row["segment_type"] != "ARC":
            raise ValueError("现行异形孔起始协议要求圆弧；纯直线轮廓的起始协议尚待确认")
        if row["start_x"] > row["end_x"] + tolerance:
            raise ValueError("指定圆弧在整体逆时针方向下不以较小 x 端起步，不能同时满足现行起始协议")
    else:
        # A 270-degree rotation maps material (x,y) to engineering (y,-x).
        # The view's upper-left convex arc is therefore lower-left in material
        # coordinates. Only accept an unambiguous convex corner candidate;
        # the specification defines no numerical tie-breaking distance.
        # Concave relief arcs are not start candidates.
        candidates = []
        for i, row in enumerate(ordered):
            if row["segment_type"] != "ARC" or row["sweep_angle"] <= 0:
                continue
            angle = math.atan2(row["start_y"]-row["center_y"], row["start_x"]-row["center_x"])
            angle += math.radians(row["sweep_angle"])/2
            nx, ny = math.cos(angle), math.sin(angle)
            if nx >= -tolerance or ny >= -tolerance:
                continue
            if row["start_x"] <= row["end_x"] + tolerance:
                candidates.append(i)
        if len(candidates) != 1:
            raise ValueError("无法唯一确定 270° 视角左上起始圆弧；请确认起始圆弧，纯直线轮廓协议仍待确认")
        index = candidates[0]
    ordered = ordered[index:] + ordered[:index]
    for i, row in enumerate(ordered, 1):
        row.pop("_source_id", None)
        row["seq"] = i
    return ordered


def _line_axes(segments, config):
    tolerance = _tolerance(config)
    parallel = _tolerance(config, "parallel_tolerance")
    if not segments or any(s.get("segment_type") not in {"LINE", "BEND_LINE"} for s in segments):
        raise ValueError("折弯只能选择直线片段")
    rows = [_primitive(s, tolerance) for s in segments]
    longest = max(rows, key=lambda r: math.dist(_point(r, "start"), _point(r, "end")))
    a, b = _point(longest, "start"), _point(longest, "end")
    length = math.dist(a, b)
    u = ((b[0]-a[0])/length, (b[1]-a[1])/length)
    if u[0] < -parallel or (abs(u[0]) <= parallel and u[1] < 0):
        u = (-u[0], -u[1])
    # Logical derived axes use exact horizontal/vertical values when within the
    # configured angular tolerance. Original DXF coordinates remain untouched.
    if abs(u[0]) <= parallel:
        u = (0.0, 1.0)
    elif abs(u[1]) <= parallel:
        u = (1.0, 0.0)
    normal = (-u[1], u[0])
    result = []
    for row in rows:
        a, b = _point(row, "start"), _point(row, "end")
        length = math.dist(a, b)
        v = ((b[0]-a[0])/length, (b[1]-a[1])/length)
        if abs(u[0]*v[1]-u[1]*v[0]) > parallel:
            raise ValueError("所选折弯片段不平行，请移除错选线段")
        offsets = [p[0]*normal[0]+p[1]*normal[1] for p in (a, b)]
        if abs(offsets[0]-offsets[1]) > tolerance:
            raise ValueError("折弯同侧片段未共线，偏差超过几何容差")
        ts = sorted(p[0]*u[0]+p[1]*u[1] for p in (a, b))
        result.append({"offset": sum(offsets)/2, "interval": ts})
    return rows, result, u, normal


def _bend(segments, options, config, direct, preview):
    tolerance = _tolerance(config)
    _, entries, u, normal = _line_axes(segments, config)
    ids = [s.get("id", str(i)) for i, s in enumerate(segments)]
    if direct:
        if max(e["offset"] for e in entries)-min(e["offset"] for e in entries) > tolerance:
            raise ValueError("直接采用中线模式要求全部片段共线，不能选择双侧边界")
        groups = [list(range(len(entries)))]
        if len(segments) == 1:
            confirmed = True
        else:
            confirmed = options.get("confirmed_span") is True
    else:
        if len(segments) < 2:
            raise ValueError("折弯双线模式至少需要两侧线段；单侧边界不能当作中线")
        if options.get("side_a_ids") is not None or options.get("side_b_ids") is not None:
            left, right = options.get("side_a_ids", []), options.get("side_b_ids", [])
            if not left or not right or len(left+right) != len(set(left+right)) or set(left+right) != set(ids):
                raise ValueError("折弯 A/B 两侧必须非空、无重复且完整覆盖所选片段")
            groups = [[ids.index(i) for i in group] for group in (left, right)]
        else:
            groups = []
            for index in sorted(range(len(entries)), key=lambda i: entries[i]["offset"]):
                if groups and abs(entries[index]["offset"]-entries[groups[-1][0]]["offset"]) <= tolerance:
                    groups[-1].append(index)
                else:
                    groups.append([index])
            if len(groups) != 2:
                raise ValueError("所选片段不能唯一分成两条共线边界；请检查错选或明确 A/B 两侧")
        for group in groups:
            offsets = [entries[i]["offset"] for i in group]
            if max(offsets)-min(offsets) > tolerance:
                raise ValueError("指定折弯同侧片段未共线")
        separation = abs(entries[groups[0][0]]["offset"]-entries[groups[1][0]]["offset"])
        if separation <= tolerance:
            raise ValueError("折弯两侧重合或间距过小，无法从单侧线确定中线")
        for group, other in ((groups[0], groups[1]), (groups[1], groups[0])):
            for index in group:
                a, b = entries[index]["interval"]
                if not any(min(b, entries[j]["interval"][1])-max(a, entries[j]["interval"][0]) > tolerance for j in other):
                    raise ValueError("两侧折弯片段缺少投影对应关系，请检查是否误选其它折弯")
        confirmed = options.get("confirmed_span") is True
    if not preview and not confirmed:
        raise ValueError("逻辑折弯跨度尚未确认，请先查看中线预览并确认跨度")
    offset = sum(sum(entries[i]["offset"] for i in group)/len(group) for group in groups)/len(groups)
    low = min(e["interval"][0] for e in entries)
    high = max(e["interval"][1] for e in entries)
    return [_row("BEND_LINE", start_x=u[0]*low+normal[0]*offset, start_y=u[1]*low+normal[1]*offset,
                 end_x=u[0]*high+normal[0]*offset, end_y=u[1]*high+normal[1]*offset)]


def _covers(intervals, low, high, tolerance):
    cursor = low
    for a, b in sorted(intervals):
        if a > cursor+tolerance:
            return False
        cursor = max(cursor, b)
    return cursor >= high-tolerance


def _bridge(segments, options, config, preview):
    tolerance = _tolerance(config)
    if len(segments) < 4 or any(s.get("segment_type") != "LINE" for s in segments):
        raise ValueError("折桥范围需要选择完整的轴对齐矩形外围直线；不能只选几个点或斜边")
    rows = [_primitive(s, tolerance) for s in segments]
    boxes = [segment_bbox(row) for row in rows]
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    if x1-x0 <= tolerance or y1-y0 <= tolerance:
        raise ValueError("折桥外围范围必须具有非零宽度和高度")
    borders = defaultdict(list)
    for row in rows:
        sx, sy, ex, ey = (row[k] for k in FIELDS[:4])
        if abs(sx-ex) <= tolerance:
            key = "left" if abs(sx-x0) <= tolerance else "right" if abs(sx-x1) <= tolerance else None
            interval = sorted((sy, ey))
        elif abs(sy-ey) <= tolerance:
            key = "bottom" if abs(sy-y0) <= tolerance else "top" if abs(sy-y1) <= tolerance else None
            interval = sorted((sx, ex))
        else:
            raise ValueError("当前 BRIDGE 只支持轴对齐外围范围，不会把斜折桥自动扩大成矩形")
        if key is None:
            raise ValueError("折桥选择含内部线段；请只选择外围，内部线会在确认后自动收纳")
        borders[key].append(interval)
    if not all(_covers(borders[key], low, high, tolerance) for key, low, high in
               (("left", y0, y1), ("right", y0, y1), ("bottom", x0, x1), ("top", x0, x1))):
        raise ValueError("折桥外围存在缺边或断口，不能从不完整选择直接生成大包围框")
    if not preview and options.get("confirmed_footprint") is not True:
        raise ValueError("折桥 footprint 尚未确认，请先预览并确认外围范围")
    return [_row("BRIDGE", start_x=x0, start_y=y0, end_x=x1, end_y=y1,
                 center_x=(x0+x1)/2, center_y=(y0+y1)/2)]


def _derive(operation, segments, config, preview):
    tolerance = _tolerance(config)
    kind = operation.get("operation_type")
    options = operation.get("geometry_options") or {}
    if not isinstance(options, dict):
        raise ValueError("geometry_options 必须是对象")
    mode = operation.get("geometry_mode") or {"BEND": "bend_midline", "BRIDGE": "bridge",
                                              "IRREGULAR_PUNCH": "contour"}.get(kind, "source")
    if mode == "none":
        if segments:
            raise ValueError("无几何模式不能同时绑定几何片段")
        return []
    if not segments:
        raise ValueError("尚未选择该动作的几何片段")
    ids = [s.get("id", str(i)) for i, s in enumerate(segments)]
    if len(ids) != len(set(ids)):
        raise ValueError("所选几何包含重复来源片段")
    if kind == "BEND":
        if mode not in {"bend_midline", "bend_direct"}:
            raise ValueError("BEND 必须使用双线中线或直接中线模式")
        return _bend(segments, options, config, mode == "bend_direct", preview)
    if kind == "BRIDGE":
        if mode != "bridge":
            raise ValueError("BRIDGE 必须使用已确认的 footprint 模式")
        return _bridge(segments, options, config, preview)
    if kind == "IRREGULAR_PUNCH" or mode == "contour":
        return _contour(segments, options, tolerance)
    if mode != "source":
        raise ValueError(f"当前动作不支持几何模式：{mode}")
    rows = [_primitive(s, tolerance) for s in segments]
    if kind in {"CIRCLE_PUNCH", "PRE_PUNCH", "FLANGING"}:
        if any(r["segment_type"] != "CIRCLE" for r in rows):
            raise ValueError("圆孔、预孔和翻孔应选择圆形实体")
        if kind != "FLANGING" and len(rows) != 1:
            raise ValueError("圆孔或预孔必须对应一个圆；请勿混入其它圆")
        if kind == "FLANGING" and any(math.dist(_point(rows[0], "center"), _point(r, "center")) > tolerance for r in rows[1:]):
            raise ValueError("翻孔所选圆必须同心")
        rows.sort(key=lambda r: (r["radius"], r["center_x"], r["center_y"]))
    for seq, row in enumerate(rows, 1):
        row["seq"] = seq
    return rows


def derive_geometry(operation, segments, config=None):
    """Return confirmed Sheet2 rows. Raises a readable ValueError on ambiguity."""
    return _derive(operation, segments, config or {}, preview=False)


def preview_geometry(operation, segments, config=None):
    """Calculate a preview; does not establish span/footprint confirmation."""
    return _derive(operation, segments, config or {}, preview=True)


def derive_region(operation, geometry, region_input):
    """Return a confirmed axis-aligned Sheet5 record; never infer a parent."""
    if operation.get("operation_type") != "BEND":
        raise ValueError("只有 BEND 动作可以创建折弯影响区")
    if region_input.get("confirmed") is not True:
        raise ValueError("折弯影响区尚未确认")
    if region_input.get("parent_confirmed") is not True:
        raise ValueError("请明确确认父影响区，或确认无父影响区")
    rid = region_input.get("influence_region_id")
    if not isinstance(rid, str) or not rid.strip():
        raise ValueError("影响区编号不能为空")
    parent = region_input.get("parent_region_id") or None
    if parent == rid:
        raise ValueError("影响区不能以自身作为父影响区")
    if not isinstance(geometry, list) or len(geometry) != 1 or geometry[0].get("segment_type") not in {"LINE", "BEND_LINE"}:
        raise ValueError("折弯影响区必须从唯一、已确认的折弯中线创建")
    line = _primitive(geometry[0])
    sx, sy, ex, ey = (line[k] for k in FIELDS[:4])
    boundary = _number(region_input.get("far_boundary_coord"), "影响区远边界")
    side = region_input.get("affected_side")
    if sx == ex:
        axis, anchor, allowed = "VERTICAL", sx, {"POSITIVE_X", "NEGATIVE_X"}
    elif sy == ey:
        axis, anchor, allowed = "HORIZONTAL", sy, {"POSITIVE_Y", "NEGATIVE_Y"}
    else:
        raise ValueError("现行 Sheet5 仅支持水平或竖直折弯；斜折弯影响区需另行确认表示")
    if side not in allowed:
        raise ValueError(f"{axis} 折弯的影响方向必须为 {' / '.join(sorted(allowed))}")
    delta = boundary-anchor
    if (side.startswith("POSITIVE") and delta <= 0) or (side.startswith("NEGATIVE") and delta >= 0):
        raise ValueError("远边界必须位于所选影响侧，且影响深度必须大于零")
    length, depth = math.dist((sx, sy), (ex, ey)), abs(delta)
    operation_id = operation.get("operation_id") or operation.get("id")
    if not operation_id:
        raise ValueError("折弯动作编号不能为空")
    return {"bend_operation_id": operation_id, "start_x": sx, "start_y": sy, "end_x": ex, "end_y": ey,
            "bend_axis": axis, "far_boundary_coord": boundary, "affected_depth": depth,
            "affected_side": side, "bend_length": length, "affected_area_proxy": length*depth,
            "influence_region_id": rid.strip(), "parent_region_id": parent}
