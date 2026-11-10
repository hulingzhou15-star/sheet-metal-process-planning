"""Validate confirmed data semantics; report findings without repairing data."""
from __future__ import annotations

from collections import Counter
import math

from .report import Severity, ValidationIssue, ValidationReport
from .schema import OperationType, PartData, SegmentType, SurfaceSide


DEFAULT_DISTANCE_TOLERANCE = 0.001  # coordinate units; numerical, not manufacturing tolerance
RULES = (
    "part_id_consistency", "operation_id_unique", "station_id_unique", "station_order",
    "operation_type_domain", "geometry_references", "no_orphan_geometry",
    "expert_station_references", "expert_station_capability", "predecessor_references",
    "expert_predecessor_order", "cad_parent_references", "cad_parent_acyclic",
    "process_group_structure", "pre_punch_flanging_pair", "expert_pre_punch_order",
    "geometry_seq", "primitive_fields", "operation_geometry_representation", "irregular_contour_continuity_closure",
    "irregular_contour_ccw", "arc_consistency", "bridge_center", "surface_side_domain",
)


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def validate(data: PartData, *, distance_tolerance: float = DEFAULT_DISTANCE_TOLERANCE) -> ValidationReport:
    if not _finite(distance_tolerance) or distance_tolerance <= 0:
        raise ValueError("distance_tolerance must be finite and positive")
    return _Validator(data, distance_tolerance).run()


