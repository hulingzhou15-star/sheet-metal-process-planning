from copy import deepcopy

from src.candidate.config import load_generator_config
from src.candidate.distance import plan_hash
from src.data.schema import Operation, Station
from m3_fixtures import context


def movable_context(count=4, station_count=3):
    ops = tuple(Operation("P", f"A{i}", "IRREGULAR_PUNCH") for i in range(count))
    stations = tuple(Station("P", name, order, frozenset({"IRREGULAR_PUNCH"}))
                     for name, order in (("OP90", 10), ("OP10", 30), ("OP05", 90))[:station_count])
    return context(ops, stations=stations)


def small_config(seed=20261012):
    config = deepcopy(load_generator_config())
    config["solver"].update(random_seed=seed, max_distance_timeout_seconds=2, per_candidate_timeout_seconds=1)
    config["generation"].update(pool_target_per_bucket=3, final_target_per_bucket=1, max_attempts_per_bucket=10)
    config["diagnostics"]["include_d3"] = False
    return config


def record(bits, score, *, bucket="NEAR", identifier="test"):
    assignments = {f"A{i}": "OP10" if value else "OP90" for i, value in enumerate(bits)}
    return {"plan_id": identifier, "assignments": assignments, "plan_hash": plan_hash(assignments),
            "expert_distance": {"raw": sum(bits), "normalized": sum(bits)/len(bits), "bucket": bucket},
            "soft": {"total_soft_penalty": score}}
