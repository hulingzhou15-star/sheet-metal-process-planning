from dataclasses import replace
import hashlib
import math
from pathlib import Path
import unittest
from unittest.mock import patch

from src.data import load_excel
from src.data.schema import ExpertReferencePlan, Operation
from src.graph import (add_geometric_relations, build_baselines, build_part_graph,
                       build_plan_overlay, build_static_graph, geometry_distance)
from src.graph.schema import RelationType
from tests.graph_fixtures import bend, circle, part

P001 = Path(__file__).resolve().parents[1] / "data/parts/P001/钣金加工数据表_P001.xlsx"


class KNNTests(unittest.TestCase):
    def test_true_geometry_distance_beats_center_proxy(self):
        # Circle A is .1 from the end of a long BEND (center 50 away),
        # and 1 from circle B (center only 3 away).
        d = part((Operation("P", "A", "CIRCLE_PUNCH", geometry_id="A"),
                  Operation("P", "B", "CIRCLE_PUNCH", geometry_id="B"),
                  Operation("P", "C", "BEND", geometry_id="C")),
                 (circle("A", (0, 0), 1), circle("B", (-3, 0), 1), bend("C", (1.1, 0), (101.1, 0))))
        s = add_geometric_relations(build_static_graph(d), k=1)
        self.assertEqual(dict(s.knn_diagnostics.selected_neighbors)[0], (2,))

    def test_tie_order_stable_operation_id_and_union_symmetric(self):
        d = part(tuple(Operation("P", name, "CIRCLE_PUNCH", geometry_id=name) for name in ("Z", "B", "A")),
                 tuple(circle(name) for name in ("Z", "B", "A")))
        s = add_geometric_relations(build_static_graph(d), k=1)
        self.assertEqual(dict(s.knn_diagnostics.selected_neighbors), {0: (1,), 1: (0,), 2: (0,)})
        self.assertEqual(s.knn_diagnostics.nodes_with_cutoff_ties, (0, 1, 2))
        self.assertEqual({(e.source_index, e.target_index) for e in s.geometric_edges}, {(0, 1), (1, 0), (0, 2), (2, 0)})
        self.assertEqual(s, add_geometric_relations(build_static_graph(replace(d, operations=tuple(reversed(d.operations)))), k=1))

    def test_small_geometric_set_and_missing_geometry_nodes(self):
        d = part((Operation("P", "A", "CIRCLE_PUNCH", geometry_id="G1"),
                  Operation("P", "B", "CIRCLE_PUNCH", geometry_id="G2"), Operation("P", "C", "TRIM")),
                 (circle("G1"), circle("G2", (8, 0))))
        s = add_geometric_relations(build_static_graph(d))
        self.assertEqual(s.knn_diagnostics.k, 8)
        self.assertEqual(s.knn_diagnostics.selected_neighbors, ((0, (1,)), (1, (0,))))
        self.assertEqual(len(s.operation_ids), 3)
        self.assertEqual(len(s.geometric_edges), 2)
        self.assertTrue(all(2 not in (e.source_index, e.target_index) for e in s.geometric_edges))

    def test_empty_and_single_geometric_node(self):
        self.assertEqual(add_geometric_relations(build_static_graph(part())).geometric_edges, ())
        d = part((Operation("P", "A", "CIRCLE_PUNCH", geometry_id="G"),), (circle(),))
        self.assertEqual(add_geometric_relations(build_static_graph(d)).geometric_edges, ())

    def test_bad_k_rejected(self):
        for k in (0, -1, 2.5, True):
            with self.subTest(k=k), self.assertRaises(ValueError):
                add_geometric_relations(build_static_graph(part()), k)

    def test_distance_dx_dy_and_reverse(self):
        d = part((Operation("P", "A", "CIRCLE_PUNCH", geometry_id="G1"),
                  Operation("P", "B", "CIRCLE_PUNCH", geometry_id="G2")),
                 (circle("G1", (0, 0), 1), circle("G2", (3, 4), 1)))
        s = add_geometric_relations(build_static_graph(d))
        a, b = s.geometric_edges
        for value, expected in ((a.distance_norm, 3), (a.relative_dx_norm, 3), (a.relative_dy_norm, 4)):
            self.assertAlmostEqual(value*s.normalization.diagonal, expected)
        self.assertEqual(a.distance_norm, b.distance_norm)
        self.assertEqual(a.relative_dx_norm, -b.relative_dx_norm)
        self.assertEqual(a.relative_dy_norm, -b.relative_dy_norm)


