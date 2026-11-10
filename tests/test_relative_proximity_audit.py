"""Numerical boundary cases for the separate exploratory audit."""
import unittest
from scripts.analyze_relative_proximity import adjacent_gap, ranked_neighbors, threshold_candidates


class RelativeProximityAuditTests(unittest.TestCase):
    def test_zero_and_ties_are_not_ratio_boundaries(self):
        self.assertEqual(adjacent_gap(0, 0), (None, "BOTH_ZERO"))
        self.assertEqual(adjacent_gap(0, 2), (None, "ZERO_TO_POSITIVE"))
        self.assertEqual(adjacent_gap(2, 2), (None, "EQUAL_DISTANCE_BUCKET"))
        self.assertEqual(adjacent_gap(2, 6), (3, "FINITE"))

    def test_missing_geometry_and_self_not_ranked(self):
        ids = ["A", "B", "C", "D"]
        matrix = [[0, 2, 2, None], [2, 0, 1, None], [2, 1, 0, None], [None]*4]
        rows = ranked_neighbors(ids, matrix)
        self.assertEqual(len(rows), 6)
        self.assertEqual([r["neighbor_id"] for r in rows if r["operation_id"] == "A"], ["B", "C"])
        self.assertFalse(any(r["operation_id"] == "D" for r in rows))

    def test_o066_distances_show_threshold_sensitivity(self):
        distances = [.675, 1.5, 2.1, 4.7, 39.755596655]
        rows = []
        for rank, (a, b) in enumerate(zip(distances, distances[1:]), 1):
            ratio, status = adjacent_gap(a, b)
            rows.append({"operation_id": "O066", "rank": rank, "next_ratio": ratio, "gap_status": status})
        results = threshold_candidates(rows, [2, 3, 4], 5)
        self.assertEqual(results[0]["candidate_cut_ranks"], [1, 3, 4])
        self.assertEqual(results[1]["candidate_cut_ranks"], [4])
        self.assertEqual(results[2]["candidate_cut_ranks"], [4])

    def test_zero_transitions_do_not_become_infinite_candidates(self):
        rows = [dict(operation_id="A", rank=1, next_ratio=None, gap_status="BOTH_ZERO"),
                dict(operation_id="A", rank=2, next_ratio=None, gap_status="ZERO_TO_POSITIVE"),
                dict(operation_id="A", rank=3, next_ratio=3, gap_status="FINITE")]
        result = threshold_candidates(rows, [3, 4], 2)
        self.assertEqual(result[0]["candidate_cut_ranks"], [3])
        self.assertEqual(result[1]["candidate_cut_ranks"], [])
        self.assertEqual(result[0]["zero_to_positive_ranks"], [2])

    def test_parallel_distance_group_does_not_depend_on_raw_noise(self):
        self.assertEqual(adjacent_gap(1.0, 1.0+1e-11)[1], "EQUAL_DISTANCE_BUCKET")


if __name__ == "__main__":
    unittest.main()
