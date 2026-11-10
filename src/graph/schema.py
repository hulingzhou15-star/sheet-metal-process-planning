"""Immutable framework-neutral graph facts, Plan state and baseline views."""
from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json

from src.data.schema import Geometry, OperationType, SourceLocation, SurfaceSide

OPERATION_TYPE_ORDER = tuple(OperationType)
SURFACE_SIDE_ORDER = tuple(SurfaceSide)


@dataclass(frozen=True)
class GeometryFeaturesV1:
    has_geometry: int = 0
    geom_center_x_norm: float = 0.0
    geom_center_y_norm: float = 0.0
    bbox_width_norm: float = 0.0
    bbox_height_norm: float = 0.0
    geometry_length_norm: float = 0.0
    geometry_area_norm: float = 0.0
    geometry_perimeter_norm: float = 0.0
    orientation_cos2: float = 0.0
    orientation_sin2: float = 0.0
    has_length: int = 0
    has_area: int = 0
    has_perimeter: int = 0
    has_orientation: int = 0

    def vector(self):
        return tuple(asdict(self).values())


@dataclass(frozen=True)
class NormalizationContext:
    bbox: tuple[float, float, float, float]
    center: tuple[float, float]
    diagonal: float
    length_scale: float
    degenerate: bool


@dataclass(frozen=True)
class StationEntry:
    station_id: str  # metadata, excluded from capability/Plan feature vectors
    order: int
    relative_order: float
    allowed_operation_types: tuple[OperationType, ...]
    capability: tuple[int, ...]


@dataclass(frozen=True)
class StationContext:
    stations: tuple[StationEntry, ...]
    operation_type_order: tuple[OperationType, ...] = OPERATION_TYPE_ORDER

    def get(self, station_id):
        for s in self.stations:
            if s.station_id == station_id:
                return s
        raise ValueError(f"Unknown Station: {station_id}")

    @property
    def order_denominator(self):
        return max(1, len(self.stations)-1)


@dataclass(frozen=True)
class NodeStaticFeatures:
    operation_type: OperationType
    surface_side: SurfaceSide
    geometry_features_v1: GeometryFeaturesV1

    def vector(self):
        return (tuple(int(self.operation_type == t) for t in OPERATION_TYPE_ORDER)
                + tuple(int(self.surface_side == s) for s in SURFACE_SIDE_ORDER)
                + self.geometry_features_v1.vector())


class RelationType(str, Enum):
    PRECEDES = "PRECEDES"
    SUCCEEDS = "SUCCEEDS"
    SAME_PROCESS_GROUP = "SAME_PROCESS_GROUP"
    CAD_PARENT = "CAD_PARENT"
    CAD_CHILD = "CAD_CHILD"
    GEOMETRIC_NEAR = "GEOMETRIC_NEAR"


class RelationSource(str, Enum):
    EXPLICIT_PREDECESSOR = "EXPLICIT_PREDECESSOR"
    PROCESS_GROUP_PRECEDENCE = "PROCESS_GROUP_PRECEDENCE"
    PROCESS_GROUP_MEMBERSHIP = "PROCESS_GROUP_MEMBERSHIP"
    CAD_PARENT_REFERENCE = "CAD_PARENT_REFERENCE"


@dataclass(frozen=True)
class SemanticEdge:
    source_index: int
    target_index: int
    relation_type: RelationType
    relation_source: RelationSource
    origin_ref: str
    reverse_message: bool = False
    source_location: SourceLocation | None = None


@dataclass(frozen=True)
class GeometricEdge:
    source_index: int
    target_index: int
    distance_norm: float
    relative_dx_norm: float
    relative_dy_norm: float
    relation_type: RelationType = RelationType.GEOMETRIC_NEAR


@dataclass(frozen=True)
class KNNDiagnostics:
    k: int
    geometric_node_count: int
    evaluated_pairs: int
    distance_sort_decimals: int
    nodes_with_distance_ties: tuple[int, ...]
    nodes_with_cutoff_ties: tuple[int, ...]
    selected_neighbors: tuple[tuple[int, tuple[int, ...]], ...]


@dataclass(frozen=True)
class StaticPartGraph:
    part_id: str
    operation_ids: tuple[str, ...]
    node_features: tuple[NodeStaticFeatures, ...]
    raw_geometries: tuple[Geometry | None, ...]
    normalization: NormalizationContext
    station_context: StationContext
    semantic_edges: tuple[SemanticEdge, ...] = ()
    geometric_edges: tuple[GeometricEdge, ...] = ()
    knn_diagnostics: KNNDiagnostics | None = None

    @property
    def node_index(self):
        return {op_id: index for index, op_id in enumerate(self.operation_ids)}

    def operation_id(self, node_index):
        return self.operation_ids[node_index]

    def fingerprint(self):
        text = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class NodePlanFeatures:
    assigned_station_id: str
    assigned_station_order: int
    assigned_station_relative_order: float
    assigned_station_capability: tuple[int, ...]

    def vector(self):
        return (self.assigned_station_relative_order, *self.assigned_station_capability)


@dataclass(frozen=True)
class PlanEdgeFeatures:
    same_station: int
    station_delta: float
    abs_station_gap: float


@dataclass(frozen=True)
class PlanOverlay:
    operation_ids: tuple[str, ...]
    station_context: StationContext
    node_features: tuple[NodePlanFeatures, ...]

    def edge_features(self, source_index, target_index):
        a, b = self.node_features[source_index], self.node_features[target_index]
        delta = (b.assigned_station_order-a.assigned_station_order) / self.station_context.order_denominator
        return PlanEdgeFeatures(int(a.assigned_station_id == b.assigned_station_id), delta, abs(delta))


@dataclass(frozen=True)
class PlanGraph:
    level: int
    static: StaticPartGraph
    overlay: PlanOverlay

    def __post_init__(self):
        if self.level not in (0, 1, 2):
            raise ValueError("Graph level must be 0, 1 or 2")
        if self.overlay.operation_ids != self.static.operation_ids or self.overlay.station_context != self.static.station_context:
            raise ValueError("PlanOverlay must align with the static graph")

    @property
    def node_index(self):
        return self.static.node_index

    @property
    def edges(self):
        if self.level == 0:
            return ()
        return self.static.semantic_edges + (self.static.geometric_edges if self.level == 2 else ())

    @property
    def plan_edge_features(self):
        return tuple(self.overlay.edge_features(e.source_index, e.target_index) for e in self.edges)
