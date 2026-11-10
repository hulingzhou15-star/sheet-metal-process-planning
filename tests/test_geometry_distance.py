from dataclasses import replace
import math
import random
import unittest

from src.graph.geometry_distance import (geometry_distance, point_arc_distance,
                                         point_line_distance, primitive_distance)
from src.graph.geometry_primitives import Arc, Line, geometry_primitives
from tests.graph_fixtures import bend, circle, rectangle


class DistanceTests(unittest.TestCase):
    def assertDistance(self, a, b, expected):
        self.assertAlmostEqual(primitive_distance(a, b), expected, places=9)
        self.assertAlmostEqual(primitive_distance(b, a), expected, places=9)

    def test_point_line_projection_endpoint_and_degenerate(self):
        self.assertEqual(point_line_distance((2, 3), Line((0, 0), (4, 0))), 3)
        self.assertEqual(point_line_distance((7, 4), Line((0, 0), (4, 0))), 5)
        self.assertEqual(point_line_distance((3, 4), Line((0, 0), (0, 0))), 5)

    def test_point_arc_restricted_and_center(self):
        arc = Arc((0, 0), 2, 0, math.pi/2)
        self.assertAlmostEqual(point_arc_distance((-2, 0), arc), math.sqrt(8))
        self.assertEqual(point_arc_distance((0, 0), arc), 2)
        self.assertAlmostEqual(point_arc_distance((3, 3), arc), math.sqrt(18)-2)

    def test_line_line_crossing_parallel_collinear_and_endpoint(self):
        self.assertDistance(Line((-1, 0), (1, 0)), Line((0, -1), (0, 1)), 0)
        self.assertDistance(Line((0, 0), (3, 0)), Line((1, 2), (4, 2)), 2)
        self.assertDistance(Line((0, 0), (3, 0)), Line((1, 0), (4, 0)), 0)
        self.assertDistance(Line((0, 0), (3, 0)), Line((4, 0), (8, 0)), 1)
        self.assertDistance(Line((0, 0), (1, 0)), Line((2, 1), (2, 3)), math.sqrt(2))

    def test_circle_circle_separate_intersect_tangent_and_nested(self):
        a = Arc((0, 0), 2)
        self.assertDistance(a, Arc((8, 0), 3), 3)
        self.assertDistance(a, Arc((3, 0), 2), 0)
        self.assertDistance(a, Arc((4, 0), 2), 0)
        self.assertDistance(a, Arc((0, 0), 1), 1)
        self.assertDistance(a, Arc((0.5, 0), 1), 0.5)
        self.assertDistance(a, a, 0)

    def test_line_circle_crossing_tangent_and_separated(self):
        a = Arc((0, 0), 2)
        self.assertDistance(Line((-5, 0), (5, 0)), a, 0)
        self.assertDistance(Line((-5, 2), (5, 2)), a, 0)
        self.assertDistance(Line((-5, 4), (5, 4)), a, 2)
        self.assertDistance(Line((0, 0), (0.5, 0)), a, 1.5)

    def test_line_arc_interior_stationary_endpoint_and_restricted_intersection(self):
        upper = Arc((0, 0), 2, 0, math.pi)
        self.assertDistance(Line((-3, 4), (3, 4)), upper, 2)
        self.assertDistance(Line((-3, -4), (3, -4)), upper, 4)
        self.assertDistance(Line((0, -3), (0, -1)), upper, math.sqrt(5))
        self.assertDistance(Line((0, 1), (0, 3)), upper, 0)
        self.assertDistance(Line((4, 0), (4, 0)), upper, 2)

    def test_circle_arc_and_arc_arc_stationary(self):
        a = Arc((0, 0), 1, -math.pi/2, math.pi)
        b = Arc((4, 0), 1, math.pi/2, math.pi)
        self.assertDistance(a, b, 2)
        self.assertDistance(a, Arc((4, 0), 1), 2)
        self.assertDistance(a, Arc((4, 0), 1, -math.pi/2, math.pi), math.sqrt(17)-1)

    def test_arc_arc_intersections_filtered_by_angles(self):
        self.assertDistance(Arc((0, 0), 2, 0, math.pi), Arc((2, 0), 2, 0, math.pi), 0)
        a = Arc((0, 0), 2, math.pi, math.pi/2)
        b = Arc((2, 0), 2, 0, math.pi/2)
        self.assertDistance(a, b, 2*math.sqrt(5))

    def test_concentric_arcs_overlap_disjoint_and_negative_sweep(self):
        a = Arc((0, 0), 2, 0, math.pi/2)
        self.assertDistance(a, Arc((0, 0), 1, math.pi/4, math.pi/2), 1)
        self.assertDistance(a, Arc((0, 0), 1, math.pi, math.pi/2), math.sqrt(5))
        self.assertDistance(a, Arc((0, 0), 2, math.pi/2, -math.pi/2), 0)
        self.assertDistance(a, Arc((0, 0), 2, math.pi/4, math.pi/4), 0)

    def test_zero_radius_circles(self):
        self.assertDistance(Arc((0, 0), 0), Arc((3, 0), 1), 2)
        self.assertDistance(Arc((0, 0), 0), Line((1, 0), (2, 0)), 1)

    def test_bridge_contour_circle_bend_and_contour_combinations(self):
        bridge = rectangle(bridge=True)
        contour = rectangle("R", start=(8, 0), end=(12, 2))
        self.assertAlmostEqual(geometry_distance(bridge, circle(center=(8, 1), radius=1)), 3)
        self.assertAlmostEqual(geometry_distance(bridge, bend(start=(8, -5), end=(8, 5))), 4)
        self.assertAlmostEqual(geometry_distance(bridge, contour), 4)
        self.assertAlmostEqual(geometry_distance(contour, circle(center=(16, 1), radius=1)), 3)
        self.assertAlmostEqual(geometry_distance(contour, rectangle("R2", start=(16, 0), end=(20, 2))), 4)

    def test_flanging_union_of_recorded_circles(self):
        outer = circle(radius=3)
        flange = replace(outer, segments=(*outer.segments, replace(outer.segments[0], seq=2, radius=1)))
        self.assertEqual(len(geometry_primitives(flange)), 2)
        self.assertAlmostEqual(geometry_distance(flange, circle(radius=2)), 1)
        self.assertAlmostEqual(geometry_distance(flange, bend(start=(6, 0), end=(8, 0))), 3)

    def test_translation_and_scaling_invariance(self):
        a, b = Arc((1, 2), 2, 0.3, -2), Line((7, 1), (9, -4))
        distance = primitive_distance(a, b)
        translated_a = replace(a, center=(a.center[0]+1000, a.center[1]-600))
        translated_b = Line((1007, -599), (1009, -604))
        self.assertAlmostEqual(primitive_distance(translated_a, translated_b), distance)
        self.assertAlmostEqual(primitive_distance(replace(a, center=(3, 6), radius=6), Line((21, 3), (27, -12))), 3*distance)

    def test_analytic_distances_against_independent_dense_sample_bounds(self):
        # Test-only sampling is an independent upper bound, never a production
        # distance implementation. Lipschitz bounds include sampling spacing.
        rng = random.Random(207)
        for _ in range(30):
            a = Arc((rng.uniform(-3, 3), rng.uniform(-3, 3)), rng.uniform(0.2, 3),
                    rng.uniform(-math.pi, math.pi), rng.choice((-1, 1))*rng.uniform(0.1, 6))
            b = (Arc((rng.uniform(-3, 3), rng.uniform(-3, 3)), rng.uniform(0.2, 3),
                     rng.uniform(-math.pi, math.pi), rng.uniform(-6, -0.1)) if rng.random() < .5 else
                 Line((rng.uniform(-3, 3), rng.uniform(-3, 3)), (rng.uniform(-3, 3), rng.uniform(-3, 3))))
            def points(p):
                return ([p.point(p.start_angle+p.sweep*t/120) for t in range(121)] if isinstance(p, Arc) else
                        [(p.start[0]+(p.end[0]-p.start[0])*t/120, p.start[1]+(p.end[1]-p.start[1])*t/120) for t in range(121)])
            sampled = min(math.dist(p, q) for p in points(a) for q in points(b))
            step_a = a.radius*abs(a.sweep)/120
            step_b = b.radius*abs(b.sweep)/120 if isinstance(b, Arc) else math.dist(b.start, b.end)/120
            exact = primitive_distance(a, b)
            self.assertLessEqual(exact, sampled+1e-9)
            self.assertLessEqual(sampled-exact, (step_a+step_b)/2+1e-9)
            self.assertAlmostEqual(exact, primitive_distance(b, a), places=9)


if __name__ == "__main__":
    unittest.main()
