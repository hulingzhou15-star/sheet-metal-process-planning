"""Graph coverage and historical supervision audit, independent of generation."""
from collections import Counter

from src.graph.builders import build_part_graph
from src.graph.geometry_distance import geometry_distance
from src.graph.plan_overlay import build_plan_overlay


def near_pairs(context, static, k):
    if type(k) is not int or not 1 <= k <= static.knn_diagnostics.k:
        raise ValueError("k must fit the frozen graph neighbor lists")
    distance = {(e.source_index, e.target_index): e.distance_norm*static.normalization.length_scale
                for e in static.geometric_edges}
    pairs = {}
    for i, neighbors in static.knn_diagnostics.selected_neighbors:
        for rank, j in enumerate(neighbors[:k], 1):
            a, b = static.operation_ids[i], static.operation_ids[j]
            key = tuple(sorted((a, b)))
            row = pairs.setdefault(key, {"operation_ids": list(key),
                                         "type_pair": sorted(context.operations[op].operation_type for op in key),
                                         "boundary_distance": distance[i, j], "ranks": {},
                                         "source_feature_refs": {op: context.operations[op].source_feature_ref for op in key}})
            row["ranks"][a] = rank
    return [pairs[key] for key in sorted(pairs)]


def coverage_audit(context, plans, *, static=None, k=5):
    static = build_part_graph(context.part_data) if static is None else static
    rows = near_pairs(context, static, k)
    index = static.node_index
    edges = {(e.source_index, e.target_index): e for e in static.geometric_edges}

    def inspect(pair, assignments):
        a, b = pair
        i, j = index[a], index[b]
        edge = edges.get((i, j))
        ga, gb = static.raw_geometries[i], static.raw_geometries[j]
        attributes = build_plan_overlay(static, assignments).edge_features(i, j)
        return {"operation_ids": list(pair), "graph2_edge_present": edge is not None,
                "both_geometries_present": ga is not None and gb is not None,
                "boundary_distance": geometry_distance(ga, gb) if ga is not None and gb is not None else None,
                "graph_distance_restored": edge.distance_norm*static.normalization.length_scale if edge else None,
                "station_ids": [assignments[a], assignments[b]],
                "overlay": {"same_station": attributes.same_station, "station_delta": attributes.station_delta,
                            "abs_station_gap": attributes.abs_station_gap}}

    plan_rows = {}
    target = ["FLANGING", "IRREGULAR_PUNCH"]
    relevant = [r for r in rows if r["type_pair"] == target]
    for plan_id, assignments in plans.items():
        hits = [r for r in relevant if assignments[r["operation_ids"][0]] == assignments[r["operation_ids"][1]]]
        details = [inspect(r["operation_ids"], assignments) for r in hits]
        plan_rows[plan_id] = {"k5_type_pair_count": len(relevant), "same_station_pair_count": len(hits),
                              "same_station_pairs_missing_graph2": sum(not r["graph2_edge_present"] for r in details),
                              "same_station_pairs": details,
                              "O131_O203": inspect(("O131", "O203"), assignments)
                              if {"O131", "O203"} <= set(index) else None}
    return {"graph_k": static.knn_diagnostics.k, "diagnostic_k": k,
            "geometry_operation_count": static.knn_diagnostics.geometric_node_count,
            "missing_geometry_operations": [op for op, g in zip(static.operation_ids, static.raw_geometries) if g is None],
            "type_pair_count": len(relevant), "plans": plan_rows,
            "static_graph_fingerprint": static.fingerprint(),
            "limitations": ["k5 diagnostic coverage does not prove all physical interference is covered",
                            "global distance normalization is frozen; cross-part scale adequacy needs evaluation",
                            "edge fields are available but no training tensor adapter or learned scorer exists yet"]}


def supervision_audit(impact, mapping, original_pairs):
    rows = impact["comparisons"]
    source_rows = {r["comparison_id"]: r for r in original_pairs}
    mismatches = [r["comparison_id"] for r in rows if r["comparison_id"] not in source_rows or
                  any(r[k] != source_rows[r["comparison_id"]][k] for k in ("plan_A", "plan_B"))]
    if mismatches or len(rows) != len(source_rows) or len({r["comparison_id"] for r in rows}) != len(rows):
        raise ValueError(f"Screenshot/form identity or direction mismatch: {mismatches}")
    preferences = [r for r in rows if r["answer_from_screenshot"] in {"A", "B"}]
    references = {b for b, internal in mapping["blind_to_internal"].items() if internal == "EXPERT_REFERENCE"}
    eligible = [r for r in preferences if r["A_feasible"] and r["B_feasible"]]
    return {"answer_source": impact["provenance"], "comparison_direction_matches_original": True,
            "comparison_count": len(rows), "unique_plan_count": len({r[k] for r in rows for k in ("plan_A", "plan_B")}),
            "answer_counts": dict(Counter(r["answer_from_screenshot"] for r in rows)),
            "explicit_preference_count": len(preferences),
            "explicit_preferences_involving_reference": sum(any(r[k] in references for k in ("plan_A", "plan_B")) for r in preferences),
            "both_v06_feasible_explicit_preference_count": len(eligible),
            "both_v06_feasible_explicit_preference_ids": [r["comparison_id"] for r in eligible],
            "D_policy": "Preserve feedback; no A/B, tie or operation-pair labels are inferred",
            "interpretation": "Historical reference-centered comparisons are diagnostic data, not an unseen-part test set"}
