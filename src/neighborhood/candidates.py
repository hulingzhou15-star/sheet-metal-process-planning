"""Explore local alternatives under H1-H7, without proximity quality assumptions."""
from collections import defaultdict
from copy import deepcopy

from ortools.sat.python import cp_model

from src.candidate.distance import plan_hash
from src.constraints.v06 import HardConstraintEngine
from src.constraints.v06.feasibility import build_feasibility_model
from src.inference.generation import (assignment_from_solution, derived_seed, generate_candidates,
                                      load_config as load_base_config, select_diverse, solver_audit, solver_for)
from src.inference.input import InferenceContext
from .context import validate_config


def propose_pair_alternatives(context, neighborhood, base_plan, pair_id, config):
    """Move each endpoint while holding the other; allow disclosed dependency moves.

    If neither endpoint-held branch finds a solution, try the unrestricted joint
    branch. INFEASIBLE proves only that branch impossible, UNKNOWN does not.
    """
    if not isinstance(context, InferenceContext):
        raise TypeError("Use expert-free facts")
    config = validate_config(config)
    if neighborhood.snapshot["facts_fingerprint"] != context.facts_fingerprint:
        raise ValueError("Neighborhood fact mismatch")
    pair = next((r for r in neighborhood.external_relations if r["pair_id"] == pair_id), None)
    if pair is None:
        raise ValueError("Local proposals require an external neighbor, not an internal or out-of-range pair")
    hard = HardConstraintEngine(context)
    base = hard.require_feasible(base_plan)
    base_hash = plan_hash(base)
    a, b = pair["operation_ids"]
    target_same = base[a] != base[b]
    trials = []
    for branch in ("MOVE_A_HOLD_B", "MOVE_B_HOLD_A", "JOINT_FALLBACK"):
        if branch == "JOINT_FALLBACK" and any(r.get("alternative") for r in trials):
            break
        model, ranks = build_feasibility_model(context)
        model.add(ranks[a] == ranks[b] if target_same else ranks[a] != ranks[b])
        held = b if branch == "MOVE_A_HOLD_B" else a if branch == "MOVE_B_HOLD_A" else None
        if held is not None:
            model.add(ranks[held] == context.station_ranks[base[held]])
        flags = []
        for op, var in ranks.items():
            flag = model.new_bool_var(f"change[{op}]")
            model.add(var != context.station_ranks[base[op]]).only_enforce_if(flag)
            model.add(var == context.station_ranks[base[op]]).only_enforce_if(flag.Not())
            flags.append(flag)
        model.minimize(sum(flags))
        seed = derived_seed(context, config["generation"]["seed"],
                            f"neighborhood:{neighborhood.fingerprint}:{base_hash}:{pair_id}:{branch}")
        solver = solver_for(config, seed)
        status = solver.solve(model)
        row = {"pair_id": pair_id, "operation_ids": [a, b], "family": pair["family"],
               "base_plan_hash": base_hash, "branch": branch, "held_operation": held,
               "base_same_station": not target_same, "target_same_station": target_same,
               "seed": seed, **solver_audit(solver, status),
               "objective_semantics": "minimum changed Operations within this branch, not quality",
               "preference_label": None}
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            alt = assignment_from_solution(context, ranks, solver)
            hard_result = hard.evaluate(alt)
            if not hard_result.feasible or (alt[a] == alt[b]) != target_same or held and alt[held] != base[held]:
                raise RuntimeError("Local branch/model validation disagreement")
            changes = [{"operation_id": op, "operation_type": context.operations[op].operation_type,
                        "base_station": base[op], "alternative_station": alt[op], "is_focus_endpoint": op in (a, b)}
                       for op in sorted(base) if base[op] != alt[op]]
            row.update(alternative={"plan_hash": plan_hash(alt), "assignments": alt, "hard": hard_result.to_dict()},
                       all_changes=changes, changed_operation_count=len(changes),
                       collateral_operation_ids=[r["operation_id"] for r in changes if not r["is_focus_endpoint"]],
                       minimum_changes_proven=status == cp_model.OPTIMAL)
        elif status == cp_model.MODEL_INVALID:
            raise RuntimeError(model.validate())
        else:
            row["skip_reason"] = "branch_hard_infeasible" if status == cp_model.INFEASIBLE else "no_solution_within_budget"
        trials.append(row)
    return trials


