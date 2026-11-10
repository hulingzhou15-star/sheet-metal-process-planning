"""Regression checks for evidence/registration boundaries, not process feasibility."""
import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
import numpy as np
from information_extract.scripts.build_process_drawing_evidence import drawing_registration

MODULE = Path(__file__).resolve().parents[1]


def read(p):
    return json.loads(p.read_text(encoding='utf-8'))


class DrawingEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = read(MODULE / 'configs/P001_process_drawing_assistance.json')
        cls.config = read(MODULE / 'configs/three_d_state_v01.json')
        cls.facts = read(MODULE / 'outputs/three_d_state_v01/P001/facts.json')
        cls.out = MODULE / cls.cfg['output']
        cls.data = read(cls.out / 'evidence.json')

    def circles(self):
        ids = self.cfg['fit_anchor_ids'] + self.cfg['held_out_anchor_ids']
        return [{'source_id': o['operation_id'], 'center': [o['segments'][0]['center_x'] + 100,
                                                          o['segments'][0]['center_y'] - 200, 0],
                 'radius': o['segments'][0]['radius']} for o in self.facts['operations'] if o['operation_id'] in ids]

    def test_held_out_error_rejects_otherwise_good_fit(self):
        cs = self.circles()
        self.assertTrue(drawing_registration(cs, self.facts, self.config, self.cfg)['independently_validated'])
        cs[-1]['center'][0] += 1
        r = drawing_registration(cs, self.facts, self.config, self.cfg)
        self.assertEqual(r['anchor_count'], 4)
        self.assertFalse(r['independently_validated'])

    def test_insufficient_anchors_do_not_create_transform(self):
        r = drawing_registration(self.circles()[:2], self.facts, self.config, self.cfg)
        self.assertFalse(r['independently_validated'])

    def test_no_scale_fitting(self):
        cs = self.circles()
        for c in cs:
            c['center'] = [v * 2 for v in c['center']]
            c['radius'] *= 2
        self.assertFalse(drawing_registration(cs, self.facts, self.config, self.cfg)['independently_validated'])

    def test_actual_views_and_withheld_points(self):
        self.assertEqual(len(self.data['views']), 9)
        good = [v for v in self.data['views'] if v['registration']['independently_validated']]
        self.assertEqual(len(good), 8)
        for v in good:
            r = v['registration']
            self.assertEqual({a['operation_id'] for a in r['anchors']}, set(self.cfg['fit_anchor_ids']))
            self.assertEqual({a['operation_id'] for a in r['held_out_checks']}, set(self.cfg['held_out_anchor_ids']))
            self.assertTrue(all(a['residual_mm'] < 1e-7 for a in r['held_out_checks']))
        self.assertFalse(next(v for v in self.data['views'] if v['id'] == 'stage01')['registration']['independently_validated'])

    def test_raw_drawing_confirms_flat_and_formed_centers_not_radii(self):
        c = self.data['local_model_comparison']
        self.assertAlmostEqual(c['flat_center_residual_mm'], .8)
        self.assertEqual([[round(m['radius_mm'], 6) for m in o['matches']] for o in c['observations']],
                         [[1.2], [1.375, 1.9], [1.2, 1.5]])
        self.assertTrue(all(m['center_residual_mm'] < 1e-7 for o in c['observations'] for m in o['matches']))

    def test_no_plan_created_and_drawings_exist(self):
        self.assertNotIn('assignments', self.data)
        self.assertEqual(self.data['usage'], 'P001_EXPERT_DRAWING_ASSISTED_MODEL_DEVELOPMENT')
        for v in self.data['views']:
            self.assertTrue(ET.parse(self.out / (v['id'] + '.svg')).getroot().tag.endswith('svg'))
            if v['registration']['independently_validated']:
                self.assertTrue((self.out / (v['id'] + '_local.svg')).exists())


if __name__ == '__main__':
    unittest.main()
