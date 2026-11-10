"""Weak per-bucket prefilter, then deterministic max-min diversity selection."""
from copy import deepcopy
from decimal import Decimal, ROUND_CEILING

from .config import BUCKET_ORDER


def penalty(candidate):
    return candidate["soft"]["total_soft_penalty"]


def soft_prefilter(candidates, fraction):
    if not 0 < fraction <= 1:
        raise ValueError("Prefilter fraction must be in (0,1]")
    ordered = sorted(candidates, key=lambda c: (penalty(c), c["plan_hash"]))
    count = int((Decimal(str(fraction))*len(ordered)).to_integral_value(rounding=ROUND_CEILING))
    return ordered[:count]


def farthest_first(candidates, count, distance, *, selected=()):
    chosen = list(selected)
    hashes = {c["plan_hash"] for c in chosen}
    remaining = [c for c in candidates if c["plan_hash"] not in hashes]
    while remaining and len(chosen) < count:
        if chosen:
            best = min(remaining, key=lambda c: (-min(distance.normalized(c["assignments"], p["assignments"])
                                                     for p in chosen), penalty(c), c["plan_hash"]))
        else:
            best = min(remaining, key=lambda c: (penalty(c), c["plan_hash"]))
        chosen.append(best)
        remaining = [c for c in remaining if c["plan_hash"] != best["plan_hash"]]
    return chosen


def select_candidates(pool, distance, *, target_per_bucket, soft_prefilter_fraction):
    if len({c["plan_hash"] for c in pool}) != len(pool):
        raise ValueError("Selection pool contains duplicate Plans")
    prefiltered, chosen, audit = {}, {}, {}
    for bucket in BUCKET_ORDER:
        candidates = [c for c in pool if c["expert_distance"]["bucket"] == bucket]
        prefiltered[bucket] = soft_prefilter(candidates, soft_prefilter_fraction)
        chosen[bucket] = farthest_first(prefiltered[bucket], target_per_bucket, distance)
        audit[bucket] = {"pool_count": len(candidates), "prefilter_count": len(prefiltered[bucket]),
                         "original_target": target_per_bucket, "initial_selected_count": len(chosen[bucket]),
                         "redistributed_extra_count": 0,
                         "prefilter_hashes": [c["plan_hash"] for c in prefiltered[bucket]]}
    target = len(BUCKET_ORDER)*target_per_bucket
    redistribution = []
    # Reallocate only the shortfall, in stable bucket order. Additional picks
    # continue that bucket's same max-min rule and remain inside its prefilter.
    while sum(map(len, chosen.values())) < target:
        progressed = False
        for bucket in BUCKET_ORDER:
            if len(chosen[bucket]) < len(prefiltered[bucket]) and sum(map(len, chosen.values())) < target:
                chosen[bucket] = farthest_first(prefiltered[bucket], len(chosen[bucket])+1, distance, selected=chosen[bucket])
                audit[bucket]["redistributed_extra_count"] += 1
                redistribution.append({"recipient_bucket": bucket, "count": 1})
                progressed = True
        if not progressed:
            break
    selected = []
    for bucket in BUCKET_ORDER:
        for index, candidate in enumerate(chosen[bucket], 1):
            record = deepcopy(candidate)
            record["pool_plan_id"] = record["plan_id"]
            record["plan_id"] = f"{bucket}_{index:03d}"
            selected.append(record)
        audit[bucket]["selected_count"] = len(chosen[bucket])
    return selected, {"method": "farthest_first", "soft_prefilter_fraction": soft_prefilter_fraction,
                      "prefilter_rounding": "ceil(fraction * pool_count)", "buckets": audit,
                      "redistribution": redistribution, "requested_total": target, "selected_total": len(selected),
                      "unfilled_total": target-len(selected), "diagnostics_used": False}
