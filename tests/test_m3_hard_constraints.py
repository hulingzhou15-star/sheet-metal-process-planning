from dataclasses import replace
import unittest

from src.constraints import FixedAssignment, HardConstraintEngine, load_constraint_context
from src.data.schema import Operation
from m3_fixtures import P001, context, region


class HardConstraintTests(unittest.TestCase):
    def setUp(self):
        self.plan = {"A": "OP90", "B": "OP10", "C": "OP90", "D": "OP10", "E": "OP80", "F": "OP10"}
        self.context = context((Operation("P", "A", "TRIM"),
                                Operation("P", "B", "IRREGULAR_PUNCH", required_predecessor_ids=("A",)),
                                Operation("P", "C", "PRE_PUNCH", process_group_id="PG"),
                                Operation("P", "D", "FLANGING", process_group_id="PG"),
                                Operation("P", "E", "BEND"), Operation("P", "F", "BEND")),
                               (region("E", "RP", area=200), region("F", "RC", parent="RP")),
                               assignments=self.plan, fixed=(FixedAssignment("A", "OP90"),))
        self.engine = HardConstraintEngine(self.context)

    def kinds(self, plan):
        result = self.engine.evaluate(plan)
        self.assertFalse(result.feasible)
        return {v.constraint_type for v in result.violations}

    def test_expert_plan_h1_h6(self):
        c = load_constraint_context(P001)
        r = HardConstraintEngine(c).evaluate(c.expert_reference_plan)
        self.assertTrue(r.feasible)
        self.assertEqual(r.to_dict()["violation_breakdown"], {f"H{i}": 0 for i in range(1, 7)})

    def test_artificial_feasible_plan(self):
        self.assertTrue(self.engine.evaluate(self.plan).feasible)

    def test_missing_operation(self):
        self.assertIn("H1", self.kinds({op: s for op, s in self.plan.items() if op != "B"}))

    def test_duplicate_multiple_and_identical_assignments(self):
        for station in ("OP10", "OP90"):
            with self.subTest(station=station):
                self.assertIn("H1", self.kinds([*self.plan.items(), ("A", station)]))

    def test_invalid_station(self):
        self.assertIn("H1", self.kinds({**self.plan, "B": "unknown"}))

    def test_unknown_operation(self):
        self.assertIn("H1", self.kinds({**self.plan, "extra": "OP90"}))

    def test_invalid_null_and_multivalue_station(self):
        for station in (None, ["OP90", "OP10"]):
            with self.subTest(station=station):
                self.assertIn("H1", self.kinds({**self.plan, "B": station}))

    def test_capability_violation(self):
        stations = tuple(replace(s, allowed_operation_types=s.allowed_operation_types-{"IRREGULAR_PUNCH"})
                         if s.station_id == "OP10" else s for s in self.context.part_data.stations)
        engine = HardConstraintEngine(replace(self.context, part_data=replace(self.context.part_data, stations=stations)))
        result = engine.evaluate(self.plan)
        self.assertEqual({v.constraint_type for v in result.violations}, {"H2"})

    def test_explicit_predecessor_violation_including_same_station(self):
        self.assertIn("H3", self.kinds({**self.plan, "B": "OP90"}))

    def test_pre_punch_flanging_violation(self):
        self.assertIn("H4", self.kinds({**self.plan, "D": "OP90"}))

    def test_fixed_trim_violation(self):
        self.assertIn("H5", self.kinds({**self.plan, "A": "OP10", "B": "OP80"}))

    def test_nested_bend_precedence_violation(self):
        self.assertIn("H6", self.kinds({**self.plan, "F": "OP80"}))

    def test_structured_violation_fields(self):
        result = self.engine.evaluate({**self.plan, "F": "OP80"})
        violation = next(v for v in result.to_dict()["violations"] if v["constraint_type"] == "H6")
        self.assertEqual(set(violation), {"constraint_type", "operation_ids", "station_ids", "message", "context"})
        self.assertEqual(violation["operation_ids"], ("F", "E"))
        self.assertEqual(violation["context"]["relation_source"], "BEND_REGION_PRECEDENCE")

    def test_no_area_distance_or_cad_parent_hard_precedence(self):
        c = context((Operation("P", "A", "BEND", cad_parent_operation_id="B"), Operation("P", "B", "BEND")),
                    (region("A", "RA", area=1000), region("B", "RB", area=1)))
        self.assertEqual(c.precedence_relations, ())
        self.assertTrue(HardConstraintEngine(c).evaluate({"A": "OP90", "B": "OP90"}).feasible)
