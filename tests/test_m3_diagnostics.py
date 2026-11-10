from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.evaluate_plan_constraints import build_report, markdown_report, write_reports
from src.constraints import FixedAssignment, load_constraint_context
from src.constraints.config import load_config
from src.constraints.diagnostics import DiagnosticEngine
from src.constraints.soft_priors import SoftPriorEngine
from src.data.schema import Geometry, GeometrySegment, Operation
from m3_fixtures import P001, bends, context, region


class DiagnosticTests(unittest.TestCase):
    def test_depth_ordering_excludes_transitive_hierarchy(self):
        c = bends((400, 200, 100), parents=(None, "R1", "R2"))
        c = replace(c, bend_influences=tuple(replace(r, affected_depth=100/(i+1)) for i, r in enumerate(c.bend_influences)))
        r = DiagnosticEngine(c).evaluate({"A": "OP80", "B": "OP10", "C": "OP90"})["D1"]
        self.assertEqual((r["comparable_pair_count"], r["excluded_hierarchy_pair_count"], r["weight"]), (0, 3, 0))

    def test_depth_inversion_and_same_station(self):
        c = bends()
        c = replace(c, bend_influences=(replace(c.bend_influences[0], affected_depth=20), c.bend_influences[1]))
        r = DiagnosticEngine(c).evaluate({"A": "OP90", "B": "OP10"})["D1"]
        self.assertEqual((r["comparable_pair_count"], r["inversion_count"], r["inversion_rate"]), (1, 1, 1))
        r = DiagnosticEngine(c).evaluate({"A": "OP90", "B": "OP90"})["D1"]
        self.assertEqual(r["inversion_count"], 0)

    def test_region_forest_levels_distribution_and_gap(self):
        c = bends((400, 200, 100, 50), parents=(None, "R1", "R2", None))
        r = DiagnosticEngine(c).evaluate({"A": "OP80", "B": "OP10", "C": "OP90", "D": "OP90"})["D2"]
        self.assertEqual((r["root_region_count"], r["leaf_region_count"], r["max_hierarchy_depth"]), (2, 2, 2))
        self.assertTrue(r["has_multilevel_chain"])
        self.assertEqual(r["levels"][0]["station_distribution"], {"OP80": 1, "OP90": 1})
        self.assertEqual(r["parent_child_gap_distribution"], {1: 2})

    def local_context(self):
        ops = (Operation("P", "A", "BEND"), Operation("P", "B", "BEND"),
               Operation("P", "Q", "CIRCLE_PUNCH", geometry_id="G"), Operation("P", "X", "MARKING"))
        geo = Geometry("P", "G", (GeometrySegment(1, "CIRCLE", center_x=2, center_y=2, radius=.25),))
        return context(ops, (region("A", "RA", area=100, depth=10),
                             region("B", "RB", area=40, depth=4, parent="RA")),
                       assignments={"A": "OP80", "B": "OP10", "Q": "OP90", "X": "OP10"}, geometries=(geo,))

    def test_d3_smallest_containing_region_single_membership(self):
        c = self.local_context()
        r = DiagnosticEngine(c).evaluate(c.expert_reference_plan)["D3"]
        self.assertEqual(r["status"], "available")
        self.assertEqual(r["assigned_operation_count"], 1)
        self.assertEqual(r["assigned_operations"][0]["assigned_region_id"], "RB")
        self.assertEqual(r["no_geometry_count"], 3)
        member = next(r for r in r["regions"] if r["region_id"] == "RB")
        self.assertEqual(member["operation_type_distribution"], {"CIRCLE_PUNCH": 1})
        self.assertEqual(member["station_distribution"], {"OP90": 1})

    def test_d3_outside_regions_ignored(self):
        c = self.local_context()
        g = c.part_data.geometries[0]
        g = replace(g, segments=(replace(g.segments[0], center_x=50),))
        c = replace(c, part_data=replace(c.part_data, geometries=(g,)))
        r = DiagnosticEngine(c).evaluate(c.expert_reference_plan)["D3"]
        self.assertEqual((r["assigned_operation_count"], r["outside_region_count"]), (0, 1))

    def test_d3_shared_boundary_is_observed_without_geometry_tolerance(self):
        c = self.local_context()
        g = c.part_data.geometries[0]
        g = replace(g, segments=(replace(g.segments[0], center_y=0),))
        c = replace(c, part_data=replace(c.part_data, geometries=(g,)))
        r = DiagnosticEngine(c).evaluate(c.expert_reference_plan)["D3"]
        self.assertEqual(r["assigned_operation_count"], 1)
        self.assertTrue(r["assigned_operations"][0]["on_boundary"])

    def test_d3_equal_smallest_regions_reported_without_tie_rule(self):
        c = self.local_context()
        c = replace(c, bend_influences=(replace(c.bend_influences[0], affected_depth=4, far_boundary_coord=4),
                                        c.bend_influences[1]))
        r = DiagnosticEngine(c).evaluate(c.expert_reference_plan)["D3"]
        self.assertEqual(r["status"], "partial")
        self.assertEqual(r["assigned_operation_count"], 0)
        self.assertEqual(len(r["ambiguous_smallest_regions"]), 1)

    def test_d3_unsupported_rectangle_reports_reason(self):
        c = self.local_context()
        c = replace(c, bend_influences=(replace(c.bend_influences[0], bend_axis="DIAGONAL"), c.bend_influences[1]))
        r = DiagnosticEngine(c).evaluate(c.expert_reference_plan)["D3"]
        self.assertEqual(r["status"], "partial")
        self.assertEqual(len(r["invalid_regions"]), 1)

    def test_d3_unreliable_geometry_reports_unavailable(self):
        c = self.local_context()
        g = replace(c.part_data.geometries[0], segments=(GeometrySegment(1, "unknown"),))
        c = replace(c, part_data=replace(c.part_data, geometries=(g,)))
        r = DiagnosticEngine(c).evaluate(c.expert_reference_plan)["D3"]
        self.assertEqual(r["status"], "unavailable")
        self.assertIn("reason", r)

    def test_diagnostic_execution_and_configuration_do_not_affect_soft_penalty(self):
        c = bends()
        plan = {"A": "OP90", "B": "OP10"}
        config = load_config()
        before = SoftPriorEngine(c, config).evaluate(plan)
        config["depth_diagnostic"]["significance_ratio"] = 2.0
        DiagnosticEngine(c, config).evaluate(plan)
        self.assertEqual(before, SoftPriorEngine(c, config).evaluate(plan))


