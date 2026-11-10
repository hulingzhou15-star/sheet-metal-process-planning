"""Evidence for every Operation, with explicitly incomplete process policies."""
from collections import Counter, defaultdict
from itertools import combinations
import math

from scripts.analyze_relative_proximity import adjacent_gap
from scripts.try_scale_aware_proximity import circular_profile, within_gate
from src.constraints.region_assignment import influence_rectangle
from src.graph.builders import DISTANCE_SORT_DECIMALS
from src.graph.geometry_primitives import Line, geometry_primitives, primitives_bbox
from src.proximity_review.witness import (closest_witness, bend_pair_features, project_point,
                                         distance_profile)


def family_for(a, b, circular_types):
    types = {a, b}
    if a in circular_types and b in circular_types:
        return "CIRCULAR_CIRCULAR"
    if "BEND" in types and types & set(circular_types):
        return "BEND_CIRCULAR"
    if "IRREGULAR_PUNCH" in types and types & set(circular_types):
        return "IRREGULAR_CIRCULAR"
    if a == b == "BEND":
        return "BEND_BEND"
    if types == {"BEND", "IRREGULAR_PUNCH"}:
        return "BEND_IRREGULAR"
    if a == b == "IRREGULAR_PUNCH":
        return "IRREGULAR_IRREGULAR"
    return "PENDING_TYPE_POLICY"


def existing_relations(a, b, operations, direct):
    oa, ob = operations[a], operations[b]
    internal = bool(oa.process_group_id and oa.process_group_id == ob.process_group_id
                    and {oa.operation_type, ob.operation_type} == {"PRE_PUNCH", "FLANGING"})
    cad = []
    if oa.cad_parent_operation_id == b:
        cad.append({"child": a, "parent": b})
    if ob.cad_parent_operation_id == a:
        cad.append({"child": b, "parent": a})
    return {"internal_pre_punch_flanging": internal,
            "process_group_id": oa.process_group_id if internal else None,
            "cad_parent_relations": cad,
            "direct_hard_precedence": direct.get((a, b), []) + direct.get((b, a), []),
            "note": "CAD parent alone does not merge manufacturing features or remove external neighbors"}


def validate_config(config):
    if not config["size_multiples"] or config["primary_size_multiple"] not in config["size_multiples"]:
        raise ValueError("Primary multiple must belong to the comparison list")
    if any(isinstance(m, bool) or not isinstance(m, (int, float)) or not math.isfinite(m) or m <= 0 for m in config["size_multiples"]):
        raise ValueError("Positive finite size multiples required")
    if set(config["absolute_thresholds"]) != {"BEND_IRREGULAR", "IRREGULAR_IRREGULAR", "BEND_BEND"}:
        raise ValueError("Unknown or incomplete threshold families")
    for value in config["absolute_thresholds"].values():
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
            raise ValueError("Explicit thresholds must be nonnegative finite values or null")
    if type(config["contour_profile_sample_count"]) is not int or config["contour_profile_sample_count"] < 2:
        raise ValueError("At least two diagnostic contour samples required")
    if config["relative_jump_ratio"] <= 1 or not math.isfinite(config["relative_jump_ratio"]):
        raise ValueError("Relative diagnostic ratio must exceed one")
    if not math.isfinite(config["numeric_witness_tolerance"]) or config["numeric_witness_tolerance"] <= 0:
        raise ValueError("Positive numerical verification tolerance required")


