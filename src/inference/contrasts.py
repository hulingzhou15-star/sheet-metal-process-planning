"""Training-only local same/different-station comparisons; no automatic labels."""
from src.candidate.distance import plan_hash
from src.constraints.v06 import HardConstraintEngine
from src.constraints.v06.feasibility import build_feasibility_model
from ortools.sat.python import cp_model

from .generation import (validate_config, derived_seed, solver_for, solver_audit,
                         assignment_from_solution)
from .input import InferenceContext


def stratified_pairs(rows, count):
    ordered = sorted(rows, key=lambda row: (row["boundary_distance"], row["operation_ids"]))
    count = min(count, len(ordered))
    if count == 0:
        return []
    if count == 1:
        return [ordered[len(ordered)//2]]
    return [ordered[round(i*(len(ordered)-1)/(count-1))] for i in range(count)]


def sample_local_contrasts(context, base_plan, pair_rows, config, *, progress=None):
    if not isinstance(context, InferenceContext):
        raise TypeError("Local sampling requires expert-free facts")
    config = validate_config(config)
    hard = HardConstraintEngine(context)
    base = hard.require_feasible(base_plan)
    expected = sorted(config["local_contrasts"]["type_pair"])
    eligible = [row for row in pair_rows if sorted(row["type_pair"]) == expected]
    chosen = stratified_pairs(eligible, config["local_contrasts"]["max_pairs"])
    trials, comparisons, comparison_keys = [], [], {}
    for index, row in enumerate(chosen):
        a, b = row["operation_ids"]
        original_same = base[a] == base[b]
        target_same = not original_same
        model, ranks = build_feasibility_model(context)
        model.add(ranks[a] == ranks[b] if target_same else ranks[a] != ranks[b])
        changed = []
        for op, var in ranks.items():
            flag = model.new_bool_var(f"change[{op}]")
            model.add(var != context.station_ranks[base[op]]).only_enforce_if(flag)
            model.add(var == context.station_ranks[base[op]]).only_enforce_if(flag.Not())
            changed.append(flag)
        model.minimize(sum(changed))
        seed = derived_seed(context, config["generation"]["seed"], "local", index)
        solver = solver_for(config, seed)
        status = solver.solve(model)
        trial = {"operation_ids": [a, b], "type_pair": row["type_pair"],
                 "boundary_distance": row["boundary_distance"], "neighbor_ranks": row["ranks"],
                 "base_same_station": original_same, "target_same_station": target_same,
                 "seed": seed, **solver_audit(solver, status)}
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            alt = assignment_from_solution(context, ranks, solver)
            hard.require_feasible(alt)
            changes = [{"operation_id": op, "operation_type": context.operations[op].operation_type,
                        "base_station": base[op], "alternative_station": alt[op]}
                       for op in sorted(base) if base[op] != alt[op]]
            same, different = (base, alt) if original_same else (alt, base)
            key = tuple(sorted([plan_hash(same), plan_hash(different)]))
            trial.update(changed_operation_count=len(changes), minimum_change_proven=status == cp_model.OPTIMAL,
                         change_isolation="single_operation" if len(changes) == 1 else "multiple_operations",
                         all_changes=changes)
            if key not in comparison_keys:
                comparisons.append({"contrast_id": f"LC_{len(comparisons)+1:03d}",
                                    "focus_operation_ids": [a, b], "trial_index": index,
                                    "focus_pairs": [[a, b]],
                                    "same_station_plan": {"plan_hash": plan_hash(same), "assignments": dict(same)},
                                    "different_station_plan": {"plan_hash": plan_hash(different), "assignments": dict(different)},
                                    "answer": None, "reason_note": None, "all_changes": changes,
                                    "change_isolation": trial["change_isolation"],
                                    "minimum_change_proven": trial["minimum_change_proven"]})
                comparison_keys[key] = comparisons[-1]
            else:
                trial["duplicate_comparison"] = True
                comparison_keys[key]["focus_pairs"].append([a, b])
            trial["contrast_id"] = comparison_keys[key]["contrast_id"]
        elif status == cp_model.MODEL_INVALID:
            raise RuntimeError(model.validate())
        else:
            trial["skip_reason"] = "target branch hard infeasible" if status == cp_model.INFEASIBLE else "no solution within budget"
        trials.append(trial)
        if progress:
            progress({"stage": "local_contrast", "trial": index+1, "status": trial["status"]})
    return {"purpose": "training_sampling_only", "eligible_type_pair_count": len(eligible),
            "sampled_pair_count": len(chosen), "comparison_count": len(comparisons),
            "base_plan_hash": plan_hash(base), "trials": trials, "comparisons": comparisons,
            "interpretation": "Same/different are internal construction roles, not blind A/B or preference labels. All changed operations are disclosed. No expert assignment was used."}
