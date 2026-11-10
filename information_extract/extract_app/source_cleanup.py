"""Apply an explicit, reversible cleanup manifest without changing source IDs."""
from copy import deepcopy
import json
from pathlib import Path
from .source_paths import companion_root

from .geometry import fully_contained, segment_bbox
from .workflow import _paths


def load_cleanup_manifest(source_path, config, source_sha256):
    relative = config.get("confirmed_source_cleanup")
    if not relative:
        return None
    root = companion_root(source_path)
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ValueError("清理清单必须位于提取模块内。")
    manifest = json.loads(target.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("source_sha256") != source_sha256:
        raise ValueError("清理清单格式或源 DXF 指纹不匹配，未执行删除。")
    return manifest


def apply_source_cleanup(segments, manifest, tolerance):
    if manifest is None:
        return segments, [], []
    groups = {}
    for key in ("operation_source_ids", "outline_source_ids", "removed_source_ids"):
        ids = manifest.get(key)
        if not isinstance(ids, list) or any(not isinstance(s, str) for s in ids) or len(ids) != len(set(ids)):
            raise ValueError("清理清单的来源列表无效或重复。")
        groups[key] = set(ids)
    protected, outline, removed = (groups[key] for key in groups)
    if (protected & outline) or (protected & removed) or (outline & removed):
        raise ValueError("清理清单不能删除或重复分类已保留的来源。")
    index = {s["id"]: s for s in segments}
    if protected | outline | removed != set(index):
        raise ValueError("清理清单未完整对应当前图纸，未执行删除。")
    if not outline:
        raise ValueError("清理必须保留已确认外轮廓。")
    paths = _paths([index[sid] for sid in sorted(outline)], tolerance)
    if len(paths) != 1 or any(row["segment_type"] not in {"LINE", "ARC"} for row in paths[0]["segments"]):
        raise ValueError("清理清单中的外轮廓必须是单一完整闭合路径。")
    boxes = [segment_bbox(index[sid]) for sid in outline]
    bounds = [min(b[0] for b in boxes), min(b[1] for b in boxes),
              max(b[2] for b in boxes), max(b[3] for b in boxes)]
    if any(not fully_contained(index[sid], bounds, tolerance) for sid in protected):
        raise ValueError("已绑定图元超出已确认外轮廓范围，停止清理。")
    audit = [{"removed_source": deepcopy(s), "reason": "USER_CONFIRMED_UNBOUND_NON_OUTLINE"}
             for s in segments if s["id"] in removed]
    return [s for s in segments if s["id"] not in removed], audit, sorted(outline)


def apply_annotation_cleanup(issues, manifest):
    """Remove only explicitly identified dimension annotations, with an audit."""
    rules = manifest.get("removed_annotation_entities", []) if manifest else []
    selected = set()
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("entity_type") != "DIMENSION" or not rule.get("source_handle"):
            raise ValueError("辅助标注清理只接受明确来源的尺寸标注。")
        key = (rule["entity_type"], rule["source_handle"], rule.get("insert_path", ""))
        if key in selected:
            raise ValueError("尺寸标注清理来源重复。")
        selected.add(key)
    actual = {(i.get("entity_type"), i.get("source_handle"), i.get("insert_path", ""))
              for i in issues if i.get("code") == "UNSUPPORTED_ENTITY"}
    if not selected <= actual:
        raise ValueError("清理清单中的尺寸标注不属于当前图纸。")
    kept, removed = [], []
    for issue in issues:
        key = (issue.get("entity_type"), issue.get("source_handle"), issue.get("insert_path", ""))
        if issue.get("code") == "UNSUPPORTED_ENTITY" and key in selected:
            removed.append({"source_issue": deepcopy(issue), "reason": "USER_CONFIRMED_AUXILIARY_ANNOTATION"})
        else:
            kept.append(issue)
    return kept, removed
