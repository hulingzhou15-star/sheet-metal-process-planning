import json
import math
from pathlib import Path
import random
import unittest

from src.graph.geometry_distance import primitive_distance, primitives_distance, point_arc_distance, point_line_distance
from src.graph.geometry_primitives import Arc, Line
from src.proximity_review.witness import closest_witness, bend_pair_features, project_point, distance_profile
from src.proximity_review.analysis import family_for, existing_relations, validate_config
from src.data.schema import Operation

ROOT = Path(__file__).resolve().parents[1]


class WitnessTests(unittest.TestCase):
    def check_pair(self, a, b):
        result = closest_witness((a,), (b,))
        self.assertAlmostEqual(result["distance"], primitive_distance(a, b), places=8)
        self.assertAlmostEqual(result["distance"], math.dist(result["point_a"], result["point_b"]), places=10)
        for point, shape in ((result["point_a"], a), (result["point_b"], b)):
            distance = point_line_distance(point, shape) if isinstance(shape, Line) else point_arc_distance(point, shape)
            self.assertLess(distance, 1e-7)
        self.assertAlmostEqual(result["distance"], closest_witness((b,), (a,))["distance"], places=8)
        return result

    def test_line_crossing_parallel_overlapping_and_degenerate(self):
        pairs = [(Line((0, 0), (4, 0)), Line((2, -1), (2, 1))),
                 (Line((0, 0), (4, 0)), Line((1, 2), (3, 2))),
                 (Line((0, 0), (4, 0)), Line((1, 0), (8, 0))),
                 (Line((0, 0), (0, 0)), Line((3, 0), (4, 0)))]
        for a, b in pairs:
            self.check_pair(a, b)

    def test_finite_bend_projection_segment_and_endpoint(self):
        line = Line((0, 0), (10, 0))
        self.assertEqual(project_point((3, 2), line)["location"], "INTERIOR")
        result = project_point((15, 2), line)
        self.assertEqual(result["location"], "END")
        self.assertEqual(result["unclamped_parameter"], 1.5)
        self.assertEqual(result["point"], (10, 0))

    def test_line_circle_crossing_tangent_and_arc_restrictions(self):
        for line, arc in [(Line((-3, 0), (3, 0)), Arc((0, 0), 2)),
                          (Line((-3, 2), (3, 2)), Arc((0, 0), 2)),
                          (Line((4, 0), (4, 0)), Arc((0, 0), 2)),
                          (Line((0, -3), (0, -1)), Arc((0, 0), 2, 0, math.pi)),
                          (Line((-3, 4), (3, 4)), Arc((0, 0), 2, 0, math.pi)),
                          (Line((0, 0), (4, 0)), Arc((2, 1), 0))]:
            self.check_pair(line, arc)

    def test_circle_pairs_crossing_nested_concentric_and_separate(self):
        for a, b in [(Arc((0, 0), 2), Arc((3, 0), 2)), (Arc((0, 0), 2), Arc((4, 0), 2)),
                     (Arc((0, 0), 2), Arc((0, 0), 1)), (Arc((0, 0), 3), Arc((1, 0), 1)),
                     (Arc((0, 0), 2), Arc((8, 0), 3)), (Arc((0, 0), 0), Arc((2, 0), 1))]:
            self.check_pair(a, b)

    def test_concentric_arcs_and_signed_sweeps(self):
        self.check_pair(Arc((0, 0), 2, 0, math.pi/2), Arc((0, 0), 1, math.pi, math.pi/2))
        self.check_pair(Arc((0, 0), 2, 0, math.pi/2), Arc((0, 0), 1, math.pi/4, math.pi/2))
        self.check_pair(Arc((0, 0), 2, 0, math.pi/2), Arc((0, 0), 2, math.pi/2, -math.pi/2))

    def test_randomized_witness_matches_authoritative_distances_and_lies_on_curves(self):
        rng = random.Random(20261016)
        def shape():
            if rng.random() < .4:
                return Line((rng.uniform(-20, 20), rng.uniform(-20, 20)), (rng.uniform(-20, 20), rng.uniform(-20, 20)))
            return Arc((rng.uniform(-20, 20), rng.uniform(-20, 20)), rng.uniform(.1, 10), rng.uniform(-math.pi, math.pi), rng.choice([-1, 1])*rng.uniform(.1, 2*math.pi))
        for _ in range(160):
            self.check_pair(shape(), shape())

    def test_geometry_union_indexes_preserve_closest_source_primitive(self):
        a = (Line((0, 0), (1, 0)), Line((10, 0), (20, 0)))
        b = (Arc((15, 3), 1),)
        w = closest_witness(a, b)
        self.assertEqual(w["primitive_index_a"], 1)
        self.assertAlmostEqual(w["distance"], primitives_distance(a, b))

    def test_bend_angle_and_projected_overlap_are_observations(self):
        f = bend_pair_features(Line((0, 0), (10, 0)), Line((5, 2), (15, 2)))
        self.assertEqual(f["acute_angle_degrees"], 0)
        self.assertEqual(f["projection_on_a"]["overlap_length"], 5)
        self.assertEqual(f["projection_on_a"]["overlap_fraction_of_source"], .5)
        g = bend_pair_features(Line((0, 0), (10, 0)), Line((5, -1), (5, 1)))
        self.assertEqual(g["acute_angle_degrees"], 90)
        self.assertEqual(g["projection_on_a"]["overlap_length"], 0)

    def test_sample_profile_not_substitute_for_exact_distance(self):
        a = (Line((0, 0), (100, 0)),)
        b = (Line((0, 1), (0, 2)),)
        profile = distance_profile(a, b, 24)
        self.assertGreater(profile["sampled_min"], primitives_distance(a, b))
        self.assertGreater(profile["q50"], profile["q10"])
        self.assertEqual(profile["sample_count"], 24)


