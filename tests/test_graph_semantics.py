from dataclasses import replace
import unittest

from src.data.schema import Operation, ProcessGroup
from src.graph import build_static_graph, build_plan_overlay
from src.graph.builders import build_baselines
from src.graph.schema import RelationType, RelationSource
from src.graph.semantic_relations import add_semantic_relations
from tests.graph_fixtures import part


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.data = part((Operation("P", "A", "PRE_PUNCH", process_group_id="PG"),
                          Operation("P", "B", "FLANGING", required_predecessor_ids=("A",), process_group_id="PG"),
                          Operation("P", "C", "TRIM", cad_parent_operation_id="A")),
                         groups=(ProcessGroup("P", "PG", ("A", "B")),))
        self.static = add_semantic_relations(self.data, build_static_graph(self.data))

    def test_explicit_precedence_and_group_precedence_preserve_both_sources(self):
        edges = [e for e in self.static.semantic_edges if e.relation_type == RelationType.PRECEDES]
        self.assertEqual(len(edges), 2)
        self.assertEqual({e.relation_source for e in edges},
                         {RelationSource.EXPLICIT_PREDECESSOR, RelationSource.PROCESS_GROUP_PRECEDENCE})
        self.assertTrue(all((e.source_index, e.target_index) == (0, 1) for e in edges))
        self.assertEqual({e.origin_ref for e in edges}, {"B", "PG"})

    def test_symmetric_group_membership(self):
        edges = [e for e in self.static.semantic_edges if e.relation_type == RelationType.SAME_PROCESS_GROUP]
        self.assertEqual({(e.source_index, e.target_index) for e in edges}, {(0, 1), (1, 0)})
        self.assertTrue(all(not e.reverse_message for e in edges))

    def test_cad_does_not_create_precedence(self):
        edges = [e for e in self.static.semantic_edges if {e.source_index, e.target_index} == {0, 2}]
        self.assertEqual({e.relation_type for e in edges}, {RelationType.CAD_PARENT, RelationType.CAD_CHILD})
        self.assertEqual(next(e for e in edges if e.relation_type == RelationType.CAD_PARENT).source_index, 0)

    def test_reverse_message_is_explicit(self):
        for e in self.static.semantic_edges:
            if e.relation_type in (RelationType.PRECEDES, RelationType.CAD_PARENT):
                reverse_type = RelationType.SUCCEEDS if e.relation_type == RelationType.PRECEDES else RelationType.CAD_CHILD
                reverse = [r for r in self.static.semantic_edges if
                           r.source_index == e.target_index and r.target_index == e.source_index and
                           r.relation_type == reverse_type and r.relation_source == e.relation_source]
                self.assertEqual(len(reverse), 1)
                self.assertTrue(reverse[0].reverse_message)
                self.assertFalse(e.reverse_message)

    def test_multi_relation_pair_not_merged(self):
        pair = [e for e in self.static.semantic_edges if (e.source_index, e.target_index) == (0, 1)]
        self.assertEqual(len(pair), 3)

    def test_order_independent_semantics(self):
        reversed_data = replace(self.data, operations=tuple(reversed(self.data.operations)))
        self.assertEqual(self.static, add_semantic_relations(reversed_data, build_static_graph(reversed_data)))

    def test_baseline_views_share_features_and_graph0_hides_relations(self):
        overlay = build_plan_overlay(self.static, {"A": "OP90", "B": "OP10", "C": "OP10"})
        g0, g1, g2 = build_baselines(self.static, overlay)
        self.assertEqual(g0.edges, ())
        self.assertEqual(g1.edges, self.static.semantic_edges)
        for graph in (g0, g1, g2):
            self.assertIs(graph.static, self.static)
            self.assertIs(graph.overlay, overlay)
            self.assertEqual(graph.node_index, g0.node_index)
        for e, f in zip(g1.edges, g1.plan_edge_features):
            r = overlay.edge_features(e.target_index, e.source_index)
            self.assertEqual(f.station_delta, -r.station_delta)
            self.assertEqual(f.abs_station_gap, r.abs_station_gap)


if __name__ == "__main__":
    unittest.main()
