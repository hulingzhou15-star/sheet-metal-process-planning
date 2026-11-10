from collections import Counter
import unittest

from src.review.plan_diff import comparison_diff, internal_plan_diff, plan_view, type_name
from src.data.schema import Operation
from m3_fixtures import context
from review_fixtures import batch


class PlanDiffTests(unittest.TestCase):
    def test_comparison_only_assignment_differences_sorted_by_type_and_id(self):
        c, candidates = batch()[0], batch()[1]
        a, b = candidates[0]["assignments"], candidates[20]["assignments"]
        diff = comparison_diff(c, a, b)
        expected = {op for op in a if a[op] != b[op]}
        self.assertEqual({row["operation_id"] for row in diff["changed_operations"]}, expected)
        self.assertEqual(diff["differing_operation_count"], len(expected))
        keys = [(row["operation_type"], row["operation_id"]) for row in diff["changed_operations"]]
        self.assertEqual(keys, sorted(keys))

    def test_station_load_comparison_all_stations_and_correct_delta(self):
        c, a, b = batch()[0], batch()[2], batch()[1][0]["assignments"]
        diff = comparison_diff(c, a, b)
        self.assertEqual([r["station_id"] for r in diff["station_load_comparison"]], [f"OP{i}0" for i in range(1, 8)])
        for row in diff["station_load_comparison"]:
            self.assertEqual(row["load_A"], Counter(a.values())[row["station_id"]])
            self.assertEqual(row["load_B"], Counter(b.values())[row["station_id"]])
            self.assertEqual(row["delta_B_minus_A"], row["load_B"]-row["load_A"])
        self.assertEqual(sum(r["delta_B_minus_A"] for r in diff["station_load_comparison"]), 0)

    def test_internal_bend_metadata_and_levels_match_sheet5(self):
        c, expert, candidate = batch()[0], batch()[2], batch()[1][0]["assignments"]
        diff = internal_plan_diff(c, expert, candidate)
        by_op = {r.bend_operation_id: r for r in c.bend_influences}
        self.assertTrue(diff["bend_changes"])
        for row in diff["bend_changes"]:
            r = by_op[row["bend_operation_id"]]
            for field in ("bend_axis", "affected_depth", "bend_length", "affected_area_proxy", "influence_region_id", "parent_region_id"):
                self.assertEqual(row[field], getattr(r, field))
            self.assertEqual(row["region_level"], len(c.region_ancestors[r.influence_region_id]))

    def test_public_bend_diff_contains_only_actual_differences(self):
        c, candidates = batch()[0], batch()[1]
        diff = comparison_diff(c, candidates[0]["assignments"], candidates[20]["assignments"])
        expected = {r["operation_id"] for r in diff["changed_operations"] if r["operation_type"] == "BEND"}
        self.assertEqual({r["operation_id"] for r in diff["bend_changes"]}, expected)

    def test_internal_precedence_participation_is_factual(self):
        c, expert, candidate = batch()[0], batch()[2], batch()[1][0]["assignments"]
        diff = internal_plan_diff(c, expert, candidate)
        for row in diff["changed_operations"]:
            expected = {r.relation_source for r in c.precedence_relations if row["operation_id"] in (r.predecessor_id, r.operation_id)}
            self.assertEqual(set(row["precedence_sources"]), expected)
            for source in ("EXPLICIT_PREDECESSOR", "PROCESS_GROUP", "BEND_REGION_PRECEDENCE"):
                self.assertEqual(row[source], source in expected)

    def test_internal_rank_delta_uses_order_not_station_name(self):
        c = context((Operation("P", "A", "IRREGULAR_PUNCH"),))
        diff = internal_plan_diff(c, {"A": "OP90"}, {"A": "OP10"})
        self.assertEqual(diff["changed_operations"][0]["station_rank_delta"], 1)

    def test_plan_view_is_complete_and_type_station_matrix_reconciles(self):
        c, assignments = batch()[0], batch()[1][0]["assignments"]
        view = plan_view(c, assignments)
        self.assertEqual(len(view["operation_assignments"]), 224)
        self.assertEqual(len(view["bend_assignments"]), 59)
        self.assertEqual(sum(row["load"] for row in view["station_loads"]), 224)
        self.assertEqual(sum(sum(row["station_counts"].values()) for row in view["operation_type_station_matrix"]), 224)
        for row in view["operation_type_station_matrix"]:
            self.assertEqual(sum(row["station_counts"].values()), sum(type_name(op.operation_type) == row["operation_type"] for op in c.part_data.operations))

    def test_incomplete_plan_rejected_before_rendering(self):
        with self.assertRaises(ValueError):
            plan_view(batch()[0], {})
