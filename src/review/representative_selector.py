"""Four ordered, deterministic roles per bucket; no new candidate generation."""
from copy import deepcopy
import math
import statistics

from .config import BUCKET_ORDER, load_review_config, validate_review_config


def soft_value(candidate):
    return candidate["soft"]["total_soft_penalty"]


def station_load(candidate, station_id):
    return sum(s == station_id for s in candidate["assignments"].values())


def select_representatives(candidates, distance, config=None):
    config = load_review_config() if config is None else validate_review_config(config)
    controls = config["representatives"]
    if len({c["plan_id"] for c in candidates}) != len(candidates) or len({c["plan_hash"] for c in candidates}) != len(candidates):
        raise ValueError("Source candidates must have unique IDs and assignments")
    representatives, bucket_audit = [], {}
    for bucket in BUCKET_ORDER:
        remaining = [c for c in candidates if c["expert_distance"]["bucket"] == bucket]
        if len(remaining) < controls["per_bucket"]:
            raise ValueError(f"{bucket} lacks four distinct representative candidates")
        chosen = []

        def accept(candidate, role, audit):
            chosen.append(candidate)
            remaining.remove(candidate)
            representatives.append({"internal_id": candidate["plan_id"], "bucket": bucket, "selection_role": role,
                                    "plan_hash": candidate["plan_hash"],
                                    "distance_to_expert_raw": candidate["expert_distance"]["raw"],
                                    "distance_to_expert_normalized": candidate["expert_distance"]["normalized"],
                                    "total_soft_penalty": soft_value(candidate),
                                    "soft_components": deepcopy(candidate["soft"]["components"]),
                                    "op60_load": station_load(candidate, controls["high_load_station"]),
                                    "selection_audit": audit})

        first = min(remaining, key=lambda c: (soft_value(c), c["plan_hash"]))
        accept(first, "LOW_SOFT", {"criterion": "minimum_total_soft_penalty"})
        distances = [c["expert_distance"]["raw"] for c in remaining]
        softs = [soft_value(c) for c in remaining]
        median_distance, median_soft = statistics.median(distances), statistics.median(softs)
        distance_range, soft_range = max(distances)-min(distances), max(softs)-min(softs)
        typical_rows = []
        for candidate in remaining:
            zd = (candidate["expert_distance"]["raw"]-median_distance)/max(1, distance_range)
            zs = (soft_value(candidate)-median_soft)/max(controls["typical_epsilon"], soft_range)
            typical_rows.append({"internal_id": candidate["plan_id"], "z_distance": zd, "z_soft": zs,
                                 "median_point_distance": math.hypot(zd, zs), "plan_hash": candidate["plan_hash"]})
        scores = {row["internal_id"]: row["median_point_distance"] for row in typical_rows}
        typical = min(remaining, key=lambda c: (scores[c["plan_id"]], soft_value(c), c["plan_hash"]))
        accept(typical, "TYPICAL", {"statistics_scope": "remaining_after_LOW_SOFT", "source_count": len(distances),
                                    "median_distance": median_distance, "median_soft": median_soft,
                                    "distance_range": distance_range, "soft_range": soft_range,
                                    "epsilon": controls["typical_epsilon"], "candidate_scores": typical_rows})
        high = min(remaining, key=lambda c: (-station_load(c, controls["high_load_station"]), soft_value(c), c["plan_hash"]))
        accept(high, "HIGH_OP60_LOAD", {"station_id": controls["high_load_station"],
                                         "criterion": "maximum_station_load_then_penalty_hash"})
        minimum_distances = {c["plan_id"]: min(distance.normalized(c["assignments"], p["assignments"]) for p in chosen)
                             for c in remaining}
        diverse = min(remaining, key=lambda c: (-minimum_distances[c["plan_id"]], soft_value(c), c["plan_hash"]))
        accept(diverse, "DIVERSITY", {"against_internal_ids": [c["plan_id"] for c in chosen],
                                       "candidate_min_distances": minimum_distances,
                                       "selected_min_distance": minimum_distances[diverse["plan_id"]]})
        bucket_audit[bucket] = {"source_count": len(distances)+1, "selected_count": len(chosen),
                                "selected_internal_ids": [c["plan_id"] for c in chosen]}
    return representatives, {"buckets": bucket_audit, "selection_order": ["LOW_SOFT", "TYPICAL", "HIGH_OP60_LOAD", "DIVERSITY"],
                             "typical_statistics_scope": "remaining_after_LOW_SOFT", "random_selection": False}
