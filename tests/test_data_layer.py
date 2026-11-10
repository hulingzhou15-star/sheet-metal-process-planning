"""Integration and small adversarial cases. Raw input is never saved or edited."""
from dataclasses import fields, replace
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import openpyxl

from src.data import (ExpertAssignment, ExpertReferencePlan, Geometry, GeometrySegment,
                      Operation, OperationType, PartData, ProcessGroup, Severity, Station,
                      SurfaceSide, WorkbookLoadError, load_excel, validate, write_report)
from src.data.loader import HEADERS


ROOT = Path(__file__).resolve().parents[1]
P001 = ROOT / "data/parts/P001/钣金加工数据表_P001.xlsx"


def sample():
    # The labels intentionally contradict their numerical-looking suffix order.
    return PartData(
        (Operation("P", "O1", "TRIM"), Operation("P", "O2", "TRIM")), (),
        (Station("P", "OP90", 1, frozenset({"TRIM"})), Station("P", "OP10", 2, frozenset({"TRIM"}))), (),
        ExpertReferencePlan((ExpertAssignment("O1", "OP90"), ExpertAssignment("O2", "OP10"))))


def with_first_operation(data, **changes):
    return replace(data, operations=(replace(data.operations[0], **changes), *data.operations[1:]))


def contour_sample():
    d = sample()
    segments = (
        GeometrySegment(1, "ARC", -1, 0, 1, 0, 0, 0, 1, 180),
        GeometrySegment(2, "LINE", 1, 0, 1, 2),
        GeometrySegment(3, "ARC", 1, 2, -1, 2, 0, 2, 1, 180),
        GeometrySegment(4, "LINE", -1, 2, -1, 0),
    )
    return replace(with_first_operation(d, operation_type="IRREGULAR_PUNCH", geometry_id="G1"),
                   geometries=(Geometry("P", "G1", segments),),
                   stations=(replace(d.stations[0], allowed_operation_types=frozenset({"TRIM", "IRREGULAR_PUNCH"})), d.stations[1]))


def group_sample():
    d = sample()
    return replace(d,
                   operations=(replace(d.operations[0], operation_type="PRE_PUNCH", process_group_id="PG1"),
                               replace(d.operations[1], operation_type="FLANGING", process_group_id="PG1")),
                   stations=(replace(d.stations[0], allowed_operation_types=frozenset({"PRE_PUNCH"})),
                             replace(d.stations[1], allowed_operation_types=frozenset({"FLANGING"}))),
                   process_groups=(ProcessGroup("P", "PG1", ("O1", "O2")),))


