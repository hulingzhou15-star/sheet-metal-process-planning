"""Geometric derivation tests independent of reference plans and workbook answers."""
import copy
import math
import unittest

from extract_app.geometry import (derive_geometry, derive_region, fully_contained,
                                  preview_geometry, segment_bbox)


def line(identifier, start, end):
    return {"id": identifier, "segment_type": "LINE", "start_x": start[0], "start_y": start[1],
            "end_x": end[0], "end_y": end[1]}


def arc(identifier, start, end, center, radius, sweep):
    return {"id": identifier, "segment_type": "ARC", "start_x": start[0], "start_y": start[1],
            "end_x": end[0], "end_y": end[1], "center_x": center[0], "center_y": center[1],
            "radius": radius, "sweep_angle": sweep}


def circle(identifier, center, radius):
    return {"id": identifier, "segment_type": "CIRCLE", "center_x": center[0], "center_y": center[1],
            "radius": radius}


def operation(kind, mode=None, **options):
    result = {"operation_id": "O001", "operation_type": kind, "geometry_options": options}
    if mode:
        result["geometry_mode"] = mode
    return result


def reversed_segment(segment):
    result = copy.deepcopy(segment)
    result["start_x"], result["start_y"], result["end_x"], result["end_y"] = (
        result["end_x"], result["end_y"], result["start_x"], result["start_y"])
    if result["segment_type"] == "ARC":
        result["sweep_angle"] *= -1
    return result


def rounded_rectangle(concave=False):
    result = [arc("a1", (0, 1), (1, 0), (1, 1), 1, 90),
              line("l1", (1, 0), (3, 0)), arc("a2", (3, 0), (4, 1), (3, 1), 1, 90),
              line("l2", (4, 1), (4, 3)), arc("a3", (4, 3), (3, 4), (3, 3), 1, 90)]
    if concave:
        result += [line("l3a", (3, 4), (2.5, 4)), arc("dent", (2.5, 4), (1.5, 4), (2, 4), .5, -180),
                   line("l3b", (1.5, 4), (1, 4))]
    else:
        result.append(line("l3", (3, 4), (1, 4)))
    result += [arc("a4", (1, 4), (0, 3), (1, 3), 1, 90), line("l4", (0, 3), (0, 1))]
    return result


def rectangle():
    return [line("bottom", (0, 0), (2, 0)), line("right", (2, 0), (2, 10)),
            line("top", (2, 10), (0, 10)), line("left", (0, 10), (0, 0))]


class CircleGeometryTests(unittest.TestCase):
    def test_circle_is_exact_and_source_unchanged(self):
        source = circle("c", (1.2345678912, 8.765432109), 0.1234567891)
        original = copy.deepcopy(source)
        result = derive_geometry(operation("CIRCLE_PUNCH"), [source])
        self.assertEqual(result[0]["center_x"], source["center_x"])
        self.assertEqual(result[0]["radius"], source["radius"])
        self.assertIsNone(result[0]["start_x"])
        self.assertEqual(source, original)

    def test_flange_accepts_concentric_circles_sorted_by_radius(self):
        result = derive_geometry(operation("FLANGING"), [circle("a", (3, 5), 4), circle("b", (3, 5), 2)])
        self.assertEqual([r["radius"] for r in result], [2, 4])
        self.assertEqual([r["seq"] for r in result], [1, 2])

    def test_nonconcentric_flange_rejected(self):
        with self.assertRaisesRegex(ValueError, "同心"):
            derive_geometry(operation("FLANGING"), [circle("a", (3, 5), 4), circle("b", (3, 6), 2)])

    def test_prepunch_cannot_absorb_two_circles(self):
        with self.assertRaisesRegex(ValueError, "一个圆"):
            derive_geometry(operation("PRE_PUNCH"), [circle("a", (0, 0), 1), circle("b", (0, 0), 2)])

    def test_none_is_explicit_and_cannot_contain_source(self):
        self.assertEqual(derive_geometry(operation("TRIM", "none"), []), [])
        with self.assertRaisesRegex(ValueError, "无几何"):
            derive_geometry(operation("TRIM", "none"), [circle("a", (0, 0), 1)])


