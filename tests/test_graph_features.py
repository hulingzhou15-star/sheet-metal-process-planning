from dataclasses import asdict, replace
import math
from pathlib import Path
import unittest

from src.data import load_excel
from src.data.schema import ExpertReferencePlan, Geometry, GeometrySegment, Operation, Station
from src.graph import build_static_graph, build_plan_overlay, build_graph0
from src.graph.geometry_features import GeometryFeatureExtractorV1
from src.graph.geometry_primitives import Arc, primitive_bbox
from src.graph.schema import OPERATION_TYPE_ORDER
from src.graph.station_context import build_station_context
from tests.graph_fixtures import bend, circle, part, rectangle

P001 = Path(__file__).resolve().parents[1] / "data/parts/P001/钣金加工数据表_P001.xlsx"


def feature(kind, geometry):
    data = part((Operation("P", "A", kind, geometry_id=geometry.geometry_id),), (geometry,))
    extractor = GeometryFeatureExtractorV1(data)
    return extractor.extract(data.operations[0]), extractor.normalization


class GeometryFeatureTests(unittest.TestCase):
    def test_circle_summary(self):
        f, n = feature("CIRCLE_PUNCH", circle(center=(10, -6)))
        self.assertEqual((f.geom_center_x_norm, f.geom_center_y_norm), (0, 0))
        self.assertAlmostEqual(f.bbox_width_norm*n.diagonal, 4)
        self.assertAlmostEqual(f.geometry_length_norm*n.diagonal, 4)
        self.assertAlmostEqual(f.geometry_area_norm*n.diagonal**2, 4*math.pi)
        self.assertAlmostEqual(f.geometry_perimeter_norm*n.diagonal, 4*math.pi)
        self.assertEqual((f.has_area, f.has_perimeter, f.has_orientation), (1, 1, 0))

    def test_pre_punch_and_dimple(self):
        for kind in ("PRE_PUNCH", "DIMPLE"):
            self.assertEqual(feature(kind, circle())[0], feature("CIRCLE_PUNCH", circle())[0])

    def test_flanging_outer_bbox_but_area_perimeter_masked(self):
        g = circle(radius=3)
        g = replace(g, segments=(*g.segments, replace(g.segments[0], seq=2, radius=1)))
        f, n = feature("FLANGING", g)
        self.assertAlmostEqual(f.geometry_length_norm*n.diagonal, 6)
        self.assertAlmostEqual(f.bbox_width_norm*n.diagonal, 6)
        self.assertEqual((f.geometry_area_norm, f.geometry_perimeter_norm, f.has_area, f.has_perimeter), (0, 0, 0, 0))

    def test_bend_summary_and_reversal(self):
        f, n = feature("BEND", bend())
        self.assertEqual(n.center, (1.5, 2))
        self.assertAlmostEqual(f.geometry_length_norm*n.diagonal, 5)
        self.assertAlmostEqual(f.orientation_cos2, -7/25)
        self.assertAlmostEqual(f.orientation_sin2, 24/25)
        self.assertEqual((f.has_area, f.has_perimeter, f.has_orientation), (0, 0, 1))
        self.assertEqual(f, feature("BEND", bend(start=(3, 4), end=(0, 0)))[0])

    def test_irregular_bbox_center_area_perimeter(self):
        f, n = feature("IRREGULAR_PUNCH", rectangle(start=(3, 4), end=(7, 6)))
        self.assertEqual(n.center, (5, 5))
        self.assertAlmostEqual(f.geometry_area_norm*n.diagonal**2, 8)
        self.assertAlmostEqual(f.geometry_perimeter_norm*n.diagonal, 12)
        self.assertEqual(f.has_orientation, 0)

    def test_curved_contour_area_and_true_arc_bbox(self):
        # Upper half-circle + diameter, including the interior y-extremum.
        g = Geometry("P", "G", (GeometrySegment(1, "ARC", 2, 0, -2, 0, 0, 0, 2, 180),
                                 GeometrySegment(2, "LINE", -2, 0, 2, 0)))
        f, n = feature("IRREGULAR_PUNCH", g)
        self.assertEqual(n.bbox, (-2, 0, 2, 2))
        self.assertAlmostEqual(f.geometry_area_norm*n.diagonal**2, 2*math.pi)
        self.assertAlmostEqual(f.geometry_perimeter_norm*n.diagonal, 2*math.pi+4)
        reverse = replace(g, segments=(GeometrySegment(1, "ARC", -2, 0, 2, 0, 0, 0, 2, -180),
                                        GeometrySegment(2, "LINE", 2, 0, -2, 0)))
        for a, b in zip(f.vector(), feature("IRREGULAR_PUNCH", reverse)[0].vector()):
            self.assertAlmostEqual(a, b)

    def test_arc_bbox_negative_wrap_and_major_sweep(self):
        box = primitive_bbox(Arc((0, 0), 1, math.pi/4, -3*math.pi/2))
        self.assertAlmostEqual(box[0], -1)
        self.assertAlmostEqual(box[1], -1)
        self.assertAlmostEqual(box[2], 1)
        self.assertAlmostEqual(box[3], math.sqrt(0.5))

    def test_bridge_summary_main_axis(self):
        f, n = feature("BRIDGE", rectangle(bridge=True))
        self.assertAlmostEqual(f.geometry_length_norm*n.diagonal, math.sqrt(20))
        self.assertAlmostEqual(f.geometry_area_norm*n.diagonal**2, 8)
        self.assertAlmostEqual(f.geometry_perimeter_norm*n.diagonal, 12)
        self.assertEqual((f.orientation_cos2, f.orientation_sin2, f.has_orientation), (1, 0, 1))
        vertical = feature("BRIDGE", rectangle(end=(2, 4), bridge=True))[0]
        self.assertEqual(vertical.orientation_cos2, -1)
        self.assertEqual(feature("BRIDGE", rectangle(end=(2, 2), bridge=True))[0].has_orientation, 0)

    def test_missing_geometry_all_zero(self):
        static = build_static_graph(part())
        self.assertEqual(static.node_features[0].geometry_features_v1.vector(), (0,)*14)
        self.assertEqual(static.normalization.diagonal, 0)
        self.assertTrue(static.normalization.degenerate)

    def test_degenerate_geometry_has_finite_values(self):
        f, n = feature("BEND", bend(start=(1, 1), end=(1, 1)))
        self.assertTrue(n.degenerate)
        self.assertEqual(n.length_scale, 1)
        self.assertEqual(f.has_orientation, 0)
        self.assertTrue(all(math.isfinite(v) for v in f.vector()))