class P001IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.digest = hashlib.sha256(P001.read_bytes()).hexdigest()
        cls.data = load_excel(P001)
        cls.report = validate(cls.data)

    @classmethod
    def tearDownClass(cls):
        if hashlib.sha256(P001.read_bytes()).hexdigest() != cls.digest:
            raise AssertionError("P001 source bytes changed during integration tests")

    def test_p001_reads_current_source(self):
        self.assertEqual(len(self.data.operations), 224)
        self.assertEqual(len(self.data.geometries), 211)
        self.assertEqual(sum(len(g.segments) for g in self.data.geometries), 689)
        self.assertEqual(len(self.data.stations), 7)
        self.assertEqual(len(self.data.process_groups), 46)
        self.assertEqual(self.data.source_sha256, self.digest)

    def test_all_source_geometry_rows_are_parsed_exactly_once(self):
        book = openpyxl.load_workbook(P001, read_only=True, data_only=False)
        try:
            rows = book["Sheet2"].iter_rows(values_only=True)
            headers = next(rows)
            source_records = []
            for row_number, values in enumerate(rows, 2):
                if any(value is not None for value in values):
                    row = dict(zip(headers, values))
                    source_records.append((row_number, row["geometry_id"], row["seq"], row["segment_type"]))
        finally:
            book.close()
        parsed_records = [(segment.source.row, geometry.geometry_id, segment.seq, segment.segment_type)
                          for geometry in self.data.geometries for segment in geometry.segments]
        self.assertEqual(len(source_records), len(parsed_records))
        self.assertCountEqual(source_records, parsed_records)

    def test_p001_passes_all_error_checks(self):
        self.assertTrue(self.report.passed, self.report.to_dict())
        self.assertEqual(self.report.counts["ERROR"], 0)
        self.assertEqual(self.report.counts["WARNING"], 0)

    def test_normalization_and_reference_separation(self):
        self.assertEqual(sum(op.surface_side == SurfaceSide.NOT_APPLICABLE for op in self.data.operations), 71)
        self.assertEqual(next(s for s in self.data.stations if s.station_id == "OP70").allowed_operation_types, frozenset())
        self.assertNotIn("expert_station", {f.name for f in fields(Operation)})
        self.assertEqual(len(self.data.expert_reference_plan.assignments), 224)
        self.assertEqual(len(self.data.predecessors), 34)
        self.assertEqual(len(self.data.cad_parents), 153)
        op = next(op for op in self.data.operations if op.operation_id == "O088")
        self.assertEqual(self.data.geometry_for(op).geometry_id, "G088")

    def test_loader_only_opens_read_only_and_never_saves(self):
        with patch("src.data.loader.openpyxl.load_workbook", wraps=openpyxl.load_workbook) as reader:
            with patch("openpyxl.workbook.workbook.Workbook.save", side_effect=AssertionError("Source save attempted")):
                load_excel(P001)
        self.assertEqual(reader.call_count, 2)
        self.assertTrue(all(call.kwargs["read_only"] for call in reader.call_args_list))
        self.assertEqual(hashlib.sha256(P001.read_bytes()).hexdigest(), self.digest)

    def test_json_report_is_structured_and_optional_geometry_is_info(self):
        value = json.loads(json.dumps(self.report.to_dict(), allow_nan=False))
        self.assertTrue(value["passed"])
        missing = [i for i in value["issues"] if i["code"] == "GEOMETRY_NOT_PROVIDED"]
        self.assertEqual(len(missing), 13)
        self.assertTrue(all(i["severity"] == "INFO" for i in missing))


