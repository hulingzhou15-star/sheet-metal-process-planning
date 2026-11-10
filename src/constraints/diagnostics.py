"""D1-D3 observe Plans. No scoring weights or predecessor/graph mutations."""
from collections import Counter, defaultdict

from .config import load_config, validate_config
from .hard_constraints import HardConstraintEngine
from .region_assignment import local_region_distribution
from .soft_priors import bend_ordering_statistics


def hierarchy_distribution(context, assignments):
    ancestors, ranks = context.region_ancestors, context.station_ranks
    children = defaultdict(list)
    for region in context.bend_influences:
        if region.parent_region_id is not None:
            children[region.parent_region_id].append(region.influence_region_id)
    levels = {}
    for level in sorted({len(a) for a in ancestors.values()}):
        regions = [r for r in context.bend_influences if len(ancestors[r.influence_region_id]) == level]
        levels[level] = {"bend_count": len(regions), "region_ids": sorted(r.influence_region_id for r in regions),
                         "station_distribution": dict(sorted(Counter(assignments[r.bend_operation_id] for r in regions).items()))}
    hierarchy, gaps = [], []
    for region in sorted(context.bend_influences, key=lambda r: r.influence_region_id):
        region_id = region.influence_region_id
        hierarchy.append({"region_id": region_id, "bend_operation_id": region.bend_operation_id,
                          "parent_region_id": region.parent_region_id, "level": len(ancestors[region_id]),
                          "child_region_ids": sorted(children[region_id]), "station_id": assignments[region.bend_operation_id]})
        if region.parent_region_id is not None:
            parent = context.regions[region.parent_region_id]
            child_station, parent_station = assignments[region.bend_operation_id], assignments[parent.bend_operation_id]
            gaps.append({"child_region_id": region_id, "parent_region_id": region.parent_region_id,
                         "operation_ids": [region.bend_operation_id, parent.bend_operation_id],
                         "station_ids": [child_station, parent_station],
                         "rank_gap": ranks[parent_station]-ranks[child_station]})
    maximum = max((len(a) for a in ancestors.values()), default=0)
    return {"root_region_count": sum(r.parent_region_id is None for r in context.bend_influences),
            "leaf_region_count": sum(not children[r.influence_region_id] for r in context.bend_influences),
            "region_count": len(context.bend_influences), "max_hierarchy_depth": maximum,
            "depth_convention": "root level=0; depth is number of parent links; level count=depth+1 for a nonempty forest",
            "hierarchy_level_count": maximum+1 if context.bend_influences else 0,
            "levels": levels, "parent_child_count": len(gaps), "parent_child_gaps": gaps,
            "parent_child_gap_distribution": dict(sorted(Counter(g["rank_gap"] for g in gaps).items())),
            "has_multilevel_chain": maximum >= 2, "hierarchy": hierarchy}


class DiagnosticEngine:
    def __init__(self, context, config=None):
        self.context = context
        self.config = load_config() if config is None else validate_config(config)

    def evaluate(self, plan):
        assignments = HardConstraintEngine(self.context).require_feasible(plan)
        return {"affects_soft_penalty": False,
                "D1": {"weight": 0, **bend_ordering_statistics(self.context, assignments, metric="affected_depth",
                        significance_ratio=self.config["depth_diagnostic"]["significance_ratio"])},
                "D2": hierarchy_distribution(self.context, assignments),
                "D3": local_region_distribution(self.context, assignments)}