class StationAndPlanTests(unittest.TestCase):
    def test_order_and_capability_do_not_parse_station_id(self):
        c = build_station_context(part().stations)
        self.assertEqual([s.station_id for s in c.stations], ["OP90", "OP10"])
        self.assertEqual([s.relative_order for s in c.stations], [0, 1])
        self.assertEqual(c.stations[0].capability, tuple(int(t.value == "TRIM") for t in OPERATION_TYPE_ORDER))

    def test_single_station_and_empty_capability(self):
        c = build_station_context((Station("P", "anything", 1, frozenset()),))
        self.assertEqual(c.stations[0].relative_order, 0)
        self.assertEqual(c.stations[0].capability, (0,)*9)

    def test_gapped_orders_use_declared_formula(self):
        d = part()
        c = build_station_context((d.stations[0], replace(d.stations[1], order=4)))
        self.assertEqual(c.stations[1].relative_order, 3)

    def test_plan_structure_rejections(self):
        s = build_static_graph(part())
        for p in ({"A": "OP90"}, {"A": "OP90", "B": "OP10", "foreign": "OP10"},
                  [("A", "OP90"), ("A", "OP10"), ("B", "OP10")], {"A": "absent", "B": "OP10"}):
            with self.subTest(plan=p), self.assertRaises(ValueError):
                build_plan_overlay(s, p)

    def test_plan_edge_features_order_reversal_and_same_station(self):
        s = build_static_graph(part())
        p = build_plan_overlay(s, {"A": "OP90", "B": "OP10"})
        self.assertEqual(asdict(p.edge_features(0, 1)), {"same_station": 0, "station_delta": 1, "abs_station_gap": 1})
        self.assertEqual(p.edge_features(1, 0).station_delta, -1)
        p = build_plan_overlay(s, {"A": "OP10", "B": "OP10"})
        self.assertEqual(asdict(p.edge_features(0, 1)), {"same_station": 1, "station_delta": 0, "abs_station_gap": 0})

    def test_graph0_has_no_edges(self):
        s = build_static_graph(part())
        p = build_plan_overlay(s, {"A": "OP90", "B": "OP10"})
        self.assertEqual(build_graph0(s, p).edges, ())
        self.assertEqual(build_graph0(s, p).plan_edge_features, ())


class P001StaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_excel(P001)
        cls.static = build_static_graph(cls.data)

    def test_all_224_nodes_stable_bidirectional_mapping(self):
        s = self.static
        self.assertEqual(len(s.operation_ids), 224)
        self.assertEqual(s.operation_ids, tuple(sorted(op.operation_id for op in self.data.operations)))
        for op, i in s.node_index.items():
            self.assertEqual(s.operation_id(i), op)
        permuted = replace(self.data, operations=tuple(reversed(self.data.operations)))
        self.assertEqual(s, build_static_graph(permuted))

    def test_raw_geometry_preserved_all_records(self):
        self.assertEqual({g for g in self.static.raw_geometries if g}, set(self.data.geometries))
        self.assertEqual(sum(len(g.segments) for g in self.static.raw_geometries if g), 689)

    def test_all_stations_including_empty_op70(self):
        self.assertEqual(len(self.static.station_context.stations), 7)
        self.assertEqual(self.static.station_context.get("OP70").capability, (0,)*9)

    def test_arbitrary_plan_changes_overlay_not_static(self):
        s = self.static
        before = s.fingerprint()
        expert = build_plan_overlay(s, self.data.expert_reference_plan)
        alternative = {op: "OP70" for op in s.operation_ids}
        self.assertNotEqual(expert, build_plan_overlay(s, alternative))
        self.assertEqual(before, s.fingerprint())
        # Even the expert assignments and workbook hash cannot affect static facts.
        altered = replace(self.data, expert_reference_plan=ExpertReferencePlan(()), source_sha256="different")
        self.assertEqual(s, build_static_graph(altered))
        self.assertNotIn("expert", str(asdict(s)).lower())
        self.assertNotIn("assigned_station", str(asdict(s)))

    def test_fixed_feature_dimensions_finite_and_no_irregular_subtypes(self):
        overlay = build_plan_overlay(self.static, self.data.expert_reference_plan)
        for static, plan in zip(self.static.node_features, overlay.node_features):
            self.assertEqual(len(static.vector()), 26)
            self.assertEqual(len(plan.vector()), 10)
            self.assertTrue(all(math.isfinite(v) for v in (*static.vector(), *plan.vector())))
        for op in range(193, 215):
            self.assertEqual(self.static.node_features[self.static.node_index[f"O{op}" ]].operation_type.value, "IRREGULAR_PUNCH")


if __name__ == "__main__":
    unittest.main()
