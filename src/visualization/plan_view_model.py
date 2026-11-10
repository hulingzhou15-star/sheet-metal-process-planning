"""Allow-listed plan facts and display states, with Station.order-based ranks."""
from src.review.plan_diff import plan_view, type_name

from .config import MODES


def build_plan_view_model(context, assignments, catalog, blind_id):
    view = plan_view(context, assignments)
    ops = context.operations
    bends = {r.bend_operation_id: r for r in context.bend_influences}
    rows = {}
    for row in view["operation_assignments"]:
        op = ops[row["operation_id"]]
        data = {**row, "station_rank": context.station_ranks[row["assigned_station"]],
                "process_group_id": op.process_group_id, "required_predecessor_ids": list(op.required_predecessor_ids),
                "cad_parent_operation_id": op.cad_parent_operation_id, "surface_side": type_name(op.surface_side),
                "region_group": catalog["membership"][op.operation_id],
                "renderable": catalog["operations"][op.operation_id]["renderable"]}
        r = bends.get(op.operation_id)
        if r:
            data["bend"] = {"bend_operation_id": op.operation_id, "assigned_station": row["assigned_station"],
                            "bend_axis": r.bend_axis, "affected_depth": r.affected_depth, "bend_length": r.bend_length,
                            "affected_area_proxy": r.affected_area_proxy, "influence_region_id": r.influence_region_id,
                            "parent_region_id": r.parent_region_id, "region_level": len(context.region_ancestors[r.influence_region_id])}
        rows[op.operation_id] = data
    return {"blind_id": blind_id, "station_ids": view["station_ids"],
            "station_ranks": context.station_ranks, "operations": rows,
            "station_loads": view["station_loads"], "operation_type_station_matrix": view["operation_type_station_matrix"]}


def operation_status(row, current_rank, mode="CURRENT_ONLY"):
    if mode not in MODES:
        raise ValueError("Unknown display mode")
    if mode == "FULL_PLAN_BY_STATION":
        return "full"
    if row["station_rank"] == current_rank:
        return "current"
    if mode == "CUMULATIVE" and row["station_rank"] < current_rank:
        return "previous"
    return "inactive"


def operation_focus(operation_id, catalog, *, changed_ids=None, difference_only=False, region_filter="ALL"):
    if difference_only and (changed_ids is None or operation_id not in changed_ids):
        return "unchanged"
    if region_filter == "ALL" or catalog["membership"][operation_id] == region_filter:
        return "primary"
    if region_filter in catalog["regions"]:
        selected = catalog["regions"][region_filter]
        if operation_id == selected["bend_operation_id"]:
            return "primary"
        if any(r["bend_operation_id"] == operation_id and (
                rid in selected["ancestor_region_ids"] or region_filter in r["ancestor_region_ids"])
               for rid, r in catalog["regions"].items()):
            return "related"
    return "filtered"


def current_station_info(view, station_id, catalog, **filters):
    if station_id not in view["station_ranks"]:
        raise ValueError("Unknown Station")
    from collections import Counter
    all_rows = [row for row in view["operations"].values() if row["assigned_station"] == station_id]
    shown = [row for row in all_rows if operation_focus(row["operation_id"], catalog, **filters) == "primary"]
    return {"station_id": station_id, "operation_count": len(all_rows), "shown_count": len(shown),
            "operation_type_counts": dict(sorted(Counter(row["operation_type"] for row in all_rows).items())),
            "operations": sorted(shown, key=lambda row: (row["operation_type"], row["operation_id"]))}
