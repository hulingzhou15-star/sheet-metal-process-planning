"""P1-P3 are candidate-generation heuristics, never process quality or labels."""
from collections import Counter
from itertools import combinations
import math

from .config import load_config, validate_config
from .hard_constraints import HardConstraintEngine


def bend_ordering_statistics(context, assignments, *, metric, significance_ratio):
    """Shared observable calculation for P1 and D1, with full pair audit."""
    ancestors, stations = context.region_ancestors, context.stations
    comparable, excluded, insignificant, inversions = [], [], [], []
    regions = sorted(context.bend_influences, key=lambda r: r.influence_region_id)
    for a, b in combinations(regions, 2):
        pair = {"region_ids": [a.influence_region_id, b.influence_region_id],
                "operation_ids": [a.bend_operation_id, b.bend_operation_id]}
        if a.influence_region_id in ancestors[b.influence_region_id] or b.influence_region_id in ancestors[a.influence_region_id]:
            excluded.append(pair)
            continue
        av, bv = getattr(a, metric), getattr(b, metric)
        if av > 0 and (bv == 0 or av / bv > significance_ratio):
            larger, smaller = a, b
        elif bv > 0 and (av == 0 or bv / av > significance_ratio):
            larger, smaller = b, a
        else:
            insignificant.append({**pair, "values": [av, bv]})
            continue
        sl, ss = assignments[larger.bend_operation_id], assignments[smaller.bend_operation_id]
        inversion = stations[sl].order < stations[ss].order
        record = {"larger_operation_id": larger.bend_operation_id, "smaller_operation_id": smaller.bend_operation_id,
                  "larger_region_id": larger.influence_region_id, "smaller_region_id": smaller.influence_region_id,
                  "larger_value": getattr(larger, metric), "smaller_value": getattr(smaller, metric),
                  "station_ids": [sl, ss], "station_orders": [stations[sl].order, stations[ss].order],
                  "same_station": sl == ss, "inversion": inversion}
        comparable.append(record)
        if inversion:
            inversions.append(record)
    missing = sorted(op.operation_id for op in context.part_data.operations
                     if op.operation_type == "BEND" and op.operation_id not in {r.bend_operation_id for r in regions})
    return {"metric": metric, "significance_ratio": significance_ratio, "bend_count": len(regions),
            "missing_bend_metadata_ids": missing, "total_pair_count": len(comparable)+len(excluded)+len(insignificant),
            "comparable_pair_count": len(comparable), "excluded_hierarchy_pair_count": len(excluded),
            "insignificant_pair_count": len(insignificant), "inversion_count": len(inversions),
            "inversion_rate": len(inversions)/len(comparable) if comparable else 0.0,
            "comparable_pairs": comparable, "excluded_hierarchy_pairs": excluded,
            "insignificant_pairs": insignificant, "inversion_pairs": inversions}


