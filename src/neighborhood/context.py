"""Fact-only neighborhoods. Inclusion is an observation, never a conflict label."""
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from functools import cached_property
import hashlib
from itertools import combinations
import json
import math
from pathlib import Path
from types import SimpleNamespace

from src.graph.builders import DISTANCE_SORT_DECIMALS
from src.graph.geometry_distance import primitives_distance
from src.graph.geometry_primitives import geometry_primitives
from src.inference.input import InferenceContext
from src.proximity_review.analysis import build_evidence, existing_relations

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "configs/neighborhood_v01.json"
ABSOLUTE_FAMILIES = {"BEND_IRREGULAR", "IRREGULAR_IRREGULAR", "BEND_BEND"}


class _EvidenceView:
    """Legacy evidence expects spreadsheet provenance; keep unknown locations null.

    In-memory inference facts legitimately have no sheet/row. This read-only view
    supplies nullable provenance without editing the facts or manufacturing data.
    """
    def __init__(self, context):
        self.context = context
        self.operations = {op: SimpleNamespace(**{**vars(o), "source": o.source or SimpleNamespace(sheet=None, row=None)})
                           for op, o in context.operations.items()}

    def __getattr__(self, name):
        return getattr(self.context, name)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def validate_config(config):
    config = deepcopy(config)
    if config["schema_version"] != "neighborhood.v0.1":
        raise ValueError("Unknown neighborhood version")
    r, g, s, v = (config[k] for k in ("rules", "generation", "solver", "view"))
    if r["coordinate_units"] != "source_coordinates_no_conversion":
        raise ValueError("No implicit geometric unit conversion")
    if sorted(r["active_types"]) != ["BEND", "FLANGING", "IRREGULAR_PUNCH"] or sorted(r["circular_types"]) != ["CIRCLE_PUNCH", "FLANGING", "PRE_PUNCH"]:
        raise ValueError("Unconfirmed operation policy")
    if r["relative_policy"] != "first_finite_jump_tiers_only" or not positive(r["relative_jump_ratio"]) or r["relative_jump_ratio"] <= 1:
        raise ValueError("Only tiering without truncation is supported")
    if not positive(r["size_multiple"]) or r["size_multiple"] not in r["size_sensitivity"]:
        raise ValueError("Primary size must be in positive sensitivity scenarios")
    if not r["absolute_sensitivity"] or not all(positive(x) for x in r["size_sensitivity"] + r["absolute_sensitivity"]):
        raise ValueError("Invalid sensitivity scenario")
    if set(r["absolute_thresholds"]) != ABSOLUTE_FAMILIES or any(not positive(x) for x in r["absolute_thresholds"].values()):
        raise ValueError("Explicit positive exploratory absolute thresholds required")
    if type(r["evidence_profile_sample_count"]) is not int or r["evidence_profile_sample_count"] < 2 or not positive(r["numeric_witness_tolerance"]):
        raise ValueError("Invalid evidence precision")
    for k in ("seed", "base_pool_target", "base_max_attempts", "base_selection_target", "pair_budget_per_base", "selected_target"):
        if type(g[k]) is not int or g[k] < (0 if k in {"seed", "pair_budget_per_base"} else 1):
            raise ValueError(f"Invalid generation {k}")
    if g["base_selection_target"] > g["base_pool_target"] or g["selection"] != "farthest_first_no_quality_prefilter":
        raise ValueError("Invalid diversity configuration")
    if s["num_search_workers"] != 1 or not all(positive(s[k]) for k in ("max_time_seconds", "max_deterministic_time")):
        raise ValueError("Single worker and positive solver budgets required")
    if type(v["initial_rows"]) is not int or v["initial_rows"] < 1 or not all(positive(v[k]) for k in ("minimum_extent", "padding_fraction")):
        raise ValueError("Invalid display configuration")
    return config


def load_config(path=DEFAULT_CONFIG):
    return validate_config(json.loads(Path(path).read_text(encoding="utf-8")))


