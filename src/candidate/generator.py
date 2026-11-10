"""CP-SAT exploration, independent validation and post-hoc heuristic scoring."""
from copy import deepcopy
import random

from src.constraints import HardConstraintEngine, SoftPriorEngine, StationOverloadGuardrail
from .config import BUCKET_ORDER, load_generator_config, validate_generator_config
from .cp_sat_model import add_random_objective, build_cp_sat_model, derived_seed, solve_model
from .distance import PlanDistance, build_distance_buckets, distance_bucket, plan_hash
from .selector import select_candidates


class GenerationConsistencyError(RuntimeError):
    def __init__(self, message, details):
        self.details = details
        super().__init__(message)


class CandidatePlanGenerator:
    def __init__(self, context, config=None, constraint_config=None):
        self.context = context
        self.config = load_generator_config() if config is None else validate_generator_config(config)
        self.hard = HardConstraintEngine(context)
        self.expert = self.hard.require_feasible(context.expert_reference_plan)
        self.distance = PlanDistance.from_context(context)
        if not self.distance.movable_operation_ids:
            raise ValueError("|M|=0; candidate generation stopped")
        self.soft = SoftPriorEngine(context, constraint_config)
        self.guard = StationOverloadGuardrail(context, constraint_config)
        self.constraint_config = deepcopy(self.soft.config)
        self.guardrail_enforced = (self.config["guardrails"]["apply_station_overload_guardrail"]
                                   and self.constraint_config["station_overload_guardrail"]["enabled"])

    def build_model(self):
        return build_cp_sat_model(self.context,
                                  apply_station_overload_guardrail=self.config["guardrails"]["apply_station_overload_guardrail"],
                                  constraint_config=self.constraint_config)

    def validate_solver_output(self, result):
        assignments = result["assignments"]
        if assignments is None:
            return None
        hard = self.hard.evaluate(assignments)
        if not hard.feasible:
            raise GenerationConsistencyError("CP-SAT returned a Plan violating frozen H1-H6", hard.to_dict())
        guard = self.guard.evaluate(assignments)
        if self.guardrail_enforced and guard["guardrail_violation"]:
            raise GenerationConsistencyError("CP-SAT returned a Plan violating enforced G1", guard)
        if self.distance.raw(assignments, self.expert) != result["expert_distance_raw"]:
            raise GenerationConsistencyError("CP-SAT distance differs from independent Hamming distance", result)
        return guard

    def maximum_expert_distance(self):
        cp = self.build_model()
        cp.model.maximize(cp.expert_distance)
        seed = derived_seed(self.config["solver"]["random_seed"], "maximum_expert_distance")
        result = solve_model(cp, seed=seed, timeout_seconds=self.config["solver"]["max_distance_timeout_seconds"])
        self.validate_solver_output(result)
        if result["assignments"] is None:
            raise GenerationConsistencyError("No maximum-distance feasible solution; expert should be feasible", result)
        maximum = result["expert_distance_raw"]
        return {"solver_status": result["solver_status"], "proven_optimal": result["solver_status"] == "OPTIMAL",
                "D_max": maximum if result["solver_status"] == "OPTIMAL" else None,
                "best_known_D_max": maximum, "solver_upper_bound": result["best_objective_bound"],
                "seed": seed, "assignments": result["assignments"],
                "guardrail_enforced": self.guardrail_enforced}

    def generate_pool(self, *, progress=None):
        maximum = self.maximum_expert_distance()
        upper = maximum["best_known_D_max"]
        if upper == 0:
            raise ValueError("Best-known maximum distance is zero; no non-expert candidate available")
        buckets = build_distance_buckets(upper, **self.config["distance_buckets"])
        pool, hashes, attempts_log = [], set(), []
        stats = {bucket: {"attempts": 0, "solve_attempts": 0, "feasible_solutions": 0,
                          "hard_validation_failures": 0, "guardrail_rejections": 0, "duplicate_rejections": 0,
                          "exact_infeasible_count": 0, "unknown_count": 0, "fallback_solve_count": 0,
                          "unique_pool_size": 0, "redistributed_pool_extra_count": 0} for bucket in BUCKET_ORDER}
        controls = self.config["generation"]

        def attempt(bucket):
            bounds, stat = buckets[bucket], stats[bucket]
            stat["attempts"] += 1
            attempt_seed = derived_seed(self.config["solver"]["random_seed"], bucket, stat["attempts"])
            target = random.Random(attempt_seed).randint(bounds["lower"], bounds["upper"])
            cp = self.build_model()
            cp.model.add(cp.expert_distance >= 1)
            cp.model.add(cp.expert_distance == target)
            if controls["use_no_good_constraints"]:
                for candidate in pool:
                    cp.forbid_plan(candidate["assignments"])
            objective = add_random_objective(cp, seed=attempt_seed,
                                             coefficient_abs_max=controls["random_objective_coefficient_abs_max"])
            result = solve_model(cp, seed=attempt_seed, timeout_seconds=self.config["solver"]["per_candidate_timeout_seconds"])
            stat["solve_attempts"] += 1
            log = {"bucket": bucket, "attempt": stat["attempts"], "seed": attempt_seed,
                   "target_distance": target, "exact_solver_status": result["solver_status"], "fallback_used": False}
            if result["solver_status"] == "INFEASIBLE":
                stat["exact_infeasible_count"] += 1
                delta = controls["exact_distance_fallback"]
                low, high = max(bounds["lower"], target-delta), min(bounds["upper"], target+delta)
                if delta and low < high:
                    cp = self.build_model()
                    cp.model.add(cp.expert_distance >= low)
                    cp.model.add(cp.expert_distance <= high)
                    if controls["use_no_good_constraints"]:
                        for candidate in pool:
                            cp.forbid_plan(candidate["assignments"])
                    seed = derived_seed(attempt_seed, "fallback")
                    objective = add_random_objective(cp, seed=seed,
                                                     coefficient_abs_max=controls["random_objective_coefficient_abs_max"])
                    result = solve_model(cp, seed=seed, timeout_seconds=self.config["solver"]["per_candidate_timeout_seconds"])
                    stat["solve_attempts"] += 1
                    stat["fallback_solve_count"] += 1
                    log.update(fallback_used=True, fallback_seed=seed, fallback_range=[low, high])
            log["solver_status"] = result["solver_status"]
            if result["solver_status"] == "UNKNOWN":
                stat["unknown_count"] += 1
            if result["assignments"] is not None:
                stat["feasible_solutions"] += 1
                try:
                    guard = self.validate_solver_output(result)
                except GenerationConsistencyError as exc:
                    field = "guardrail_rejections" if exc.details.get("guardrail_type") else "hard_validation_failures"
                    stat[field] += 1
                    raise GenerationConsistencyError(str(exc), {"failure": exc.details, "stats": stats, "attempt": log}) from exc
                assignments = result["assignments"]
                raw = self.distance.raw(assignments, self.expert)
                if distance_bucket(raw, buckets) != bucket:
                    raise GenerationConsistencyError("Solver solution left target bucket", log)
                if ((not log["fallback_used"] and raw != target)
                        or (log["fallback_used"] and not log["fallback_range"][0] <= raw <= log["fallback_range"][1])):
                    raise GenerationConsistencyError("Solver solution violates requested expert-distance bounds", log)
                identity = plan_hash(assignments)
                if identity in hashes:
                    stat["duplicate_rejections"] += 1
                    log["accepted"] = False
                    log["rejection_reason"] = "duplicate_plan"
                else:
                    score = self.soft.evaluate(assignments)  # Only after solve and validation.
                    stat["unique_pool_size"] += 1
                    record = {"plan_id": f"{bucket}_POOL_{stat['unique_pool_size']:03d}", "assignments": assignments,
                              "plan_hash": identity, "expert_distance": {"raw": raw, "normalized": raw/len(self.distance.movable_operation_ids),
                                                                        "bucket": bucket},
                              "hard_feasibility": {"feasible": True, "violation_count": 0},
                              "guardrail": {"pass": not guard["guardrail_violation"], "enforced": self.guardrail_enforced,
                                            "stations": guard["stations"]},
                              "soft": {"components": {name: {k: component[k] for k in ("raw_component", "weight", "weighted_contribution")}
                                                       for name, component in score["components"].items()},
                                       "total_soft_penalty": score["total_soft_penalty"]},
                              "generation": {"seed": log.get("fallback_seed", attempt_seed), "attempt_seed": attempt_seed,
                                             "target_distance": target, "fallback_used": log["fallback_used"],
                                             "fallback_range": log.get("fallback_range"), "solver_status": result["solver_status"],
                                             "objective": {**objective, "objective_value": result["objective_value"]}}}
                    pool.append(record)
                    hashes.add(identity)
                    log.update(accepted=True, actual_distance=raw, plan_hash=identity)
            attempts_log.append(log)
            if progress and stat["attempts"] % self.config["reporting"]["progress_every_attempts"] == 0:
                progress({"phase": "pool", "bucket": bucket, **stat})

        for bucket in BUCKET_ORDER:
            if not buckets[bucket]["empty"]:
                while stats[bucket]["unique_pool_size"] < controls["pool_target_per_bucket"] and stats[bucket]["attempts"] < controls["max_attempts_per_bucket"]:
                    attempt(bucket)
        original_counts = {b: stats[b]["unique_pool_size"] for b in BUCKET_ORDER}
        pool_target = len(BUCKET_ORDER)*controls["pool_target_per_bucket"]
        while len(pool) < pool_target:
            active = [b for b in BUCKET_ORDER if not buckets[b]["empty"] and stats[b]["attempts"] < controls["max_attempts_per_bucket"]]
            if not active:
                break
            for bucket in active:
                if len(pool) < pool_target:
                    attempt(bucket)
        for bucket in BUCKET_ORDER:
            stats[bucket]["redistributed_pool_extra_count"] = stats[bucket]["unique_pool_size"]-original_counts[bucket]
        return pool, {"maximum_distance": maximum, "movable_operation_ids": list(self.distance.movable_operation_ids),
                      "movable_operation_count": len(self.distance.movable_operation_ids), "distance_buckets": buckets,
                      "guardrail_enforced": self.guardrail_enforced, "bucket_generation": stats, "attempt_log": attempts_log,
                      "unique_pool_size": len(pool), "requested_pool_total": pool_target,
                      "unfilled_pool_total": pool_target-len(pool)}

    def run(self, *, progress=None):
        pool, generation = self.generate_pool(progress=progress)
        selected, selection = select_candidates(pool, self.distance,
                                                target_per_bucket=self.config["generation"]["final_target_per_bucket"],
                                                soft_prefilter_fraction=self.config["selection"]["soft_prefilter_fraction"])
        return pool, selected, {**generation, "selection": selection,
                                "generator_configuration": deepcopy(self.config),
                                "constraint_configuration": deepcopy(self.constraint_config)}
