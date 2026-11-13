"""Editor station facts and isolated expert-reference diagnostics."""
import hashlib
import re
from pathlib import Path

CAPABILITY_TYPES = {
    "PUNCH": "冲孔", "FLANGING": "翻孔", "BEND": "折弯",
    "DIMPLE": "凸米", "DRAW_FORM": "拉包",
    "BRIDGE": "折桥", "MARKING": "刻线",
}
PUNCH_TYPES = ("CIRCLE_PUNCH", "IRREGULAR_PUNCH", "PRE_PUNCH")
PLANNING_SCOPE = "seven_classes_no_trim_v1"


def capability_class(kind):
    return "PUNCH" if kind in PUNCH_TYPES else kind


def excludes_trim(project):
    return project.get("planning_scope") == PLANNING_SCOPE


def planning_operations(project):
    return [op for op in project.get("operations", [])
            if not op.get('superseded_by_operation_id')
            and not (excludes_trim(project) and op.get("operation_type") == "TRIM")]


def not_later_relations(project):
    return [{'operation_id':op['operation_id'], 'not_later_than':target}
            for op in planning_operations(project) for target in op.get('not_later_than_operation_ids', [])]


def grouped_capabilities(value, *, omit_trim=False):
    original = tokens(value)
    result = list(dict.fromkeys(capability_class(t) for t in original if not (omit_trim and t == "TRIM")))
    return result or (["NONE"] if original else [])


def expanded_capabilities(value, *, omit_trim=False):
    """Adapt class-level capability to the unchanged solver's operation enum."""
    return list(dict.fromkeys(x for t in grouped_capabilities(value, omit_trim=omit_trim)
                             for x in (PUNCH_TYPES if t == "PUNCH" else (t,))))


def tokens(value):
    return list(value) if isinstance(value, (list, tuple)) else re.findall(r"[^,，;；\s]+", str(value or ""))


