"""Read facts only from one workbook snapshot. Expert columns are never parsed.

The frozen M1 loader remains unchanged. Primitive objects and fact validation
methods are reused, without constructing an ExpertReferencePlan placeholder.
"""
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, fields
from io import BytesIO
import hashlib
import json
import math
from pathlib import Path

import openpyxl

from src.constraints.context import (BendInfluence, FixedAssignment, METADATA_HEADERS,
                                     ContextValidationError, MetadataIssue)
from src.constraints.v06.context import RuleContext
from src.data.loader import HEADERS, _text, _enum, _tokens
from src.data.schema import (PartData, Operation, OperationType, Geometry, GeometrySegment,
                             Station, SurfaceSide, SegmentType, SourceLocation, ProcessGroup)
from src.data.validator import _Validator, DEFAULT_DISTANCE_TOLERANCE, RULES
from src.data.report import ValidationReport


@dataclass(frozen=True)
class InferenceFacts:
    operations: tuple
    geometries: tuple
    stations: tuple
    process_groups: tuple
    source_path: Path | None = None
    source_sha256: str | None = None

    # These methods only access static facts, never the M1 expert field.
    predecessors = PartData.predecessors
    cad_parents = PartData.cad_parents
    geometry_for = PartData.geometry_for


@dataclass(frozen=True)
class InferenceContext(RuleContext):
    def __post_init__(self):
        if not isinstance(self.part_data, InferenceFacts):
            raise TypeError("InferenceContext requires InferenceFacts with no expert field")
        super().__post_init__()

    @property
    def expert_reference_plan(self):
        raise AttributeError("InferenceContext does not expose expert assignments")

    @property
    def facts_fingerprint(self):
        def clean(value):
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items()
                        if k not in {"source", "source_path", "source_sha256", "metadata_sha256"}}
            if isinstance(value, (tuple, list, set, frozenset)):
                return [clean(v) for v in value]
            return value
        # Canonical ordering avoids dependence on cell positions or set iteration.
        facts = {}
        for name, key in (("operations", "operation_id"), ("geometries", "geometry_id"),
                          ("stations", "station_id"), ("process_groups", "process_group_id")):
            rows = [clean(asdict(v)) for v in sorted(getattr(self.part_data, name), key=lambda x: getattr(x, key))]
            if name == "stations":
                for row in rows:
                    row["allowed_operation_types"] = sorted(row["allowed_operation_types"])
            facts[name] = rows
        facts["fixed"] = [clean(asdict(v)) for v in sorted(self.fixed_assignments, key=lambda x: x.operation_id)]
        facts["regions"] = [clean(asdict(v)) for v in sorted(self.bend_influences, key=lambda x: x.influence_region_id)]
        return hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class _FactValidator(_Validator):
    """Reuse static M1 checks explicitly; never call its expert-aware run/init."""
    def __init__(self, data):
        self.data, self.tolerance, self.issues = data, DEFAULT_DISTANCE_TOLERANCE, []
        self.op_types = set(OperationType)
        self.operations = self._unique_index(data.operations, "operation_id")
        self.stations = self._unique_index(data.stations, "station_id")
        self.geometries = self._unique_index(data.geometries, "geometry_id")

    def check_before(self, *args, **kwargs):
        # This legacy hook checks expert station order only. Manufacturing
        # precedence is validated by the independent H1-H7 engine/model.
        return

    def run(self):
        self.check_identifiers_and_domains()
        self.check_relations()
        self.check_process_groups()
        self.check_geometry()
        return ValidationReport(tuple(self.issues),
                                {"operations": len(self.data.operations), "stations": len(self.data.stations),
                                 "geometries": len(self.data.geometries),
                                 "operation_types": dict(Counter(o.operation_type for o in self.data.operations))},
                                tuple(r for r in RULES if not r.startswith("expert_")),
                                str(self.data.source_path) if self.data.source_path else None,
                                self.data.source_sha256, self.tolerance)


def validate_facts(data):
    return _FactValidator(data).run()


