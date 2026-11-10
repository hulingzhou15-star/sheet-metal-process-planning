from dataclasses import replace
import math
import unittest

from m3_fixtures import region
from src.graph.geometry_primitives import Arc, Line
from src.visualization.geometry_adapter import build_geometry_catalog, material_to_svg, primitive_shape
from src.visualization.svg_renderer import shape_markup
from visual_fixtures import actual, mini


class GeometryAdapterTests(unittest.TestCase):
    def test_circle_keeps_true_center_and_radius(self):
        s=primitive_shape(Arc((4,6),2)); self.assertEqual(s,{"kind":"circle","center":(4,-6),"radius":2})
        self.assertIn('r="2"',shape_markup(s))

    def test_horizontal_vertical_bend_endpoints(self):
        _,_,_,_,cat=mini()
        self.assertEqual(cat["operations"]["B1"]["shapes"][0]["end"],(20,0))
        self.assertEqual(cat["operations"]["B3"]["shapes"][0]["end"],(30,-20))

    def test_signed_arc_sweep_and_large_arc_flags(self):
        p=primitive_shape(Arc((0,0),2,0,math.pi/2)); self.assertEqual(p["sweep_flag"],0)
        n=primitive_shape(Arc((0,0),2,0,-3*math.pi/2)); self.assertEqual((n["sweep_flag"],n["large_arc_flag"]),(1,1))
        self.assertAlmostEqual(p["end"][1],-2)
        self.assertIn('A 2 2 0 1 1',shape_markup(n))

    def test_irregular_line_arc_sequence_preserved(self):
        _,_,_,_,cat=mini()
        self.assertEqual([s["kind"] for s in cat["operations"]["I"]["shapes"]],["line","arc","line","line"])
        self.assertEqual(cat["operations"]["I"]["shapes"][-1]["end"],cat["operations"]["I"]["shapes"][0]["start"])

    def test_flanging_only_recorded_concentric_circles(self):
        _,_,_,_,cat=mini(); self.assertEqual([s["radius"] for s in cat["operations"]["F"]["shapes"]],[2,3])

    def test_bridge_recorded_rectangle(self):
        _,_,_,_,cat=mini(); shapes=cat["operations"]["BR"]["shapes"]
        self.assertEqual(len(shapes),4);self.assertEqual(shapes[0]["start"],(11,-11));self.assertEqual(shapes[-1]["end"],(11,-11))

    def test_influence_rectangles_from_frozen_d3(self):
        _,_,_,_,cat=mini()
        self.assertEqual(cat["regions"]["R_SMALL"]["bbox"],(0,5,10,10))
        self.assertEqual(cat["regions"]["R_VERTICAL"]["bbox"],(30,0,50,20))

    def test_smallest_region_outside_and_no_geometry(self):
        _,_,_,_,cat=mini()
        self.assertEqual(cat["membership"]["C"],"R_SMALL")
        self.assertEqual(cat["membership"]["X"],"OUTSIDE_REGION")
        self.assertEqual(cat["membership"]["T"],"NO_GEOMETRY")

    def test_equal_smallest_not_guessed(self):
        c,_,_,cfg,_=mini()
        r=region("B3","R_DUP",start=(0,5),end=(10,5),far=10,depth=5)
        c=replace(c,bend_influences=(*c.bend_influences[:2],r))
        cat=build_geometry_catalog(c,cfg)
        self.assertEqual(cat["membership"]["C"],"UNRESOLVED_REGION")
        self.assertTrue(cat["audit"]["d3"]["ambiguous_smallest_regions"])

    def test_y_axis_conversion_no_x_rotation(self):
        self.assertEqual(material_to_svg((-4,12)),(-4,-12))
        self.assertEqual(primitive_shape(Line((-3,4),(2,-5)))["start"],(-3,-4))

    def test_bbox_contains_all_geometry_positive_extent(self):
        _,_,_,_,cat=mini();vx,vy,vw,vh=cat["view_box"];x0,y0,x1,y1=cat["bbox"]
        self.assertLess(vx,x0);self.assertLess(vy,-y1);self.assertGreater(vx+vw,x1);self.assertGreater(vy+vh,-y0)

    def test_p001_coverage_no_geometry_and_d3_exact(self):
        _,_,cat,*_=actual(); audit=cat["audit"]
        self.assertEqual(audit["renderable_count"],211);self.assertEqual(len(audit["no_geometry_ids"]),13)
        self.assertEqual(audit["geometry_type_coverage"]["BEND"]["rendered_count"],59)
        self.assertEqual(audit["d3"]["assigned_operation_count"],186);self.assertEqual(audit["d3"]["outside_region_count"],25)
        self.assertFalse(audit["bend_geometry_mapping_issues"])
