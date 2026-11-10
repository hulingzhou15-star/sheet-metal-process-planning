"""Static graph construction deliberately never accesses the expert plan."""
from src.data.schema import OperationType, SurfaceSide
from .geometry_features import GeometryFeatureExtractorV1
from .schema import NodeStaticFeatures, StaticPartGraph
from .station_context import build_station_context


def build_static_graph(data):
    operations = sorted(data.operations, key=lambda op: op.operation_id)
    if not operations or len({op.operation_id for op in operations}) != len(operations):
        raise ValueError("Operation IDs must be nonempty and unique")
    parts = {x.part_id for x in (*operations, *data.geometries, *data.stations, *data.process_groups)}
    if len(parts) != 1:
        raise ValueError("Static records must belong to one part")
    extractor = GeometryFeatureExtractorV1(data)
    context = build_station_context(data.stations)
    features = tuple(NodeStaticFeatures(OperationType(op.operation_type), SurfaceSide(op.surface_side), extractor.extract(op)) for op in operations)
    raw = tuple(data.geometry_for(op) for op in operations)
    return StaticPartGraph(operations[0].part_id, tuple(op.operation_id for op in operations), features, raw,
                           extractor.normalization, context)
