"""Candidate exploration on H1-H7 only; no expert-centered search or G1."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random

from ortools.sat.python import cp_model

from src.candidate.distance import PlanDistance, plan_hash
from src.constraints.v06 import HardConstraintEngine
from src.constraints.v06.feasibility import build_feasibility_model
from .input import InferenceContext

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "configs/m3_reference_free_v07.json"


def load_config(path=DEFAULT_CONFIG):
    return validate_config(json.loads(Path(path).read_text(encoding="utf-8")))


def validate_config(config):
    config = deepcopy(config)
    if config["schema_version"] != "m3.reference_free.v0.7":
        raise ValueError("Unexpected inference version")
    for name, lower in (("seed", 0), ("pool_target", 1), ("selected_target", 1),
                        ("max_attempts", 1), ("random_cost_abs_max", 1)):
        value = config["generation"][name]
        if type(value) is not int or value < lower:
            raise ValueError(f"Invalid generation {name}")
    if config["generation"]["selected_target"] > config["generation"]["pool_target"]:
        raise ValueError("selected_target exceeds pool_target")
    solver = config["solver"]
    for name in ("max_time_seconds", "max_deterministic_time"):
        v = solver[name]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v < float("inf"):
            raise ValueError(f"Invalid solver {name}")
    if solver["num_search_workers"] != 1:
        raise ValueError("First reproducible entry requires one worker")
    local = config["local_contrasts"]
    if type(local["analysis_k"]) is not int or not 1 <= local["analysis_k"] <= 8:
        raise ValueError("analysis_k must fit frozen Graph2 k=8")
    if type(local["max_pairs"]) is not int or local["max_pairs"] < 0:
        raise ValueError("Invalid max_pairs")
    if sorted(local["type_pair"]) != ["FLANGING", "IRREGULAR_PUNCH"]:
        raise ValueError("Only the discussed type pair is supported by this sampling entry")
    if local["sampling"] != "distance_rank_stratified" or local["objective"] != "minimum_changed_operations":
        raise ValueError("Unknown local sampling policy")
    if config["selection"] != {"method": "farthest_first", "soft_prefilter_enabled": False}:
        raise ValueError("This revision uses diversity selection, with soft scores reported separately")
    return config


def derived_seed(context, seed, stage, index=0):
    text = f"{context.facts_fingerprint}:{seed}:{stage}:{index}"
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big") % (2**31-1)


def solver_for(config, seed):
    solver = cp_model.CpSolver()
    settings = config["solver"]
    solver.parameters.max_time_in_seconds = settings["max_time_seconds"]
    solver.parameters.max_deterministic_time = settings["max_deterministic_time"]
    solver.parameters.num_search_workers = settings["num_search_workers"]
    solver.parameters.random_seed = seed
    return solver


def assignment_from_solution(context, ranks, solver):
    return {op: context.ordered_stations[solver.value(v)].station_id for op, v in sorted(ranks.items())}


def solver_audit(solver, status):
    return {"status": solver.status_name(status), "wall_time_seconds": solver.wall_time,
            "deterministic_time": solver.response_proto.deterministic_time,
            "objective_value": solver.objective_value if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
            "best_objective_bound": solver.best_objective_bound}


def generate_candidates(context, config, *, progress=None):
    if not isinstance(context, InferenceContext):
        raise TypeError("Use the explicit expert-free InferenceContext")
    config = validate_config(config)
    opts = config["generation"]
    hard, pool, logs = HardConstraintEngine(context), [], []
    for attempt in range(opts["max_attempts"]):
        model, ranks = build_feasibility_model(context)
        ordered_ops = sorted(ranks)
        if pool:
            model.add_forbidden_assignments([ranks[op] for op in ordered_ops],
                                           [[context.station_ranks[row["assignments"][op]] for op in ordered_ops] for row in pool])
        seed = derived_seed(context, opts["seed"], "candidate", attempt)
        rng, terms, costs = random.Random(seed), [], []
        for op in ordered_ops:
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
        log = {"attempt": attempt+1, "seed": seed, **solver_audit(solver, status),
               "costs_sha256": hashlib.sha256(json.dumps(costs, separators=(",", ":")).encode()).hexdigest(),
               "no_good_count": len(pool)}
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            assignments = assignment_from_solution(context, ranks, solver)
            result = hard.evaluate(assignments)
            if not result.feasible:
                raise RuntimeError(f"Model/validator disagreement: {result.to_dict()}")
            digest = plan_hash(assignments)
            if any(row["plan_hash"] == digest for row in pool):
                raise RuntimeError("No-good constraint returned a duplicate")
            pool.append({"plan_id": f"RF_{len(pool)+1:03d}", "plan_hash": digest,
                         "assignments": assignments, "hard": result.to_dict(), "generation": log.copy()})
        elif status == cp_model.MODEL_INVALID:
            raise RuntimeError(f"Invalid CP-SAT model: {model.validate()}")
        logs.append(log)
        if progress:
            progress({"stage": "candidate", "attempt": attempt+1, "pool_size": len(pool), "status": log["status"]})
        if len(pool) >= opts["pool_target"] or status == cp_model.INFEASIBLE:
            break
    exhausted = bool(logs and logs[-1]["status"] == "INFEASIBLE")
    state = ("target_reached" if len(pool) >= opts["pool_target"] else
             "feasible_set_exhausted" if exhausted and pool else "hard_infeasible" if exhausted else
             "partial_budget_exhausted" if pool else "no_solution_within_budget")
    return pool, {"status": state, "requested_pool": opts["pool_target"], "actual_pool": len(pool),
                  "attempts": logs, "facts_fingerprint": context.facts_fingerprint,
                  "uses_reference": False, "uses_expert_load_guardrail": False,
                  "objective": "seeded random per-assignment costs; not uniform sampling and not quality scoring"}


def select_diverse(context, pool, target):
    if type(target) is not int or target < 1:
        raise ValueError("target must be positive")
    distance = PlanDistance.from_context(context)
    remaining = sorted(pool, key=lambda r: r["plan_hash"])
    selected, steps = [], []
    while remaining and len(selected) < target:
        def nearest(row):
            return min((distance.raw(row["assignments"], s["assignments"]) for s in selected), default=0)
        row = min(remaining, key=lambda r: (-nearest(r), r["plan_hash"]))
        gap = nearest(row)
        selected.append(row)
        remaining.remove(row)
        steps.append({"plan_id": row["plan_id"], "minimum_raw_distance_to_previously_selected": gap})
    return selected, {"method": "farthest_first", "start": "smallest plan_hash", "soft_prefilter_enabled": False,
                      "movable_operation_count": len(distance.movable_operation_ids), "requested": target,
                      "actual": len(selected), "steps": steps}