class ReportTests(unittest.TestCase):
    def test_expert_failure_stops_every_subsequent_engine(self):
        c = context((Operation("P", "A", "TRIM"),), fixed=(FixedAssignment("A", "OP10"),))
        with patch("scripts.evaluate_plan_constraints.SoftPriorEngine") as soft, \
             patch("scripts.evaluate_plan_constraints.DiagnosticEngine") as diag, \
             patch("scripts.evaluate_plan_constraints.StationOverloadGuardrail") as guard:
            report = build_report(c, c.expert_reference_plan, config=load_config())
        self.assertEqual(report["status"], "stopped_expert_infeasible")
        self.assertNotIn("soft_priors", report)
        soft.assert_not_called()
        diag.assert_not_called()
        guard.assert_not_called()

    def test_expert_gate_also_stops_feasible_supplied_plan(self):
        c = context((Operation("P", "A", "TRIM"),), fixed=(FixedAssignment("A", "OP10"),))
        report = build_report(c, {"A": "OP10"}, config=load_config(), plan_source="supplied")
        self.assertEqual(report["status"], "stopped_expert_infeasible")

    def test_invalid_plan_no_score(self):
        c = bends()
        report = build_report(c, {"A": "OP90"}, config=load_config())
        self.assertEqual(report["status"], "stopped_plan_infeasible")
        self.assertNotIn("soft_priors", report)

    def test_report_full_audit_and_output_protection(self):
        before = hashlib.sha256(P001.read_bytes()).hexdigest()
        c = load_constraint_context(P001)
        report = build_report(c, c.expert_reference_plan, config=load_config())
        self.assertEqual(report["status"], "evaluated")
        components = report["soft_priors"]["components"]
        self.assertEqual(len(components["bend_area_ordering"]["audit"]["comparable_pairs"]), 1460)
        self.assertEqual(len(components["precedence_extra_gap"]["audit"]["relations"]), 112)
        self.assertEqual(len(report["diagnostics"]["D2"]["hierarchy"]), 59)
        self.assertIn("G1", markdown_report(report))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = write_reports(report, root / "outputs/m3", project_root=root)
            saved = json.loads(paths[0].read_text(encoding="utf-8"))
            self.assertEqual(saved["soft_priors"], json.loads(json.dumps(report["soft_priors"])))
            with self.assertRaises(ValueError):
                write_reports(report, root / "data/parts/P001", project_root=root)
        self.assertEqual(before, hashlib.sha256(P001.read_bytes()).hexdigest())
