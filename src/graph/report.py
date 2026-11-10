"""JSON exports and feature diagnostics for framework-neutral graph views."""
from collections import Counter
from dataclasses import asdict, fields
import json
import math
from pathlib import Path

from .builders import build_baselines
from .schema import GeometryFeaturesV1, OPERATION_TYPE_ORDER, SURFACE_SIDE_ORDER

GEOMETRY_FIELD_ORDER = tuple(f.name for f in fields(GeometryFeaturesV1))
STATIC_FIELD_ORDER = (tuple(f"operation_type:{t.value}" for t in OPERATION_TYPE_ORDER)
                      + tuple(f"surface_side:{s.value}" for s in SURFACE_SIDE_ORDER) + GEOMETRY_FIELD_ORDER)
PLAN_FIELD_ORDER = ("assigned_station_relative_order",
                    *(f"assigned_station_capability:{t.value}" for t in OPERATION_TYPE_ORDER))


def feature_statistics(static):
    rows = [asdict(n.geometry_features_v1) for n in static.node_features]
    stats = {}
    masks = {"geometry_length_norm": "has_length", "geometry_area_norm": "has_area",
             "geometry_perimeter_norm": "has_perimeter", "orientation_cos2": "has_orientation",
             "orientation_sin2": "has_orientation"}
    for name in GEOMETRY_FIELD_ORDER:
        values = [r[name] for r in rows]
        applicable = [r[name] for r in rows if r[masks.get(name, "has_geometry")]]
        finite = [x for x in values if math.isfinite(x)]
        low, high = min(finite), max(finite)
        stats[name] = {"min": low, "max": high, "nonfinite_count": len(values)-len(finite),
                       "zero_count": values.count(0), "unique_count": len(set(values)), "constant": low == high,
                       "numerically_constant": high-low <= 1e-12*max(1, abs(low), abs(high)),
                       "applicable_count": len(applicable),
                       "applicable_min": min(applicable) if applicable else None,
                       "applicable_max": max(applicable) if applicable else None}
    return stats


def build_statistics(static, overlay):
    graphs = build_baselines(static, overlay)
    relation_counts = Counter(e.relation_type.value for e in static.semantic_edges)
    sources = Counter(f"{e.relation_type.value}/{e.relation_source.value}" for e in static.semantic_edges)
    facts = Counter(e.relation_source.value for e in static.semantic_edges if not e.reverse_message)
    semantic_pairs = {(e.source_index, e.target_index) for e in static.semantic_edges}
    geometric_pairs = {(e.source_index, e.target_index) for e in static.geometric_edges}
    diagnostics = static.knn_diagnostics
    knn = asdict(diagnostics) if diagnostics else None
    if knn:
        knn["nodes_with_distance_ties"] = [static.operation_id(i) for i in diagnostics.nodes_with_distance_ties]
        knn["nodes_with_cutoff_ties"] = [static.operation_id(i) for i in diagnostics.nodes_with_cutoff_ties]
        knn["selected_neighbors"] = {static.operation_id(i): [static.operation_id(j) for j in js]
                                     for i, js in diagnostics.selected_neighbors}
    return {
        "part_id": static.part_id, "static_fingerprint": static.fingerprint(),
        "operation_type_counts": dict(sorted(Counter(f.operation_type.value for f in static.node_features).items())),
        "geometry_record_count": sum(len(g.segments) for g in static.raw_geometries if g),
        "geometry_node_count": sum(g is not None for g in static.raw_geometries),
        "missing_geometry_operations": [op for op, g in zip(static.operation_ids, static.raw_geometries) if g is None],
        "normalization": asdict(static.normalization),
        "graphs": [{"level": g.level, "node_count": len(static.operation_ids), "edge_count": len(g.edges),
                    "semantic_edge_count": len(static.semantic_edges) if g.level >= 1 else 0,
                    "geometric_edge_count": len(static.geometric_edges) if g.level >= 2 else 0,
                    "station_count": len(static.station_context.stations), "static_vector_dimension": 26,
                    "plan_vector_dimension": 10} for g in graphs],
        "semantic_edge_counts": dict(sorted(relation_counts.items())),
        "semantic_edge_sources": dict(sorted(sources.items())),
        "semantic_nonreverse_message_counts": dict(sorted(facts.items())),
        "semantic_geometric_overlap_directed_pairs": len(semantic_pairs & geometric_pairs),
        "geometric_undirected_pair_count": len(static.geometric_edges)//2,
        "zero_distance_geometric_directed_edges": sum(e.distance_norm == 0 for e in static.geometric_edges),
        "knn": knn,
        "geometry_features_v1": feature_statistics(static),
        "static_field_order": STATIC_FIELD_ORDER, "plan_field_order": PLAN_FIELD_ORDER,
        "notes": [
            "FLANGING area/perimeter are zero with masks off, as SPEC 12/13 require while their meaning is deferred.",
            "Distance uses recorded curve point sets: circle circumferences, LINE/ARC contours and BRIDGE boundary.",
            "BRIDGE orientation is its rectangle long axis; square and zero-length BEND have no unique direction.",
            "kNN sorts by (round(source-unit distance, 9), operation_id); saved distance remains unrounded.",
            "Reverse PRECEDES/CAD_PARENT edges are message representations, not new process facts.",
            "PlanOverlay checks complete assignments only; it is not a general feasibility solver."]}


