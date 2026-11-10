from dataclasses import replace
import hashlib
import unittest

from src.constraints import ContextValidationError, FixedAssignment, load_constraint_context
from src.constraints.config import load_config
from src.data.schema import Operation
from src.graph.static_graph import build_static_graph
from m3_fixtures import P001, bends


class ContextTests(unittest.TestCase):
    def test_p001_snapshot_metadata_and_frozen_static_separation(self):
        before = hashlib.sha256(P001.read_bytes()).hexdigest()
        c = load_constraint_context(P001)
        self.assertEqual(len(c.operations), 224)
        self.assertEqual(len(c.stations), 7)
        self.assertEqual(len(c.fixed_assignments), 10)
        self.assertEqual(len(c.bend_influences), 59)
        self.assertEqual(c.metadata_sha256, c.part_data.source_sha256)
        self.assertEqual(len(c.precedence_relations), 112)
        static = build_static_graph(c.part_data)
        modified_metadata = replace(c, bend_influences=tuple(replace(r, affected_area_proxy=r.affected_area_proxy*2)
                                                           for r in c.bend_influences))
        self.assertEqual(static.fingerprint(), build_static_graph(modified_metadata.part_data).fingerprint())
        self.assertEqual(before, hashlib.sha256(P001.read_bytes()).hexdigest())

    def assert_issue(self, function, code):
        with self.assertRaises(ContextValidationError) as caught:
            function()
        self.assertIn(code, {issue.code for issue in caught.exception.issues})

    def test_fixed_operation_must_exist(self):
        c = bends()
        self.assert_issue(lambda: replace(c, fixed_assignments=(FixedAssignment("missing", "OP90"),)), "FIXED_OPERATION_INVALID")

    def test_fixed_station_must_exist(self):
        c = bends()
        d = replace(c.part_data, operations=(Operation("P", "A", "TRIM"), c.part_data.operations[1]))
        self.assert_issue(lambda: replace(c, part_data=d, bend_influences=(),
                                          fixed_assignments=(FixedAssignment("A", "missing"),)), "FIXED_STATION_INVALID")

    def test_region_operation_must_be_bend(self):
        c = bends()
        d = replace(c.part_data, operations=(Operation("P", "A", "TRIM"), c.part_data.operations[1]))
        self.assert_issue(lambda: replace(c, part_data=d), "REGION_BEND_INVALID")

    def test_region_id_unique(self):
        c = bends()
        self.assert_issue(lambda: replace(c, bend_influences=(c.bend_influences[0],
                                                             replace(c.bend_influences[1], influence_region_id="R1"))), "IDENTIFIER_INVALID")

    def test_parent_region_must_exist(self):
        c = bends()
        self.assert_issue(lambda: replace(c, bend_influences=(replace(c.bend_influences[0], parent_region_id="missing"),
                                                             c.bend_influences[1])), "PARENT_REGION_INVALID")

    def test_region_hierarchy_cycle(self):
        c = bends((300, 200, 100), parents=(None, "R1", "R2"))
        self.assert_issue(lambda: replace(c, bend_influences=(replace(c.bend_influences[0], parent_region_id="R3"),
                                                             *c.bend_influences[1:])), "REGION_HIERARCHY_CYCLE")

    def test_self_parent_cycle(self):
        c = bends()
        self.assert_issue(lambda: replace(c, bend_influences=(replace(c.bend_influences[0], parent_region_id="R1"),
                                                             c.bend_influences[1])), "REGION_HIERARCHY_CYCLE")

    def test_parent_bend_mapping_ambiguity_is_reported(self):
        c = bends()
        self.assert_issue(lambda: replace(c, bend_influences=(c.bend_influences[0],
                                                             replace(c.bend_influences[1], bend_operation_id="A"))), "BEND_METADATA_AMBIGUOUS")

    def test_nonfinite_metadata_not_silently_repaired(self):
        c = bends()
        self.assert_issue(lambda: replace(c, bend_influences=(replace(c.bend_influences[0], affected_area_proxy=float("nan")),
                                                             c.bend_influences[1])), "REGION_NUMERIC_INVALID")

    def test_rank_uses_station_order_including_empty_capability(self):
        c = bends()
        self.assertEqual(c.station_ranks, {"OP90": 0, "OP10": 1, "OP80": 2, "OP05": 3})

    def test_yaml_compatible_configuration_is_loaded(self):
        config = load_config()
        self.assertEqual(config["soft_weights"], {"bend_area_ordering": .45, "precedence_extra_gap": .20, "type_fragmentation": .35})
