import copy
import json
import math
from pathlib import Path
import unittest
import numpy as np
from information_extract.three_d.finite_bend import frames, panel_transforms, bend_point, band_area

OUT = Path(__file__).resolve().parents[1] / 'outputs/finite_bend_v01/P001'


class FiniteBendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = json.loads((OUT / 'model.json').read_text(encoding='utf-8'))
        cls.validation = json.loads((OUT / 'validation.json').read_text(encoding='utf-8'))

    def test_flat_middle_final_feature_positions(self):
        for fr, expected in [({'O009': 0, 'O010': 0}, [354.8, .3, 0]),
                             ({'O009': 0, 'O010': 1}, [354.8, -4.1, -5.6]),
                             ({'O009': 1, 'O010': 1}, [354.8, -8.9, 10])]:
            point = (panel_transforms(self.model, fr)[-1] @ [354.8, -8.9, 10, 1])[:3]
            np.testing.assert_allclose(point, expected, atol=1e-8, rtol=0)

    def test_flat_coordinates_have_no_fitted_global_translation(self):
        np.testing.assert_allclose(self.model['root_start_top_yz_mm'], [-14.5, 0], atol=1e-8)
        for b in self.model['bends']:
            self.assertAlmostEqual(b['developed_width_mm'], .4)
            self.assertEqual(len(b['boundary_source_ids']), 2)

    def test_band_joins_panels_continuously_at_all_angles(self):
        for a in np.linspace(0, 1, 11):
            for b in [0, .37, 1]:
                f = frames(self.model, {'O009': float(a), 'O010': b})
                for z, panel in zip(f['zones'], f['panels']):
                    np.testing.assert_allclose(bend_point(**z, u=0), z['start'], atol=1e-10)
                    np.testing.assert_allclose(bend_point(**z), panel['start_top_yz_mm'], atol=1e-10)
                p = np.array(f['panels'][0]['start_top_yz_mm']); phi = f['panels'][0]['phi']
                np.testing.assert_allclose(f['zones'][1]['start'], p + self.model['middle_straight_length_mm'] * np.array([math.cos(phi), math.sin(phi)]), atol=1e-10)

    def test_thickness_is_constant_but_not_claimed_volume_conserving(self):
        for z in frames(self.model, {'O009': .43, 'O010': .72})['zones']:
            for u in np.linspace(0, 1, 11):
                self.assertAlmostEqual(float(np.linalg.norm(bend_point(**z, u=float(u)) - bend_point(**z, depth=.8, u=float(u)))), .8)
        self.assertGreater(band_area(self.model, 0, 1) / band_area(self.model, 0, 0) - 1, .57)
        self.assertIn('FAIL', self.validation['physical_gate'])
        self.assertFalse(self.model['collision_filter_enabled'])
        self.assertFalse(self.model['physical_forming_validated'])

    def test_zero_angle_limit_is_continuous(self):
        z = {'start': [-14.5, 0], 'phi': 0, 'angle': 0, 'length': .4, 'h': .25}
        np.testing.assert_allclose(bend_point(**z), [-14.1, 0], atol=1e-12)
        z['angle'] = 1e-12
        np.testing.assert_allclose(bend_point(**z), [-14.1, 0], atol=1e-11)

    def test_all_joints_and_valid_fractions_required(self):
        for f in [{}, {'O009': 1}, {'O009': -1, 'O010': 0}, {'O009': float('nan'), 'O010': 0}]:
            with self.assertRaises(ValueError): frames(self.model, f)

    def test_same_time_joint_progress_is_composed(self):
        f = frames(self.model, {'O009': .5, 'O010': .5})
        self.assertAlmostEqual(f['panels'][0]['phi'], math.pi / 4)
        self.assertAlmostEqual(f['panels'][1]['phi'], 0)

    def test_width_perturbation_does_not_auto_fit_held_out_feature(self):
        model = copy.deepcopy(self.model)
        model['bends'][0]['developed_width_mm'] += .1
        point = (panel_transforms(model, {'O009': 0, 'O010': 0})[-1] @ [354.8, -8.9, 10, 1])[:3]
        self.assertGreater(float(np.linalg.norm(point - [354.8, .3, 0])), .05)

    def test_actual_drawing_and_cad_validation(self):
        self.assertEqual(len(self.validation['checks']), 7)
        self.assertTrue(all(c['passed'] for c in self.validation['checks']))
        self.assertLess(self.validation['max_geometric_residual_mm'], 1e-7)
        for b in self.model['bends']:
            self.assertEqual(b['inner_radius_in_exported_cad_mm'], 0)
            self.assertTrue(b['inner_sharp_edge_evidence'])


if __name__ == '__main__': unittest.main()