class BendGeometryTests(unittest.TestCase):
    def test_four_separated_fragments_form_one_midline(self):
        selected = []
        for i in range(4):
            selected.extend([line(f"a{i}", (2*i, 0), (2*i+1, 0)),
                             line(f"b{i}", (2*i+1, 2), (2*i, 2))])
        original = copy.deepcopy(selected)
        result = derive_geometry(operation("BEND", confirmed_span=True), selected)
        self.assertEqual(len(result), 1)
        self.assertEqual([result[0][k] for k in ("start_x", "start_y", "end_x", "end_y")], [0, 1, 7, 1])
        self.assertEqual(result[0]["segment_type"], "BEND_LINE")
        self.assertEqual(selected, original)

    def test_midline_vertical_arbitrary_separation(self):
        rows = [line("a", (1, 0), (1, 8)), line("b", (4, 8), (4, 0))]
        result = derive_geometry(operation("BEND", confirmed_span=True), rows)[0]
        self.assertEqual((result["start_x"], result["end_x"]), (2.5, 2.5))
        self.assertEqual((result["start_y"], result["end_y"]), (0, 8))

    def test_preview_does_not_require_span_confirmation(self):
        rows = [line("a", (0, 0), (8, 0)), line("b", (0, 2), (8, 2))]
        self.assertEqual(preview_geometry(operation("BEND"), rows)[0]["start_y"], 1)
        with self.assertRaisesRegex(ValueError, "跨度尚未确认"):
            derive_geometry(operation("BEND"), rows)

    def test_one_side_does_not_become_midline(self):
        with self.assertRaisesRegex(ValueError, "两侧"):
            derive_geometry(operation("BEND", confirmed_span=True), [line("a", (0, 0), (8, 0))])

    def test_nonparallel_rejected(self):
        with self.assertRaisesRegex(ValueError, "不平行"):
            derive_geometry(operation("BEND", confirmed_span=True),
                            [line("a", (0, 0), (8, 0)), line("b", (0, 2), (8, 3))])

    def test_three_boundaries_rejected(self):
        with self.assertRaisesRegex(ValueError, "两条"):
            derive_geometry(operation("BEND", confirmed_span=True),
                            [line(str(y), (0, y), (8, y)) for y in (0, 2, 4)])

    def test_explicit_side_ids_must_cover_selected(self):
        rows = [line("a", (0, 0), (8, 0)), line("b", (0, 2), (8, 2))]
        with self.assertRaisesRegex(ValueError, "完整覆盖"):
            derive_geometry(operation("BEND", confirmed_span=True, side_a_ids=["a"], side_b_ids=["other"]), rows)
        result = derive_geometry(operation("BEND", confirmed_span=True, side_a_ids=["a"], side_b_ids=["b"]), rows)
        self.assertEqual(result[0]["start_y"], 1)

    def test_nonoverlap_parallel_lines_not_a_pair(self):
        with self.assertRaisesRegex(ValueError, "对应"):
            derive_geometry(operation("BEND", confirmed_span=True),
                            [line("a", (0, 0), (1, 0)), line("b", (2, 2), (3, 2))])

    def test_direct_midline_is_explicit(self):
        rows = [line("a", (8, 1), (0, 1))]
        result = derive_geometry(operation("BEND", "bend_direct"), rows)[0]
        self.assertEqual((result["start_x"], result["end_x"]), (0, 8))

    def test_direct_mode_cannot_average_two_sides(self):
        with self.assertRaisesRegex(ValueError, "共线"):
            derive_geometry(operation("BEND", "bend_direct"),
                            [line("a", (0, 0), (8, 0)), line("b", (0, 2), (8, 2))])