def measure_geometry(context, config, *, progress=None):
    """Works on a new part: no precomputed audit, graph, plan or expert required."""
    if not isinstance(context, InferenceContext):
        raise TypeError("Use fact-only InferenceContext")
    rules = validate_config(config)["rules"]
    ids = sorted(context.operations)
    shapes = {op: geometry_primitives(g) for op, o in context.operations.items()
              if (g := context.part_data.geometry_for(o)) is not None}
    matrix = [[None for _ in ids] for _ in ids]
    for i, op in enumerate(ids):
        if op in shapes:
            matrix[i][i] = 0.0
    for i, j in combinations(range(len(ids)), 2):
        if ids[i] in shapes and ids[j] in shapes:
            matrix[i][j] = matrix[j][i] = primitives_distance(shapes[ids[i]], shapes[ids[j]])
    evidence_config = {"circular_types": rules["circular_types"], "active_review_types": rules["active_types"],
                       "size_multiples": rules["size_sensitivity"], "primary_size_multiple": rules["size_multiple"],
                       "relative_jump_ratio": rules["relative_jump_ratio"],
                       "absolute_thresholds": {k: None for k in ABSOLUTE_FAMILIES},
                       "contour_profile_sample_count": rules["evidence_profile_sample_count"],
                       "numeric_witness_tolerance": rules["numeric_witness_tolerance"]}
    evidence, _ = build_evidence(_EvidenceView(context), {"operation_ids": ids, "distances": matrix}, evidence_config, progress=progress)
    evidence["source"] = {"facts_fingerprint": context.facts_fingerprint}
    return evidence


def validate_evidence(context, evidence):
    # Workbook bytes/path may differ solely because an ignored expert column changed.
    # They are provenance only. Cache compatibility is by fact fingerprint.
    if evidence.get("source", {}).get("facts_fingerprint") != context.facts_fingerprint:
        raise ValueError("Geometry evidence belongs to different facts")
    if evidence.get("expert_assignments_used") is not False:
        raise ValueError("Evidence must explicitly declare no expert assignments")
    expected = {op for op, o in context.operations.items() if context.part_data.geometry_for(o) is not None}
    if set(evidence["operations"]) != set(context.operations) or {op for op, row in evidence["operations"].items() if row["has_geometry"]} != expected:
        raise ValueError("Evidence operation coverage mismatch")
    keys = set()
    for row in evidence["pairs"]:
        a, b = row["a"], row["b"]
        if a not in expected or b not in expected or a >= b or (a, b) in keys:
            raise ValueError("Incomplete or duplicate evidence pair")
        if row["type_a"] != context.operations[a].operation_type or row["type_b"] != context.operations[b].operation_type:
            raise ValueError("Evidence operation types mismatch")
        if not math.isfinite(row["boundary_distance"]) or row["boundary_distance"] < 0:
            raise ValueError("Invalid geometric distance")
        keys.add((a, b))
    if len(keys) != len(expected)*(len(expected)-1)//2:
        raise ValueError("Evidence must contain all geometry pairs")


@dataclass(frozen=True)
class NeighborhoodContext:
    snapshot: dict

    @cached_property
    def fingerprint(self):
        return digest(self.snapshot)

    @property
    def external_relations(self):
        return [r for r in self.snapshot["relations"] if r["kind"] == "EXTERNAL_NEIGHBOR"]

    def to_dict(self):
        return deepcopy(self.snapshot)


def _tier_arcs(relations, ratio):
    groups = defaultdict(list)
    for row in relations:
        if row["kind"] == "EXTERNAL_NEIGHBOR":
            for arc in row["arcs"]:
                groups[arc["anchor"], row["family"], arc["metric"]].append((row, arc))
    reports = []
    for (anchor, family, metric), entries in sorted(groups.items()):
        entries.sort(key=lambda x: (round(x[1]["measurement"], DISTANCE_SORT_DECIMALS), x[1]["neighbor"]))
        gaps, first_cut = [], None
        for rank, ((left, la), (right, ra)) in enumerate(zip(entries, entries[1:]), 1):
            a, b = (round(x["measurement"], DISTANCE_SORT_DECIMALS) for x in (la, ra))
            value = b/a if a > 0 and b > a else None
            status = "FINITE" if value is not None else "ZERO_TO_POSITIVE" if a == 0 and b > 0 else "EQUAL_DISTANCE_BUCKET"
            qualifies = value is not None and value >= ratio
            gaps.append({"after_rank": rank, "left_pair": left["pair_id"], "right_pair": right["pair_id"],
                         "ratio": value, "status": status, "qualifies": qualifies})
            if qualifies and first_cut is None:
                first_cut = rank
        for rank, (_, arc) in enumerate(entries, 1):
            arc.update(rank_in_metric_family=rank, tier="PRIORITY" if first_cut is None or rank <= first_cut else "EXTENDED")
        reports.append({"anchor": anchor, "family": family, "metric": metric, "first_cut_after_rank": first_cut,
                        "pair_ids": [r["pair_id"] for r, _ in entries], "gaps": gaps, "automatic_truncation": False})
    return reports


