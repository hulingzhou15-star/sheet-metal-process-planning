"""M3 metadata kept outside the frozen M1 schema and M2 graphs."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from io import BytesIO
import hashlib
import math
from pathlib import Path

import openpyxl

from src.data.loader import load_excel
from src.data.schema import PartData, SourceLocation


@dataclass(frozen=True)
class MetadataIssue:
    code: str
    message: str
    context: dict
    source: SourceLocation | None = None


class ContextValidationError(ValueError):
    def __init__(self, issues):
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{i.code}: {i.message}" for i in self.issues))

    def to_dict(self):
        return {"valid": False, "issues": [asdict(i) for i in self.issues]}


@dataclass(frozen=True)
class FixedAssignment:
    operation_id: str
    station_id: str
    source: SourceLocation | None = None


@dataclass(frozen=True)
class BendInfluence:
    bend_operation_id: str
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    bend_axis: str
    far_boundary_coord: float
    affected_depth: float
    affected_side: str
    bend_length: float
    affected_area_proxy: float
    influence_region_id: str
    parent_region_id: str | None = None
    source: SourceLocation | None = None


@dataclass(frozen=True)
class HardPrecedenceRelation:
    predecessor_id: str
    operation_id: str
    constraint_type: str
    relation_source: str
    origin_ref: str
    source: SourceLocation | None = None


@dataclass(frozen=True)
class ConstraintContext:
    part_data: PartData
    fixed_assignments: tuple[FixedAssignment, ...] = ()
    bend_influences: tuple[BendInfluence, ...] = ()
    metadata_sha256: str | None = None

    def __post_init__(self):
        issues = []

        def fail(code, message, source=None, **context):
            issues.append(MetadataIssue(code, message, context, source))

        def unique(items, field):
            counts = Counter(getattr(item, field) for item in items)
            for key, count in counts.items():
                if not isinstance(key, str) or not key.strip() or count != 1:
                    fail("IDENTIFIER_INVALID", f"{field} must be nonempty and unique", identifier=key, count=count)
            return {getattr(item, field): item for item in items if counts[getattr(item, field)] == 1}

        ops = unique(self.part_data.operations, "operation_id")
        stations = unique(self.part_data.stations, "station_id")
        regions = unique(self.bend_influences, "influence_region_id")
        if not ops or not stations:
            fail("DATA_EMPTY", "Operations and Stations must be nonempty")
        parts = {x.part_id for x in (*self.part_data.operations, *self.part_data.stations)}
        if len(parts) != 1:
            fail("PART_ID_INCONSISTENT", "ConstraintContext must describe one part", part_ids=sorted(parts))
        orders = [s.order for s in self.part_data.stations]
        if any(type(x) is not int or x <= 0 for x in orders) or len(orders) != len(set(orders)):
            fail("STATION_ORDER_INVALID", "Station.order must be unique positive integers", orders=orders)
        for op in self.part_data.operations:
            for pre in op.required_predecessor_ids:
                if pre not in ops:
                    fail("PREDECESSOR_REFERENCE_INVALID", "Predecessor must exist", op.source,
                         operation_id=op.operation_id, predecessor_id=pre)
        fixed_counts = Counter(a.operation_id for a in self.fixed_assignments)
        for a in self.fixed_assignments:
            if fixed_counts[a.operation_id] != 1:
                fail("FIXED_ASSIGNMENT_AMBIGUOUS", "Sheet4 must determine one fixed Station", a.source,
                     operation_id=a.operation_id)
            if a.operation_id not in ops:
                fail("FIXED_OPERATION_INVALID", "Sheet4 Operation must exist", a.source, operation_id=a.operation_id)
            elif ops[a.operation_id].operation_type != "TRIM":
                fail("FIXED_OPERATION_NOT_TRIM", "Sheet4 contains fixed TRIM metadata", a.source, operation_id=a.operation_id)
            if a.station_id not in stations:
                fail("FIXED_STATION_INVALID", "Sheet4 Station must exist", a.source, station_id=a.station_id)
        bend_counts = Counter(r.bend_operation_id for r in self.bend_influences)
        numeric = ("start_x", "start_y", "end_x", "end_y", "far_boundary_coord", "affected_depth",
                   "bend_length", "affected_area_proxy")
        for r in self.bend_influences:
            if r.bend_operation_id not in ops or ops[r.bend_operation_id].operation_type != "BEND":
                fail("REGION_BEND_INVALID", "Sheet5 bend_operation_id must reference a BEND", r.source,
                     bend_operation_id=r.bend_operation_id)
            if bend_counts[r.bend_operation_id] != 1:
                fail("BEND_METADATA_AMBIGUOUS", "Multiple region records leave per-BEND prior inputs ambiguous", r.source,
                     bend_operation_id=r.bend_operation_id)
            for field in numeric:
                value = getattr(r, field)
                if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                        or (field in ("affected_depth", "bend_length", "affected_area_proxy") and value < 0)):
                    fail("REGION_NUMERIC_INVALID", f"Invalid Sheet5 {field}", r.source,
                         region_id=r.influence_region_id, field=field, value=value)
            if r.parent_region_id is not None and r.parent_region_id not in regions:
                fail("PARENT_REGION_INVALID", "Parent region must exist and determine one BEND", r.source,
                     region_id=r.influence_region_id, parent_region_id=r.parent_region_id)
        visited = set()
        for start in regions:
            chain, positions, current = [], {}, start
            while current in regions and current not in visited:
                if current in positions:
                    fail("REGION_HIERARCHY_CYCLE", "Region hierarchy must be acyclic",
                         cycle=chain[positions[current]:] + [current])
                    break
                positions[current] = len(chain)
                chain.append(current)
                current = regions[current].parent_region_id
            visited.update(chain)
        if issues:
            raise ContextValidationError(issues)

    @property
    def operations(self):
        return {op.operation_id: op for op in self.part_data.operations}

    @property
    def stations(self):
        return {s.station_id: s for s in self.part_data.stations}

    @property
    def ordered_stations(self):
        return tuple(sorted(self.part_data.stations, key=lambda s: s.order))

    @property
    def station_ranks(self):
        return {s.station_id: rank for rank, s in enumerate(self.ordered_stations)}

    @property
    def fixed_station_by_operation(self):
        return {a.operation_id: a.station_id for a in self.fixed_assignments}

    @property
    def regions(self):
        return {r.influence_region_id: r for r in self.bend_influences}

    @property
    def expert_reference_plan(self):
        return self.part_data.expert_reference_plan

    @property
    def region_ancestors(self):
        regions = self.regions
        result = {}
        for region_id, region in regions.items():
            ancestors, parent = set(), region.parent_region_id
            while parent is not None:
                ancestors.add(parent)
                parent = regions[parent].parent_region_id
            result[region_id] = frozenset(ancestors)
        return result

    def allowed_stations(self, operation_id, *, include_fixed=True):
        op = self.operations[operation_id]
        fixed = self.fixed_station_by_operation.get(operation_id) if include_fixed else None
        return tuple(s for s in self.ordered_stations if op.operation_type in s.allowed_operation_types
                     and (fixed is None or s.station_id == fixed))

    @property
    def precedence_relations(self):
        relations = [HardPrecedenceRelation(pre, op.operation_id, "H3", "EXPLICIT_PREDECESSOR",
                                             op.operation_id, op.source)
                     for op in self.part_data.operations for pre in op.required_predecessor_ids]
        groups = defaultdict(list)
        for op in self.part_data.operations:
            if op.process_group_id:
                groups[op.process_group_id].append(op)
        for group_id, members in sorted(groups.items()):
            for pre in members:
                if pre.operation_type == "PRE_PUNCH":
                    for flange in members:
                        if flange.operation_type == "FLANGING":
                            relations.append(HardPrecedenceRelation(pre.operation_id, flange.operation_id,
                                                                    "H4", "PROCESS_GROUP", group_id))
        regions = self.regions
        for r in self.bend_influences:
            if r.parent_region_id is not None:
                parent = regions[r.parent_region_id]
                relations.append(HardPrecedenceRelation(r.bend_operation_id, parent.bend_operation_id,
                                                        "H6", "BEND_REGION_PRECEDENCE",
                                                        r.influence_region_id, r.source))
        return tuple(sorted(relations, key=lambda r: (r.constraint_type, r.predecessor_id, r.operation_id, r.origin_ref)))


METADATA_HEADERS = {
    "Sheet4": ("operation_id", "fixed_station"),
    "Sheet5": ("bend_operation_id", "start_x", "start_y", "end_x", "end_y", "bend_axis",
               "far_boundary_coord", "affected_depth", "affected_side", "bend_length", "affected_area_proxy",
               "influence_region_id", "parent_region_id"),
}


def load_constraint_context(path: str | Path) -> ConstraintContext:
    """Reuse M1 untouched, then read Sheet4/5 from a matching read-only snapshot."""
    path = Path(path).resolve()
    data = load_excel(path)
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != data.source_sha256:
        raise ContextValidationError((MetadataIssue("SOURCE_CHANGED", "Workbook changed between snapshots", {}),))
    issues, tables = [], {}
    cached = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True)
    formulas = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=False)

    def text(value):
        return None if value is None or str(value).strip() == "" else str(value).strip()

    try:
        for name, expected in METADATA_HEADERS.items():
            if name not in cached.sheetnames:
                issues.append(MetadataIssue("SHEET_MISSING", f"Required {name} is missing", {}))
                continue
            rows, expressions = cached[name].iter_rows(values_only=True), formulas[name].iter_rows()
            headers = tuple(text(v) for v in next(rows, ()))
            next(expressions, ())
            if any(headers.count(field) != 1 for field in expected):
                issues.append(MetadataIssue("HEADERS_INVALID", "Required metadata columns must occur once",
                                            {"expected": expected, "actual": headers}, SourceLocation(name, 1)))
                continue
            records = []
            for index, (values, cells) in enumerate(zip(rows, expressions), 2):
                if not any(v is not None for v in values) and not any(c.data_type == "f" for c in cells):
                    continue
                for column, cell in enumerate(cells):
                    if cell.data_type == "e" or (cell.data_type == "f" and values[column] is None):
                        issues.append(MetadataIssue("METADATA_CELL_ERROR", "Error cell or formula without saved value",
                                                    {"value": cell.value}, SourceLocation(name, index, headers[column])))
                records.append((index, dict(zip(headers, values))))
            tables[name] = records
    finally:
        cached.close()
        formulas.close()
    if issues:
        raise ContextValidationError(issues)
    fixed = tuple(FixedAssignment(text(v["operation_id"]) or "", text(v["fixed_station"]) or "",
                                  SourceLocation("Sheet4", row)) for row, v in tables["Sheet4"])
    numeric = {"start_x", "start_y", "end_x", "end_y", "far_boundary_coord", "affected_depth", "bend_length", "affected_area_proxy"}
    regions = []
    for row, values in tables["Sheet5"]:
        fields = {field: values[field] if field in numeric else text(values[field]) for field in METADATA_HEADERS["Sheet5"]}
        regions.append(BendInfluence(**fields, source=SourceLocation("Sheet5", row)))
    return ConstraintContext(data, fixed, tuple(regions), digest)