class BridgeAndContainmentTests(unittest.TestCase):
    def test_bridge_rectangle_and_midpoint(self):
        result = derive_geometry(operation("BRIDGE", confirmed_footprint=True), rectangle())[0]
        self.assertEqual((result["start_x"], result["start_y"], result["end_x"], result["end_y"]), (0, 0, 2, 10))
        self.assertEqual((result["center_x"], result["center_y"]), (1, 5))

    def test_bridge_preview_and_confirmation(self):
        self.assertEqual(preview_geometry(operation("BRIDGE"), rectangle())[0]["segment_type"], "BRIDGE")
        with self.assertRaisesRegex(ValueError, "尚未确认"):
            derive_geometry(operation("BRIDGE"), rectangle())

    def test_incomplete_bridge_cannot_become_large_bbox(self):
        rows = rectangle()
        rows[-1] = line("short", (0, 10), (0, 4))
        with self.assertRaisesRegex(ValueError, "缺边"):
            derive_geometry(operation("BRIDGE", confirmed_footprint=True), rows)

    def test_bridge_outer_side_can_be_split(self):
        rows = rectangle()[:-1] + [line("l1", (0, 10), (0, 4)), line("l2", (0, 4), (0, 0))]
        self.assertEqual(len(derive_geometry(operation("BRIDGE", confirmed_footprint=True), rows)), 1)

    def test_bridge_internal_lines_should_be_captured_not_selected(self):
        with self.assertRaisesRegex(ValueError, "内部线段"):
            derive_geometry(operation("BRIDGE", confirmed_footprint=True), rectangle()+[line("inside", (0, 5), (2, 5))])

    def test_internal_segment_inside_crossing_segment_outside(self):
        bbox = [0, 0, 2, 10]
        self.assertTrue(fully_contained(line("in", (0, 4), (2, 4)), bbox))
        self.assertFalse(fully_contained(line("cross", (-1, 4), (3, 4)), bbox))

    def test_arc_endpoints_inside_but_curve_crosses_box(self):
        r = math.sqrt(2)
        selected = arc("arc", (r, r), (-r, r), (0, 0), 2, 90)
        self.assertFalse(fully_contained(selected, [-1.5, 1, 1.5, 1.5]))
        self.assertAlmostEqual(segment_bbox(selected)[3], 2)

    def test_clockwise_major_arc_bbox(self):
        selected = arc("arc", (1, 0), (0, 1), (0, 0), 1, -270)
        self.assertEqual(segment_bbox(selected), [-1, -1, 1, 1])


class ContourGeometryTests(unittest.TestCase):
    def test_shuffled_reversed_contour_normalized(self):
        rows = rounded_rectangle()
        mixed = [reversed_segment(rows[i]) if i % 2 else rows[i] for i in (5, 2, 6, 1, 4, 0, 7, 3)]
        result = derive_geometry(operation("IRREGULAR_PUNCH"), mixed)
        self.assertEqual(result[0]["segment_type"], "ARC")
        self.assertEqual((result[0]["start_x"], result[0]["start_y"]), (0, 1))
        self.assertEqual([r["seq"] for r in result], list(range(1, 9)))
        for a, b in zip(result, result[1:]+result[:1]):
            self.assertEqual((a["end_x"], a["end_y"]), (b["start_x"], b["start_y"]))
        self.assertTrue(all(r["sweep_angle"] == 90 for r in result if r["segment_type"] == "ARC"))

    def test_globally_ccw_preserves_negative_concave_arc(self):
        result = derive_geometry(operation("IRREGULAR_PUNCH"), list(reversed(rounded_rectangle(True))))
        self.assertEqual([r["sweep_angle"] for r in result if r["sweep_angle"] == -180], [-180])

    def test_open_contour_rejected(self):
        with self.assertRaisesRegex(ValueError, "断口"):
            derive_geometry(operation("IRREGULAR_PUNCH"), rounded_rectangle()[:-1])

    def test_duplicate_source_rejected(self):
        rows = rounded_rectangle()
        with self.assertRaisesRegex(ValueError, "重复"):
            derive_geometry(operation("IRREGULAR_PUNCH"), rows+[rows[0]])

    def test_plain_line_contour_protocol_not_invented(self):
        with self.assertRaisesRegex(ValueError, "纯直线"):
            derive_geometry(operation("IRREGULAR_PUNCH"), rectangle())

    def test_start_arc_conflicting_with_ccw_rejected(self):
        with self.assertRaisesRegex(ValueError, "较小 x"):
            derive_geometry(operation("IRREGULAR_PUNCH", start_segment_id="a3"), rounded_rectangle())

    def test_inconsistent_arc_is_not_repaired(self):
        rows = rounded_rectangle()
        rows[0]["sweep_angle"] = -90
        with self.assertRaisesRegex(ValueError, "不一致"):
            derive_geometry(operation("IRREGULAR_PUNCH"), rows)