class ValidatorTests(unittest.TestCase):
    def assert_error(self, data, code):
        report = validate(data)
        self.assertFalse(report.passed)
        self.assertIn(code, [i.code for i in report.issues if i.severity == Severity.ERROR], report.to_dict())

    def test_valid_minimal_data(self):
        self.assertTrue(validate(sample()).passed)
        self.assertTrue(validate(contour_sample()).passed)
        self.assertTrue(validate(group_sample()).passed)

    def test_missing_predecessor(self):
        self.assert_error(with_first_operation(sample(), required_predecessor_ids=("missing",)), "PREDECESSOR_REFERENCE_INVALID")

    def test_explicit_predecessor_strict_order(self):
        self.assert_error(with_first_operation(sample(), required_predecessor_ids=("O2",)), "EXPERT_PREDECESSOR_ORDER")

    def test_order_field_is_authoritative(self):
        d = sample()
        d = replace(d, operations=(d.operations[0], replace(d.operations[1], required_predecessor_ids=("O1",))))
        self.assertTrue(validate(d).passed)  # OP90 precedes OP10 because order is 1 then 2.

    def test_unknown_capability(self):
        d = sample()
        self.assert_error(replace(d, stations=(replace(d.stations[0], allowed_operation_types=frozenset({"UNKNOWN"})), d.stations[1])),
                          "STATION_CAPABILITY_INVALID")

    def test_expert_capability_violation(self):
        d = sample()
        self.assert_error(replace(d, stations=(replace(d.stations[0], allowed_operation_types=frozenset()), d.stations[1])),
                          "EXPERT_CAPABILITY_VIOLATION")

    def test_invalid_geometry_reference(self):
        self.assert_error(with_first_operation(sample(), geometry_id="missing"), "GEOMETRY_REFERENCE_INVALID")

    def test_orphan_geometry(self):
        g = Geometry("P", "orphan", (GeometrySegment(1, "CIRCLE", center_x=0, center_y=0, radius=1),))
        self.assert_error(replace(sample(), geometries=(g,)), "GEOMETRY_ORPHAN")

    def test_noncontinuous_or_duplicate_seq(self):
        d = contour_sample()
        for seq in (0, 2, 6, 1.5):
            with self.subTest(seq=seq):
                g = d.geometries[0]
                g = replace(g, segments=(replace(g.segments[0], seq=seq), *g.segments[1:]))
                self.assert_error(replace(d, geometries=(g,)), "GEOMETRY_SEQ_INVALID")

    def test_unclosed_contour(self):
        d = contour_sample()
        g = d.geometries[0]
        g = replace(g, segments=(*g.segments[:-1], replace(g.segments[-1], end_y=0.1)))
        self.assert_error(replace(d, geometries=(g,)), "CONTOUR_NOT_CLOSED")

    def test_discontinuous_contour(self):
        d = contour_sample()
        g = d.geometries[0]
        g = replace(g, segments=(g.segments[0], replace(g.segments[1], start_x=1.1), *g.segments[2:]))
        self.assert_error(replace(d, geometries=(g,)), "CONTOUR_DISCONTINUOUS")

    def test_clockwise_contour(self):
        d = contour_sample()
        g = d.geometries[0]
        reversed_segments = tuple(replace(s, seq=i, start_x=s.end_x, start_y=s.end_y,
                                         end_x=s.start_x, end_y=s.start_y,
                                         sweep_angle=-s.sweep_angle if s.sweep_angle is not None else None)
                                  for i, s in enumerate(reversed(g.segments), 1))
        self.assert_error(replace(d, geometries=(replace(g, segments=reversed_segments),)), "CONTOUR_NOT_CCW")

    def test_signed_arc_and_radius_errors(self):
        arc = GeometrySegment(1, "ARC", 1, 0, 0, -1, 0, 0, 1, -90)
        self.assertAlmostEqual(arc.arc_endpoint()[1], -1)
        d = with_first_operation(sample(), geometry_id="G")
        d = replace(d, geometries=(Geometry("P", "G", (arc,)),))
        self.assertTrue(validate(d).passed)
        for bad in (replace(arc, sweep_angle=90), replace(arc, radius=2)):
            with self.subTest(segment=bad):
                self.assert_error(replace(d, geometries=(Geometry("P", "G", (bad,)),)), "ARC_INCONSISTENT")

    def test_arc_tolerance_boundary(self):
        arc = GeometrySegment(1, "ARC", 1, 0, 0, 1, 0, 0, 1.0005, 90)
        d = replace(with_first_operation(sample(), geometry_id="G"), geometries=(Geometry("P", "G", (arc,)),))
        self.assertTrue(validate(d, distance_tolerance=0.001).passed)
        self.assertFalse(validate(d, distance_tolerance=0.0001).passed)

    def test_missing_primitive_fields(self):
        d = replace(with_first_operation(sample(), geometry_id="G"), geometries=(Geometry("P", "G", (GeometrySegment(1, "ARC"),)),))
        self.assert_error(d, "SEGMENT_FIELDS_INVALID")

    def test_operation_geometry_representation_mismatch(self):
        d = sample()
        d = replace(with_first_operation(d, operation_type="CIRCLE_PUNCH", geometry_id="G"),
                    geometries=(Geometry("P", "G", (GeometrySegment(1, "LINE", 0, 0, 1, 0),)),),
                    stations=(replace(d.stations[0], allowed_operation_types=frozenset({"CIRCLE_PUNCH"})), d.stations[1]))
        self.assert_error(d, "OPERATION_GEOMETRY_MISMATCH")

    def test_bridge_midpoint_and_footprint(self):
        bridge = GeometrySegment(1, "BRIDGE", 0, 0, 4, 2, 2, 1)
        self.assertEqual(bridge.bridge_footprint(), ((0, 0), (4, 0), (4, 2), (0, 2)))
        d = replace(with_first_operation(sample(), geometry_id="G"), geometries=(Geometry("P", "G", (bridge,)),))
        self.assertTrue(validate(d).passed)
        self.assert_error(replace(d, geometries=(Geometry("P", "G", (replace(bridge, center_x=3),)),)), "BRIDGE_CENTER_INCONSISTENT")

    def test_cad_parent_cycle_and_self_cycle(self):
        d = sample()
        self.assert_error(with_first_operation(d, cad_parent_operation_id="O1"), "CAD_PARENT_CYCLE")
        self.assert_error(replace(d, operations=(replace(d.operations[0], cad_parent_operation_id="O2"),
                                                replace(d.operations[1], cad_parent_operation_id="O1"))), "CAD_PARENT_CYCLE")

    def test_cad_parent_does_not_imply_precedence(self):
        self.assertTrue(validate(with_first_operation(sample(), cad_parent_operation_id="O2")).passed)

    def test_missing_cad_parent(self):
        self.assert_error(with_first_operation(sample(), cad_parent_operation_id="missing"), "CAD_PARENT_REFERENCE_INVALID")

    def test_invalid_process_group_membership(self):
        d = group_sample()
        self.assert_error(replace(d, process_groups=(replace(d.process_groups[0], operation_ids=("O1",)),)), "PROCESS_GROUP_STRUCTURE")
        self.assert_error(replace(d, process_groups=()), "PROCESS_GROUP_REFERENCE_INVALID")

    def test_invalid_pre_punch_flanging_pair(self):
        d = group_sample()
        self.assert_error(replace(d, operations=(d.operations[0], replace(d.operations[1], operation_type="PRE_PUNCH"))), "PROCESS_GROUP_PAIR_INVALID")

    def test_pre_punch_strictly_before_flanging(self):
        d = group_sample()
        self.assert_error(replace(d, expert_reference_plan=ExpertReferencePlan((ExpertAssignment("O1", "OP10"), ExpertAssignment("O2", "OP90")))),
                          "EXPERT_PRE_PUNCH_ORDER")

    def test_other_group_semantics_are_warning_not_invented_hard_rule(self):
        d = sample()
        d = replace(d, operations=tuple(replace(op, process_group_id="PG") for op in d.operations),
                    process_groups=(ProcessGroup("P", "PG", ("O1", "O2")),))
        report = validate(d)
        self.assertTrue(report.passed)
        self.assertEqual(report.counts["WARNING"], 1)

    def test_part_id_mismatch(self):
        d = sample()
        self.assert_error(replace(d, stations=(replace(d.stations[0], part_id="OTHER"), d.stations[1])), "PART_ID_INCONSISTENT")

    def test_duplicate_operation_and_station_ids(self):
        d = sample()
        self.assert_error(replace(d, operations=(d.operations[0], d.operations[0])), "OPERATION_ID_DUPLICATE")
        self.assert_error(replace(d, stations=(d.stations[0], d.stations[0])), "STATION_ID_DUPLICATE")

    def test_invalid_and_duplicate_station_order(self):
        d = sample()
        for value in (0, -1, 1.5, True):
            with self.subTest(order=value):
                self.assert_error(replace(d, stations=(replace(d.stations[0], order=value), d.stations[1])), "STATION_ORDER_INVALID")
        self.assert_error(replace(d, stations=(d.stations[0], replace(d.stations[1], order=1))), "STATION_ORDER_DUPLICATE")

    def test_unknown_operation_type_and_surface_side(self):
        self.assert_error(with_first_operation(sample(), operation_type="UNKNOWN"), "OPERATION_TYPE_INVALID")
        self.assert_error(with_first_operation(sample(), surface_side="TOP"), "SURFACE_SIDE_INVALID")

    def test_missing_expert_station_and_assignment(self):
        d = sample()
        self.assert_error(replace(d, expert_reference_plan=ExpertReferencePlan((ExpertAssignment("O1", "missing"), ExpertAssignment("O2", "OP10")))),
                          "EXPERT_STATION_INVALID")
        self.assert_error(replace(d, expert_reference_plan=ExpertReferencePlan(())), "EXPERT_ASSIGNMENT_MISSING")

    def test_invalid_tolerance_rejected(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(tolerance=value), self.assertRaises(ValueError):
                validate(sample(), distance_tolerance=value)


class LoaderAndOutputTests(unittest.TestCase):
    def workbook_bytes(self, edit=None):
        # A synthetic workbook is authored only in memory, then in a temporary
        # directory outside data/raw. This never opens the P001 file for writing.
        book = openpyxl.Workbook()
        book.remove(book.active)
        for name, headers in HEADERS.items():
            book.create_sheet(name).append(headers)
        book["Sheet1"].append(("P", "O1", "ref", "BEND", None, None, None, None, "slot", "G1"))
        book["Sheet2"].append(("P", "G1", 1, "BEND_LINE", 0, 0, 1, 0, None, None, None, None))
        book["Sheet3"].append(("P", "slot", 1, "BEND"))
        if edit:
            edit(book)
        stream = BytesIO()
        book.save(stream)
        book.close()
        return stream.getvalue()

    def load_temporary(self, content):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.xlsx"
            path.write_bytes(content)
            return load_excel(path)

    def test_minimal_workbook(self):
        self.assertTrue(validate(self.load_temporary(self.workbook_bytes())).passed)

    def test_geometry_sheet_may_be_empty_when_no_operation_needs_geometry(self):
        def edit(book):
            book["Sheet1"]["D2"] = "MARKING"
            book["Sheet1"]["J2"] = None
            book["Sheet3"]["D2"] = "MARKING"
            book["Sheet2"].delete_rows(2)
        self.assertTrue(validate(self.load_temporary(self.workbook_bytes(edit))).passed)

    def test_corrupt_workbook_has_structured_error(self):
        with self.assertRaises(WorkbookLoadError) as result:
            self.load_temporary(b"not an Excel workbook")
        self.assertEqual(result.exception.report.issues[0].code, "WORKBOOK_UNREADABLE")

    def test_missing_sheet_and_header_produce_structured_errors(self):
        for edit, code in ((lambda w: w.remove(w["Sheet3"]), "SHEET_MISSING"),
                           (lambda w: setattr(w["Sheet1"]["B1"], "value", "wrong"), "HEADERS_INVALID")):
            with self.subTest(code=code), self.assertRaises(WorkbookLoadError) as result:
                self.load_temporary(self.workbook_bytes(edit))
            self.assertIn(code, [i.code for i in result.exception.report.issues])
            self.assertFalse(result.exception.report.passed)

    def test_invalid_numeric_cell_produces_structured_error(self):
        for value in (1.5, "bad", None, True):
            with self.subTest(value=value), self.assertRaises(WorkbookLoadError) as result:
                self.load_temporary(self.workbook_bytes(lambda w: setattr(w["Sheet3"]["C2"], "value", value)))
            self.assertIn("INVALID_NUMERIC_CELL", [i.code for i in result.exception.report.issues])

    def test_missing_formula_cache_is_reported_not_recalculated(self):
        with self.assertRaises(WorkbookLoadError) as result:
            self.load_temporary(self.workbook_bytes(lambda w: setattr(w["Sheet2"]["I2"], "value", "=(E2+G2)/2")))
        self.assertIn("FORMULA_CACHE_MISSING", [i.code for i in result.exception.report.issues])

    def test_unknown_enum_tokens_are_retained_for_validator(self):
        d = self.load_temporary(self.workbook_bytes(lambda w: setattr(w["Sheet1"]["D2"], "value", "UNKNOWN")))
        self.assertEqual(d.operations[0].operation_type, "UNKNOWN")
        self.assertIn("OPERATION_TYPE_INVALID", [i.code for i in validate(d).issues])

    def test_none_alone_is_empty_capability_but_mixed_none_is_invalid(self):
        d = self.load_temporary(self.workbook_bytes(lambda w: setattr(w["Sheet3"]["D2"], "value", "NONE")))
        self.assertEqual(d.stations[0].allowed_operation_types, frozenset())
        d = self.load_temporary(self.workbook_bytes(lambda w: setattr(w["Sheet3"]["D2"], "value", "NONE,BEND")))
        self.assertIn("STATION_CAPABILITY_INVALID", [i.code for i in validate(d).issues])

    def test_report_destinations_are_restricted(self):
        report = validate(sample())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for relative in ("outputs/report.json", "data/processed/report.json"):
                target = write_report(report, root / relative, project_root=root)
                self.assertTrue(json.loads(target.read_text(encoding="utf-8"))["passed"])
            for relative in ("data/raw/report.json", "outside/report.json", "outputs/../report.json", "outputs/report.xlsx"):
                with self.subTest(relative=relative), self.assertRaises(ValueError):
                    write_report(report, root / relative, project_root=root)
            self.assertFalse((root / "data/parts/P001").exists())

    def test_report_rejects_hard_links_to_raw_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw = root / "data/raw/protected.json"
            raw.parent.mkdir(parents=True)
            raw.write_bytes(b"protected source bytes")
            alias = root / "outputs/report.json"
            alias.parent.mkdir()
            os.link(raw, alias)
            with self.assertRaises(ValueError):
                write_report(validate(sample()), alias, project_root=root)
            self.assertEqual(raw.read_bytes(), b"protected source bytes")


if __name__ == "__main__":
    unittest.main()
