"""Reference-free hard feasibility and exact direction occupancy bounds, not a generator."""
from copy import deepcopy

from ortools.sat.python import cp_model

from .context import RuleContext
from .hard_constraints import HardConstraintEngine


class BoundComputationError(ValueError):
    pass


def build_feasibility_model(context):
    if not isinstance(context, RuleContext):
        raise TypeError("Expected RuleContext")
    model = cp_model.CpModel()
    ranks = {}
    for op in sorted(context.operations):
        ranks[op] = model.new_int_var(0, len(context.ordered_stations)-1, f"rank[{op}]")
        allowed = [[context.station_ranks[s.station_id]] for s in context.allowed_stations(op)]
        model.add_allowed_assignments([ranks[op]], allowed)
    for a, b in sorted({(r.predecessor_id, r.operation_id) for r in context.precedence_relations}):
        model.add(ranks[a] < ranks[b])
    return model, ranks


def make_solver(settings):
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = settings["max_time_seconds"]
    solver.parameters.random_seed = settings["random_seed"]
    solver.parameters.num_search_workers = settings["num_search_workers"]
    return solver


class DirectionFragmentation:
    def __init__(self, context, *, enabled_directions, solver_settings):
        self.context = context
        self.directions = tuple(enabled_directions)
        self.settings = deepcopy(solver_settings)
        self._bounds = None

    def bounds(self):
        if self._bounds is not None:
            return deepcopy(self._bounds)
        ops = self.context.operations
        invalid = sorted(o.operation_id for o in ops.values()
                         if o.operation_type == "BEND" and o.surface_side not in self.directions)
        if invalid:
            raise BoundComputationError(f"BEND direction unavailable: {invalid}")
        model, _ = build_feasibility_model(self.context)
        solver = make_solver(self.settings)
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise BoundComputationError(f"Hard feasible set not established: {solver.status_name(status)}")
        rows = []
        for direction in self.directions:
            ids = sorted(o.operation_id for o in ops.values() if o.operation_type == "BEND" and o.surface_side == direction)
            bounds, proofs = {}, {}
            for name, maximize in (("K_min", False), ("K_max", True)):
                if not ids:
                    bounds[name], proofs[name] = 0, {"status": "ABSENT", "value": 0}
                    continue
                model, ranks = build_feasibility_model(self.context)
                used = []
                for station in self.context.ordered_stations:
                    flags = []
                    for op in ids:
                        if station not in self.context.allowed_stations(op):
                            continue
                        flag = model.new_bool_var(f"at[{op},{station.station_id}]")
                        model.add(ranks[op] == self.context.station_ranks[station.station_id]).only_enforce_if(flag)
                        model.add(ranks[op] != self.context.station_ranks[station.station_id]).only_enforce_if(flag.Not())
                        flags.append(flag)
                    if flags:
                        use = model.new_bool_var(f"used[{station.station_id}]")
                        model.add_max_equality(use, flags)
                        used.append(use)
                (model.maximize if maximize else model.minimize)(sum(used))
                solver = make_solver(self.settings)
                status = solver.solve(model)
                if status != cp_model.OPTIMAL:
                    raise BoundComputationError(f"Exact {direction} {name} not proven: {solver.status_name(status)}")
                value = int(round(solver.objective_value))
                bounds[name] = value
                proofs[name] = {"status": solver.status_name(status), "value": value, "best_bound": solver.best_objective_bound}
            rows.append({"direction": direction, "operation_ids": ids, "operation_count": len(ids),
                         **bounds, "proofs": proofs, "participating": bool(ids) and bounds["K_max"] > bounds["K_min"]})
        self._bounds = rows
        return deepcopy(rows)

    def evaluate(self, plan):
        assignments = HardConstraintEngine(self.context).require_feasible(plan)
        rows, penalties = self.bounds(), []
        for row in rows:
            used = sorted({assignments[op] for op in row["operation_ids"]}, key=self.context.station_ranks.get)
            actual = len(used)
            penalty = ((actual-row["K_min"])/(row["K_max"]-row["K_min"]) if row["participating"] else 0.0)
            if not row["K_min"] <= actual <= row["K_max"] or not 0 <= penalty <= 1:
                raise BoundComputationError("Direction occupancy contradicts exact bounds")
            row.update(K_actual=actual, actual_used_stations=used, fragmentation_penalty=penalty,
                       skip_reason=None if row["participating"] else "absent or no adjustable occupancy")
            if row["participating"]:
                penalties.append(penalty)
        return {"value": sum(penalties)/len(penalties) if penalties else 0.0, "directions": rows,
                "participating_direction_count": len(penalties), "weight": None, "included_in_total": False,
                "bounds_source": "H1-H7 feasible set; no expert or guardrail inputs"}