class RegionGeometryTests(unittest.TestCase):
    def region(self, **kwargs):
        return {"influence_region_id": "R001", "parent_region_id": None, "parent_confirmed": True,
                "confirmed": True, "affected_side": "POSITIVE_Y", "far_boundary_coord": 5, **kwargs}

    def test_region_values_and_explicit_no_parent(self):
        geom = derive_geometry(operation("BEND", "bend_direct"), [line("a", (0, 1), (8, 1))])
        result = derive_region(operation("BEND"), geom, self.region())
        self.assertEqual((result["bend_axis"], result["affected_depth"], result["bend_length"], result["affected_area_proxy"]),
                         ("HORIZONTAL", 4, 8, 32))
        self.assertIsNone(result["parent_region_id"])

    def test_parent_missing_confirmation_different_from_no_parent(self):
        geom = derive_geometry(operation("BEND", "bend_direct"), [line("a", (0, 1), (8, 1))])
        with self.assertRaisesRegex(ValueError, "父影响区"):
            derive_region(operation("BEND"), geom, self.region(parent_confirmed=False))

    def test_four_valid_sides(self):
        for side, coordinates, boundary in (("POSITIVE_Y", ((0, 1), (8, 1)), 5),
                                             ("NEGATIVE_Y", ((0, 1), (8, 1)), -5),
                                             ("POSITIVE_X", ((1, 0), (1, 8)), 5),
                                             ("NEGATIVE_X", ((1, 0), (1, 8)), -5)):
            geom = derive_geometry(operation("BEND", "bend_direct"), [line("a", *coordinates)])
            result = derive_region(operation("BEND"), geom, self.region(affected_side=side, far_boundary_coord=boundary))
            self.assertGreater(result["affected_area_proxy"], 0)

    def test_wrong_side_and_far_boundary_rejected(self):
        geom = derive_geometry(operation("BEND", "bend_direct"), [line("a", (0, 1), (8, 1))])
        for side, boundary in (("POSITIVE_X", 5), ("NEGATIVE_Y", 5), ("POSITIVE_Y", 1)):
            with self.assertRaises(ValueError):
                derive_region(operation("BEND"), geom, self.region(affected_side=side, far_boundary_coord=boundary))

    def test_diagonal_bend_region_not_fabricated(self):
        geom = derive_geometry(operation("BEND", "bend_direct"), [line("a", (0, 0), (8, 8))])
        with self.assertRaisesRegex(ValueError, "斜折弯"):
            derive_region(operation("BEND"), geom, self.region())

    def test_self_parent_and_unconfirmed_region_rejected(self):
        geom = derive_geometry(operation("BEND", "bend_direct"), [line("a", (0, 1), (8, 1))])
        for values in ({"parent_region_id": "R001"}, {"confirmed": False}):
            with self.assertRaises(ValueError):
                derive_region(operation("BEND"), geom, self.region(**values))


if __name__ == "__main__":
    unittest.main()