class PolicyTests(unittest.TestCase):
    def test_family_definitions(self):
        circ = ["FLANGING", "PRE_PUNCH", "CIRCLE_PUNCH"]
        self.assertEqual(family_for("BEND", "FLANGING", circ), "BEND_CIRCULAR")
        self.assertEqual(family_for("IRREGULAR_PUNCH", "FLANGING", circ), "IRREGULAR_CIRCULAR")
        self.assertEqual(family_for("BEND", "IRREGULAR_PUNCH", circ), "BEND_IRREGULAR")
        self.assertEqual(family_for("BEND", "BEND", circ), "BEND_BEND")
        self.assertEqual(family_for("IRREGULAR_PUNCH", "IRREGULAR_PUNCH", circ), "IRREGULAR_IRREGULAR")
        self.assertEqual(family_for("BEND", "DIMPLE", circ), "PENDING_TYPE_POLICY")

    def test_only_explicit_same_process_group_prehole_is_internal(self):
        ops = {"A": Operation("P", "A", "FLANGING", process_group_id="G"),
               "B": Operation("P", "B", "PRE_PUNCH", process_group_id="G"),
               "C": Operation("P", "C", "IRREGULAR_PUNCH", cad_parent_operation_id="A")}
        self.assertTrue(existing_relations("A", "B", ops, {})["internal_pre_punch_flanging"])
        relation = existing_relations("A", "C", ops, {})
        self.assertFalse(relation["internal_pre_punch_flanging"])
        self.assertEqual(relation["cad_parent_relations"], [{"child": "C", "parent": "A"}])

    def test_null_thresholds_remain_null_and_invalid_config_fails(self):
        config = json.loads((ROOT / "configs/proximity_geometry_review.json").read_text(encoding="utf-8"))
        validate_config(config)
        self.assertTrue(all(v is None for v in config["absolute_thresholds"].values()))
        config["absolute_thresholds"]["BEND_IRREGULAR"] = -1
        with self.assertRaises(ValueError):
            validate_config(config)


