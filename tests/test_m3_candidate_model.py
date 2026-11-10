from itertools import product
import unittest

from src.candidate.cp_sat_model import build_cp_sat_model, derived_seed, solve_model
from src.candidate.generator import CandidatePlanGenerator
from src.constraints import FixedAssignment, HardConstraintEngine
from src.data.schema import Operation, Station
from m3_fixtures import context, region
from candidate_fixtures import movable_context, small_config


def force(cp, pairs):
    pairs = list(pairs.items()) if isinstance(pairs, dict) else list(pairs)
    if any(op not in cp.operation_ids for op, station in pairs):
        cp.model.add(False)
    for op in cp.operation_ids:
        values = [station for assigned_op, station in pairs if assigned_op == op]
        if len(values) != 1:
            cp.model.add(False)
        for (assigned_op, station), variable in cp.assignment_variables.items():
            if assigned_op == op:
                cp.model.add(variable == int(station in values))


class CandidateModelTests(unittest.TestCase):
    def setUp(self):
        ops = (Operation("P", "T", "TRIM"), Operation("P", "A", "IRREGULAR_PUNCH", required_predecessor_ids=("T",)),
               Operation("P", "P", "PRE_PUNCH", process_group_id="PG"), Operation("P", "F", "FLANGING", process_group_id="PG"),
               Operation("P", "B", "BEND"), Operation("P", "C", "BEND"))
        stations = (Station("P", "OP90", 10, frozenset({"TRIM", "IRREGULAR_PUNCH", "PRE_PUNCH", "BEND"})),
                    Station("P", "OP10", 30, frozenset({"TRIM", "IRREGULAR_PUNCH", "PRE_PUNCH", "FLANGING", "BEND"})),
                    Station("P", "OP05", 90, frozenset({"IRREGULAR_PUNCH", "FLANGING", "BEND"})))
        self.plan = {"T": "OP90", "A": "OP10", "P": "OP90", "F": "OP10", "B": "OP05", "C": "OP10"}
        self.context = context(ops, (region("B", "RB"), region("C", "RC", parent="RB")), stations=stations,
                               assignments=self.plan, fixed=(FixedAssignment("T", "OP90"),))

    def test_model_and_frozen_h1_h6_agree_on_named_cases(self):
        cases = [("expert", self.plan, None), ("missing", {k: v for k, v in self.plan.items() if k != "A"}, "H1"),
                 ("duplicate", [*self.plan.items(), ("T", "OP10")], "H1"),
                 ("invalid Station", {**self.plan, "A": "missing"}, "H1"),
                 ("capability", {**self.plan, "P": "OP05"}, "H2"),
                 ("explicit", {**self.plan, "A": "OP90"}, "H3"),
                 ("group", {**self.plan, "P": "OP10"}, "H4"),
                 ("fixed", {**self.plan, "T": "OP10", "A": "OP05"}, "H5"),
                 ("parent", {**self.plan, "C": "OP05"}, "H6")]
        for name, plan, expected in cases:
            with self.subTest(case=name):
                hard = HardConstraintEngine(self.context).evaluate(plan)
                cp = build_cp_sat_model(self.context, apply_station_overload_guardrail=False)
                force(cp, plan)
                result = solve_model(cp, seed=1, timeout_seconds=1)
                self.assertEqual(result["assignments"] is not None, hard.feasible)
                if expected:
                    self.assertIn(expected, {v.constraint_type for v in hard.violations})

    def test_exhaustive_small_nested_model_has_no_extra_process_constraints(self):
        c = context((Operation("P", "A", "BEND", cad_parent_operation_id="B"), Operation("P", "B", "BEND"), Operation("P", "C", "BEND")),
                    (region("A", "RA", area=1000), region("B", "RB", area=1), region("C", "RC", parent="RB")),
                    stations=self.context.part_data.stations,
                    assignments={"A": "OP90", "B": "OP05", "C": "OP10"})
        for values in product(("OP90", "OP10", "OP05"), repeat=3):
            plan = dict(zip(("A", "B", "C"), values))
            cp = build_cp_sat_model(c, apply_station_overload_guardrail=False)
            force(cp, plan)
            result = solve_model(cp, seed=1, timeout_seconds=1)
            self.assertEqual(result["assignments"] is not None, HardConstraintEngine(c).evaluate(plan).feasible)

    def test_generated_model_solution_is_independently_feasible(self):
        cp = build_cp_sat_model(self.context)
        result = solve_model(cp, seed=3, timeout_seconds=1)
        self.assertTrue(HardConstraintEngine(self.context).evaluate(result["assignments"]).feasible)

    def test_g1_enabled_and_ablation_change_domain_only(self):
        c = movable_context(count=12)
        plan = {f"A{i}": "OP10" if i < 6 else "OP90" for i in range(12)}
        self.assertTrue(HardConstraintEngine(c).evaluate(plan).feasible)
        for enabled in (True, False):
            cp = build_cp_sat_model(c, apply_station_overload_guardrail=enabled)
            force(cp, plan)
            result = solve_model(cp, seed=1, timeout_seconds=1)
            self.assertEqual(result["assignments"] is not None, not enabled)

    def test_maximum_distance_optimal_and_guardrail_limits(self):
        g = CandidatePlanGenerator(movable_context(count=12), small_config())
        result = g.maximum_expert_distance()
        self.assertEqual((result["solver_status"], result["D_max"]), ("OPTIMAL", 10))

    def test_expert_no_movement_after_full_constraints_reports_zero_maximum(self):
        cp = build_cp_sat_model(self.context)
        force(cp, self.plan)
        cp.model.maximize(cp.expert_distance)
        result = solve_model(cp, seed=1, timeout_seconds=1)
        self.assertEqual(result["expert_distance_raw"], 0)

    def test_no_good_forbids_full_assignment(self):
        c = movable_context(count=1, station_count=2)
        cp = build_cp_sat_model(c)
        cp.forbid_plan({"A0": "OP90"})
        result = solve_model(cp, seed=1, timeout_seconds=1)
        self.assertEqual(result["assignments"], {"A0": "OP10"})

    def test_derived_seed_deterministic_and_phase_specific(self):
        self.assertEqual(derived_seed(42, "NEAR", 1), derived_seed(42, "NEAR", 1))
        self.assertNotEqual(derived_seed(42, "NEAR", 1), derived_seed(42, "FAR", 1))
