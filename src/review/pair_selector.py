"""24 globally unique unordered comparisons, with auditable challenge thresholds."""
from itertools import combinations
from decimal import Decimal

from .config import BUCKET_ORDER, load_review_config, validate_review_config
from .representative_selector import soft_value


def unordered_pair(a, b):
    return tuple(sorted((a, b)))


def select_pairs(representatives, plans, distance, *, reference_id="EXPERT_REFERENCE", config=None):
    config = load_review_config() if config is None else validate_review_config(config)
    controls, seen, pairs = config["pairs"], set(), []
    roles = {(r["bucket"], r["selection_role"]): r["internal_id"] for r in representatives}
    ids = [r["internal_id"] for r in representatives] + [reference_id]

    def metrics(a, b):
        return {"internal_plan_1": a, "internal_plan_2": b,
                "distance_plan1_plan2_raw": distance.raw(plans[a]["assignments"], plans[b]["assignments"]),
                "distance_plan1_plan2": distance.normalized(plans[a]["assignments"], plans[b]["assignments"]),
                "plan1_expert_distance": plans[a]["expert_distance"]["raw"], "plan2_expert_distance": plans[b]["expert_distance"]["raw"],
                "plan1_soft": soft_value(plans[a]), "plan2_soft": soft_value(plans[b]),
                "soft_absolute_difference": abs(soft_value(plans[a])-soft_value(plans[b])),
                "expert_distance_absolute_difference": abs(plans[a]["expert_distance"]["raw"]-plans[b]["expert_distance"]["raw"])}

    def hash_tie(row):
        return tuple(sorted((plans[row["internal_plan_1"]]["plan_hash"], plans[row["internal_plan_2"]]["plan_hash"])))

    def append(a, b, group, subgroup, *, threshold=None):
        identity = unordered_pair(a, b)
        if a == b or identity in seen:
            raise ValueError(f"Duplicate unordered comparison: {identity}")
        seen.add(identity)
        pairs.append({"group": group, "subgroup": subgroup, **metrics(a, b), "selection_threshold": threshold})

    for bucket in BUCKET_ORDER:
        for role in ("LOW_SOFT", "DIVERSITY"):
            append(reference_id, roles[bucket, role], "A", role)
    for bucket in BUCKET_ORDER:
        for first, second in (("LOW_SOFT", "HIGH_OP60_LOAD"), ("TYPICAL", "DIVERSITY")):
            append(roles[bucket, first], roles[bucket, second], "B", f"{first}/{second}")
    adjacent_audit = []
    for first, second in zip(BUCKET_ORDER, BUCKET_ORDER[1:]):
        options = [metrics(a["internal_id"], b["internal_id"]) for a in representatives if a["bucket"] == first
                   for b in representatives if b["bucket"] == second]
        options.sort(key=lambda row: (row["soft_absolute_difference"], hash_tie(row)))
        eligible = [row for row in options if unordered_pair(row["internal_plan_1"], row["internal_plan_2"]) not in seen]
        if len(eligible) < controls["adjacent_per_boundary"]:
            raise ValueError("Insufficient adjacent-bucket comparisons")
        for row in eligible[:controls["adjacent_per_boundary"]]:
            append(row["internal_plan_1"], row["internal_plan_2"], "C", f"{first}/{second}")
        adjacent_audit.append({"boundary": [first, second], "greedy_order": eligible})
    challenge_audit = []
    for kind in ("SIMILAR_EXPERT_DISTANCE_LARGE_SOFT_GAP", "SIMILAR_SOFT_LARGE_ASSIGNMENT_DISTANCE"):
        distance_mode = kind.startswith("SIMILAR_EXPERT")
        threshold = (Decimal(controls["challenge_distance_initial"]) if distance_mode
                     else Decimal(str(controls["challenge_soft_initial"])))
        step = (Decimal(controls["challenge_distance_step"]) if distance_mode else Decimal(str(controls["challenge_soft_step"])))
        selected_count, rounds = 0, []
        while selected_count < controls["challenge_per_kind"]:
            available = [metrics(a, b) for a, b in combinations(ids, 2) if unordered_pair(a, b) not in seen]
            if not available:
                raise ValueError("Insufficient distinct challenge comparisons")
            field = "expert_distance_absolute_difference" if distance_mode else "soft_absolute_difference"
            eligible = [row for row in available if Decimal(str(row[field])) <= threshold]
            if distance_mode:
                eligible.sort(key=lambda row: (-row["soft_absolute_difference"], row["expert_distance_absolute_difference"], hash_tie(row)))
            else:
                eligible.sort(key=lambda row: (-row["distance_plan1_plan2"], row["soft_absolute_difference"], hash_tie(row)))
            taking = eligible[:controls["challenge_per_kind"]-selected_count]
            for row in taking:
                append(row["internal_plan_1"], row["internal_plan_2"], "D", kind, threshold=float(threshold))
            selected_count += len(taking)
            rounds.append({"threshold": float(threshold), "eligible_count": len(eligible), "chosen_count": len(taking),
                           "chosen_pairs": [[row["internal_plan_1"], row["internal_plan_2"]] for row in taking]})
            if selected_count < controls["challenge_per_kind"]:
                threshold += step
        challenge_audit.append({"kind": kind, "initial_threshold": rounds[0]["threshold"],
                                "final_threshold": rounds[-1]["threshold"], "relaxed": len(rounds) > 1, "rounds": rounds})
    if len(pairs) != 24 or len(seen) != len(pairs):
        raise ValueError("Review requires 24 distinct unordered comparisons")
    return pairs, {"adjacent_matching": adjacent_audit, "challenge_design": challenge_audit,
                   "unordered_pair_count": len(seen), "group_counts": {g: sum(p["group"] == g for p in pairs) for g in "ABCD"}}