class P001GraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.digest = hashlib.sha256(P001.read_bytes()).hexdigest()
        cls.data = load_excel(P001)
        cls.static = build_part_graph(cls.data)
        cls.overlay = build_plan_overlay(cls.static, cls.data.expert_reference_plan)
        cls.graphs = build_baselines(cls.static, cls.overlay)

    @classmethod
    def tearDownClass(cls):
        if hashlib.sha256(P001.read_bytes()).hexdigest() != cls.digest:
            raise AssertionError("Raw Excel changed while constructing graphs")

    def test_identical_node_mapping_static_plan_and_station_context(self):
        for g in self.graphs:
            self.assertEqual(len(g.node_index), 224)
            self.assertEqual(g.node_index, self.graphs[0].node_index)
            self.assertIs(g.static.node_features, self.graphs[0].static.node_features)
            self.assertIs(g.overlay.node_features, self.graphs[0].overlay.node_features)
            self.assertIs(g.static.station_context, self.graphs[0].static.station_context)
        self.assertEqual(self.graphs[0].edges, ())
        self.assertEqual(self.graphs[1].edges, self.static.semantic_edges)
        self.assertEqual(self.graphs[2].edges, self.static.semantic_edges+self.static.geometric_edges)

    def test_default8_all_geometric_nodes_select8_neighbors(self):
        d = self.static.knn_diagnostics
        self.assertEqual((d.k, d.geometric_node_count, d.evaluated_pairs), (8, 211, 22155))
        self.assertTrue(all(len(neighbors) == 8 for i, neighbors in d.selected_neighbors))

    def test_symmetric_no_self_and_missing_geometry_excluded(self):
        by_pair = {(e.source_index, e.target_index): e for e in self.static.geometric_edges}
        for (i, j), e in by_pair.items():
            self.assertNotEqual(i, j)
            self.assertIsNotNone(self.static.raw_geometries[i])
            self.assertIsNotNone(self.static.raw_geometries[j])
            r = by_pair[j, i]
            self.assertEqual(e.distance_norm, r.distance_norm)
            self.assertEqual(e.relative_dx_norm, -r.relative_dx_norm)
            self.assertEqual(e.relative_dy_norm, -r.relative_dy_norm)
        for i, neighbors in self.static.knn_diagnostics.selected_neighbors:
            for j in neighbors:
                self.assertIn((i, j), by_pair)

    def test_semantic_and_geometric_edges_coexist(self):
        overlap = ({(e.source_index, e.target_index) for e in self.static.semantic_edges} &
                   {(e.source_index, e.target_index) for e in self.static.geometric_edges})
        self.assertTrue(overlap)
        a, b = next(iter(overlap))
        types = {e.relation_type for e in self.graphs[2].edges if (e.source_index, e.target_index) == (a, b)}
        self.assertIn(RelationType.GEOMETRIC_NEAR, types)
        self.assertGreater(len(types), 1)

    def test_all_derived_fields_finite(self):
        for g in self.graphs:
            for f in g.plan_edge_features:
                self.assertTrue(all(math.isfinite(x) for x in (f.same_station, f.station_delta, f.abs_station_gap)))
        for e in self.static.geometric_edges:
            self.assertTrue(all(math.isfinite(x) for x in (e.distance_norm, e.relative_dx_norm, e.relative_dy_norm)))

    def test_builder_only_objects_and_no_expert_dependency(self):
        altered = replace(self.data, expert_reference_plan=ExpertReferencePlan(()), source_sha256="unrelated")
        with patch("pathlib.Path.read_bytes", side_effect=AssertionError("No source reads")), \
             patch("openpyxl.load_workbook", side_effect=AssertionError("No workbook reads")):
            other = build_part_graph(altered)
        self.assertEqual(self.static.fingerprint(), other.fingerprint())

    def test_changed_plan_preserves_full_static_hash(self):
        before = self.static.fingerprint()
        p = build_plan_overlay(self.static, {op: "OP70" for op in self.static.operation_ids})
        other_graphs = build_baselines(self.static, p)
        self.assertNotEqual(p, self.overlay)
        self.assertNotEqual(other_graphs[1].plan_edge_features, self.graphs[1].plan_edge_features)
        self.assertEqual(self.static.fingerprint(), before)

    def test_geometry_distances_equal_public_function_for_actual_combinations(self):
        seen = set()
        for e in self.static.geometric_edges:
            a, b = self.static.raw_geometries[e.source_index], self.static.raw_geometries[e.target_index]
            key = tuple(sorted((tuple(sorted({s.segment_type.value for s in a.segments})),
                                tuple(sorted({s.segment_type.value for s in b.segments})))))
            if key not in seen:
                seen.add(key)
                self.assertAlmostEqual(e.distance_norm*self.static.normalization.diagonal, geometry_distance(a, b), places=8)
        self.assertGreaterEqual(len(seen), 8)


if __name__ == "__main__":
    unittest.main()