def precedence_extra_gap(context, assignments):
    ranks, relations = context.station_ranks, context.precedence_relations
    multiplicities = Counter((r.predecessor_id, r.operation_id) for r in relations)
    overlaps = [list(pair) for pair, count in multiplicities.items() if count > 1]
    if overlaps:
        raise ValueError(f"P2 relation multiplicity is not defined for repeated directed pairs: {overlaps}")
    records = []
    for relation in relations:
        a, b = relation.predecessor_id, relation.operation_id
        allowed_a, allowed_b = context.allowed_stations(a), context.allowed_stations(b)
        gaps = [ranks[sb.station_id]-ranks[sa.station_id] for sa in allowed_a for sb in allowed_b
                if ranks[sb.station_id] > ranks[sa.station_id]]
        if not gaps:
            raise ValueError(f"No locally feasible Station combination for {a} -> {b}")
        minimum, maximum = min(gaps), max(gaps)
        actual = ranks[assignments[b]]-ranks[assignments[a]]
        penalty = (actual-minimum)/(maximum-minimum) if maximum != minimum else 0.0
        if not 0 <= penalty <= 1:
            raise ValueError("P2 outside [0,1]; check feasibility and local bounds")
        records.append({"operation_ids": [a, b], "constraint_type": relation.constraint_type,
                        "relation_source": relation.relation_source, "origin_ref": relation.origin_ref,
                        "allowed_predecessor_stations": [s.station_id for s in allowed_a],
                        "allowed_successor_stations": [s.station_id for s in allowed_b],
                        "fixed_assignments": {op: context.fixed_station_by_operation[op] for op in (a, b)
                                              if op in context.fixed_station_by_operation},
                        "station_ids": [assignments[a], assignments[b]],
                        "g_min": minimum, "g_actual": actual, "g_max": maximum,
                        "extra_gap_penalty": penalty,
                        "component_contribution": penalty/len(relations)})
    sources = {}
    for source in sorted({r.relation_source for r in relations}):
        rows = [r for r in records if r["relation_source"] == source]
        sources[source] = {"relation_count": len(rows),
                           "g_min_distribution": dict(sorted(Counter(r["g_min"] for r in rows).items())),
                           "g_actual_distribution": dict(sorted(Counter(r["g_actual"] for r in rows).items())),
                           "g_max_distribution": dict(sorted(Counter(r["g_max"] for r in rows).items())),
                           "extra_gap_sum": math.fsum(r["extra_gap_penalty"] for r in rows),
                           "extra_gap_mean": math.fsum(r["extra_gap_penalty"] for r in rows)/len(rows),
                           "component_contribution": math.fsum(r["component_contribution"] for r in rows)}
    return {"relation_count": len(records), "relation_source_counts": {s: d["relation_count"] for s, d in sources.items()},
            "sources": sources, "relations": records,
            "value": math.fsum(r["extra_gap_penalty"] for r in records)/len(records) if records else 0.0}


def type_fragmentation(context, assignments, enabled_types):
    records, penalties = [], []
    for kind in enabled_types:
        allowed = [s.station_id for s in context.ordered_stations if kind in s.allowed_operation_types]
        ids = sorted(op.operation_id for op in context.part_data.operations if op.operation_type == kind)
        used = [s.station_id for s in context.ordered_stations if s.station_id in {assignments[op] for op in ids}]
        participating = len(allowed) > 1
        if participating and not ids:
            raise ValueError(f"P3 is undefined for enabled type {kind} with no Operations")
        penalty = (len(used)-1)/(len(allowed)-1) if participating else 0.0
        if participating:
            penalties.append(penalty)
        records.append({"operation_type": kind, "operation_count": len(ids), "allowed_stations": allowed,
                        "actual_used_stations": used, "K_allowed": len(allowed), "K_actual": len(used),
                        "participating": participating, "fragmentation_penalty": penalty,
                        "skip_reason": None if participating else "K_allowed <= 1"})
    return {"enabled_types": list(enabled_types), "participating_type_count": len(penalties), "types": records,
            "value": math.fsum(penalties)/len(penalties) if penalties else 0.0}


class SoftPriorEngine:
    def __init__(self, context, config=None):
        self.context = context
        self.config = load_config() if config is None else validate_config(config)

    def evaluate(self, plan):
        assignments = HardConstraintEngine(self.context).require_feasible(plan)
        area = bend_ordering_statistics(self.context, assignments, metric="affected_area_proxy",
                                        significance_ratio=self.config["bend_area_ordering"]["significance_ratio"])
        gap = precedence_extra_gap(self.context, assignments)
        fragmentation = type_fragmentation(self.context, assignments, self.config["type_fragmentation"]["enabled_types"])
        values = {"bend_area_ordering": area["inversion_rate"], "precedence_extra_gap": gap["value"],
                  "type_fragmentation": fragmentation["value"]}
        details = {"bend_area_ordering": area, "precedence_extra_gap": gap, "type_fragmentation": fragmentation}
        components = {name: {"raw_component": value, "weight": self.config["soft_weights"][name],
                             "weighted_contribution": value*self.config["soft_weights"][name],
                             "audit": details[name]} for name, value in values.items()}
        total = math.fsum(c["weighted_contribution"] for c in components.values())
        if any(not 0 <= value <= 1 for value in (*values.values(), total)):
            raise ValueError("All soft components and SoftPenalty must be in [0,1]")
        return {"purpose": "candidate_generation_heuristic_only", "components": components,
                "total_soft_penalty": total}