class SavedP001EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((ROOT / "outputs/proximity_geometry_review/geometry_evidence.json").read_text(encoding="utf-8"))
        cls.pairs = {r["pair_id"]: r for r in cls.data["pairs"]}

    def test_all_operations_have_explicit_state_without_claiming_no_need(self):
        self.assertEqual(len(self.data["operations"]), 224)
        self.assertEqual(sum(o["has_geometry"] for o in self.data["operations"].values()), 211)
        self.assertTrue(all(o["need_for_process_neighbor"] == "NOT_DETERMINED_BY_GEOMETRY_ALONE" for o in self.data["operations"].values()))
        self.assertFalse(self.data["all_operations_have_final_neighbor_policy"])

    def test_bends_and_irregulars_both_have_complete_pair_evidence(self):
        for kind, count in (("BEND", 59), ("IRREGULAR_PUNCH", 38)):
            rows = [o for o in self.data["operations"].values() if o["operation_type"] == kind]
            self.assertEqual(len(rows), count)
            self.assertTrue(all(o["pair_count"] == 210 for o in rows))

    def test_all_unordered_geometry_pairs_unique(self):
        self.assertEqual(len(self.data["pairs"]), 211 * 210 // 2)
        self.assertEqual(len(self.pairs), len(self.data["pairs"]))

    def test_o066_bend_projection_and_new_metric_explicit(self):
        p = self.pairs["O025__O066"]
        g = p["gates"][0]
        self.assertEqual(g["metric"], "CENTER_TO_FINITE_BEND")
        self.assertEqual(g["projection"]["location"], "INTERIOR")
        self.assertTrue(g["passes"]["3"])
        self.assertAlmostEqual(g["measurement"], 3.4, places=8)
        self.assertAlmostEqual(p["boundary_distance"], 1.5, places=8)
        self.assertIn("legacy_boundary_passes", g)

    def test_own_prehole_excluded_from_external_relative_order_but_not_deleted(self):
        rel = self.data["relative_external_analysis"]["O184"]
        self.assertIn("O184__O185", rel["internal_pair_ids"])
        self.assertNotIn("O184__O185", rel["external_pair_ids"])
        self.assertFalse(rel["automatic_cut"])
        self.assertEqual(self.data["summary"]["internal_pre_punch_flanging_pairs"], 46)

    def test_user_confirmed_irregular_flange_pairs_still_in_size_range(self):
        for a in ("O015", "O031", "O035"):
            p = self.pairs[f"{a}__O196"]
            self.assertEqual(p["geometric_status"], "WITHIN_TRIAL_RANGE")
            self.assertFalse(p["relations"]["internal_pre_punch_flanging"])

    def test_zero_distance_is_not_automatic_conflict_or_calibrated_neighbor(self):
        p = self.pairs["O025__O196"]
        self.assertTrue(p["curve_contact"])
        self.assertEqual(p["geometric_status"], "UNRESOLVED")
        self.assertIsNone(p["absolute_threshold"])
        self.assertIn("profile_a_to_b", p)

    def test_existing_cad_and_hard_precedence_preserved_as_facts(self):
        p = self.pairs["O023__O024"]
        self.assertIn({"child": "O024", "parent": "O023"}, p["relations"]["cad_parent_relations"])
        self.assertTrue(p["relations"]["internal_pre_punch_flanging"])
        self.assertIn({"before": "O024", "after": "O023", "source": "FEATURE_TREE_PRECEDENCE", "origin": "O024"}, p["relations"]["direct_hard_precedence"])
        bends = self.pairs["O009__O010"]
        self.assertEqual(bends["existing_precedence_path"], ["O010", "O009"])
        self.assertFalse(bends["relations"]["internal_pre_punch_flanging"])
        self.assertIn("acute_angle_degrees", bends["bend_geometry"])

    def test_missing_geometry_and_pending_type_are_not_no_neighbor(self):
        self.assertEqual(self.data["operations"]["O001"]["review_status"], "MISSING_GEOMETRY")
        self.assertEqual(self.data["operations"]["O186"]["review_status"], "TARGET_ONLY_BY_TRIAL_POLICY")
        self.assertEqual(self.data["operations"]["O008"]["review_status"], "PENDING_TYPE_POLICY")

    def test_no_expert_or_station_assignments_or_production_integration(self):
        self.assertFalse(self.data["expert_assignments_used"])
        self.assertFalse(self.data["production_integration"])
        text = json.dumps(self.data)
        self.assertNotIn('"expert_station"', text)
        self.assertNotIn('"assigned_station"', text)
        self.assertEqual(self.data["summary"]["physical_conflict_labels_generated"], 0)


if __name__ == "__main__":
    unittest.main()