def build_neighborhood(context, config, *, evidence=None, progress=None):
    if not isinstance(context, InferenceContext):
        raise TypeError("Neighborhoods require fact-only InferenceContext")
    config = validate_config(config)
    rules = config["rules"]
    evidence = measure_geometry(context, config, progress=progress) if evidence is None else evidence
    validate_evidence(context, evidence)
    active = set(rules["active_types"])
    assessments, relations = [], []
    for p in evidence["pairs"]:
        a, b, family = p["a"], p["b"], p["family"]
        internal = p["relations"]["internal_pre_punch_flanging"]
        gates, arcs = [], []
        if family in ABSOLUTE_FAMILIES:
            threshold = rules["absolute_thresholds"][family]
            gates = [{"metric": "RECORDED_BOUNDARY", "measurement": p["boundary_distance"], "threshold": threshold,
                      "scale_operation": None, "scale_value": None, "parameter_status": "UNCALIBRATED_ABSOLUTE_TRIAL",
                      "threshold_source": "rules.absolute_thresholds."+family}]
        else:
            for g in p["gates"]:
                gates.append({"metric": g["metric"], "measurement": g["measurement"],
                              "threshold": rules["size_multiple"]*g["diameter"], "scale_operation": g["scale_operation"],
                              "scale_value": g["diameter"], "parameter_status": "SIZE_MULTIPLE_TRIAL",
                              "threshold_source": "rules.size_multiple"})
        for g in gates:
            g["passes"] = round(g["measurement"], DISTANCE_SORT_DECIMALS) <= round(g["threshold"], DISTANCE_SORT_DECIMALS)
            g["normalized_distance"] = g["measurement"]/g["threshold"]
            if g["passes"] and not internal:
                # Two FLANGING scales stay directed: union inclusion, no forced averaging.
                anchors = [g["scale_operation"]] if family == "CIRCULAR_CIRCULAR" else [a, b]
                for anchor in anchors:
                    if context.operations[anchor].operation_type in active:
                        arcs.append({"anchor": anchor, "neighbor": b if anchor == a else a,
                                     "metric": g["metric"], "measurement": g["measurement"],
                                     "threshold": g["threshold"], "normalized_distance": g["normalized_distance"],
                                     "scale_operation": g["scale_operation"]})
        status = "INTERNAL_PROCESS_RELATION" if internal else "IN_RANGE" if arcs else "OUTSIDE_RANGE" if gates else "POLICY_UNRESOLVED"
        assessments.append({"pair_id": p["pair_id"], "a": a, "b": b, "family": family, "status": status, "gates": gates})
        if internal or arcs:
            item = {"pair_id": p["pair_id"], "operation_ids": [a, b], "operation_types": [p["type_a"], p["type_b"]],
                    "family": family, "kind": "INTERNAL_PROCESS_RELATION" if internal else "EXTERNAL_NEIGHBOR",
                    "boundary_distance": p["boundary_distance"], "gates": gates, "arcs": arcs,
                    "relations": deepcopy(p["relations"]), "boundary_witness": deepcopy(p["boundary_witness"]),
                    "curve_contact": p["curve_contact"]}
            for key in ("bend_geometry", "existing_precedence_path", "recorded_region_ancestor_related", "profile_a_to_b", "profile_b_to_a"):
                if key in p:
                    item[key] = deepcopy(p[key])
            # Preserve center projections/positions, independently from boundary witnesses.
            item["geometric_gates"] = deepcopy(p["gates"])
            relations.append(item)
    # Process-group facts survive even when one or both Operations lack geometry.
    # They are separate from the all-valid-geometry pair assessment population.
    present = {r["pair_id"] for r in relations}
    groups, direct = defaultdict(list), defaultdict(list)
    for op, o in context.operations.items():
        if o.process_group_id:
            groups[o.process_group_id].append(op)
    for r in context.precedence_relations:
        direct[r.predecessor_id, r.operation_id].append({"before": r.predecessor_id, "after": r.operation_id,
                                                       "source": r.relation_source, "origin": r.origin_ref})
    for group in groups.values():
        for a, b in combinations(sorted(group), 2):
            if {context.operations[a].operation_type, context.operations[b].operation_type} != {"PRE_PUNCH", "FLANGING"}:
                continue
            key = f"{a}__{b}"
            if key not in present:
                relations.append({"pair_id": key, "operation_ids": [a, b],
                    "operation_types": [context.operations[a].operation_type, context.operations[b].operation_type],
                    "family": "CIRCULAR_CIRCULAR", "kind": "INTERNAL_PROCESS_RELATION",
                    "boundary_distance": None, "boundary_witness": None, "curve_contact": None,
                    "gates": [], "arcs": [], "geometric_gates": [],
                    "relations": existing_relations(a, b, context.operations, direct),
                    "geometry_status": "MISSING_GEOMETRY"})
    relations.sort(key=lambda r: r["pair_id"])
    relative = _tier_arcs(relations, rules["relative_jump_ratio"])
    outgoing, incoming, internal_by_op = defaultdict(list), defaultdict(list), defaultdict(list)
    unresolved = Counter()
    for p in assessments:
        if p["status"] == "POLICY_UNRESOLVED":
            unresolved.update((p["a"], p["b"]))
    for p in relations:
        if p["kind"] == "INTERNAL_PROCESS_RELATION":
            for op in p["operation_ids"]:
                internal_by_op[op].append(p["pair_id"])
        for arc in p["arcs"]:
            outgoing[arc["anchor"]].append(p["pair_id"])
            incoming[arc["neighbor"]].append(p["pair_id"])
    operations = {}
    for op, o in sorted(context.operations.items()):
        geometry = evidence["operations"][op]
        policy = "MISSING_GEOMETRY" if not geometry["has_geometry"] else "ACTIVE_TRIAL" if o.operation_type in active else "TARGET_ONLY" if o.operation_type in rules["circular_types"] else "TYPE_POLICY_PENDING"
        operations[op] = {"operation_id": op, "operation_type": o.operation_type, "policy": policy,
                          "has_geometry": geometry["has_geometry"], "bbox": geometry.get("bbox"),
                          "outgoing_pair_ids": sorted(set(outgoing[op])), "incoming_pair_ids": sorted(set(incoming[op])),
                          "internal_pair_ids": internal_by_op[op], "unresolved_geometry_pair_count": unresolved[op],
                          "external_match_state": "HAS_EXTERNAL_MATCH" if outgoing[op] else "NO_EXTERNAL_MATCH_UNDER_TRIAL" if policy == "ACTIVE_TRIAL" else "NOT_AN_ACTIVE_DECISION",
                          "process_neighbor_need": "NOT_DETERMINED_BY_GEOMETRY"}
    return NeighborhoodContext({"schema_version": config["schema_version"], "rules": rules,
        "facts_fingerprint": context.facts_fingerprint, "operations": operations,
        "pair_assessments": assessments, "relations": relations, "relative_groups": relative,
        "summary": {"operations": len(operations), "geometry_operations": sum(r["has_geometry"] for r in operations.values()),
                    "all_geometry_pairs": len(assessments), "internal_pairs": sum(r["kind"] == "INTERNAL_PROCESS_RELATION" for r in relations),
                    "external_pairs": sum(r["kind"] == "EXTERNAL_NEIGHBOR" for r in relations),
                    "directed_external_arcs": sum(len(r["arcs"]) for r in relations),
                    "tier_counts": dict(Counter(arc["tier"] for r in relations for arc in r["arcs"])),
                    "external_family_counts": dict(Counter(r["family"] for r in relations if r["arcs"])),
                    "pair_status_counts": dict(Counter(r["status"] for r in assessments)),
                    "policy_counts": dict(Counter(r["policy"] for r in operations.values()))},
        "integration": {"candidate_local_proposals": True, "hard_constraint": False, "soft_penalty": False,
                        "trained_model": False, "graph_0_1_2_modified": False, "expert_assignments_used": False,
                        "all_types_finalized": False, "physical_conflict_labels_generated": False}})
