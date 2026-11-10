"""Revised P1/P2, unchanged P3 weights, and a separately reported direction prior."""
from collections import Counter, defaultdict
from dataclasses import asdict
import math

from ..soft_priors import bend_ordering_statistics, precedence_extra_gap as legacy_extra_gap, type_fragmentation
from .config import load_config, validate_config
from .context import RULE_VERSION
from .feasibility import DirectionFragmentation
from .hard_constraints import HardConstraintEngine


def area_ordering(context, assignments, significance_ratio):
    result = bend_ordering_statistics(context, assignments, metric="affected_area_proxy", significance_ratio=significance_ratio)
    excluded = []

    def retain(record, *, comparable):
        a, b = ((record["larger_operation_id"], record["smaller_operation_id"]) if comparable else record["operation_ids"])
        before, after = ((a, b) if b in context.precedence_reachability[a] else (b, a))
        if after in context.precedence_reachability[before]:
            excluded.append({"operation_ids": [a, b], "before": before, "after": after,
                             "hard_precedence_path": context.precedence_path(before, after)})
            return False
        return True

    result["comparable_pairs"] = [r for r in result["comparable_pairs"] if retain(r, comparable=True)]
    result["insignificant_pairs"] = [r for r in result["insignificant_pairs"] if retain(r, comparable=False)]
    result["inversion_pairs"] = [r for r in result["comparable_pairs"] if r["inversion"]]
    result["comparable_pair_count"] = len(result["comparable_pairs"])
    result["insignificant_pair_count"] = len(result["insignificant_pairs"])
    result["inversion_count"] = len(result["inversion_pairs"])
    result["inversion_rate"] = result["inversion_count"]/result["comparable_pair_count"] if result["comparable_pair_count"] else 0.0
    result["excluded_additional_hard_precedence_pairs"] = excluded
    result["excluded_additional_hard_precedence_pair_count"] = len(excluded)
    result["excluded_pair_count"] = len(excluded)+result["excluded_hierarchy_pair_count"]
    return result


class _UniqueRelationView:
    def __init__(self, context, relations):
        self.context, self.precedence_relations = context, tuple(relations)

    def __getattr__(self, name):
        return getattr(self.context, name)


def precedence_extra_gap(context, assignments):
    grouped = defaultdict(list)
    for r in context.precedence_relations:
        grouped[r.predecessor_id, r.operation_id].append(r)
    result = legacy_extra_gap(_UniqueRelationView(context, [grouped[pair][0] for pair in sorted(grouped)]), assignments)
    for row in result["relations"]:
        originals = grouped[tuple(row["operation_ids"])]
        row["all_sources"] = [asdict(r) for r in originals]
        row["relation_sources"] = sorted({r.relation_source for r in originals})
        row["constraint_types"] = sorted({r.constraint_type for r in originals})

    def stats(rows):
        return {"unique_pair_count": len(rows),
                **{name+"_distribution": dict(sorted(Counter(r[name] for r in rows).items())) for name in ("g_min", "g_actual", "g_max")},
                "extra_gap_mean": math.fsum(r["extra_gap_penalty"] for r in rows)/len(rows) if rows else 0.0,
                "component_contribution": math.fsum(r["component_contribution"] for r in rows)}

    result["primary_sources"] = result.pop("sources")
    result["sources"] = {s: stats([r for r in result["relations"] if s in r["relation_sources"]])
                         for s in sorted({r.relation_source for r in context.precedence_relations})}
    result["relation_source_counts"] = {s: rows["unique_pair_count"] for s, rows in result["sources"].items()}
    source_sets = defaultdict(list)
    for row in result["relations"]:
        source_sets[" + ".join(row["relation_sources"])].append(row)
    result["exclusive_source_sets"] = {s: stats(rows) for s, rows in sorted(source_sets.items())}
    result["raw_relation_count"] = len(context.precedence_relations)
    result["duplicate_source_record_count"] = result["raw_relation_count"]-result["relation_count"]
    result["source_groups_overlap"] = True
    return result


class SoftPriorEngine:
    def __init__(self, context, config=None):
        self.context = context
        self.config = load_config() if config is None else validate_config(config)
        self.direction = DirectionFragmentation(context,
            enabled_directions=self.config["bend_direction_fragmentation"]["enabled_directions"],
            solver_settings=self.config["solver"])

    def evaluate(self, plan):
        assignments = HardConstraintEngine(self.context).require_feasible(plan)
        area = area_ordering(self.context, assignments, self.config["bend_area_ordering"]["significance_ratio"])
        gap = precedence_extra_gap(self.context, assignments)
        fragment = type_fragmentation(self.context, assignments, self.config["type_fragmentation"]["enabled_types"])
        values = {"bend_area_ordering": area["inversion_rate"], "precedence_extra_gap": gap["value"], "type_fragmentation": fragment["value"]}
        audits = dict(zip(values, (area, gap, fragment)))
        components = {name: {"raw_component": value, "weight": self.config["soft_weights"][name],
                             "weighted_contribution": value*self.config["soft_weights"][name], "audit": audits[name]}
                      for name, value in values.items()}
        total = math.fsum(r["weighted_contribution"] for r in components.values())
        if any(not 0 <= value <= 1 for value in (*values.values(), total)):
            raise ValueError("Soft components must be within [0,1]")
        return {"rule_version": RULE_VERSION, "purpose": "candidate_generation_heuristic_only",
                "components": components, "total_soft_penalty": total, "total_scope": "P1-P3 only, under v0.6 definitions",
                "unweighted_components": {"bend_direction_fragmentation": self.direction.evaluate(assignments)}}