def inspect_process(project):
    errors, reference_issues = [], []
    stations, orders = {}, set()
    for row in project.get("station_rows", []):
        sid, order = row.get("station_id"), row.get("order")
        if not isinstance(sid, str) or not sid.strip() or sid != sid.strip() or sid in stations:
            errors.append("工序编号不能为空、带首尾空格或重复。")
            continue
        stations[sid] = row
        if type(order) is not int or order < 1 or order in orders:
            errors.append(f"{sid}：工序顺序必须是互不重复的正整数。")
        else:
            orders.add(order)
        allowed = grouped_capabilities(row.get("allowed_operation_types"), omit_trim=excludes_trim(project))
        if not allowed:
            errors.append(f"{sid}：能力尚未指定；确无本项目动作时请选择 NONE。")
        elif allowed != ["NONE"] and ("NONE" in allowed or any(t not in CAPABILITY_TYPES and not (t == "TRIM" and not excludes_trim(project)) for t in allowed)):
            errors.append(f"{sid}：能力含未支持或混用的类型 {', '.join(allowed)}。")
    assigned = 0
    operations = planning_operations(project)
    excluded = {op['operation_id'] for op in project.get('operations', []) if op not in operations}
    all_ops = {op['operation_id']:op for op in project.get('operations', [])}
    for op in all_ops.values():
        replacement=op.get('superseded_by_operation_id')
        if replacement and (replacement not in all_ops or replacement==op['operation_id'] or all_ops[replacement].get('superseded_by_operation_id') or op['operation_id'] not in all_ops[replacement].get('composite_operation_ids', [])):
            errors.append(f"{op['operation_id']}：合并替代关系无效，请核对 {replacement}。")
        if op.get('feature_type')=='BEND_Z':
            members=op.get('composite_operation_ids', [])
            if len(members)!=2 or len(set(members))!=2 or any(m not in all_ops or all_ops[m].get('superseded_by_operation_id')!=op['operation_id'] or all_ops[m].get('operation_type')!='BEND' for m in members):
                errors.append(f"{op['operation_id']}：Z折必须对应两个已退出独立分配的原折弯动作。")
            elif set(op.get('source_segment_ids', []))!={s for m in members for s in all_ops[m].get('source_segment_ids', [])}:
                errors.append(f"{op['operation_id']}：Z折来源须保留两个原折弯的全部图元，请同步核对。")
        if op.get('composite_policy')=='INTEGRATED_DRAW_FORM':
            members=op.get('composite_operation_ids', [])
            primary=op.get('primary_source_segment_ids', [])
            if op.get('operation_type')!='DRAW_FORM' or not primary or not members or len(members)!=len(set(members)) or any(m not in all_ops or all_ops[m].get('superseded_by_operation_id')!=op['operation_id'] or all_ops[m].get('operation_type') not in PUNCH_TYPES for m in members):
                errors.append(f"{op['operation_id']}：拉包组成孔及主轮廓记录不完整。")
            elif set(op.get('source_segment_ids', []))!=set(primary)|{s for m in members for s in all_ops[m].get('source_segment_ids', [])}:
                errors.append(f"{op['operation_id']}：拉包须保留原主轮廓及组成孔的全部图元。")
    for op in operations:
        references = {op.get('cad_parent_operation_id'), *op.get('required_predecessor_ids', []), *op.get('not_later_than_operation_ids', [])} & excluded
        if references:
            errors.append(f"{op['operation_id']} 仍引用已退出分配的动作 {', '.join(sorted(references))}，请核对关系；不会自动删除依赖。")
        for target in op.get('not_later_than_operation_ids', []):
            if target not in all_ops or target==op['operation_id']:
                errors.append(f"{op['operation_id']}：不晚于关系目标无效：{target}。")
    by_id = {op.get("operation_id"): op for op in operations}
    for op in operations:
        sid = op.get("expert_station")
        if not sid:
            continue
        assigned += 1
        oid = op.get("operation_id")
        if not isinstance(sid, str) or sid not in stations:
            reference_issues.append(f"{oid}：专家工序 {sid} 不存在。")
            continue
        station = stations[sid]
        if capability_class(op.get("operation_type")) not in grouped_capabilities(station.get("allowed_operation_types")):
            reference_issues.append(f"{oid}：专家工序 {sid} 未允许 {op.get('operation_type')}。")
        # H7 child -> parent, and explicit strict predecessors. Never infer an
        # ordering within a station, nor feed the reference to formal inference.
        edges = [(op, by_id.get(op.get("cad_parent_operation_id")))]
        edges += [(by_id.get(pred), op) for pred in op.get("required_predecessor_ids", [])]
        for child, parent in edges:
            if not child or not parent:
                continue
            a, b = (stations.get(item.get("expert_station")) for item in (child, parent))
            if a and b and type(a.get("order")) is int and type(b.get("order")) is int and a["order"] >= b["order"]:
                reference_issues.append(f"{child['operation_id']} → {parent['operation_id']}：专家分配不满足严格先后（同序也不满足）。")
        for target in op.get('not_later_than_operation_ids', []):
            other=by_id.get(target, {})
            a,b=stations.get(sid),stations.get(other.get('expert_station'))
            if a and b and type(a.get('order')) is int and type(b.get('order')) is int and a['order']>b['order']:
                reference_issues.append(f"{oid} ≤ {target}：专家分配不满足不晚于约束（允许同序）。")
    capacity_issues=[]
    if not errors:
        for relation in not_later_relations(project):
            a,b=relation['operation_id'],relation['not_later_than']
            if a not in by_id or b not in by_id:continue
            domains=[[s for s in stations.values() if capability_class(by_id[oid]['operation_type']) in grouped_capabilities(s.get('allowed_operation_types'))] for oid in (a,b)]
            if all(domains) and min(s['order'] for s in domains[0])>max(s['order'] for s in domains[1]):
                capacity_issues.append(f"{a} ≤ {b} 与工序能力冲突：{a} 仅允许 {', '.join(s['station_id'] for s in domains[0])}；{b} 仅允许 {', '.join(s['station_id'] for s in domains[1])}，不存在满足不晚于关系的分配。")
    return {"station_errors": errors, "reference_issues": list(dict.fromkeys(reference_issues)),
            "relation_capacity_issues":capacity_issues,
            "assigned": assigned, "total": len(operations),
            "excluded_trim": sorted(o['operation_id'] for o in project.get('operations',[]) if o['operation_id'] in excluded and o.get('operation_type')=='TRIM'),
            "superseded_operations": sorted(o['operation_id'] for o in project.get('operations',[]) if o.get('superseded_by_operation_id'))}


def existing_station_defaults(module_root, part):
    """Explicit, read-only reuse of this repository's existing Sheet3 only."""
    import openpyxl
    from .exporter import _read_table, HEADERS
    if not re.fullmatch(r"P\d{3}", part):
        return {"station_rows": [], "source": None}
    path = Path(module_root).parent / "data" / "parts" / part / f"钣金加工数据表_{part}.xlsx"
    if not path.is_file():
        return {"station_rows": [], "source": None}
    book = openpyxl.load_workbook(path, read_only=True, data_only=False)
    try:
        rows = _read_table(book, "Sheet3", HEADERS["Sheet3"])
    finally:
        book.close()
    return {"station_rows": rows, "source": {"path": path.relative_to(Path(module_root).parent).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "sheets": ["Sheet3"]}}
