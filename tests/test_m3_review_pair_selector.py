from collections import Counter
from copy import deepcopy
import unittest

from src.review.pair_selector import select_pairs, unordered_pair
from review_fixtures import batch, review_data


class PairSelectorTests(unittest.TestCase):
    def test_twenty_four_comparisons(self):
        self.assertEqual(len(review_data()[4]), 24)

    def test_global_unordered_pair_uniqueness(self):
        pairs = review_data()[4]
        self.assertEqual(len({unordered_pair(p["internal_plan_1"], p["internal_plan_2"]) for p in pairs}), 24)

    def test_group_a_reference_low_soft_and_diversity_two_each_bucket(self):
        reps, pairs = review_data()[0], review_data()[4]
        group = [p for p in pairs if p["group"] == "A"]
        self.assertEqual(len(group), 6)
        expected = {r["internal_id"] for r in reps if r["selection_role"] in ("LOW_SOFT", "DIVERSITY")}
        self.assertEqual({p["internal_plan_2"] for p in group}, expected)
        self.assertTrue(all(p["internal_plan_1"] == "EXPERT_REFERENCE" for p in group))

    def test_group_b_six_prescribed_role_pairs(self):
        reps, pairs = review_data()[0], review_data()[4]
        roles = {(r["bucket"], r["selection_role"]): r["internal_id"] for r in reps}
        expected = {unordered_pair(roles[b, first], roles[b, second]) for b in ("NEAR", "MEDIUM", "FAR")
                    for first, second in (("LOW_SOFT", "HIGH_OP60_LOAD"), ("TYPICAL", "DIVERSITY"))}
        self.assertEqual({unordered_pair(p["internal_plan_1"], p["internal_plan_2"]) for p in pairs if p["group"] == "B"}, expected)

    def test_group_c_six_greedy_nearest_soft_pairs(self):
        reps, plans, pairs = review_data()[0], review_data()[2], review_data()[4]
        group = [p for p in pairs if p["group"] == "C"]
        self.assertEqual(len(group), 6)
        for a, b in (("NEAR", "MEDIUM"), ("MEDIUM", "FAR")):
            scores = sorted(abs(plans[x["internal_id"]]["soft"]["total_soft_penalty"]-plans[y["internal_id"]]["soft"]["total_soft_penalty"])
                            for x in reps if x["bucket"] == a for y in reps if y["bucket"] == b)
            actual = sorted(p["soft_absolute_difference"] for p in group if p["subgroup"] == f"{a}/{b}")
            self.assertEqual(actual, scores[:3])

    def test_group_d_six_challenge_pairs_and_initial_thresholds(self):
        pairs, audit = review_data()[4], review_data()[5]
        group = [p for p in pairs if p["group"] == "D"]
        self.assertEqual(len(group), 6)
        self.assertEqual(sorted(Counter(p["subgroup"] for p in group).values()), [3, 3])
        for p in group:
            if p["subgroup"].startswith("SIMILAR_EXPERT"):
                self.assertLessEqual(p["expert_distance_absolute_difference"], 5)
            else:
                self.assertLessEqual(p["soft_absolute_difference"], .01)
        self.assertTrue(all(not row["relaxed"] for row in audit["challenge_design"]))

    def test_group_d_exact_actual_challenge_sets(self):
        pairs = review_data()[4]
        large_soft = {unordered_pair(p["internal_plan_1"], p["internal_plan_2"]) for p in pairs if p["group"] == "D" and p["subgroup"].startswith("SIMILAR_EXPERT")}
        large_distance = {unordered_pair(p["internal_plan_1"], p["internal_plan_2"]) for p in pairs if p["group"] == "D" and p["subgroup"].startswith("SIMILAR_SOFT")}
        self.assertEqual(large_soft, {unordered_pair("FAR_001", "FAR_004"), unordered_pair("MEDIUM_001", "MEDIUM_015"), unordered_pair("FAR_001", "FAR_005")})
        self.assertEqual(large_distance, {unordered_pair("NEAR_002", "FAR_010"), unordered_pair("NEAR_005", "FAR_001"), unordered_pair("NEAR_005", "FAR_010")})

    def test_challenge_relaxation_is_explicit_and_recorded(self):
        config = deepcopy(batch()[4])
        config["pairs"].update(challenge_distance_initial=0, challenge_soft_initial=1e-12)
        pairs, audit = select_pairs(review_data()[0], review_data()[2], batch()[3], config=config)
        self.assertEqual(len(pairs), 24)
        self.assertTrue(all(row["relaxed"] for row in audit["challenge_design"]))
        self.assertTrue(all(row["final_threshold"] > row["initial_threshold"] for row in audit["challenge_design"]))
