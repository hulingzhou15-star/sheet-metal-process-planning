"""Derive one PRE_PUNCH--FLANGING group from an explicit, unique CAD link.

This is collection metadata automation, not a new manufacturing constraint.
Names, distances, operation IDs and row order are never pairing evidence.
"""
from collections import Counter, defaultdict
from copy import deepcopy

AUTO_SOURCE = "AUTO_CAD_PAIR"
PAIR_TYPES = {"PRE_PUNCH", "FLANGING"}
PAIR_FIELDS = ("process_group_id", "process_group_source", "process_group_pair_uids")


def synchronize_pairing(project):
    """Update derived fields only, returning an auditable report; no I/O."""
    operations = project.get("operations", [])
    original = {op["uid"]: deepcopy(op) for op in operations}
    for op in operations:
        if op.get("process_group_source") == AUTO_SOURCE:
            op["process_group_id"] = None
            op.pop("process_group_source", None)
            op.pop("process_group_pair_uids", None)
    counts = Counter(op.get("operation_id") for op in operations)
    by_id = {op["operation_id"]: op for op in operations
             if op.get("operation_id") and counts[op["operation_id"]] == 1}
    children = defaultdict(list)
    statuses, issues, group_renames = {}, [], []
    for op in operations:
        if op.get("operation_type") not in PAIR_TYPES:
            continue
        statuses[op["uid"]] = {"status": "PENDING", "message": "待根据明确 CAD 关系确定对应预孔/翻孔。"}
        if op.get("operation_type") == "PRE_PUNCH":
            parent = by_id.get(op.get("cad_parent_operation_id"))
            if parent and parent.get("operation_type") == "FLANGING":
                children[parent["uid"]].append(op)
            elif op.get("cad_parent_operation_id") and not parent:
                statuses[op["uid"]]["message"] = f"父动作 {op['cad_parent_operation_id']} 尚未录入或编号不唯一，补齐后自动配对。"
            else:
                statuses[op["uid"]]["message"] = "尚未找到直接对应的翻孔父动作；请按真实特征树补充关系，不按距离或名称猜配对。"
    for flange in operations:
        if flange.get("operation_type") != "FLANGING":
            continue
        pres = children.get(flange["uid"], [])
        if len(pres) > 1:
            members = [flange, *pres]
            message = f"翻孔 {flange.get('operation_id')} 对应多个预孔候选：{', '.join(p.get('operation_id') or '未编号' for p in pres)}；未自动选择。"
            issues.append({"code": "PAIRING_AMBIGUOUS", "message": message,
                           "operation_ids": [m.get("operation_id") for m in members]})
            for member in members:
                statuses[member["uid"]] = {"status": "AMBIGUOUS", "message": message}
            continue
        if len(pres) != 1:
            continue
        pre = pres[0]
        if not pre.get("operation_id") or counts[pre["operation_id"]] != 1 or counts[flange.get("operation_id")] != 1:
            continue
        pair = [pre, flange]
        pair_uids = sorted(m["uid"] for m in pair)
        explicit_ids = {original[m["uid"]].get("process_group_id") for m in pair
                        if original[m["uid"]].get("process_group_id") and original[m["uid"]].get("process_group_source") != AUTO_SOURCE}
        if len(explicit_ids) > 1:
            group = None
        else:
            group = f"PG_{flange['operation_id']}"
        if not group or any(o["uid"] not in pair_uids and
                            (o.get("process_group_id") == group or o.get("process_group_id") in explicit_ids)
                            for o in operations):
            message = f"{pre['operation_id']} 与 {flange['operation_id']} 的既有加工组记录冲突，未覆盖，请核对已有记录。"
            issues.append({"code": "PAIRING_GROUP_CONFLICT", "message": message,
                           "operation_ids": [pre["operation_id"], flange["operation_id"]]})
            for member in pair:
                statuses[member["uid"]] = {"status": "CONFLICT", "message": message}
            continue
        old_ids = sorted({original[m["uid"]].get("process_group_id") for m in pair
                          if original[m["uid"]].get("process_group_id") and original[m["uid"]].get("process_group_id") != group})
        if old_ids:
            group_renames.append({"operation_ids": [pre["operation_id"], flange["operation_id"]],
                                  "old_group_ids": old_ids, "new_group_id": group})
        for member, partner in ((pre, flange), (flange, pre)):
            member.update(process_group_id=group, process_group_source=AUTO_SOURCE,
                          process_group_pair_uids=pair_uids)
            statuses[member["uid"]] = {"status": "PAIRED", "group_id": group,
                                       "partner_operation_ids": [partner["operation_id"]],
                                       "message": f"已与 {partner['operation_id']} 自动配对（CAD 关系），加工组 {group}。"}
    # Preserve previously explicit pairs even without an automatically usable CAD link.
    groups = defaultdict(list)
    for op in operations:
        if op.get("process_group_id"):
            groups[op["process_group_id"]].append(op)
    for group, members in groups.items():
        if Counter(m.get("operation_type") for m in members) == Counter({"PRE_PUNCH": 1, "FLANGING": 1}):
            for member in members:
                if statuses[member["uid"]]["status"] == "PENDING":
                    partner = next(m for m in members if m["uid"] != member["uid"])
                    statuses[member["uid"]] = {"status": "EXPLICIT_PAIR", "group_id": group,
                                               "partner_operation_ids": [partner["operation_id"]],
                                               "message": f"保留已有明确加工组 {group}（对应 {partner['operation_id']}）。"}
    return {"assignments": [{"uid": op["uid"], **{field: deepcopy(op.get(field)) for field in PAIR_FIELDS}} for op in operations],
            "statuses": statuses, "issues": issues, "group_renames": group_renames,
            "basis": "unique direct CAD link: PRE_PUNCH child -> FLANGING parent; no geometry/name/row-order inference"}
