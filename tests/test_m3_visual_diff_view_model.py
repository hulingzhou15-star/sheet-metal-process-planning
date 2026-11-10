import unittest

from src.visualization.diff_view_model import build_diff_view_model
from visual_fixtures import actual, mini


class DiffViewTests(unittest.TestCase):
    def setUp(self):
        c,a,b,cfg,cat=mini();self.c,self.a,self.b,self.cat=c,a,b,cat
        self.diff=build_diff_view_model(c,a,b,cat)

    def test_changed_ids_exact_assignment_inequality(self):
        self.assertEqual(set(self.diff["changed_ids"]),{op for op in self.a if self.a[op]!=self.b[op]})
        self.assertEqual(self.diff["differing_operation_count"],3)

    def test_rank_delta_not_numeric_op_string_gap(self):
        rows={r["operation_id"]:r for r in self.diff["changed_operations"]}
        self.assertEqual(rows["B2"]["station_delta_B_minus_A"],-2);self.assertEqual(rows["C"]["station_delta_B_minus_A"],1)

    def test_bend_earlier_later_signs(self):
        self.assertEqual(self.diff["bend_A_earlier_count"],0);self.assertEqual(self.diff["bend_A_later_count"],1)
        self.assertIn("1 个 BEND 在 A 中比 B 更晚",self.diff["summary"][-1])

    def test_type_counts_and_transition_summary(self):
        self.assertEqual(sum(r["differing_operation_count"] for r in self.diff["operation_type_summary"]),3)
        self.assertEqual(sum(r["count"] for r in self.diff["station_transitions"]),3)

    def test_station_load_delta_conserves_operations(self):
        rows=self.diff["station_load_comparison"]
        self.assertEqual(sum(r["delta_B_minus_A"] for r in rows),0)
        self.assertEqual(sum(r["load_A"] for r in rows),12)

    def test_region_grouping_is_smallest_containing_only(self):
        group={r["region_group"]:r for r in self.diff["region_differences"]}
        self.assertIn("C",group["R_SMALL"]["operation_ids"])
        self.assertEqual(sum(r["changed_operation_count"] for r in group.values()),3)

    def test_outside_and_no_geometry_diff_groups(self):
        b={**self.a,"X":"OP10","M":"OP10"};diff=build_diff_view_model(self.c,self.a,b,self.cat)
        self.assertEqual({r["region_group"] for r in diff["region_differences"]},{"OUTSIDE_REGION","NO_GEOMETRY"})

    def test_identical_plan_no_quality_claim(self):
        diff=build_diff_view_model(self.c,self.a,self.a,self.cat)
        self.assertEqual(diff["differing_operation_count"],0);self.assertFalse(diff["changed_ids"])
        self.assertNotIn("更合理","".join(diff["summary"]))

    def test_all_twenty_four_diffs_match_frozen_orientations(self):
        c,_,_,_,comparisons,_,plans,_,pairs,_=actual()
        self.assertEqual(len(comparisons),24)
        for visual,pair in zip(comparisons,pairs):
            self.assertEqual((visual["comparison_id"],visual["blind_plan_A"],visual["blind_plan_B"]),(pair["comparison_id"],pair["blind_plan_A"],pair["blind_plan_B"]))
            a=plans[pair["internal_plan_A"]]["assignments"];b=plans[pair["internal_plan_B"]]["assignments"]
            self.assertEqual(set(visual["diff"]["changed_ids"]),{op for op in c.operations if a[op]!=b[op]})
