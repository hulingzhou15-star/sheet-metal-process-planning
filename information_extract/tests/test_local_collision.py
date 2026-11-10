import json
import unittest
from pathlib import Path
import numpy as np
from information_extract.three_d.local_collision import pair_check, transformed, trajectory, band_solid, volume, valid_solid
from information_extract.three_d.finite_bend import frames, band_area
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt


def box(x, y=0, z=0):
    return BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), 1, 1, 1).Shape()


class CollisionTests(unittest.TestCase):
    def test_separated_distance(self):
        result = pair_check(box(0), box(2), 1e-6, 1e-6)
        self.assertEqual(result['status'], 'SEPARATED')
        self.assertAlmostEqual(result['distance_mm'], 1)

    def test_connected_contact_not_penetration(self):
        result = pair_check(box(0), box(1), 1e-6, 1e-6, adjacent=True)
        self.assertEqual(result['status'], 'CONNECTED_CONTACT')
        self.assertEqual(result['intersection_volume_mm3'], 0)

    def test_unconnected_touch_is_reported(self):
        self.assertEqual(pair_check(box(0), box(1), 1e-6, 1e-6)['status'], 'NONADJACENT_CONTACT')

    def test_adjacent_overlap_not_exempted(self):
        result = pair_check(box(0), box(.25), 1e-6, 1e-6, adjacent=True)
        self.assertEqual(result['status'], 'OVERLAP')
        self.assertAlmostEqual(result['intersection_volume_mm3'], .75)

    def test_full_containment_detected(self):
        a = BRepPrimAPI_MakeBox(4, 4, 4).Shape()
        result = pair_check(a, box(1, 1, 1), 1e-6, 1e-6)
        self.assertEqual(result['status'], 'OVERLAP')
        self.assertAlmostEqual(result['intersection_volume_mm3'], 1)

    def test_clear_endpoints_do_not_hide_sampled_mid_motion(self):
        samples = [pair_check(box(0), box(x), 1e-6, 1e-6)['status'] for x in [-2, 0, 2]]
        self.assertEqual(samples, ['SEPARATED', 'OVERLAP', 'SEPARATED'])

    def test_rigid_coordinate_change_preserves_intersection(self):
        tr = np.array([[0,-1,0,50],[1,0,0,-70],[0,0,1,15],[0,0,0,1]], dtype=float)
        r = pair_check(transformed(box(0), tr), transformed(box(.25), tr), 1e-6, 1e-6)
        self.assertAlmostEqual(r['intersection_volume_mm3'], .75)

    def test_three_paths_and_same_time(self):
        child = trajectory('child_first', 12)
        parent = trajectory('parent_first', 12)
        same = trajectory('simultaneous', 12)
        self.assertEqual(child[12]['fractions'], {'O009': 0, 'O010': 1})
        self.assertEqual(parent[12]['fractions'], {'O009': 1, 'O010': 0})
        self.assertTrue(all(s['fractions']['O009'] == s['fractions']['O010'] for s in same))
        self.assertEqual(child[-1]['fractions'], parent[-1]['fractions'])
        self.assertEqual(parent[-1]['fractions'], same[-1]['fractions'])
        with self.assertRaises(ValueError): trajectory('unknown', 12)

    def test_bands_valid_and_volume_converges_to_section_formula(self):
        model = json.loads((Path(__file__).resolve().parents[1]/'outputs/finite_bend_v01/P001/model.json').read_text(encoding='utf-8'))
        for fraction in (0, .5, 1):
            zones = frames(model, {'O009': fraction, 'O010': fraction})['zones']
            for i, z in enumerate(zones):
                coarse = band_solid(model, z, i, 16, 1e-6)
                fine = band_solid(model, z, i, 32, 1e-6)
                width = np.diff(model['bends'][i]['x_limits_mm'])[0]
                target = band_area(model, i, fraction)*width
                self.assertLessEqual(abs(volume(fine)-target), abs(volume(coarse)-target)+1e-10)
                self.assertLess(abs(volume(fine)-target), .002)
                valid_solid(fine)

    def test_audit_covers_all_pairs_and_preserves_unknown(self):
        path = Path(__file__).resolve().parents[1]/'outputs/local_collision_v01/P001/report.json'
        report = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(len(report['runs']), 9)
        self.assertFalse(report['candidate_filter_enabled'])
        self.assertFalse(report['sheet5_used'])
        for r in report['runs']:
            self.assertEqual(r['manufacturing_feasibility'], 'UNKNOWN')
            self.assertFalse(r['h6_relaxation_authorized'])
            self.assertEqual(r['pair_checks'], 10*r['sample_count'])
            for s in r['samples']:
                self.assertEqual(len({tuple(p['components']) for p in s['pairs']}), 10)
                self.assertTrue(all(np.isfinite(p['distance_mm']) and np.isfinite(p['intersection_volume_mm3']) for p in s['pairs']))

    def test_audit_sources_still_match(self):
        import hashlib
        report = json.loads((Path(__file__).resolve().parents[1]/'outputs/local_collision_v01/P001/report.json').read_text(encoding='utf-8'))
        for path, sha in report['source_sha256'].items():
            self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), sha, path)


if __name__ == '__main__':
    unittest.main()