def choose_pairs(context, neighborhood, base, budget):
    """Round-robin families, tiers and current same/different states, no quality rank."""
    groups, excluded = defaultdict(list), []
    for row in neighborhood.external_relations:
        a, b = row["operation_ids"]
        if b in context.precedence_reachability[a] or a in context.precedence_reachability[b]:
            excluded.append({"pair_id": row["pair_id"], "reason": "same_station_already_hard_impossible"})
            continue
        tier = "PRIORITY" if any(arc["tier"] == "PRIORITY" for arc in row["arcs"]) else "EXTENDED"
        groups[row["family"], tier, base[a] == base[b]].append(row)
    # Evenly spaced distances within each bucket cover more than just nearest cases.
    streams = []
    for key, rows in sorted(groups.items()):
        rows.sort(key=lambda r: (min(a["normalized_distance"] for a in r["arcs"]), r["pair_id"]))
        count = min(len(rows), budget)
        order = list(range(len(rows))) if count == len(rows) else ([len(rows)//2] if count == 1 else
                [round(i*(len(rows)-1)/(count-1)) for i in range(count)])
        streams.append([rows[i] for i in order])
    selected = []
    while len(selected) < budget and any(streams):
        for stream in streams:
            if stream and len(selected) < budget:
                selected.append(stream.pop(0))
    selected_ids = {r["pair_id"] for r in selected}
    excluded_ids = {r["pair_id"] for r in excluded}
    return selected, {"eligible_pairs": sum(len(v) for v in groups.values()), "budget": budget,
                      "chosen_pair_ids": [r["pair_id"] for r in selected], "excluded": excluded,
                      "not_sampled_due_to_budget": sorted(r["pair_id"] for r in neighborhood.external_relations
                                                          if r["pair_id"] not in selected_ids | excluded_ids)}


def explore_neighborhood_candidates(context, neighborhood, config, *, progress=None):
    config = validate_config(config)
    g = config["generation"]
    old = load_base_config()
    old["generation"].update(seed=g["seed"], pool_target=g["base_pool_target"],
                             selected_target=g["base_selection_target"], max_attempts=g["base_max_attempts"])
    old["solver"] = deepcopy(config["solver"])
    bases, run = generate_candidates(context, old, progress=progress)
    chosen_bases, base_selection = select_diverse(context, bases, g["base_selection_target"])
    pool, by_hash, trials, pair_sampling = [], {}, [], []

    def include(assignments, origin):
        key = plan_hash(assignments)
        if key not in by_hash:
            row = {"plan_id": f"NB_{len(pool)+1:03d}", "plan_hash": key, "assignments": dict(assignments),
                   "hard": HardConstraintEngine(context).evaluate(assignments).to_dict(), "origins": []}
            if not row["hard"]["feasible"]:
                raise RuntimeError("Invalid neighborhood candidate")
            pool.append(row)
            by_hash[key] = row
        by_hash[key]["origins"].append(origin)

    for base in bases:
        include(base["assignments"], {"method": "reference_free_random_base", "seed": base["generation"]["seed"]})
    for index, base in enumerate(chosen_bases, 1):
        rows, sampling = choose_pairs(context, neighborhood, base["assignments"], g["pair_budget_per_base"])
        pair_sampling.append({"base_plan_hash": base["plan_hash"], **sampling})
        for pair in rows:
            results = propose_pair_alternatives(context, neighborhood, base["assignments"], pair["pair_id"], config)
            for trial in results:
                trial["trial_id"] = f"NBT_{len(trials)+1:04d}"
                if "alternative" in trial:
                    include(trial["alternative"]["assignments"], {"method": "neighborhood_local_proposal", "trial_id": trial["trial_id"],
                                                               "base_plan_hash": base["plan_hash"], "pair_id": pair["pair_id"], "branch": trial["branch"]})
                trials.append(trial)
            if progress:
                progress({"stage": "neighborhood_local", "base": index, "pair": pair["pair_id"],
                          "statuses": [r["status"] for r in results], "unique_candidates": len(pool)})
    selected, selection = select_diverse(context, pool, g["selected_target"])
    comparisons = {}
    for trial in trials:
        if "alternative" not in trial:
            continue
        key = tuple(sorted((trial["base_plan_hash"], trial["alternative"]["plan_hash"])))
        if key not in comparisons:
            comparisons[key] = {"contrast_id": f"NBC_{len(comparisons)+1:03d}", "plan_hashes": list(key),
                                "plan_ids": [by_hash[k]["plan_id"] for k in key], "trial_ids": [], "focus_pair_ids": [],
                                "answer": None, "confidence": None, "reason_note": None}
        comp = comparisons[key]
        comp["trial_ids"].append(trial["trial_id"])
        if trial["pair_id"] not in comp["focus_pair_ids"]:
            comp["focus_pair_ids"].append(trial["pair_id"])
    return {"schema_version": "neighborhood_candidates.v0.1", "config": config,
            "facts_fingerprint": context.facts_fingerprint, "neighborhood_fingerprint": neighborhood.fingerprint,
            "base_generation": run, "base_selection": base_selection, "pair_sampling": pair_sampling,
            "pool": pool, "selected": selected, "selection": selection, "trials": trials,
            "comparisons": list(comparisons.values()),
            "summary": {"base_pool_count": len(bases), "base_plans_explored": len(chosen_bases),
                        "local_trials": len(trials), "successful_trials": sum("alternative" in t for t in trials),
                        "pool_count": len(pool), "selected_count": len(selected), "unlabeled_comparisons": len(comparisons)},
            "expert_used": False, "soft_penalty_used": False, "quality_model_trained": False,
            "ready_for_expert_blind_review": False,
            "interpretation": "Experimental internal alternatives; same-station and different-station roles are not preference labels. No same-station guarantee for selected candidates."}