def graph_export(static, overlay):
    """Shared inputs + explicit per-baseline edges; Graph 0 gets no relations."""
    return {"schema_version": 1, "part_id": static.part_id, "static_fingerprint": static.fingerprint(),
            "node_index": static.node_index, "operation_ids": static.operation_ids,
            "station_context": asdict(static.station_context),
            "static_field_order": STATIC_FIELD_ORDER, "static_vectors": [f.vector() for f in static.node_features],
            "plan_field_order": PLAN_FIELD_ORDER, "plan_vectors": [f.vector() for f in overlay.node_features],
            "plan_metadata": [{"operation_id": op, "station_id": f.assigned_station_id,
                               "order": f.assigned_station_order} for op, f in zip(static.operation_ids, overlay.node_features)],
            "graphs": [{"level": g.level, "edges": [asdict(e) for e in g.edges],
                        "plan_edge_features": [asdict(f) for f in g.plan_edge_features]}
                       for g in build_baselines(static, overlay)]}


def write_graph_outputs(static, overlay, directory, *, project_root, provenance=None):
    """Write derived artifacts only below outputs/, also checking path aliases."""
    root, target = Path(project_root).resolve(), Path(directory).resolve()
    protected = tuple((root / p).resolve() for p in (
        "data/raw", "data/parts", "data/supervision", "baselines",
        "information_extract/sources", "information_extract/projects"))
    if any(target.is_relative_to(p) for p in protected) or not target.is_relative_to(root / "outputs"):
        raise ValueError("Milestone 2 artifacts must be below project outputs/")
    payloads = {"static_part_graph.json": asdict(static), "plan_overlay.json": asdict(overlay),
                "plan_graphs.json": graph_export(static, overlay),
                "statistics.json": {**build_statistics(static, overlay), "provenance": provenance or {}}}
    destinations = {name: (target / name).resolve() for name in payloads}
    for path in destinations.values():
        if any(path.is_relative_to(p) for p in protected) or not path.is_relative_to(root / "outputs"):
            raise ValueError("Output file aliases a protected or external path")
        if path.exists() and any(path.samefile(p) for folder in protected for p in folder.rglob("*") if p.is_file()):
            raise ValueError("Output file is a hard link to raw input")
    # Serialize all payloads before any write so NaN/Inf cannot create a partial
    # export. The caller owns naming of the Plan; no expert-specific builder.
    serialized = {name: json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n"
                  for name, value in payloads.items()}
    target.mkdir(parents=True, exist_ok=True)
    for name, text in serialized.items():
        destinations[name].write_text(text, encoding="utf-8")
    return destinations
