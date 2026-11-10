"""Observation only: reuse M2 ranked neighbors; do not create conflict rules."""
from collections import defaultdict

from src.graph.builders import build_part_graph
from .hard_constraints import HardConstraintEngine


def proximity_statistics(context, plan, *, k, static=None):
    assignments = HardConstraintEngine(context).require_feasible(plan)
    static = build_part_graph(context.part_data) if static is None else static
    diagnostic = static.knn_diagnostics
    if diagnostic is None or type(k) is not int or not 1 <= k <= diagnostic.k:
        raise ValueError("k must be positive and fit the cached original ranked neighbor list")
    if set(static.operation_ids) != set(context.operations):
        raise ValueError("Graph Operations must match the constraint context")
    if static.raw_geometries != tuple(context.part_data.geometry_for(context.operations[op]) for op in static.operation_ids):
        raise ValueError("Cached Graph geometry must match the current constraint facts")
    # Edge distances come from M2's one shared analytic geometry implementation.
    distances = {(e.source_index, e.target_index): e.distance_norm*static.normalization.length_scale
                 for e in static.geometric_edges}
    ops, directed, pairs = context.operations, [], {}
    for i, neighbors in diagnostic.selected_neighbors:
        for rank, j in enumerate(neighbors[:k], 1):
            a, b = static.operation_ids[i], static.operation_ids[j]
            directed.append({"operation_i": a, "operation_j": b, "neighbor_rank": rank,
                             "boundary_distance": distances[i, j], "type_pair": sorted([ops[a].operation_type, ops[b].operation_type]),
                             "same_station": assignments[a] == assignments[b]})
            pair = tuple(sorted((a, b)))
            if pair not in pairs:
                x, y = pair
                common = sorted({s.station_id for s in context.allowed_stations(x)} &
                                {s.station_id for s in context.allowed_stations(y)}, key=context.station_ranks.get)
                hard_path = (context.precedence_path(x, y) if y in context.precedence_reachability[x]
                             else context.precedence_path(y, x) if x in context.precedence_reachability[y] else [])
                irregular = [op for op in pair if ops[op].operation_type == "IRREGULAR_PUNCH"]
                pairs[pair] = {"operation_ids": list(pair), "type_pair": sorted([ops[x].operation_type, ops[y].operation_type]),
                               "source_feature_refs": {op: ops[op].source_feature_ref for op in pair},
                               "boundary_distance": distances[i, j], "ranks": {},
                               "station_ids": [assignments[x], assignments[y]], "same_station": assignments[x] == assignments[y],
                               "common_allowed_stations": common, "hard_precedence_path": hard_path,
                               "proven_forced_different": not common or bool(hard_path),
                               "irregular_bend_parent_facts": {op: ops[op].cad_parent_operation_id is not None and
                                   ops[ops[op].cad_parent_operation_id].operation_type == "BEND" for op in irregular}}
            pairs[pair]["ranks"][a] = rank

    def summarize(rows):
        groups = defaultdict(list)
        for row in rows:
            groups[tuple(row["type_pair"])].append(row)
        result = []
        for types, group in sorted(groups.items()):
            same = sum(row["same_station"] for row in group)
            result.append({"type_pair": types, "pair_count": len(group), "same_station_count": same,
                           "different_station_count": len(group)-same, "same_station_ratio": same/len(group),
                           "different_station_ratio": 1-same/len(group),
                           "proven_forced_different_count": sum(row.get("proven_forced_different", False) for row in group)})
        return result

    rows = [pairs[pair] for pair in sorted(pairs)]
    flanging = []
    for op in sorted(ops):
        if ops[op].operation_type != "FLANGING":
            continue
        neighbors = [{**row, "neighbor_type": ops[row["operation_j"]].operation_type,
                      "neighbor_source_feature_ref": ops[row["operation_j"]].source_feature_ref}
                     for row in directed if row["operation_i"] == op]
        flanging.append({"operation_id": op, "source_feature_ref": ops[op].source_feature_ref,
                         "neighbors": neighbors, "irregular_neighbor_ids": [row["operation_j"] for row in neighbors
                                                                               if row["neighbor_type"] == "IRREGULAR_PUNCH"]})
    return {"purpose": "diagnostic_only", "included_in_total": False, "conflict_prior_status": "pending_type_set_and_weight_confirmation",
            "frozen_graph_k": diagnostic.k, "analysis_k": k, "geometry_operation_count": diagnostic.geometric_node_count,
            "distance_sort_decimals": diagnostic.distance_sort_decimals,
            "directed_count": len(directed), "undirected_union_count": len(rows),
            "mutual_pair_count": sum(len(row["ranks"]) == 2 for row in rows),
            "directed_type_statistics": summarize(directed), "undirected_type_statistics": summarize(rows),
            "directed_pairs": directed, "undirected_pairs": rows, "flanging_neighbors": flanging,
            "expert_example_O131_O203": pairs.get(("O131", "O203")),
            "interpretation": "CAD bend-parent is only an auxiliary fact. O203 has no parent; feature names are preserved, not retyped or paired automatically. Forced-different counts are sufficient-condition lower bounds, not complete feasibility tests."}
