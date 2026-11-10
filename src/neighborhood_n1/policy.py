"""Only FLANGING-anchored PRIORITY external IRREGULAR_PUNCH relations enter N1."""
from copy import deepcopy
import json
from pathlib import Path

from src.constraints.v06 import HardConstraintEngine
from src.inference.input import InferenceContext
from src.neighborhood.context import digest, positive

DEFAULT_CONFIG = Path(__file__).resolve().parents[2]/"configs/neighborhood_n1.json"
POLICY = {"name": "N1", "status": "review_trial_only", "relation_kind": "EXTERNAL_NEIGHBOR",
          "anchor_type": "FLANGING", "neighbor_type": "IRREGULAR_PUNCH", "tier": "PRIORITY", "condition": "different_station"}


def validate_config(config):
    config = deepcopy(config)
    if config["schema_version"] != "neighborhood_n1.v0.1" or config["policy"] != POLICY:
        raise ValueError("N1 scope must remain the explicitly authorized review trial")
    g, s = config["generation"], config["solver"]
    for name in ("seed", "pool_target", "selected_target", "max_attempts", "random_cost_abs_max"):
        if type(g[name]) is not int or g[name] < (0 if name == "seed" else 1):
            raise ValueError(f"Invalid generation {name}")
    if g["selected_target"] > g["pool_target"]:
        raise ValueError("Selected target exceeds requested pool")
    if s["num_search_workers"] != 1 or not all(positive(s[k]) for k in ("max_time_seconds", "max_deterministic_time")):
        raise ValueError("Positive solver budgets and a single worker required")
    if type(config["conflict_analysis"]["max_deletion_checks"]) is not int or config["conflict_analysis"]["max_deletion_checks"] < 0:
        raise ValueError("Invalid deletion-check budget")
    if not all(positive(config["view"][k]) for k in ("minimum_extent", "padding_fraction", "hit_radius_px")):
        raise ValueError("Invalid display parameters")
    return config


def load_config(path=DEFAULT_CONFIG):
    return validate_config(json.loads(Path(path).read_text(encoding="utf-8")))


def collect_policy(context, neighborhood, config):
    if not isinstance(context, InferenceContext):
        raise TypeError("Use the fact-only inference entry")
    config = validate_config(config)
    if neighborhood.snapshot["facts_fingerprint"] != context.facts_fingerprint:
        raise ValueError("Neighborhood/fact mismatch")
    included, excluded = [], []
    for row in neighborhood.snapshot["relations"]:
        a, b = row["operation_ids"]
        types = {context.operations[a].operation_type, context.operations[b].operation_type}
        if row["kind"] != "EXTERNAL_NEIGHBOR":
            excluded.append({"pair_id": row["pair_id"], "reason": "internal_process_relation"})
            continue
        if types != {"FLANGING", "IRREGULAR_PUNCH"}:
            excluded.append({"pair_id": row["pair_id"], "reason": "other_operation_type_combination"})
            continue
        flange = a if context.operations[a].operation_type == "FLANGING" else b
        irregular = b if flange == a else a
        arcs = [arc for arc in row["arcs"] if arc["anchor"] == flange and arc["neighbor"] == irregular and arc["tier"] == "PRIORITY"]
        if not arcs:
            excluded.append({"pair_id": row["pair_id"], "reason": "no_priority_arc_from_flanging_anchor"})
            continue
        included.append({"pair_id": row["pair_id"], "operation_ids": [a, b],
                         "flanging_operation_id": flange, "irregular_operation_id": irregular,
                         "condition": "different_station", "source_arc": deepcopy(arcs[0]),
                         "boundary_distance": row["boundary_distance"], "gates": deepcopy(row["gates"]),
                         "neighborhood_inclusion_reason": "FLANGING-anchored PRIORITY external IRREGULAR_PUNCH",
                         "existing_relations": deepcopy(row["relations"])})
    included.sort(key=lambda r: r["pair_id"])
    if len({r["pair_id"] for r in included}) != len(included):
        raise ValueError("Duplicate N1 pair")
    payload = {"schema_version": config["schema_version"], "policy": config["policy"],
               "facts_fingerprint": context.facts_fingerprint, "neighborhood_fingerprint": neighborhood.fingerprint,
               "neighborhood_rules": deepcopy(neighborhood.snapshot["rules"]), "pairs": included, "excluded": excluded,
               "interpretation": "Independent experimental candidate admission, not H8, SoftPenalty, process truth or a training label"}
    return {"fingerprint": digest(payload), **payload}


def check_policy_context(context, policy):
    if not isinstance(context, InferenceContext) or policy["facts_fingerprint"] != context.facts_fingerprint:
        raise ValueError("N1 context/fact mismatch")
    if policy["policy"] != POLICY:
        raise ValueError("Unexpected N1 scope")
    body = {k: v for k, v in policy.items() if k != "fingerprint"}
    if policy["fingerprint"] != digest(body):
        raise ValueError("N1 policy fingerprint mismatch")


def audit_plan(context, policy, plan):
    check_policy_context(context, policy)
    hard = HardConstraintEngine(context).evaluate(plan)
    if not hard.feasible:
        return {"hard": hard.to_dict(), "n1_evaluated": False, "n1_pass": None,
                "trial_admitted": False, "violations": [], "violation_count": None}
    assignments = HardConstraintEngine(context).require_feasible(plan)
    violations = []
    for r in policy["pairs"]:
        a, b = r["operation_ids"]
        if assignments[a] == assignments[b]:
            violations.append({"pair_id": r["pair_id"], "operation_ids": [a, b],
                               "station_ids": [assignments[a], assignments[b]],
                               "policy_type": "N1_REVIEW_TRIAL", "message": "N1 trial pair is assigned to the same Station"})
    return {"hard": hard.to_dict(), "n1_evaluated": True, "n1_pass": not violations,
            "trial_admitted": not violations, "violation_count": len(violations), "violations": violations,
            "quality_score": None, "preference_label": None}
