"""Descriptive cohort comparisons; no feature/quality or label fabrication."""
from collections import Counter
from itertools import combinations
import math

from src.candidate.distance import PlanDistance, plan_hash
from .policy import audit_plan


def distribution(values):
    return {"count": len(values), "min": min(values) if values else None,
            "mean": math.fsum(values)/len(values) if values else None, "max": max(values) if values else None}


def audit_cohort(context, neighborhood, policy, plans, name):
    rows, n1_ids = [], {r["pair_id"] for r in policy["pairs"]}
    for plan in plans:
        audit = audit_plan(context, policy, plan["assignments"])
        external = []
        if audit["hard"]["feasible"]:
            for relation in neighborhood.external_relations:
                a, b = relation["operation_ids"]
                external.append({"pair_id": relation["pair_id"], "family": relation["family"],
                                 "within_n1": relation["pair_id"] in n1_ids,
                                 "same_station": plan["assignments"][a] == plan["assignments"][b]})
        rows.append({"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"], **audit,
                     "other_external_same_station_pair_ids": [r["pair_id"] for r in external if r["same_station"] and not r["within_n1"]],
                     "external_states": external})
    distance = PlanDistance.from_context(context)
    gaps = [distance.raw(a["assignments"], b["assignments"]) for a, b in combinations(plans, 2)]
    per_pair = {r["pair_id"]: sum(any(v["pair_id"] == r["pair_id"] for v in p["violations"]) for p in rows) for r in policy["pairs"]}
    station_loads = {s.station_id: distribution([sum(station == s.station_id for station in p["assignments"].values()) for p in plans])
                     for s in context.ordered_stations}
    station_by_type = {s.station_id: {t: distribution([sum(context.operations[op].operation_type == t and station == s.station_id
                       for op, station in p["assignments"].items()) for p in plans]) for t in sorted({o.operation_type for o in context.operations.values()})}
                       for s in context.ordered_stations}
    valid_counts = [r["violation_count"] for r in rows if r["n1_evaluated"]]
    n = len(distance.movable_operation_ids)
    return {"cohort": name, "plan_count": len(plans), "unique_plan_count": len({p["plan_hash"] for p in plans}),
            "hard_pass_count": sum(r["hard"]["feasible"] for r in rows), "n1_pass_count": sum(r["trial_admitted"] for r in rows),
            "n1_violation_distribution": distribution(valid_counts), "per_pair_same_station_count": per_pair,
            "pairwise_raw_hamming": distribution(gaps),
            "pairwise_normalized_hamming": distribution([g/n for g in gaps]) if n else distribution([]),
            "movable_operation_count": n, "station_load_distribution": station_loads,
            "station_operation_type_distribution": station_by_type,
            "other_external_same_station_count": distribution([len(r["other_external_same_station_pair_ids"]) for r in rows]),
            "per_operation_used_stations": {op: sorted({p["assignments"][op] for p in plans}, key=context.station_ranks.get) for op in sorted(context.operations)},
            "plans": rows, "interpretation": "Descriptive samples, not feasible-space volume or process quality scores"}


def compare_candidates(context, neighborhood, policy, before, after):
    """Closest baseline for display only, never used as an inference warm start."""
    distance = PlanDistance.from_context(context)
    result = []
    if not before:
        return result
    for new in after:
        old = min(before, key=lambda p: (distance.raw(p["assignments"], new["assignments"]), p["plan_hash"]))
        changed = [{"operation_id": op, "operation_type": context.operations[op].operation_type,
                    "before_station": old["assignments"][op], "after_station": new["assignments"][op]}
                   for op in sorted(context.operations) if old["assignments"][op] != new["assignments"][op]]
        impacts = []
        n1_ids = {r["pair_id"] for r in policy["pairs"]}
        for r in neighborhood.external_relations:
            a, b = r["operation_ids"]
            pre = old["assignments"][a] == old["assignments"][b]
            post = new["assignments"][a] == new["assignments"][b]
            if pre != post:
                impacts.append({"pair_id": r["pair_id"], "within_n1": r["pair_id"] in n1_ids,
                                "before_same_station": pre, "after_same_station": post})
        result.append({"comparison_id": f"N1_INTERNAL_{len(result)+1:03d}", "before_plan_id": old["plan_id"],
                       "after_plan_id": new["plan_id"], "before_plan_hash": old["plan_hash"], "after_plan_hash": new["plan_hash"],
                       "raw_hamming": distance.raw(old["assignments"], new["assignments"]), "all_changes": changed,
                       "external_same_station_changes": impacts, "answer": None,
                       "interpretation": "Post-generation display pairing, not an isolated one-operation contrast or preference label"})
    return result


def known_case_comparisons(context, neighborhood, policy, before, after, cases):
    """Post-generation regression views. Case IDs never change candidate decisions."""
    distance = PlanDistance.from_context(context)
    policy_pairs = {r["pair_id"]: r["operation_ids"] for r in policy["pairs"]}
    result = []
    for case in cases:
        key = case["pair_id"]
        if key not in policy_pairs or not after:
            continue
        a, b = policy_pairs[key]
        bad = [r for r in before if r["assignments"][a] == r["assignments"][b]]
        if not bad:
            continue
        old, new = min(product_pairs(bad, after), key=lambda ab: (distance.raw(ab[0]["assignments"], ab[1]["assignments"]), ab[0]["plan_hash"], ab[1]["plan_hash"]))
        row = compare_candidates(context, neighborhood, policy, [old], [new])[0]
        row.update(comparison_id=f"N1_KNOWN_{len(result)+1:03d}", focus_pair_id=key,
                   source="offline_known_case_display_only")
        result.append(row)
    return result


def product_pairs(before, after):
    return ((a, b) for a in before for b in after)
