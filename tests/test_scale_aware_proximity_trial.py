import math
from pathlib import Path
import json
import unittest

from scripts.try_scale_aware_proximity import circular_profile, measure_pair, within_gate, relative_candidates, build_trial
from src.graph.geometry_primitives import Arc, Line
from src.inference.input import load_inference_context

ROOT = Path(__file__).resolve().parents[1]


class ProximityGeometryTests(unittest.TestCase):
    def test_outer_recorded_diameter_not_prehole_diameter(self):
        a = circular_profile((Arc((0, 0), 1.375), Arc((0, 0), 1.9)))
        self.assertEqual(a["diameter"], 3.8)
        self.assertEqual(circular_profile((Arc((0, 0), .7),))["diameter"], 1.4)

    def test_nonconcentric_nonfull_or_zero_circle_not_guessed(self):
        self.assertIsNone(circular_profile((Arc((0, 0), 1), Arc((1, 0), 1))))
        self.assertIsNone(circular_profile((Arc((0, 0), 1, 0, math.pi),)))
        self.assertIsNone(circular_profile((Arc((0, 0), 0),)))
        self.assertIsNone(circular_profile((Line((0, 0), (1, 1)),)))

    def test_circle_center_is_used_instead_of_boundary(self):
        a, b = {"center": [0, 0], "diameter": 2}, {"center": [9, 0], "diameter": 10}
        measured = measure_pair(a, b, 3)
        self.assertEqual(measured["measurement"], 9)
        self.assertFalse(within_gate(measured["measurement"], a["diameter"], 3))
        self.assertTrue(within_gate(measured["measurement"], b["diameter"], 3))

    def test_noncircle_uses_boundary_and_missing_does_not_become_zero(self):
        a = {"center": [0, 0], "diameter": 2}
        self.assertEqual(measure_pair(a, None, 4)["measurement"], 4)
        self.assertIsNone(measure_pair(a, None, None)["measurement"])
        self.assertFalse(within_gate(None, 2, 3))

    def test_threshold_inclusive_and_no_forced_minimum_or_maximum_count(self):
        self.assertTrue(within_gate(6, 2, 3))
        self.assertTrue(within_gate(11.4, 3.8, 3))
        self.assertTrue(within_gate(11.4 + 1e-12, 3.8, 3))
        self.assertFalse(within_gate(6.001, 2, 3))
        for diameter, multiple in [(0, 3), (2, -1), (2, math.inf)]:
            with self.assertRaises(ValueError):
                within_gate(1, diameter, multiple)

    def test_relative_analysis_does_not_cut_off_own_prehole_or_other_neighbors(self):
        rows = [dict(target_id="A", boundary_distance=.675), dict(target_id="B", boundary_distance=4)]
        result = relative_candidates(rows, 3)
        self.assertEqual(result["ordered_ids"], ["A", "B"])
        self.assertEqual(result["candidate_cuts"][0]["after_rank"], 1)
        self.assertFalse(result["final_relative_cut_selected"])

    def test_relative_zero_and_ties_not_divided_or_split(self):
        rows = [dict(target_id="B", boundary_distance=0), dict(target_id="A", boundary_distance=0), dict(target_id="C", boundary_distance=2)]
        result = relative_candidates(rows, 3)
        self.assertEqual(result["ordered_ids"], ["A", "B", "C"])
        self.assertEqual(result["candidate_cuts"], [])
        self.assertEqual(relative_candidates([], 3)["ordered_ids"], [])


class P001TrialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = load_inference_context(ROOT / "data/parts/P001/钣金加工数据表_P001.xlsx")
        matrix = json.loads((ROOT / "outputs/relative_proximity_audit/distance_matrix.json").read_text(encoding="utf-8"))
        config = json.loads((ROOT / "configs/relative_proximity_trial.json").read_text(encoding="utf-8"))
        cls.trial, _ = build_trial(cls.context, matrix, config)

    def scenario(self, op, multiple=3):
        anchor = next(a for a in self.trial["anchors"] if a["anchor_id"] == op)
        return next(s for s in anchor["scenarios"] if s["size_multiple"] == multiple)

    def test_o184_only_own_prehole(self):
        s = self.scenario("O184")
        self.assertEqual(s["passed_ids"], ["O185"])
        self.assertTrue(s["only_own_pre_punch"])

    def test_o066_keeps_confirmed_four(self):
        self.assertEqual(self.scenario("O066")["passed_ids"], ["O067", "O025", "O200", "O065"])

    def test_confirmed_flange_irregular_neighbors_retained(self):
        for a in ("O015", "O031", "O035"):
            self.assertIn("O196", self.scenario(a)["passed_ids"])

    def test_simple_hole_not_active_anchor_but_target_records_exist(self):
        self.assertNotIn("O186", [a["anchor_id"] for a in self.trial["anchors"]])
        self.assertEqual(len([r for r in self.trial["pair_records"] if r["target_id"] == "O186"]), 46)

    def test_numeric_multiplier_keys_agree_with_browser_number_strings(self):
        row = next(r for r in self.trial["pair_records"] if r["anchor_id"] == "O184" and r["target_id"] == "O185")
        self.assertEqual(set(row["passes"]), {"2", "3", "4"})
        self.assertTrue(row["passes"]["3"])

    def test_empty_and_more_than_five_are_not_filled_or_truncated(self):
        summary = next(s for s in self.trial["summary"] if s["size_multiple"] == 3)
        self.assertIn(6, summary["count_histogram"])
        self.assertEqual(summary["count_histogram"][6], 2)
        self.assertEqual(len(self.trial["pair_records"]), 46 * 223)

    def test_no_expert_access_or_station_assignments(self):
        with self.assertRaises(AttributeError):
            _ = self.context.expert_reference_plan
        text = json.dumps(self.trial)
        self.assertNotIn('"expert_station"', text)
        self.assertNotIn('"assigned_station"', text)


if __name__ == "__main__":
    unittest.main()
