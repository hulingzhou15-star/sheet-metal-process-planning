from copy import deepcopy
import unittest

from src.candidate.distance import PlanDistance
from src.candidate.selector import farthest_first, select_candidates, soft_prefilter
from candidate_fixtures import record


class CandidateSelectorTests(unittest.TestCase):
    def setUp(self):
        self.distance = PlanDistance(tuple(f"A{i}" for i in range(4)), tuple(f"A{i}" for i in range(4)))
        self.candidates = [record((1, 0, 0, 0), .1, identifier="a"), record((1, 1, 0, 0), .2, identifier="b"),
                           record((0, 1, 1, 1), .3, identifier="c")]

    def test_prefilter_retains_correct_fraction_with_ceil(self):
        records = [record(tuple(int(i == j) for j in range(10)), i/10, identifier=str(i)) for i in range(10)]
        chosen = soft_prefilter(records, .7)
        self.assertEqual(len(chosen), 7)
        self.assertEqual([c["plan_id"] for c in chosen], [str(i) for i in range(7)])
        self.assertEqual(len(soft_prefilter(records[:5], .7)), 4)

    def test_prefilter_decimal_rounding_not_accidental_extra_item(self):
        records = [{"plan_hash": str(i), "soft": {"total_soft_penalty": i}} for i in range(50)]
        self.assertEqual(len(soft_prefilter(records, .14)), 7)

    def test_farthest_first_starts_lowest_penalty(self):
        self.assertEqual(farthest_first(self.candidates, 1, self.distance)[0]["plan_id"], "a")

    def test_farthest_first_selects_max_min_not_second_lowest_penalty(self):
        self.assertEqual([c["plan_id"] for c in farthest_first(self.candidates, 2, self.distance)], ["a", "c"])

    def test_distance_tie_prefers_lower_penalty(self):
        other = record((1, 0, 1, 0), .15, identifier="d")
        selected = farthest_first([*self.candidates[:2], other], 2, self.distance)
        self.assertEqual(selected[1]["plan_id"], "d")

    def test_equal_distance_and_penalty_tie_uses_hash(self):
        other = record((1, 0, 1, 0), .2, identifier="d")
        expected = min((self.candidates[1], other), key=lambda c: c["plan_hash"])
        selected = farthest_first([*self.candidates[:2], other], 2, self.distance)
        self.assertEqual(selected[1]["plan_hash"], expected["plan_hash"])

    def test_selection_deterministic_after_input_reordering(self):
        first = farthest_first(self.candidates, 3, self.distance)
        second = farthest_first(list(reversed(self.candidates)), 3, self.distance)
        self.assertEqual(first, second)

    def test_short_bucket_redistributes_without_copying_or_leaving_prefilter(self):
        pool = [record((1, 0, 0, 0), .1, bucket="NEAR")]
        pool += [record(bits, score, bucket="MEDIUM") for bits, score in
                 [((1, 1, 0, 0), .1), ((1, 0, 1, 0), .2), ((1, 0, 0, 1), .3)]]
        pool += [record(bits, score, bucket="FAR") for bits, score in
                 [((1, 1, 1, 0), .1), ((1, 1, 0, 1), .2), ((1, 0, 1, 1), .3)]]
        selected, audit = select_candidates(pool, self.distance, target_per_bucket=2, soft_prefilter_fraction=1)
        self.assertEqual(len(selected), 6)
        self.assertEqual([audit["buckets"][b]["selected_count"] for b in ("NEAR", "MEDIUM", "FAR")], [1, 3, 2])
        self.assertEqual(len({c["plan_hash"] for c in selected}), 6)
        self.assertTrue(audit["redistribution"])
        self.assertIn("MEDIUM_003", [c["plan_id"] for c in selected])

    def test_diagnostic_fields_do_not_affect_selection(self):
        before = farthest_first(self.candidates, 2, self.distance)
        modified = deepcopy(self.candidates)
        for i, candidate in enumerate(modified):
            candidate["diagnostics"] = {"D1": 1000*i, "D3": -1000*i}
        after = farthest_first(modified, 2, self.distance)
        self.assertEqual([c["plan_hash"] for c in before], [c["plan_hash"] for c in after])

    def test_duplicate_pool_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            select_candidates([self.candidates[0], self.candidates[0]], self.distance, target_per_bucket=1, soft_prefilter_fraction=.7)
