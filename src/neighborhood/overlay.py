"""Candidate-specific observations; this is not a quality scorer or tensor model."""
from src.candidate.distance import plan_hash
from src.constraints.v06 import HardConstraintEngine


def build_neighborhood_overlay(context, neighborhood, plan):
    if neighborhood.snapshot["facts_fingerprint"] != context.facts_fingerprint:
        raise ValueError("Neighborhood belongs to another part/fact version")
    assignments = HardConstraintEngine(context).require_feasible(plan)
    rows = []
    for r in neighborhood.snapshot["relations"]:
        a, b = r["operation_ids"]
        delta = context.station_ranks[assignments[b]]-context.station_ranks[assignments[a]]
        rows.append({"pair_id": r["pair_id"], "kind": r["kind"], "operation_ids": [a, b],
                     "station_ids": [assignments[a], assignments[b]],
                     "same_station": delta == 0, "station_rank_delta_a_to_b": delta,
                     "station_rank_gap": abs(delta),
                     "order": "SAME" if delta == 0 else "A_BEFORE_B" if delta > 0 else "B_BEFORE_A"})
    external = [r for r in rows if r["kind"] == "EXTERNAL_NEIGHBOR"]
    return {"schema_version": "neighborhood_overlay.v0.1", "neighborhood_fingerprint": neighborhood.fingerprint,
            "plan_hash": plan_hash(assignments), "relations": rows,
            "summary": {"external_same_station_pairs": sum(r["same_station"] for r in external),
                        "external_different_station_pairs": sum(not r["same_station"] for r in external)},
            "quality_score": None, "preference_label": None, "used_in_selection": False}
