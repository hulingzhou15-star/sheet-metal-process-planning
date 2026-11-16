"""Regression checks for the non-rigid P002 paths and sampled contact."""
import unittest
import numpy as np

from information_extract.scripts.check_p002_curve_motion import setup
from information_extract.three_d.local_forming import box
from information_extract.three_d.meshing import mesh
from information_extract.three_d.p002_curve_motion import bind_vertices, frame, sweep
from information_extract.three_d.p002_motion_reference import CHECK_CONFIG
from information_extract.three_d.sampled_mesh_contact import contact, triangle_bounds


class CurveMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model, plan_data, _, cls.solids, _ = setup()
        cls.plans = plan_data['plans']

    def test_all_curve_walls_reach_step_and_move_at_intermediate_angle(self):
        for cid, cfg in self.model['curve_guides'].items():
            triangles = np.asarray(mesh(self.solids[cid],CHECK_CONFIG)['triangles'])
            points = triangles.reshape(-1,3)
            binding = bind_vertices(points,cfg['guides'])
            np.testing.assert_array_equal(sweep(points,binding,1,cfg['z_sign']),points)
            self.assertGreater(np.linalg.norm(sweep(points,binding,.5,cfg['z_sign'])-points),1.)
            duplicate = np.stack([points[0],points[0]])
            moved = sweep(duplicate,bind_vertices(duplicate,cfg['guides']),.5,cfg['z_sign'])
            np.testing.assert_array_equal(moved[0],moved[1])

    def test_carrier_stays_continuous_from_entry_to_completion(self):
        for plan in self.plans:
            a = plan['assignments']
            s = a['O088']
            start = frame(self.model,plan,s,'entry',0)
            near = frame(self.model,plan,s,'motion',1e-8)
            end = frame(self.model,plan,s,'completed',1)
            np.testing.assert_allclose(start['transforms']['C007'],near['transforms']['C007'],atol=2e-6)
            np.testing.assert_allclose(end['transforms']['C007'],np.eye(4),atol=1e-10)
            self.assertEqual(near['curve_fractions']['C004'],1e-8)
            self.assertNotIn('O088',near['executed_operation_ids'])
            self.assertIn('O094',near['executed_operation_ids'])
            self.assertEqual(near['unknown_cells'],{})


class MeshContactTests(unittest.TestCase):
    def check_boxes(self, lo, hi):
        a = np.asarray(mesh(box([0,0,0],[2,2,2]),CHECK_CONFIG)['triangles'])
        b = np.asarray(mesh(box(lo,hi),CHECK_CONFIG)['triangles'])
        return contact(a,b,triangle_bounds(a),triangle_bounds(b))

    def test_disjoint_and_coplanar_touch(self):
        self.assertEqual(self.check_boxes([3,0,0],[4,1,1]),'AABB_SEPARATED')
        self.assertEqual(self.check_boxes([2,.1,.1],[3,1.5,1.5]),'MESH_CONTACT_OR_EDGE_CROSSING')

    def test_surface_crossing_and_containment(self):
        self.assertEqual(self.check_boxes([1,.35,.45],[3,1.30,1.60]),'MESH_CROSSING')
        self.assertEqual(self.check_boxes([.2,.3,.4],[.5,.6,.7]),'MESH_CONTAINMENT')


if __name__ == '__main__':
    unittest.main()