class _Validator:
    def __init__(self, data, tolerance):
        self.data, self.tolerance = data, tolerance
        self.issues = []
        self.op_types = set(OperationType)
        self.operations = self._unique_index(data.operations, "operation_id")
        self.stations = self._unique_index(data.stations, "station_id")
        self.geometries = self._unique_index(data.geometries, "geometry_id")
        self.assignments = self._unique_index(data.expert_reference_plan.assignments, "operation_id")

    @staticmethod
    def _unique_index(items, key):
        counts = Counter(getattr(x, key) for x in items)
        return {getattr(x, key): x for x in items if counts[getattr(x, key)] == 1}

    def issue(self, code, message, entity=None, source=None, *, severity=Severity.ERROR, **details):
        self.issues.append(ValidationIssue(severity, code, message, entity, source, details))

    def identifiers(self, items, key, code):
        counts = Counter(getattr(x, key) for x in items)
        for identifier, count in counts.items():
            if not isinstance(identifier, str) or not identifier.strip():
                self.issue("IDENTIFIER_EMPTY", f"{key} must be nonempty", str(identifier), field=key)
            if count > 1:
                self.issue(code, f"{key} is duplicated", identifier, count=count)

    def run(self):
        self.check_identifiers_and_domains()
        self.check_expert_plan()
        self.check_relations()
        self.check_process_groups()
        self.check_geometry()
        d = self.data
        stats = {
            "part_ids": sorted({x.part_id for x in (*d.operations, *d.geometries, *d.stations)}),
            "operations": len(d.operations), "geometries": len(d.geometries),
            "geometry_segments": sum(len(g.segments) for g in d.geometries),
            "stations": len(d.stations), "process_groups": len(d.process_groups),
            "explicit_predecessors": len(d.predecessors), "cad_parent_relations": len(d.cad_parents),
            "operation_types": dict(Counter(op.operation_type for op in d.operations)),
            "segment_types": dict(Counter(s.segment_type for g in d.geometries for s in g.segments)),
            "surface_sides": dict(Counter(op.surface_side for op in d.operations)),
            "expert_station_counts": dict(Counter(a.station_id for a in d.expert_reference_plan.assignments)),
        }
        return ValidationReport(tuple(self.issues), stats, RULES,
                                str(d.source_path) if d.source_path else None,
                                d.source_sha256, self.tolerance)

    def check_identifiers_and_domains(self):
        d = self.data
        self.identifiers(d.operations, "operation_id", "OPERATION_ID_DUPLICATE")
        self.identifiers(d.stations, "station_id", "STATION_ID_DUPLICATE")
        self.identifiers(d.geometries, "geometry_id", "GEOMETRY_ID_DUPLICATE")
        for label, items in (("Operations", d.operations), ("Stations", d.stations)):
            if not items:
                self.issue("DATA_SECTION_EMPTY", f"{label} has no records")
        all_records = (*d.operations, *d.geometries, *d.stations, *d.process_groups)
        part_ids = {x.part_id for x in all_records}
        if len(part_ids) != 1 or not all(isinstance(p, str) and p.strip() for p in part_ids):
            self.issue("PART_ID_INCONSISTENT", "All records across sheets must belong to one nonempty part_id",
                       part_ids=sorted(part_ids))
        orders = Counter(s.order for s in d.stations)
        for station in d.stations:
            if not _positive_int(station.order):
                self.issue("STATION_ORDER_INVALID", "Station order must be a positive integer", station.station_id, station.source)
            if orders[station.order] > 1:
                self.issue("STATION_ORDER_DUPLICATE", "Station order must be unique", station.station_id, station.source, order=station.order)
            unknown = station.allowed_operation_types - self.op_types
            if unknown:
                self.issue("STATION_CAPABILITY_INVALID", "Capability contains unknown operation types", station.station_id, station.source,
                           unknown=sorted(str(x) for x in unknown))
            if not station.allowed_operation_types:
                self.issue("STATION_EMPTY_CAPABILITY", "Station has an empty capability set", station.station_id, station.source, severity=Severity.INFO)
        referenced = {op.geometry_id for op in d.operations if op.geometry_id is not None}
        for op in d.operations:
            if op.operation_type not in self.op_types:
                self.issue("OPERATION_TYPE_INVALID", "Unknown operation_type", op.operation_id, op.source, value=op.operation_type)
            if op.surface_side not in set(SurfaceSide):
                self.issue("SURFACE_SIDE_INVALID", "Allowed values are SMOOTH, ROUGH and NOT_APPLICABLE", op.operation_id, op.source,
                           value=op.surface_side)
            if op.geometry_id is not None and op.geometry_id not in self.geometries:
                self.issue("GEOMETRY_REFERENCE_INVALID", "Geometry reference is missing or ambiguous", op.operation_id, op.source,
                           geometry_id=op.geometry_id)
            if op.geometry_id is None:
                # SPEC 5.8 permits absent geometry. Do not invent a CAD requirement.
                self.issue("GEOMETRY_NOT_PROVIDED", "No geometry provided; absence alone is not a data error", op.operation_id, op.source,
                           severity=Severity.INFO)
        for geometry in d.geometries:
            if geometry.geometry_id not in referenced:
                self.issue("GEOMETRY_ORPHAN", "Sheet2 geometry is not referenced by any Operation", geometry.geometry_id)

    def check_expert_plan(self):
        assignments = self.data.expert_reference_plan.assignments
        self.identifiers(assignments, "operation_id", "EXPERT_ASSIGNMENT_DUPLICATE")
        for op in self.data.operations:
            assignment = self.assignments.get(op.operation_id)
            if assignment is None:
                self.issue("EXPERT_ASSIGNMENT_MISSING", "Operation must have one expert reference assignment", op.operation_id, op.source)
                continue
            station = self.stations.get(assignment.station_id)
            if station is None:
                self.issue("EXPERT_STATION_INVALID", "Expert Station does not exist or is ambiguous", op.operation_id, assignment.source,
                           station_id=assignment.station_id)
            elif op.operation_type in self.op_types and op.operation_type not in station.allowed_operation_types:
                self.issue("EXPERT_CAPABILITY_VIOLATION", "Expert Station cannot execute this operation_type", op.operation_id, assignment.source,
                           station_id=station.station_id, operation_type=op.operation_type)
        for assignment in assignments:
            if assignment.operation_id not in self.operations:
                self.issue("EXPERT_OPERATION_INVALID", "Expert assignment references a missing or ambiguous Operation", assignment.operation_id, assignment.source)

    def order_of(self, op_id):
        assignment = self.assignments.get(op_id)
        station = self.stations.get(assignment.station_id) if assignment else None
        return station.order if station and _positive_int(station.order) else None

    def check_before(self, before, after, code, source=None):
        first, second = self.order_of(before), self.order_of(after)
        if first is not None and second is not None and first >= second:
            self.issue(code, "Expert reference plan must satisfy strict Station order", after, source,
                       predecessor_id=before, predecessor_order=first, operation_order=second)

    def check_relations(self):
        for relation in self.data.predecessors:
            if relation.predecessor_id not in self.operations:
                self.issue("PREDECESSOR_REFERENCE_INVALID", "Predecessor does not exist or is ambiguous", relation.operation_id, relation.source,
                           predecessor_id=relation.predecessor_id)
            else:
                self.check_before(relation.predecessor_id, relation.operation_id, "EXPERT_PREDECESSOR_ORDER", relation.source)
        parents = {}
        for relation in self.data.cad_parents:
            if relation.parent_id not in self.operations:
                self.issue("CAD_PARENT_REFERENCE_INVALID", "CAD parent does not exist or is ambiguous", relation.operation_id, relation.source,
                           parent_id=relation.parent_id)
            elif relation.operation_id in self.operations:
                parents[relation.operation_id] = relation.parent_id
        # A CAD parent chain is structural; no manufacturing order is inferred.
        visited = set()
        for start in parents:
            path, positions, current = [], {}, start
            while current in parents and current not in visited:
                if current in positions:
                    cycle = path[positions[current]:]
                    self.issue("CAD_PARENT_CYCLE", "CAD parent references contain a cycle", current, cycle=cycle)
                    break
                positions[current] = len(path)
                path.append(current)
                current = parents[current]
            visited.update(path)

    def check_process_groups(self):
        d = self.data
        self.identifiers(d.process_groups, "process_group_id", "PROCESS_GROUP_ID_DUPLICATE")
        groups = self._unique_index(d.process_groups, "process_group_id")
        for op in d.operations:
            if op.process_group_id is not None:
                group = groups.get(op.process_group_id)
                if group is None or op.operation_id not in group.operation_ids:
                    self.issue("PROCESS_GROUP_REFERENCE_INVALID", "Operation group membership is missing or ambiguous", op.operation_id, op.source)
            elif op.operation_type in {OperationType.PRE_PUNCH, OperationType.FLANGING}:
                self.issue("PROCESS_GROUP_UNSPECIFIED", "No corresponding pre-punch/flanging group is specified", op.operation_id, op.source, severity=Severity.WARNING)
        for group in d.process_groups:
            gid, ids = group.process_group_id, group.operation_ids
            expected = Counter(op.operation_id for op in d.operations if op.process_group_id == gid and op.part_id == group.part_id)
            if len(ids) < 2 or len(set(ids)) != len(ids) or Counter(ids) != expected:
                self.issue("PROCESS_GROUP_STRUCTURE", "Group must contain distinct members matching Operation group references", gid,
                           members=list(ids), expected=dict(expected))
            members = []
            for op_id in ids:
                op = self.operations.get(op_id)
                if op is None or op.part_id != group.part_id or op.process_group_id != gid:
                    self.issue("PROCESS_GROUP_MEMBER_INVALID", "Group member is missing, ambiguous or belongs to another group/part", gid,
                               operation_id=op_id)
                else:
                    members.append(op)
            types = Counter(op.operation_type for op in members)
            pair = {OperationType.PRE_PUNCH, OperationType.FLANGING}
            if pair.intersection(types):
                if types != Counter({OperationType.PRE_PUNCH: 1, OperationType.FLANGING: 1}):
                    self.issue("PROCESS_GROUP_PAIR_INVALID", "A pre-punch/flanging group must contain one of each", gid, types=dict(types))
                else:
                    pre = next(op for op in members if op.operation_type == OperationType.PRE_PUNCH)
                    flanging = next(op for op in members if op.operation_type == OperationType.FLANGING)
                    self.check_before(pre.operation_id, flanging.operation_id, "EXPERT_PRE_PUNCH_ORDER", flanging.source)
            elif members:
                self.issue("PROCESS_GROUP_SEMANTICS_UNSPECIFIED", "No additional hard precedence rule is defined for this group type", gid,
                           severity=Severity.WARNING, types=dict(types))

    def check_geometry(self):
        for geometry in self.data.geometries:
            seq = [s.seq for s in geometry.segments]
            if not seq or not all(_positive_int(x) for x in seq) or sorted(seq) != list(range(1, len(seq) + 1)):
                self.issue("GEOMETRY_SEQ_INVALID", "Geometry seq must be unique and continuous from 1", geometry.geometry_id, seq=seq)
            for segment in geometry.segments:
                self.check_segment(geometry.geometry_id, segment)
        for op in self.data.operations:
            geometry = self.geometries.get(op.geometry_id)
            if geometry is None:
                continue
            types = [s.segment_type for s in geometry.segments]
            expected = {
                OperationType.CIRCLE_PUNCH: {SegmentType.CIRCLE},
                OperationType.PRE_PUNCH: {SegmentType.CIRCLE},
                OperationType.FLANGING: {SegmentType.CIRCLE},
                OperationType.BEND: {SegmentType.LINE, SegmentType.BEND_LINE},
                OperationType.BRIDGE: {SegmentType.BRIDGE},
            }.get(op.operation_type)
            if expected is not None:
                single = op.operation_type != OperationType.FLANGING
                if not types or any(t not in expected for t in types) or (single and len(types) != 1):
                    self.issue("OPERATION_GEOMETRY_MISMATCH", "Provided geometry does not match the defined Operation representation",
                               op.operation_id, op.source, geometry_id=geometry.geometry_id, segment_types=types)
            if op.operation_type == OperationType.IRREGULAR_PUNCH:
                self.check_contour(geometry)
            if op.operation_type == OperationType.FLANGING:
                circles = [s for s in geometry.segments if s.segment_type == SegmentType.CIRCLE]
                if len(circles) == len(geometry.segments) and len(circles) >= 2 and all(_finite(s.center_x) and _finite(s.center_y) for s in circles):
                    if any(math.dist(circles[0].center, s.center) > self.tolerance for s in circles[1:]):
                        self.issue("FLANGING_NOT_CONCENTRIC", "Flanging circles must be concentric", geometry.geometry_id)

    def check_segment(self, gid, s):
        requirements = {
            SegmentType.LINE: ("start_x", "start_y", "end_x", "end_y"),
            SegmentType.BEND_LINE: ("start_x", "start_y", "end_x", "end_y"),
            SegmentType.CIRCLE: ("center_x", "center_y", "radius"),
            SegmentType.ARC: ("start_x", "start_y", "end_x", "end_y", "center_x", "center_y", "radius", "sweep_angle"),
            SegmentType.BRIDGE: ("start_x", "start_y", "end_x", "end_y", "center_x", "center_y"),
        }
        if s.segment_type not in requirements:
            self.issue("SEGMENT_TYPE_INVALID", "Unknown segment_type", gid, s.source, seq=s.seq, value=s.segment_type)
            return
        bad = [key for key in requirements[s.segment_type] if not _finite(getattr(s, key))]
        if bad:
            self.issue("SEGMENT_FIELDS_INVALID", "Required primitive fields must be finite numbers", gid, s.source, seq=s.seq, fields=bad)
            return
        if s.segment_type in {SegmentType.ARC, SegmentType.CIRCLE} and s.radius <= 0:
            self.issue("RADIUS_INVALID", "Circle/ARC radius must be positive", gid, s.source, seq=s.seq)
        if s.segment_type in {SegmentType.LINE, SegmentType.BEND_LINE} and s.start == s.end:
            self.issue("LINE_DEGENERATE", "Line endpoints must be distinct", gid, s.source, seq=s.seq)
        if s.segment_type == SegmentType.ARC:
            residuals = {"start_radius": abs(math.dist(s.start, s.center) - s.radius),
                         "end_radius": abs(math.dist(s.end, s.center) - s.radius),
                         "signed_rotation_endpoint": math.dist(s.arc_endpoint(), s.end)}
            if max(residuals.values()) > self.tolerance:
                self.issue("ARC_INCONSISTENT", "ARC start/end/center/radius/signed sweep disagree", gid, s.source,
                           seq=s.seq, residuals=residuals, tolerance=self.tolerance)
        if s.segment_type == SegmentType.BRIDGE:
            midpoint = ((s.start_x + s.end_x) / 2, (s.start_y + s.end_y) / 2)
            residual = math.dist(midpoint, s.center)
            if residual > self.tolerance:
                self.issue("BRIDGE_CENTER_INCONSISTENT", "BRIDGE center must equal the footprint diagonal midpoint", gid, s.source,
                           seq=s.seq, residual=residual, tolerance=self.tolerance)

    def check_contour(self, geometry):
        segments = geometry.segments
        if not segments or any(s.segment_type not in {SegmentType.LINE, SegmentType.ARC} for s in segments):
            self.issue("CONTOUR_SEGMENTS_INVALID", "Irregular contours require a nonempty LINE/ARC chain", geometry.geometry_id)
            return
        if not all(_positive_int(s.seq) and all(_finite(v) for v in (s.start_x, s.start_y, s.end_x, s.end_y)) for s in segments):
            return  # Invalid fields already reported; dependent arithmetic is unsafe.
        ordered = sorted(segments, key=lambda s: s.seq)
        for first, second in zip(ordered, ordered[1:]):
            gap = math.dist(first.end, second.start)
            if gap > self.tolerance:
                self.issue("CONTOUR_DISCONTINUOUS", "Adjacent contour segments do not join", geometry.geometry_id, second.source,
                           from_seq=first.seq, to_seq=second.seq, gap=gap)
        gap = math.dist(ordered[-1].end, ordered[0].start)
        if gap > self.tolerance:
            self.issue("CONTOUR_NOT_CLOSED", "Last contour endpoint does not join the first start", geometry.geometry_id,
                       gap=gap)
        if not all(s.segment_type != SegmentType.ARC or (_finite(s.radius) and _finite(s.sweep_angle)) for s in ordered):
            return
        # Exact signed area, including curved segments; local negative ARC sweeps
        # are compatible with a globally counterclockwise boundary (SPEC 7.4.3).
        origin_x, origin_y = ordered[0].start
        area = 0.0
        for s in ordered:
            sx, sy, ex, ey = s.start_x-origin_x, s.start_y-origin_y, s.end_x-origin_x, s.end_y-origin_y
            area += (sx * ey - ex * sy) / 2
            if s.segment_type == SegmentType.ARC:
                theta = math.radians(s.sweep_angle)
                area += s.radius ** 2 * (theta - math.sin(theta)) / 2
        if area <= 0:
            self.issue("CONTOUR_NOT_CCW", "Irregular contour must traverse counterclockwise", geometry.geometry_id, signed_area=area)
