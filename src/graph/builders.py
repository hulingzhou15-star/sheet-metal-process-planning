"""Pure PartData -> static facts; arbitrary Plan -> three aligned views."""
from dataclasses import replace
from itertools import combinations

from .geometry_distance import primitives_distance
from .geometry_primitives import geometry_primitives
from .schema import GeometricEdge, KNNDiagnostics, PlanGraph
from .semantic_relations import add_semantic_relations
from .static_graph import build_static_graph

DISTANCE_SORT_DECIMALS = 9  # Source-unit numerical tie bucket, not process tolerance.


def add_geometric_relations(static, k=8):
    if type(k) is not int or k < 1:
        raise ValueError("k must be a positive integer")
    primitives = {i: geometry_primitives(g) for i, g in enumerate(static.raw_geometries) if g is not None}
    indices = sorted(primitives)
    distances = {(i, j): primitives_distance(primitives[i], primitives[j]) for i, j in combinations(indices, 2)}

    def distance(i, j):
        return distances[min(i, j), max(i, j)]

    selections, ties, cutoff_ties, pairs = [], [], [], set()
    count = min(k, max(0, len(indices)-1))
    for i in indices:
        ranked = sorted((j for j in indices if j != i),
                        key=lambda j: (round(distance(i, j), DISTANCE_SORT_DECIMALS), static.operation_ids[j]))
        buckets = [round(distance(i, j), DISTANCE_SORT_DECIMALS) for j in ranked]
        if len(set(buckets)) != len(buckets):
            ties.append(i)
        if 0 < count < len(ranked) and buckets[count-1] == buckets[count]:
            cutoff_ties.append(i)
        neighbors = tuple(ranked[:count])
        selections.append((i, neighbors))
        pairs.update((min(i, j), max(i, j)) for j in neighbors)
    edges = []
    for i, j in sorted(pairs):
        a, b = (static.node_features[n].geometry_features_v1 for n in (i, j))
        d = distance(i, j)/static.normalization.length_scale
        dx, dy = b.geom_center_x_norm-a.geom_center_x_norm, b.geom_center_y_norm-a.geom_center_y_norm
        edges.extend((GeometricEdge(i, j, d, dx, dy), GeometricEdge(j, i, d, -dx, -dy)))
    edges.sort(key=lambda e: (e.source_index, e.target_index))
    diagnostics = KNNDiagnostics(k, len(indices), len(distances), DISTANCE_SORT_DECIMALS,
                                 tuple(ties), tuple(cutoff_ties), tuple(selections))
    return replace(static, geometric_edges=tuple(edges), knn_diagnostics=diagnostics)


def build_part_graph(data, k=8):
    """Build once per Part; never read Excel or access expert assignments."""
    static = add_semantic_relations(data, build_static_graph(data))
    return add_geometric_relations(static, k)


def build_graph0(static, overlay):
    return PlanGraph(0, static, overlay)


def build_graph1(static, overlay):
    return PlanGraph(1, static, overlay)


def build_graph2(static, overlay):
    return PlanGraph(2, static, overlay)


def build_baselines(static, overlay):
    return tuple(PlanGraph(level, static, overlay) for level in (0, 1, 2))
