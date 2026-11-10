"""Static manufacturing facts and a separate expert reference plan.

Tuples preserve source rows, including duplicate IDs for the validator to report.
Unknown enum tokens remain strings so they produce diagnostics, not silent fixes.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from pathlib import Path


class OperationType(str, Enum):
    CIRCLE_PUNCH = "CIRCLE_PUNCH"
    IRREGULAR_PUNCH = "IRREGULAR_PUNCH"
    PRE_PUNCH = "PRE_PUNCH"
    FLANGING = "FLANGING"
    BEND = "BEND"
    TRIM = "TRIM"
    DIMPLE = "DIMPLE"
    BRIDGE = "BRIDGE"
    MARKING = "MARKING"


class SurfaceSide(str, Enum):
    SMOOTH = "SMOOTH"
    ROUGH = "ROUGH"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class SegmentType(str, Enum):
    LINE = "LINE"
    ARC = "ARC"
    CIRCLE = "CIRCLE"
    BEND_LINE = "BEND_LINE"
    BRIDGE = "BRIDGE"


@dataclass(frozen=True)
class SourceLocation:
    sheet: str
    row: int
    field: str | None = None


@dataclass(frozen=True)
class Operation:
    part_id: str
    operation_id: str
    operation_type: OperationType | str
    source_feature_ref: str | None = None
    surface_side: SurfaceSide | str = SurfaceSide.NOT_APPLICABLE
    required_predecessor_ids: tuple[str, ...] = ()
    cad_parent_operation_id: str | None = None
    process_group_id: str | None = None
    geometry_id: str | None = None
    source: SourceLocation | None = None


@dataclass(frozen=True)
class GeometrySegment:
    seq: int
    segment_type: SegmentType | str
    start_x: float | None = None
    start_y: float | None = None
    end_x: float | None = None
    end_y: float | None = None
    center_x: float | None = None
    center_y: float | None = None
    radius: float | None = None
    sweep_angle: float | None = None
    source: SourceLocation | None = None

    @property
    def start(self) -> tuple[float, float] | None:
        return None if self.start_x is None or self.start_y is None else (self.start_x, self.start_y)

    @property
    def end(self) -> tuple[float, float] | None:
        return None if self.end_x is None or self.end_y is None else (self.end_x, self.end_y)

    @property
    def center(self) -> tuple[float, float] | None:
        return None if self.center_x is None or self.center_y is None else (self.center_x, self.center_y)

    def arc_endpoint(self) -> tuple[float, float]:
        """Recover an ARC endpoint using signed degrees; never infer direction."""
        if self.segment_type != SegmentType.ARC or self.start is None or self.center is None or self.sweep_angle is None:
            raise ValueError("ARC start, center and signed sweep are required")
        cx, cy = self.center
        dx, dy = self.start_x - cx, self.start_y - cy
        angle = math.radians(self.sweep_angle)
        return (cx + dx * math.cos(angle) - dy * math.sin(angle),
                cy + dx * math.sin(angle) + dy * math.cos(angle))

    def bridge_footprint(self) -> tuple[tuple[float, float], ...]:
        """Axis-aligned rectangle from the recorded opposite corners."""
        if self.segment_type != SegmentType.BRIDGE or self.start is None or self.end is None:
            raise ValueError("BRIDGE footprint opposite corners are required")
        x0, x1 = sorted((self.start_x, self.end_x))
        y0, y1 = sorted((self.start_y, self.end_y))
        return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


@dataclass(frozen=True)
class Geometry:
    part_id: str
    geometry_id: str
    segments: tuple[GeometrySegment, ...]


@dataclass(frozen=True)
class Station:
    part_id: str
    station_id: str
    order: int
    allowed_operation_types: frozenset[OperationType | str]
    source: SourceLocation | None = None


@dataclass(frozen=True)
class ProcessGroup:
    part_id: str
    process_group_id: str
    operation_ids: tuple[str, ...]


@dataclass(frozen=True)
class PredecessorRelation:
    predecessor_id: str
    operation_id: str
    source: SourceLocation | None = None


@dataclass(frozen=True)
class CadParentRelation:
    parent_id: str
    operation_id: str
    source: SourceLocation | None = None


@dataclass(frozen=True)
class ExpertAssignment:
    operation_id: str
    station_id: str | None
    source: SourceLocation | None = None


@dataclass(frozen=True)
class ExpertReferencePlan:
    assignments: tuple[ExpertAssignment, ...]


@dataclass(frozen=True)
class PartData:
    operations: tuple[Operation, ...]
    geometries: tuple[Geometry, ...]
    stations: tuple[Station, ...]
    process_groups: tuple[ProcessGroup, ...]
    expert_reference_plan: ExpertReferencePlan
    source_path: Path | None = None
    source_sha256: str | None = None

    @property
    def predecessors(self) -> tuple[PredecessorRelation, ...]:
        return tuple(PredecessorRelation(pre, op.operation_id, op.source)
                     for op in self.operations for pre in op.required_predecessor_ids)

    @property
    def cad_parents(self) -> tuple[CadParentRelation, ...]:
        return tuple(CadParentRelation(op.cad_parent_operation_id, op.operation_id, op.source)
                     for op in self.operations if op.cad_parent_operation_id is not None)

    def geometry_for(self, operation: Operation) -> Geometry | None:
        if operation.geometry_id is None:
            return None
        matches = [g for g in self.geometries if g.geometry_id == operation.geometry_id and g.part_id == operation.part_id]
        if len(matches) != 1:
            raise ValueError(f"Geometry reference is missing or ambiguous: {operation.geometry_id}")
        return matches[0]
