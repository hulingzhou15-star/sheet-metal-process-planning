"""Joint feasibility and reference-free generation with an independent N1 filter."""
import hashlib
import json
import random

from ortools.sat.python import cp_model

from src.candidate.distance import plan_hash
from src.constraints.v06.feasibility import build_feasibility_model
from src.inference.generation import assignment_from_solution, derived_seed, solver_audit, solver_for
from .policy import audit_plan, check_policy_context, validate_config


def build_model(context, policy, pair_ids=None, *, assumptions=False):
    check_policy_context(context, policy)
    model, ranks = build_feasibility_model(context)
    rows = {r["pair_id"]: r for r in policy["pairs"]}
    pair_ids = sorted(rows) if pair_ids is None else sorted(pair_ids)
    if not set(pair_ids) <= set(rows):
        raise ValueError("Unknown N1 pair")
    literals = {}
    for key in pair_ids:
        a, b = rows[key]["operation_ids"]
        constraint = model.add(ranks[a] != ranks[b])
        if assumptions:
            flag = model.new_bool_var("N1["+key+"]")
            constraint.only_enforce_if(flag)
            model.add_assumption(flag)
            literals[flag.index] = key
    return model, ranks, literals


def _solve(context, policy, config, pair_ids=None, *, assumptions=False, stage="joint"):
    model, ranks, literals = build_model(context, policy, pair_ids, assumptions=assumptions)
    seed = derived_seed(context, config["generation"]["seed"], "N1:"+stage)
    solver = solver_for(config, seed)
    status = solver.solve(model)
    if status == cp_model.MODEL_INVALID:
        raise RuntimeError(model.validate())
    return status, solver, ranks, literals, {"seed": seed, **solver_audit(solver, status)}


def joint_feasibility(context, policy, config):
    config = validate_config(config)
    status, _, _, _, base = _solve(context, policy, config, [], stage="hard_baseline")
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"state": "hard_infeasible" if status == cp_model.INFEASIBLE else "hard_feasibility_unknown",
                "hard_baseline": base, "generation_allowed": False, "n1_conflict_proven": False}
    status, solver, ranks, literals, joint = _solve(context, policy, config, assumptions=True)
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        plan = assignment_from_solution(context, ranks, solver)
        audit = audit_plan(context, policy, plan)
        if not audit["trial_admitted"]:
            raise RuntimeError("Joint solver disagrees with independent N1/H1-H7 audit")
        return {"state": "joint_feasible", "hard_baseline": base, "joint": joint, "generation_allowed": True,
                "witness": {"assignments": plan, "plan_hash": plan_hash(plan), "audit": audit},
                "n1_pair_count": len(policy["pairs"]), "n1_conflict_proven": False}
    if status != cp_model.INFEASIBLE:
        return {"state": "joint_feasibility_unknown", "hard_baseline": base, "joint": joint,
                "generation_allowed": False, "n1_conflict_proven": False}
    core = sorted(literals[int(index)] for index in solver.sufficient_assumptions_for_infeasibility())
    initial, checks = core.copy(), []
    for key in initial:
        if len(checks) >= config["conflict_analysis"]["max_deletion_checks"]:
            break
        subset = [p for p in core if p != key]
        state, _, _, _, audit = _solve(context, policy, config, subset, stage="delete:"+key)
        removed = state == cp_model.INFEASIBLE
        checks.append({"removed_for_test": key, "subset": subset, "deletion_retained": removed, **audit})
        if removed:
            core = subset
    minimal = len(checks) == len(initial) and all(c["status"] in {"INFEASIBLE", "OPTIMAL", "FEASIBLE"} for c in checks)
    return {"state": "joint_infeasible", "hard_baseline": base, "joint": joint, "generation_allowed": False,
            "n1_conflict_proven": True, "initial_sufficient_core": initial, "conflict_pair_ids": core,
            "deletion_checks": checks, "deletion_minimal_verified": minimal,
            "minimum_cardinality_claimed": False,
            "scope": "These N1 pairs conflict in the presence of the entire frozen H1-H7 model; hard rules themselves were not minimized"}


def generate_candidates(context, policy, config, joint, *, progress=None):
    config = validate_config(config)
    check_policy_context(context, policy)
    if not joint.get("generation_allowed") or joint.get("state") != "joint_feasible":
        return [], {"state": "skipped_joint_not_established", "attempts": [], "actual_pool": 0}
    # The joint witness is validation only; it is not injected as a candidate.
    opts = config["generation"]
    pool, logs = [], []
    for index in range(opts["max_attempts"]):
        model, ranks, _ = build_model(context, policy)
        ordered = sorted(ranks)
        if pool:
            model.add_forbidden_assignments([ranks[o] for o in ordered],
                [[context.station_ranks[p["assignments"][o]] for o in ordered] for p in pool])
        seed = derived_seed(context, opts["seed"], "N1:candidate:"+policy["fingerprint"], index)
        rng, costs, terms = random.Random(seed), [], []
        for op in ordered:
            allowed = context.allowed_stations(op)
            if len(allowed) <= 1:
                continue
            for station in allowed:
                flag = model.new_bool_var(f"at[{op},{station.station_id}]")
                model.add(ranks[op] == context.station_ranks[station.station_id]).only_enforce_if(flag)
                model.add(ranks[op] != context.station_ranks[station.station_id]).only_enforce_if(flag.Not())
                cost = rng.randint(-opts["random_cost_abs_max"], opts["random_cost_abs_max"])
                terms.append(cost*flag)
                costs.append([op, station.station_id, cost])
        model.minimize(sum(terms))
        solver = solver_for(config, seed)
        status = solver.solve(model)
        log = {"attempt": index+1, "seed": seed, **solver_audit(solver, status), "no_good_count": len(pool),
               "objective_costs_sha256": hashlib.sha256(json.dumps(costs, separators=(",", ":")).encode()).hexdigest()}
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            assignments = assignment_from_solution(context, ranks, solver)
            audit = audit_plan(context, policy, assignments)
            if not audit["trial_admitted"]:
                raise RuntimeError("Candidate failed independent H1-H7 or N1 audit")
            key = plan_hash(assignments)
            if any(r["plan_hash"] == key for r in pool):
                raise RuntimeError("No-good constraint returned duplicate candidate")
            pool.append({"plan_id": f"N1_{len(pool)+1:03d}", "plan_hash": key, "assignments": assignments,
                         "audit": audit, "generation": log.copy(), "preference_label": None})
        elif status == cp_model.MODEL_INVALID:
            raise RuntimeError(model.validate())
        logs.append(log)
        if progress:
            progress({"stage": "N1_generation", "attempt": index+1, "pool_size": len(pool), "status": log["status"]})
        if len(pool) >= opts["pool_target"] or status == cp_model.INFEASIBLE:
            break
    exhausted = bool(logs and logs[-1]["status"] == "INFEASIBLE")
    state = "target_reached" if len(pool) >= opts["pool_target"] else "feasible_set_exhausted" if exhausted else "partial_budget_exhausted" if pool else "no_solution_within_budget"
    return pool, {"state": state, "requested_pool": opts["pool_target"], "actual_pool": len(pool), "attempts": logs,
                  "uses_expert": False, "uses_soft_penalty": False, "uses_witness_warm_start": False,
                  "objective": "Seeded random assignment costs, not quality scoring or uniform sampling"}
