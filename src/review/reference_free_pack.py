"""New batch from immutable v0.7 plans; no generation, scores or reference."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import re

from src.candidate.distance import plan_hash, PlanDistance
from src.constraints.v06 import HardConstraintEngine
from src.visualization import (build_geometry_catalog, build_plan_view_model,
                               build_diff_view_model, load_visualization_config)
from src.visualization.outline_adapter import load_display_outline
from .blinding import assign_blind_ids, blind_comparisons

DEFAULT_CONFIG = Path(__file__).resolve().parents[2]/"configs/m3_review_v08.json"


def load_pack_config(path=DEFAULT_CONFIG):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if config["schema_version"] != "m3.review.v0.8":
        raise ValueError("Unexpected review version")
    for key in ("batch_id", "blind_plan_prefix", "comparison_prefix"):
        if not re.fullmatch(r"[A-Z0-9_]+", config[key]):
            raise ValueError(f"Invalid {key}")
    for value in config["seeds"].values():
        if type(value) is not int or value < 0:
            raise ValueError("Invalid blinding seed")
    if config["whole_comparisons"]["method"] != "seeded_cycle" or config["local_comparisons"] != {"include_all_unique": True}:
        raise ValueError("Unknown comparison policy")
    return config


def load_sampling_batch(context, folder):
    folder = Path(folder)
    report = json.loads((folder/"generation_report.json").read_text(encoding="utf-8"))
    if report["run"]["facts_fingerprint"] != context.facts_fingerprint:
        raise ValueError("Current facts differ from the sampled candidate facts")
    records = [json.loads(line) for line in (folder/"selected_candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    local = json.loads((folder/"local_contrasts.json").read_text(encoding="utf-8"))
    hard, plans, hash_to_id = HardConstraintEngine(context), {}, {}

    def register(identifier, record):
        assignments = hard.require_feasible(record["assignments"])
        digest = plan_hash(assignments)
        if digest != record["plan_hash"]:
            raise ValueError("Plan hash does not match assignments")
        if digest in hash_to_id:
            return hash_to_id[digest]
        if identifier in plans:
            raise ValueError("Source identity collision")
        plans[identifier] = {"assignments": assignments, "plan_hash": digest}
        hash_to_id[digest] = identifier
        return identifier

    selected_ids = []
    for record in records:
        identifier = register(record["plan_id"], record)
        if identifier in selected_ids:
            raise ValueError("Duplicate selected Plan")
        selected_ids.append(identifier)
    local_pairs = []
    for row in local["comparisons"]:
        same, different = row["same_station_plan"], row["different_station_plan"]
        if row.get("answer") is not None or row.get("reason_note") is not None:
            raise ValueError("Sampling material contains human annotations; preserve and review separately")
        a, b = row["focus_operation_ids"]
        if same["assignments"][a] != same["assignments"][b] or different["assignments"][a] == different["assignments"][b]:
            raise ValueError("Local construction role mismatch")
        actual = sorted(op for op in same["assignments"] if same["assignments"][op] != different["assignments"][op])
        if actual != sorted(change["operation_id"] for change in row["all_changes"]):
            raise ValueError("Local change audit differs from complete assignments")
        left = register(row["contrast_id"]+"_SAME", same)
        right = register(row["contrast_id"]+"_DIFFERENT", different)
        if left == right:
            raise ValueError("Identical comparison")
        local_pairs.append({"internal_plan_1": left, "internal_plan_2": right,
                            "group": "LOCAL", "source_contrast_id": row["contrast_id"],
                            "focus_pairs": row.get("focus_pairs", [row["focus_operation_ids"]])})
    return plans, selected_ids, local_pairs


def design_comparisons(context, plans, selected_ids, local_pairs, config):
    expected = config["whole_comparisons"]["expected_selected_count"]
    if len(selected_ids) != expected or len(selected_ids) < 3:
        raise ValueError("Whole comparison policy requires the configured number of at least three selected Plans")
    cycle = sorted(selected_ids, key=lambda op: plans[op]["plan_hash"])
    random.Random(config["seeds"]["whole_pair_cycle"]).shuffle(cycle)
    pairs = [{"internal_plan_1": a, "internal_plan_2": b, "group": "WHOLE"}
             for a, b in zip(cycle, cycle[1:]+cycle[:1])]
    pairs.extend(deepcopy(local_pairs))
    identities = [tuple(sorted((r["internal_plan_1"], r["internal_plan_2"]))) for r in pairs]
    if len(set(identities)) != len(identities):
        raise ValueError("Comparison policy produced duplicate unordered pairs")
    if set(plans) != {r[k] for r in pairs for k in ("internal_plan_1", "internal_plan_2")}:
        raise ValueError("Some review plans have no comparison")
    original = assign_blind_ids(plans, config["seeds"]["plan_blinding"])
    blind_to_internal = {f"{config['blind_plan_prefix']}{i:02d}": identifier
                         for i, identifier in enumerate(original["blind_to_internal"].values(), 1)}
    mapping = {"blind_to_internal": blind_to_internal,
               "internal_to_blind": {value: key for key, value in blind_to_internal.items()},
               "batch_id": config["batch_id"], "plan_blinding_seed": config["seeds"]["plan_blinding"]}
    comparisons = blind_comparisons(pairs, mapping, config["seeds"]["comparison_blinding"], shuffle_order=True)
    distance = PlanDistance.from_context(context)
    for i, row in enumerate(comparisons, 1):
        row["comparison_id"] = f"{config['comparison_prefix']}{i:03d}"
        row["assignment_distance_raw"] = distance.raw(plans[row["internal_plan_A"]]["assignments"], plans[row["internal_plan_B"]]["assignments"])
    whole_degree = Counter(op for row in pairs if row["group"] == "WHOLE" for op in (row["internal_plan_1"], row["internal_plan_2"]))
    audit = {"group_counts": dict(Counter(r["group"] for r in comparisons)), "unique_plan_count": len(plans),
             "whole_plan_degree": dict(whole_degree), "all_selected_have_two_whole_comparisons": all(whole_degree[op] == 2 for op in selected_ids),
             "unique_unordered_pair_count": len(set(identities)),
             "local_same_role_on_A": sum(r["group"] == "LOCAL" and r["internal_plan_A"] == r["internal_plan_1"] for r in comparisons),
             "uses_reference": False, "uses_soft_scores": False,
             "limitation": "Local comparisons share a generated base; overlapping plans are not independent training observations"}
    return mapping, comparisons, audit


def prepare_views(context, plans, mapping, comparisons, root):
    config = load_visualization_config()
    part_id = next(iter(context.operations.values())).part_id
    outline, outline_audit = load_display_outline(config, root, part_id)
    first = plans[min(plans)]["assignments"]
    catalog = build_geometry_catalog(context, config, display_outline=outline, outline_audit=outline_audit,
                                     region_assignments=first)
    views = {blind: build_plan_view_model(context, plans[internal]["assignments"], catalog, blind)
             for blind, internal in mapping["blind_to_internal"].items()}
    public_rows = [{"comparison_id": row["comparison_id"], "blind_plan_A": row["blind_plan_A"],
                    "blind_plan_B": row["blind_plan_B"], "diff": build_diff_view_model(context,
                    plans[row["internal_plan_A"]]["assignments"], plans[row["internal_plan_B"]]["assignments"], catalog)}
                   for row in comparisons]
    return catalog, views, public_rows, config