def build_evidence(context, matrix, config, *, progress=None):
    validate_config(config)
    ids, operations = matrix["operation_ids"], context.operations
    if ids != sorted(operations):
        raise ValueError("Matrix ids must match the current facts exactly")
    primitives, profiles, catalogue = {}, {}, {}
    region_for = {r.bend_operation_id: r for r in context.bend_influences}
    for op in ids:
        obj = operations[op]
        geometry = context.part_data.geometry_for(obj)
        row = {"operation_id": op, "operation_type": obj.operation_type,
               "source_feature_ref": obj.source_feature_ref, "geometry_id": obj.geometry_id,
               "process_group_id": obj.process_group_id, "cad_parent_operation_id": obj.cad_parent_operation_id,
               "has_geometry": geometry is not None, "source_sheet": obj.source.sheet, "source_row": obj.source.row}
        if geometry is not None:
            primitives[op] = geometry_primitives(geometry)
            profiles[op] = circular_profile(primitives[op]) if obj.operation_type in config["circular_types"] else None
            row.update(bbox=primitives_bbox(primitives[op]), circle=profiles[op])
        if op in region_for:
            r = region_for[op]
            row["recorded_region"] = {"id": r.influence_region_id, "parent_id": r.parent_region_id,
                                      "bbox": influence_rectangle(r), "display_context_only": True}
        catalogue[op] = row
    direct = defaultdict(list)
    for r in context.precedence_relations:
        direct[r.predecessor_id, r.operation_id].append({"before": r.predecessor_id, "after": r.operation_id,
                                                        "source": r.relation_source, "origin": r.origin_ref})
    rows, max_residual = [], 0
    node_rows = defaultdict(list)
    valid = [i for i, op in enumerate(ids) if op in primitives]
    for number, (i, j) in enumerate(combinations(valid, 2), 1):
        a, b = ids[i], ids[j]
        pa, pb, oa, ob = primitives[a], primitives[b], operations[a], operations[b]
        distance = matrix["distances"][i][j]
        witness = closest_witness(pa, pb)
        residual = abs(witness["distance"] - distance)
        max_residual = max(max_residual, residual)
        if residual > config["numeric_witness_tolerance"]:
            raise ValueError(f"Witness conflicts with authoritative M2 distance: {a}/{b}, residual={residual}")
        family = family_for(oa.operation_type, ob.operation_type, config["circular_types"])
        row = {"pair_id": f"{a}__{b}", "a": a, "b": b, "type_a": oa.operation_type, "type_b": ob.operation_type,
               "family": family, "boundary_distance": distance, "boundary_witness": witness,
               "numeric_residual": residual, "curve_contact": round(distance, DISTANCE_SORT_DECIMALS) == 0,
               "relations": existing_relations(a, b, operations, direct),
               "gates": [], "geometric_status": "UNRESOLVED", "reason": "No calibrated scale for this pair family"}
        if family in {"BEND_CIRCULAR", "IRREGULAR_CIRCULAR", "CIRCULAR_CIRCULAR"}:
            circles = [x for x in (a, b) if operations[x].operation_type in config["circular_types"]]
            if family == "CIRCULAR_CIRCULAR":
                circles = [x for x in circles if operations[x].operation_type == "FLANGING"]
            for circle in circles:
                other = b if circle == a else a
                profile = profiles[circle]
                if profile is None:
                    continue
                gate = {"scale_operation": circle, "diameter": profile["diameter"], "center": profile["center"]}
                if family == "BEND_CIRCULAR":
                    if len(primitives[other]) != 1 or not isinstance(primitives[other][0], Line):
                        continue
                    projection = project_point(profile["center"], primitives[other][0])
                    metric = math.dist(profile["center"], projection["point"])
                    gate.update(metric="CENTER_TO_FINITE_BEND", measurement=metric,
                                point_from=profile["center"], point_to=projection["point"], projection=projection,
                                legacy_boundary_measurement=distance)
                elif family == "CIRCULAR_CIRCULAR":
                    if profiles[other] is None:
                        continue
                    metric = math.dist(profile["center"], profiles[other]["center"])
                    gate.update(metric="CENTER_TO_CENTER", measurement=metric,
                                point_from=profile["center"], point_to=profiles[other]["center"])
                else:
                    metric = distance
                    gate.update(metric="RECORDED_BOUNDARY", measurement=metric)
                gate["passes"] = {f"{m:g}": within_gate(metric, profile["diameter"], m) for m in config["size_multiples"]}
                if family == "BEND_CIRCULAR":
                    gate["legacy_boundary_passes"] = {f"{m:g}": within_gate(distance, profile["diameter"], m) for m in config["size_multiples"]}
                row["gates"].append(gate)
            if row["gates"]:
                primary = f"{config['primary_size_multiple']:g}"
                decisions = [g["passes"][primary] for g in row["gates"]]
                row["geometric_status"] = "WITHIN_TRIAL_RANGE" if all(decisions) else "OUTSIDE_TRIAL_RANGE" if not any(decisions) else "DIRECTION_DEPENDENT_RANGE"
                row["reason"] = "Experimental size gate; physical interference has not been judged"
        elif family in config["absolute_thresholds"]:
            threshold = config["absolute_thresholds"][family]
            row["absolute_threshold"] = threshold
            if threshold is not None:
                row["geometric_status"] = "WITHIN_TRIAL_RANGE" if round(distance, DISTANCE_SORT_DECIMALS) <= round(threshold, DISTANCE_SORT_DECIMALS) else "OUTSIDE_TRIAL_RANGE"
                row["reason"] = "Explicit exploratory boundary-distance threshold from configuration"
        if family == "BEND_BEND":
            row["bend_geometry"] = bend_pair_features(pa[0], pb[0])
            path = context.precedence_path(a, b) if b in context.precedence_reachability[a] else context.precedence_path(b, a) if a in context.precedence_reachability[b] else []
            row["existing_precedence_path"] = path
            ra, rb = region_for.get(a), region_for.get(b)
            row["recorded_region_ancestor_related"] = bool(ra and rb and (ra.influence_region_id in context.region_ancestors[rb.influence_region_id] or rb.influence_region_id in context.region_ancestors[ra.influence_region_id]))
        if family in {"BEND_IRREGULAR", "IRREGULAR_IRREGULAR"}:
            n = config["contour_profile_sample_count"]
            row["profile_a_to_b"] = distance_profile(pa, pb, n)
            row["profile_b_to_a"] = distance_profile(pb, pa, n)
        rows.append(row)
        node_rows[a].append(row)
        node_rows[b].append(row)
        if progress and number % 2000 == 0:
            progress({"pairs": number})
    relative = {}
    for op, attached in node_rows.items():
        external = sorted((r for r in attached if not r["relations"]["internal_pre_punch_flanging"]),
                          key=lambda r: (round(r["boundary_distance"], DISTANCE_SORT_DECIMALS), r["b"] if r["a"] == op else r["a"]))
        cuts = []
        for rank, (left, right) in enumerate(zip(external, external[1:]), 1):
            ratio, status = adjacent_gap(left["boundary_distance"], right["boundary_distance"])
            if ratio is not None and ratio >= config["relative_jump_ratio"]:
                cuts.append({"after_external_rank": rank, "left_pair": left["pair_id"], "right_pair": right["pair_id"],
                             "ratio": ratio, "status": status})
        relative[op] = {"internal_pair_ids": [r["pair_id"] for r in attached if r["relations"]["internal_pre_punch_flanging"]],
                        "external_pair_ids": [r["pair_id"] for r in external], "candidate_cuts": cuts, "automatic_cut": False}
    for op, row in catalogue.items():
        attached = node_rows[op]
        if not row["has_geometry"]:
            status = "MISSING_GEOMETRY"
        elif row["operation_type"] in config["active_review_types"]:
            status = "PARTIAL_GEOMETRIC_POLICY"
        elif row["operation_type"] in config["circular_types"]:
            status = "TARGET_ONLY_BY_TRIAL_POLICY"
        else:
            status = "PENDING_TYPE_POLICY"
        row.update(review_status=status, pair_count=len(attached),
                   internal_pair_count=sum(r["relations"]["internal_pre_punch_flanging"] for r in attached),
                   within_trial_pair_count=sum(r["geometric_status"] == "WITHIN_TRIAL_RANGE" for r in attached),
                   unresolved_pair_count=sum(r["geometric_status"] == "UNRESOLVED" for r in attached),
                   need_for_process_neighbor="NOT_DETERMINED_BY_GEOMETRY_ALONE")
    # Nearest external pair within each uncalibrated family: descriptive data only.
    distributions = {}
    for family in config["absolute_thresholds"]:
        nearest = []
        for op, attached in node_rows.items():
            eligible = [r for r in attached if r["family"] == family and not r["relations"]["internal_pre_punch_flanging"]]
            if eligible:
                r = min(eligible, key=lambda r: (r["boundary_distance"], r["pair_id"]))
                nearest.append({"operation": op, "pair_id": r["pair_id"], "distance": r["boundary_distance"]})
        ordered = sorted(r["distance"] for r in nearest)
        distributions[family] = {"nearest_per_operation": nearest,
                                 "nearest_min": ordered[0] if ordered else None,
                                 "nearest_median": (ordered[(len(ordered)-1)//2] + ordered[len(ordered)//2])/2 if ordered else None,
                                 "nearest_max": ordered[-1] if ordered else None,
                                 "chosen_threshold": config["absolute_thresholds"][family]}
    summary = {"operations": len(ids), "geometry_operations": len(primitives), "all_geometric_pairs": len(rows),
               "operation_type_counts": dict(Counter(o.operation_type for o in operations.values())),
               "operation_status_counts": dict(Counter(r["review_status"] for r in catalogue.values())),
               "pair_family_counts": dict(Counter(r["family"] for r in rows)),
               "pair_status_counts": dict(Counter(r["geometric_status"] for r in rows)),
               "internal_pre_punch_flanging_pairs": sum(r["relations"]["internal_pre_punch_flanging"] for r in rows),
               "numeric_max_witness_residual": max_residual,
               "center_vs_legacy_bend_gate_changes_at_primary": sum(any(g["passes"][f"{config['primary_size_multiple']:g}"] != g["legacy_boundary_passes"][f"{config['primary_size_multiple']:g}"] for g in r["gates"]) for r in rows if r["family"] == "BEND_CIRCULAR"),
               "physical_conflict_labels_generated": 0, "operations_declared_not_needing_neighbors": 0}
    return {"config": config, "operations": catalogue, "pairs": rows, "relative_external_analysis": relative,
            "uncalibrated_distributions": distributions, "summary": summary,
            "production_integration": False, "expert_assignments_used": False,
            "all_operations_have_final_neighbor_policy": False}, primitives