def load_inference_context(path):
    path = Path(path).resolve()
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    cached = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True)
    formulas = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=False)
    tables, issues = {}, []
    expected = {**HEADERS, **METADATA_HEADERS}
    expected["Sheet1"] = tuple(k for k in HEADERS["Sheet1"] if k != "expert_station")
    try:
        for sheet, required in expected.items():
            if sheet not in cached.sheetnames:
                issues.append(MetadataIssue("SHEET_MISSING", f"Required {sheet} missing", {}))
                continue
            values, expressions = cached[sheet].iter_rows(values_only=True), formulas[sheet].iter_rows()
            header = tuple(_text(v) for v in next(values, ()))
            next(expressions, ())
            if any(header.count(k) != 1 for k in required):
                issues.append(MetadataIssue("HEADERS_INVALID", "Required fact columns must occur once",
                                            {"sheet": sheet, "required": required}))
                continue
            columns = {k: header.index(k) for k in required}
            records = []
            for rownum, (row, cells) in enumerate(zip(values, expressions), 2):
                # Only required fact columns affect row existence or validation.
                if not any(row[i] is not None or cells[i].data_type == "f" for i in columns.values()):
                    continue
                for k, i in columns.items():
                    if cells[i].data_type == "e" or (cells[i].data_type == "f" and row[i] is None):
                        issues.append(MetadataIssue("FACT_CELL_ERROR", "Fact error or missing formula cache",
                                                    {}, SourceLocation(sheet, rownum, k)))
                records.append((rownum, {k: row[i] for k, i in columns.items()}))
            if not records and sheet in {"Sheet1", "Sheet3"}:
                issues.append(MetadataIssue("SHEET_EMPTY", f"Required {sheet} has no facts", {}))
            tables[sheet] = records
    finally:
        cached.close()
        formulas.close()
    if issues:
        raise ContextValidationError(issues)

    def number(value, source, *, integer=False):
        if value is None:
            return None
        try:
            result = float(value)
            if isinstance(value, bool) or not math.isfinite(result) or (integer and not result.is_integer()):
                raise ValueError
            return int(result) if integer else result
        except (ValueError, TypeError, OverflowError) as exc:
            raise ContextValidationError((MetadataIssue("INVALID_NUMERIC_CELL", "Expected finite number", {}, source),)) from exc

    operations, groups, segments, stations = [], defaultdict(list), defaultdict(list), []
    for row, v in tables["Sheet1"]:
        op = Operation(_text(v["part_id"]) or "", _text(v["operation_id"]) or "",
                       _enum(OperationType, _text(v["operation_type"]) or ""), _text(v["source_feature_ref"]),
                       _enum(SurfaceSide, _text(v["surface_side"]) or "NOT_APPLICABLE"),
                       _tokens(v["required_predecessor_ids"]), _text(v["cad_parent_operation_id"]),
                       _text(v["process_group_id"]), _text(v["geometry_id"]), SourceLocation("Sheet1", row))
        operations.append(op)
        if op.process_group_id:
            groups[op.part_id, op.process_group_id].append(op.operation_id)
    for row, v in tables["Sheet2"]:
        numeric = {k: number(v[k], SourceLocation("Sheet2", row, k)) for k in HEADERS["Sheet2"][4:]}
        segment = GeometrySegment(number(v["seq"], SourceLocation("Sheet2", row, "seq"), integer=True),
                                  _enum(SegmentType, _text(v["segment_type"]) or ""),
                                  **numeric, source=SourceLocation("Sheet2", row))
        segments[_text(v["part_id"]) or "", _text(v["geometry_id"]) or ""].append(segment)
    for row, v in tables["Sheet3"]:
        tokens = _tokens(v["allowed_operation_types"])
        stations.append(Station(_text(v["part_id"]) or "", _text(v["station_id"]) or "",
                                number(v["order"], SourceLocation("Sheet3", row, "order"), integer=True),
                                frozenset() if tokens == ("NONE",) else frozenset(_enum(OperationType, t) for t in tokens),
                                SourceLocation("Sheet3", row)))
    facts = InferenceFacts(tuple(operations),
                           tuple(Geometry(p, g, tuple(sorted(ss, key=lambda x: x.seq or 0))) for (p, g), ss in segments.items()),
                           tuple(stations), tuple(ProcessGroup(p, g, tuple(ids)) for (p, g), ids in groups.items()), path, digest)
    report = validate_facts(facts)
    if not report.passed:
        raise ContextValidationError(tuple(MetadataIssue(i.code, i.message, i.details, i.source)
                                           for i in report.issues if i.severity == "ERROR"))
    fixed = tuple(FixedAssignment(_text(v["operation_id"]) or "", _text(v["fixed_station"]) or "",
                                   SourceLocation("Sheet4", row)) for row, v in tables["Sheet4"])
    numeric = {f.name for f in fields(BendInfluence) if f.name not in
               {"bend_operation_id", "bend_axis", "affected_side", "influence_region_id", "parent_region_id", "source"}}
    regions = tuple(BendInfluence(**{k: number(v[k], SourceLocation("Sheet5", row, k)) if k in numeric else _text(v[k])
                                     for k in METADATA_HEADERS["Sheet5"]}, source=SourceLocation("Sheet5", row))
                    for row, v in tables["Sheet5"])
    return InferenceContext(facts, fixed, regions, digest)
