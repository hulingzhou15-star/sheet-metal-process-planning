"""Translate H1-H6 literally; generation filters and objectives stay separate."""
from dataclasses import dataclass
import hashlib
import json
import random

from ortools.sat.python import cp_model

from src.constraints import HardConstraintEngine, StationOverloadGuardrail
from .distance import PlanDistance


def derived_seed(base_seed, *parts):
    text = json.dumps([base_seed, *parts], separators=(",", ":"))
    # CP-SAT's random_seed is a nonnegative signed 32-bit integer.
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:4], "big") % (2**31-1)


@dataclass
class CandidateCPModel:
    model: cp_model.CpModel
    assignment_variables: dict
    station_rank: dict
    expert_distance: object
    operation_ids: tuple
    stations: tuple
    guardrail_enforced: bool
    guardrail_statistics: dict

    def forbid_plan(self, assignments):
        # Identity includes all Operations, not just the movable subset.
        matches = [self.assignment_variables.get((op, assignments[op]), 0) for op in self.operation_ids]
        self.model.add(sum(matches) <= len(self.operation_ids)-1)

    def extract_plan(self, solver):
        return {op: next(s.station_id for s in self.stations
                         if (op, s.station_id) in self.assignment_variables
                         and solver.value(self.assignment_variables[op, s.station_id])) for op in self.operation_ids}


def build_cp_sat_model(context, *, apply_station_overload_guardrail=True, constraint_config=None):
    expert = HardConstraintEngine(context).require_feasible(context.expert_reference_plan)
    guard = StationOverloadGuardrail(context, constraint_config).evaluate(expert)
    effective_guardrail = apply_station_overload_guardrail and guard["enabled"]
    model, variables, ranks = cp_model.CpModel(), {}, {}
    stations, operation_ids = context.ordered_stations, tuple(sorted(context.operations))
    rank_by_station = context.station_ranks
    for op_id in operation_ids:
        op = context.operations[op_id]
        for station in stations:
            if op.operation_type in station.allowed_operation_types:
                variables[op_id, station.station_id] = model.new_bool_var(f"x[{op_id},{station.station_id}]")
        choices = [(s, variables[op_id, s.station_id]) for s in stations if (op_id, s.station_id) in variables]
        model.add(sum(v for s, v in choices) == 1)  # H1/H2
        ranks[op_id] = model.new_int_var(0, len(stations)-1, f"rank[{op_id}]")
        model.add(ranks[op_id] == sum(rank_by_station[s.station_id]*v for s, v in choices))
    for relation in context.precedence_relations:  # H3/H4/H6; no CAD or inferred relation.
        model.add(ranks[relation.predecessor_id] < ranks[relation.operation_id])
    for fixed in context.fixed_assignments:  # H5
        model.add(ranks[fixed.operation_id] == rank_by_station[fixed.station_id])
    if effective_guardrail:
        for record in guard["stations"]:
            load = sum(v for (op, station), v in variables.items() if station == record["station_id"])
            model.add(load <= record["guardrail_upper_bound"])
    movable = PlanDistance.from_context(context).movable_operation_ids
    distance = model.new_int_var(0, len(movable), "expert_hamming_distance")
    model.add(distance == sum(1-variables[op, expert[op]] for op in movable))
    return CandidateCPModel(model, variables, ranks, distance, operation_ids, stations, effective_guardrail, guard)


def add_random_objective(cp, *, seed, coefficient_abs_max):
    if type(coefficient_abs_max) is not int or coefficient_abs_max < 1:
        raise ValueError("Random coefficient bound must be a positive integer")
    rng = random.Random(seed)
    coefficients = [(op, station, rng.randint(-coefficient_abs_max, coefficient_abs_max))
                    for op, station in cp.assignment_variables]
    expression = sum(coefficient*cp.assignment_variables[op, station] for op, station, coefficient in coefficients)
    direction = rng.choice(("maximize", "minimize"))
    getattr(cp.model, direction)(expression)
    return {"kind": "seeded_random_assignment_linear_objective", "direction": direction,
            "coefficient_abs_max": coefficient_abs_max, "coefficient_count": len(coefficients),
            "coefficients_sha256": hashlib.sha256(json.dumps(coefficients, separators=(",", ":")).encode()).hexdigest(),
            "coefficient_seed": seed, "contains_soft_priors": False}


def solve_model(cp, *, seed, timeout_seconds, num_search_workers=1):
    solver = cp_model.CpSolver()
    solver.parameters.random_seed = seed
    solver.parameters.max_time_in_seconds = timeout_seconds
    solver.parameters.num_search_workers = num_search_workers
    status = solver.solve(cp.model)
    if status == cp_model.MODEL_INVALID:
        raise RuntimeError(f"Invalid CP-SAT model: {cp.model.validate()}")
    available = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    return {"solver_status": solver.status_name(status),
            "assignments": cp.extract_plan(solver) if available else None,
            "objective_value": solver.objective_value if available else None,
            "best_objective_bound": solver.best_objective_bound,
            "expert_distance_raw": solver.value(cp.expert_distance) if available else None}
