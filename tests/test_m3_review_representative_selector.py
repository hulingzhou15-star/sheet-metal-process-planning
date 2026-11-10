from collections import Counter
from copy import deepcopy
import math
import statistics
import unittest

from src.review import select_representatives
from src.review.representative_selector import soft_value, station_load
from review_fixtures import batch, review_data


class RepresentativeTests(unittest.TestCase):
    def test_four_per_bucket(self):
        representatives = review_data()[0]
        self.assertEqual(Counter(r["bucket"] for r in representatives), {"NEAR": 4, "MEDIUM": 4, "FAR": 4})

    def test_representatives_are_unique(self):
        representatives = review_data()[0]
        self.assertEqual(len({r["internal_id"] for r in representatives}), 12)
        self.assertEqual(len({r["plan_hash"] for r in representatives}), 12)

    def test_low_soft_is_bucket_minimum(self):
        candidates = batch()[1]
        for r in review_data()[0]:
            if r["selection_role"] == "LOW_SOFT":
                best = min((c for c in candidates if c["expert_distance"]["bucket"] == r["bucket"]), key=lambda c: (soft_value(c), c["plan_hash"]))
                self.assertEqual(r["internal_id"], best["plan_id"])

    def test_typical_uses_remaining_nineteen_medians_and_scaled_euclidean_distance(self):
        candidates, config = batch()[1], batch()[4]
        records = review_data()[0]
        expected_ids = {"NEAR": "NEAR_005", "MEDIUM": "MEDIUM_011", "FAR": "FAR_004"}
        for r in records:
            if r["selection_role"] != "TYPICAL":
                continue
            low_id = next(p["internal_id"] for p in records if p["bucket"] == r["bucket"] and p["selection_role"] == "LOW_SOFT")
            remaining = [c for c in candidates if c["expert_distance"]["bucket"] == r["bucket"] and c["plan_id"] != low_id]
            ds, ss = [c["expert_distance"]["raw"] for c in remaining], [soft_value(c) for c in remaining]
            audit = r["selection_audit"]
            self.assertEqual(audit["source_count"], 19)
            self.assertEqual(audit["median_distance"], statistics.median(ds))
            self.assertEqual(audit["median_soft"], statistics.median(ss))
            expected_scores = {c["plan_id"]: math.hypot((c["expert_distance"]["raw"]-statistics.median(ds))/max(1, max(ds)-min(ds)),
                                                        (soft_value(c)-statistics.median(ss))/max(config["representatives"]["typical_epsilon"], max(ss)-min(ss))) for c in remaining}
            self.assertEqual(r["internal_id"], expected_ids[r["bucket"]])
            self.assertEqual(expected_scores[r["internal_id"]], min(expected_scores.values()))

    def test_high_op60_load_maximum_then_low_soft(self):
        records, candidates = review_data()[0], batch()[1]
        for r in records:
            if r["selection_role"] == "HIGH_OP60_LOAD":
                excluded = {p["internal_id"] for p in records if p["bucket"] == r["bucket"] and p["selection_role"] in ("LOW_SOFT", "TYPICAL")}
                remaining = [c for c in candidates if c["expert_distance"]["bucket"] == r["bucket"] and c["plan_id"] not in excluded]
                best = min(remaining, key=lambda c: (-station_load(c, "OP60"), soft_value(c), c["plan_hash"]))
                self.assertEqual(r["internal_id"], best["plan_id"])
                self.assertEqual(r["op60_load"], 7)

    def test_diversity_is_max_min_of_preceding_three_roles(self):
        records, candidates, distance = review_data()[0], batch()[1], batch()[3]
        by_id = {c["plan_id"]: c for c in candidates}
        for r in records:
            if r["selection_role"] == "DIVERSITY":
                anchors = [p["internal_id"] for p in records if p["bucket"] == r["bucket"] and p["selection_role"] != "DIVERSITY"]
                values = {c["plan_id"]: min(distance.normalized(c["assignments"], by_id[a]["assignments"]) for a in anchors)
                          for c in candidates if c["expert_distance"]["bucket"] == r["bucket"] and c["plan_id"] not in anchors}
                self.assertEqual(values[r["internal_id"]], max(values.values()))
                self.assertEqual(r["selection_audit"]["against_internal_ids"], anchors)

    def test_input_not_mutated_and_reordering_is_deterministic(self):
        candidates, distance, config = batch()[1], batch()[3], batch()[4]
        before = deepcopy(candidates)
        first = select_representatives(candidates, distance, config)[0]
        second = select_representatives(list(reversed(candidates)), distance, config)[0]
        self.assertEqual([r["internal_id"] for r in first], [r["internal_id"] for r in second])
        self.assertEqual(candidates, before)

    def test_zero_soft_range_uses_epsilon(self):
        candidates = deepcopy(batch()[1])
        for c in candidates:
            c["soft"]["total_soft_penalty"] = .1
        reps, audit = select_representatives(candidates, batch()[3], batch()[4])
        for r in reps:
            if r["selection_role"] == "TYPICAL":
                self.assertEqual(r["selection_audit"]["soft_range"], 0)
                self.assertTrue(all(math.isfinite(p["median_point_distance"]) for p in r["selection_audit"]["candidate_scores"]))

    def test_insufficient_bucket_is_not_fabricated(self):
        candidates = [c for c in batch()[1] if c["expert_distance"]["bucket"] != "NEAR"]+batch()[1][:3]
        with self.assertRaisesRegex(ValueError, "lacks four"):
            select_representatives(candidates, batch()[3], batch()[4])
