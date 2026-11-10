from dataclasses import replace
import unittest

from src.candidate.distance import PlanDistance, build_distance_buckets, distance_bucket, plan_hash
from src.candidate.generator import CandidatePlanGenerator
from src.constraints import FixedAssignment
from src.data.schema import Operation, Station
from m3_fixtures import context
from candidate_fixtures import movable_context, small_config


class CandidateDistanceTests(unittest.TestCase):
    def test_movable_set_uses_only_local_capability_and_fixed_assignment(self):
        c = context((Operation("P", "A", "TRIM"), Operation("P", "B", "IRREGULAR_PUNCH"), Operation("P", "C", "MARKING")),
                    stations=(Station("P", "S0", 10, frozenset({"TRIM", "IRREGULAR_PUNCH", "MARKING"})),
                              Station("P", "S1", 50, frozenset({"TRIM", "IRREGULAR_PUNCH"}))),
                    fixed=(FixedAssignment("A", "S0"),))
        self.assertEqual(PlanDistance.from_context(c).movable_operation_ids, ("B",))

    def test_raw_normalized_and_expert_zero(self):
        c = movable_context()
        d = PlanDistance.from_context(c)
        expert = {f"A{i}": "OP90" for i in range(4)}
        other = {**expert, "A0": "OP10", "A2": "OP05"}
        self.assertEqual(d.raw(expert, other), 2)
        self.assertEqual(d.normalized(expert, other), .5)
        self.assertEqual(d.raw(expert, c.expert_reference_plan), 0)

    def test_nonmovable_operation_ignored_in_distance(self):
        d = PlanDistance(("A", "B"), ("A",))
        self.assertEqual(d.raw({"A": "S0", "B": "S0"}, {"A": "S0", "B": "S1"}), 0)

    def test_empty_movable_set_stops_generation(self):
        c = movable_context(count=1, station_count=1)
        with self.assertRaisesRegex(ValueError, r"\|M\|=0"):
            CandidatePlanGenerator(c, small_config())
        with self.assertRaisesRegex(ValueError, "empty"):
            PlanDistance.from_context(c).normalized({"A0": "OP90"}, {"A0": "OP90"})

    def test_incomplete_and_duplicate_plan_distance_rejected(self):
        d = PlanDistance.from_context(movable_context(count=1))
        for invalid in ({}, [("A0", "OP90"), ("A0", "OP10")]):
            with self.assertRaises(ValueError):
                d.raw(invalid, {"A0": "OP90"})

    def test_bucket_ranges_cover_once_without_overlap(self):
        for maximum in range(101):
            buckets = build_distance_buckets(maximum, near_max_ratio=.25, medium_max_ratio=.55)
            covered = [d for r in buckets.values() for d in range(r["lower"], r["upper"]+1)]
            self.assertEqual(covered, list(range(1, maximum+1)))
            self.assertEqual(len(covered), len(set(covered)))

    def test_actual_p001_and_decimal_integer_boundaries(self):
        buckets = build_distance_buckets(79, near_max_ratio=.25, medium_max_ratio=.55)
        self.assertEqual([(r["lower"], r["upper"]) for r in buckets.values()], [(1, 19), (20, 43), (44, 79)])
        self.assertEqual(distance_bucket(20, buckets), "MEDIUM")
        self.assertEqual(build_distance_buckets(100, near_max_ratio=.25, medium_max_ratio=.55)["MEDIUM"]["upper"], 55)

    def test_small_maximum_has_explicit_empty_buckets(self):
        buckets = build_distance_buckets(1, near_max_ratio=.25, medium_max_ratio=.55)
        self.assertTrue(buckets["NEAR"]["empty"])
        self.assertTrue(buckets["MEDIUM"]["empty"])
        self.assertFalse(buckets["FAR"]["empty"])

    def test_hash_deterministic_order_independent_and_full_identity(self):
        self.assertEqual(plan_hash({"B": "S2", "A": "S1"}), plan_hash([("A", "S1"), ("B", "S2")]))
        self.assertNotEqual(plan_hash({"B": "S2", "A": "S1"}), plan_hash({"B": "S3", "A": "S1"}))

    def test_hash_rejects_duplicate_ids(self):
        with self.assertRaises(ValueError):
            plan_hash([("A", "S1"), ("A", "S2")])
