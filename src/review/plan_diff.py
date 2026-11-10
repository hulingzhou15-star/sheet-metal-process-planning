"""Factual assignment differences and frozen bend metadata, without judgments."""
from collections import Counter, defaultdict

from src.constraints import HardConstraintEngine


def type_name(value):
    return getattr(value, "value", value)


def plan_view(context, assignments):
    assignments = HardConstraintEngine(context).require_feasible(assignments)
    stations = [s.station_id for s in context.ordered_stations]
    types = sorted({type_name(op.operation_type) for op in context.part_data.operations})
    ops, regions = context.operations, {r.bend_operation_id: r for r in context.bend_influences}
    counts = Counter(assignments.values())
    assignment_rows = [{"operation_id": op, "operation_type": type_name(ops[op].operation_type), "assigned_station": assignments[op]}
                       for op in sorted(ops)]
    bend_rows = []
    for row in assignment_rows:
        if row["operation_type"] == "BEND":
            region = regions.get(row["operation_id"])
            bend_rows.append({"bend_operation_id": row["operation_id"], "assigned_station": row["assigned_station"],
                              "affected_area_proxy": region.affected_area_proxy if region else None,
                              "affected_depth": region.affected_depth if region else None,
                              "region_id": region.influence_region_id if region else None,
                              "parent_region_id": region.parent_region_id if region else None})
    return {"station_ids": stations, "station_loads": [{"station_id": s, "load": counts[s]} for s in stations],
            "operation_type_station_matrix": [{"operation_type": kind, "station_counts": {
                s: sum(type_name(ops[op].operation_type) == kind and assignments[op] == s for op in ops) for s in stations}} for kind in types],
            "operation_assignments": assignment_rows, "bend_assignments": bend_rows}


def comparison_diff(context, assignments_a, assignments_b):
    hard = HardConstraintEngine(context)
    a, b = hard.require_feasible(assignments_a), hard.require_feasible(assignments_b)
    ops = context.operations
    changed = [{"operation_id": op, "operation_type": type_name(ops[op].operation_type),
                "station_A": a[op], "station_B": b[op]} for op in ops if a[op] != b[op]]
    changed.sort(key=lambda row: (row["operation_type"], row["operation_id"]))
    loads_a, loads_b = Counter(a.values()), Counter(b.values())
    station_loads = [{"station_id": s.station_id, "load_A": loads_a[s.station_id], "load_B": loads_b[s.station_id],
                      "delta_B_minus_A": loads_b[s.station_id]-loads_a[s.station_id]} for s in context.ordered_stations]
    types = sorted({type_name(op.operation_type) for op in context.part_data.operations})
    changes_by_type = Counter(row["operation_type"] for row in changed)
    regions = {r.bend_operation_id: r for r in context.bend_influences}
    bends = []
    for row in changed:
        if row["operation_type"] == "BEND":
            r = regions.get(row["operation_id"])
            bends.append({"operation_id": row["operation_id"], "station_A": row["station_A"], "station_B": row["station_B"],
                          "affected_area_proxy": r.affected_area_proxy if r else None,
                          "affected_depth": r.affected_depth if r else None,
                          "region_id": r.influence_region_id if r else None, "parent_region_id": r.parent_region_id if r else None})
    return {"differing_operation_count": len(changed), "changed_operations": changed, "station_load_comparison": station_loads,
            "operation_type_summary": [{"operation_type": kind, "differing_operation_count": changes_by_type[kind]} for kind in types],
            "bend_changes": bends}


def internal_plan_diff(context, baseline, candidate):
    diff = comparison_diff(context, baseline, candidate)
    ops, ranks = context.operations, context.station_ranks
    participation = defaultdict(set)
    for relation in context.precedence_relations:
        participation[relation.predecessor_id].add(relation.relation_source)
        participation[relation.operation_id].add(relation.relation_source)
    changed = []
    for row in diff["changed_operations"]:
        op = ops[row["operation_id"]]
        sources = participation[op.operation_id]
        changed.append({"operation_id": op.operation_id, "operation_type": type_name(op.operation_type),
                        "expert_station": row["station_A"], "candidate_station": row["station_B"],
                        "station_rank_delta": ranks[row["station_B"]]-ranks[row["station_A"]],
                        "process_group_id": op.process_group_id, "required_predecessor_ids": list(op.required_predecessor_ids),
                        "precedence_sources": sorted(sources),
                        **{source: source in sources for source in ("EXPLICIT_PREDECESSOR", "PROCESS_GROUP", "BEND_REGION_PRECEDENCE")}})
    by_type = []
    for row in diff["operation_type_summary"]:
        kind = row["operation_type"]
        total = sum(type_name(op.operation_type) == kind for op in ops.values())
        transitions = Counter(f"{r['expert_station']} -> {r['candidate_station']}" for r in changed if r["operation_type"] == kind)
        by_type.append({"operation_type": kind, "total_operation_count": total, "changed_count": row["differing_operation_count"],
                        "unchanged_count": total-row["differing_operation_count"], "station_transitions": dict(sorted(transitions.items()))})
    regions = {r.bend_operation_id: r for r in context.bend_influences}
    ancestors = context.region_ancestors
    bend_rows = []
    for row in changed:
        if row["operation_type"] == "BEND":
            r = regions.get(row["operation_id"])
            bend_rows.append({"bend_operation_id": row["operation_id"], "expert_station": row["expert_station"],
                              "candidate_station": row["candidate_station"], "station_rank_delta": row["station_rank_delta"],
                              "bend_axis": r.bend_axis if r else None, "affected_depth": r.affected_depth if r else None,
                              "bend_length": r.bend_length if r else None, "affected_area_proxy": r.affected_area_proxy if r else None,
                              "influence_region_id": r.influence_region_id if r else None, "parent_region_id": r.parent_region_id if r else None,
                              "region_level": len(ancestors[r.influence_region_id]) if r else None})
    return {"changed_operation_count": len(changed), "changed_operations": changed, "bend_changes": bend_rows,
            "station_loads": [{"station_id": row["station_id"], "expert_load": row["load_A"], "candidate_load": row["load_B"],
                               "delta": row["delta_B_minus_A"]} for row in diff["station_load_comparison"]],
            "operation_type_movement": by_type,
            "precedence_related_changed_operations": [row for row in changed if row["precedence_sources"]]}
