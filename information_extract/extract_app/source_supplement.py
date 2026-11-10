"""Append an explicit DXF subset using one verified translation for the whole drawing."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from .source_paths import companion_root

from .geometry import segment_bbox


def _contained(root, relative):
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError("补充清单及参考图必须位于提取模块内。")
    return target


def _translated(segment, translation):
    row = deepcopy(segment)
    for p in ("start", "end", "center"):
        if row.get(p + "_x") is not None:
            row[p + "_x"] += translation[0]
            row[p + "_y"] += translation[1]
    row["bbox"] = list(segment_bbox(row))
    return row


def _arc_point(segment, fraction):
    angle = math.atan2(segment["start_y"] - segment["center_y"], segment["start_x"] - segment["center_x"])
    angle += math.radians(segment["sweep_angle"]) * fraction
    return (segment["center_x"] + segment["radius"] * math.cos(angle),
            segment["center_y"] + segment["radius"] * math.sin(angle))


def _residual(a, b):
    if a["segment_type"] != b["segment_type"]:
        return math.inf
    kind, error = a["segment_type"], 0.0
    if kind in {"ARC", "CIRCLE"}:
        error = max(abs(a["radius"] - b["radius"]),
                    math.dist((a["center_x"], a["center_y"]), (b["center_x"], b["center_y"])))
    if kind in {"LINE", "ARC"}:
        p = [(a[t+"_x"], a[t+"_y"]) for t in ("start", "end")]
        q = [(b[t+"_x"], b[t+"_y"]) for t in ("start", "end")]
        direct = max(math.dist(p[i], q[i]) for i in (0, 1))
        reverse = max(math.dist(p[i], q[1-i]) for i in (0, 1))
        error = max(error, min(direct, reverse))
        if kind == "ARC":
            error = max(error, a["radius"] * math.radians(abs(abs(a["sweep_angle"]) - abs(b["sweep_angle"]))))
            for f in (.25, .5, .75):
                error = max(error, math.dist(_arc_point(a, f), _arc_point(b, 1-f if reverse < direct else f)))
    return error


def append_confirmed_supplement(base_path, config, base_sha256, segments, units, bounds):
    relative = config.get("confirmed_geometry_supplement")
    if not relative:
        return segments, None, []
    from .drawing import load_drawing
    root = companion_root(base_path)
    manifest_path = _contained(root, relative)
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    registration = manifest.get("registration", {})
    if (manifest.get("schema_version") != 1 or manifest.get("baseline_sha256") != base_sha256
            or config.get("coordinate_mode") != "world_xy"):
        raise ValueError("补充清单格式、基准指纹或 WCS XY 不匹配。")
    if manifest.get("local_registrations") or registration.get("type") != "TRANSLATION":
        raise ValueError("仅允许整图统一平移，禁止局部镜像、局部拟合或其它变换。")
    translation = registration.get("translation_xy")
    if (not isinstance(translation, list) or len(translation) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in translation)):
        raise ValueError("整图平移必须为两个有限数值。")
    reference_path = _contained(root, manifest["reference_file"])
    if reference_path == Path(base_path).resolve():
        raise ValueError("参考图不能为基准图本身。")
    reference_config = {key: config[key] for key in ("geometry_tolerance", "parallel_tolerance")}
    reference_config.update(coordinate_mode="world_xy", material_block="*Model_Space", display_annotations=False)
    if config.get("confirmed_source_units"):
        reference_config["confirmed_source_units"] = config["confirmed_source_units"]
    reference = load_drawing(reference_path, reference_config)
    if reference["source"]["sha256"] != manifest.get("reference_sha256") or reference["source"]["units"] != units:
        raise ValueError("参考图指纹或单位不匹配。")
    tolerance = config["geometry_tolerance"]
    expected_bounds = [value + translation[i % 2] for i, value in enumerate(reference["bounds"])]
    if registration.get("require_matching_global_bounds") is not True or any(abs(a-b) > tolerance for a, b in zip(bounds, expected_bounds)):
        raise ValueError("整图包围范围未重合，不允许补线。")
    base_index = {s["id"]: s for s in segments}
    reference_index = {s["id"]: s for s in reference["segments"]}
    translated = {sid: _translated(row, translation) for sid, row in reference_index.items()}
    residuals, base_ids, points, circles = [], set(), [], 0
    for pair in registration.get("verified_pairs", []):
        a, b = base_index.get(pair["baseline_id"]), translated.get(pair["reference_id"])
        if not a or not b or a["id"] in base_ids:
            raise ValueError("整图配准对应无效或重复。")
        base_ids.add(a["id"])
        residuals.append(_residual(a, b))
        if a["segment_type"] == "CIRCLE":
            points.append((a["center_x"], a["center_y"]))
            circles += 1
        else:
            points.extend((a[p+"_x"], a[p+"_y"]) for p in ("start", "end"))
    if len(base_ids) < 3 or max(residuals) > tolerance:
        raise ValueError("整图共有图元的平移残差超过几何容差，或对应不足。")
    p = points[0]
    q = next((v for v in points if math.dist(p, v) > tolerance), None)
    if not q or all(abs((q[0]-p[0])*(v[1]-p[1]) - (q[1]-p[1])*(v[0]-p[0])) <= tolerance * math.dist(p, q) for v in points):
        raise ValueError("配准对应点不能全部共线。")
    ids = manifest.get("selected_source_ids", [])
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("补充图元清单为空或重复。")
    added = []
    for sid in ids:
        if sid not in translated or translated[sid]["segment_type"] != "LINE" or not translated[sid]["is_visible"]:
            raise ValueError("补充必须是参考图中真实存在的可见直线，不能包含文字。")
        row = deepcopy(translated[sid])
        if any(_residual(row, s) <= tolerance for s in segments + added):
            raise ValueError("补充直线与已有来源重合，拒绝重复导入。")
        identity = [base_sha256, reference["source"]["sha256"], sid, translation]
        row["id"] = "S_" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
        row["source_group"] = "supplement:" + reference_path.name + ":" + row["source_group"]
        row["provenance"] = {"source_file": reference_path.name, "source_sha256": reference["source"]["sha256"],
                             "original_source_id": sid, "translation_xy": translation}
        added.append(row)
    omitted = set()
    for pair in manifest.get("omitted_duplicate_sources", []):
        a, b = reference_index.get(pair["retained_id"]), reference_index.get(pair["omitted_id"])
        if (not a or not b or a["id"] not in ids or b["id"] in ids or b["id"] in omitted
                or a["segment_type"] != "LINE" or b["segment_type"] != "LINE" or _residual(a, b) > tolerance):
            raise ValueError("未导入重线的审计对应不匹配。")
        omitted.add(b["id"])
    if len({s["id"] for s in segments + added}) != len(segments) + len(added):
        raise ValueError("补充来源 ID 冲突。")
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    audit = {"manifest": relative, "manifest_sha256": manifest_sha, "baseline_sha256": base_sha256,
             "reference_file": reference_path.name, "reference_sha256": reference["source"]["sha256"],
             "registration": {"type": "TRANSLATION", "translation_xy": translation, "verified_pair_count": len(base_ids),
                              "circle_pair_count": circles, "max_pair_residual_mm": max(residuals),
                              "global_bounds_match": True},
             "known_drawing_differences": deepcopy(manifest.get("known_drawing_differences", {})),
             "added_source_ids": [s["id"] for s in added], "added_segments": added,
             "omitted_duplicate_sources": deepcopy(manifest.get("omitted_duplicate_sources", [])), "imported_text_count": 0}
    return segments + added, audit, [{"file_name": manifest["reference_file"], "sha256": reference["source"]["sha256"]},
                                       {"file_name": relative, "sha256": manifest_sha}]
