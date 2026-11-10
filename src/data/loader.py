"""Read a workbook snapshot without writing, saving or recalculating the source."""
from __future__ import annotations

from collections import defaultdict
from io import BytesIO
import hashlib
import math
from pathlib import Path
import re
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

import openpyxl

from .report import Severity, ValidationIssue, ValidationReport
from .schema import (ExpertAssignment, ExpertReferencePlan, Geometry, GeometrySegment,
                     Operation, OperationType, PartData, ProcessGroup, SegmentType,
                     SourceLocation, Station, SurfaceSide)


HEADERS = {
    "Sheet1": ("part_id", "operation_id", "source_feature_ref", "operation_type", "surface_side",
               "required_predecessor_ids", "cad_parent_operation_id", "process_group_id", "expert_station", "geometry_id"),
    "Sheet2": ("part_id", "geometry_id", "seq", "segment_type", "start_x", "start_y", "end_x", "end_y",
               "center_x", "center_y", "radius", "sweep_angle"),
    "Sheet3": ("part_id", "station_id", "order", "allowed_operation_types"),
}


class WorkbookLoadError(ValueError):
    """Unreadable structure or cell values; diagnostics remain serializable."""
    def __init__(self, report: ValidationReport):
        self.report = report
        super().__init__("Workbook could not be parsed; inspect exception.report")


def _text(value) -> str | None:
    return None if value is None or str(value).strip() == "" else str(value).strip()


def _enum(enum_class, value):
    try:
        return enum_class(value)
    except ValueError:
        return value


def _tokens(value) -> tuple[str, ...]:
    text = _text(value)
    return tuple(x.strip() for x in re.split(r"[,;\s]+", text) if x.strip()) if text else ()


def load_excel(path: str | Path) -> PartData:
    path = Path(path).resolve()
    content = path.read_bytes()  # rb only: a consistent snapshot with provenance.
    digest = hashlib.sha256(content).hexdigest()
    issues: list[ValidationIssue] = []

    def fail(code, message, source=None, **details):
        issues.append(ValidationIssue(Severity.ERROR, code, message, source=source, details=details))

    def report():
        return ValidationReport(tuple(issues), source_path=str(path), source_sha256=digest)

    def number(value, location, *, integer=False, required=False):
        if value is None and not required:
            return None
        try:
            if value is None or isinstance(value, bool):
                raise ValueError
            converted = float(value)
            if not math.isfinite(converted) or (integer and not converted.is_integer()):
                raise ValueError
            return int(converted) if integer else converted
        except (ValueError, TypeError, OverflowError):
            fail("INVALID_NUMERIC_CELL", "Expected a finite integer" if integer else "Expected a finite number",
                 location, value=str(value))
            return None

    cached = None
    try:
        cached = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True)
        formulas = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=False)
    except (OSError, ValueError, KeyError, BadZipFile, ParseError, openpyxl.utils.exceptions.InvalidFileException) as exc:
        if cached is not None:
            cached.close()
        fail("WORKBOOK_UNREADABLE", str(exc))
        raise WorkbookLoadError(report()) from exc

    tables = {}
    try:
        for name, expected in HEADERS.items():
            if name not in cached.sheetnames:
                fail("SHEET_MISSING", f"Required sheet {name} is missing")
                continue
            values = cached[name].iter_rows(values_only=True)
            expressions = formulas[name].iter_rows()
            header = tuple(_text(x) for x in next(values, ()))
            next(expressions, ())
            if any(header.count(x) != 1 for x in expected):
                fail("HEADERS_INVALID", "Required columns must each occur exactly once", SourceLocation(name, 1),
                     expected=list(expected), actual=list(header))
                continue
            rows = []
            for index, (row, expression_row) in enumerate(zip(values, expressions), 2):
                if not any(x is not None for x in row):
                    # A row of formulas with absent caches is still source data.
                    if not any(c.data_type == "f" for c in expression_row):
                        continue
                for column, cell in enumerate(expression_row):
                    if cell.data_type == "f" and row[column] is None:
                        fail("FORMULA_CACHE_MISSING", "A formula has no saved value; source is not recalculated",
                             SourceLocation(name, index, header[column]))
                    if cell.data_type == "e":
                        fail("EXCEL_CELL_ERROR", "Excel contains an error value", SourceLocation(name, index, header[column]), value=str(cell.value))
                rows.append((index, dict(zip(header, row))))
            if not rows and name != "Sheet2":
                fail("SHEET_EMPTY", f"Required sheet {name} contains no data")
            tables[name] = rows
    finally:
        cached.close()
        formulas.close()
    if issues:
        raise WorkbookLoadError(report())

    operations, assignments, stations = [], [], []
    grouped_segments = defaultdict(list)
    grouped_operations = defaultdict(list)
    for row, values in tables["Sheet1"]:
        location = SourceLocation("Sheet1", row)
        op = Operation(
            part_id=_text(values["part_id"]) or "", operation_id=_text(values["operation_id"]) or "",
            operation_type=_enum(OperationType, _text(values["operation_type"]) or ""),
            source_feature_ref=_text(values["source_feature_ref"]),
            surface_side=_enum(SurfaceSide, _text(values["surface_side"]) or SurfaceSide.NOT_APPLICABLE.value),
            required_predecessor_ids=_tokens(values["required_predecessor_ids"]),
            cad_parent_operation_id=_text(values["cad_parent_operation_id"]),
            process_group_id=_text(values["process_group_id"]), geometry_id=_text(values["geometry_id"]), source=location)
        operations.append(op)
        assignments.append(ExpertAssignment(op.operation_id, _text(values["expert_station"]), location))
        if op.process_group_id:
            grouped_operations[(op.part_id, op.process_group_id)].append(op.operation_id)
    for row, values in tables["Sheet2"]:
        location = SourceLocation("Sheet2", row)
        numeric = {key: number(values[key], SourceLocation("Sheet2", row, key)) for key in HEADERS["Sheet2"][4:]}
        segment = GeometrySegment(
            seq=number(values["seq"], SourceLocation("Sheet2", row, "seq"), integer=True, required=True),
            segment_type=_enum(SegmentType, _text(values["segment_type"]) or ""), source=location, **numeric)
        grouped_segments[(_text(values["part_id"]) or "", _text(values["geometry_id"]) or "")].append(segment)
    for row, values in tables["Sheet3"]:
        tokens = _tokens(values["allowed_operation_types"])
        # NONE denotes the empty set only when it is the entire cell.
        capabilities = frozenset() if tokens == ("NONE",) else frozenset(_enum(OperationType, x) for x in tokens)
        stations.append(Station(
            part_id=_text(values["part_id"]) or "", station_id=_text(values["station_id"]) or "",
            order=number(values["order"], SourceLocation("Sheet3", row, "order"), integer=True, required=True),
            allowed_operation_types=capabilities, source=SourceLocation("Sheet3", row)))
    if issues:
        raise WorkbookLoadError(report())
    geometries = tuple(Geometry(part, gid, tuple(sorted(segments, key=lambda s: s.seq)))
                       for (part, gid), segments in grouped_segments.items())
    groups = tuple(ProcessGroup(part, gid, tuple(ids)) for (part, gid), ids in grouped_operations.items())
    return PartData(tuple(operations), geometries, tuple(stations), groups,
                    ExpertReferencePlan(tuple(assignments)), path, digest)
