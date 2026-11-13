"""Material schedule integrity and independent P001 neighbourhood coverage."""
import copy
import unittest

from information_extract.scripts.experiment_bend_allocation_3d import (
    map_material_states, enumerate_neighbourhood, audit_h6, kernel_controls,
    load_inference_context, WORKBOOK, OUT, read,
)
from information_extract.three_d.band_material import state_transforms, station_fractions
import numpy as np


class MaterialScheduleTests(unittest.TestCase):
    def setUp(self):
        self.viewer = dict(model={'bends': [{'operation_id': 'B'}]},
            stations=[{'station_id': 'OP10', 'order': 1}, {'station_id': 'OP20', 'order': 2}],
            variants=[dict(material_id='EMPTY', executed_removal_ids=[], executed_forming_ids=[]),
                      dict(material_id='CUT', executed_removal_ids=['P'], executed_forming_ids=[]),
                      dict(material_id='FORM', executed_removal_ids=['P'], executed_forming_ids=['F'])])
        self.base = dict(plan_id='base', assignments={'B': 'OP20', 'P': 'OP10', 'F': 'OP20'})
        self.variant = dict(plan_id='variant', assignments={'B': 'OP10', 'P': 'OP10', 'F': 'OP20'})

    def test_entry_excludes_same_station_removal_and_forming(self):
        records = map_material_states(self.viewer, self.base, [self.variant])
        self.assertEqual([r['material_id'] for r in records], ['EMPTY', 'CUT', 'CUT', 'FORM'])
        self.assertTrue(all(r['plan_id'] == 'variant' for r in records))

    def test_missing_signature_is_not_silently_replaced(self):
        self.viewer['variants'].pop(1)
        with self.assertRaisesRegex(ValueError, 'Missing material signature'):
            map_material_states(self.viewer, self.base, [self.variant])

    def test_nonbend_move_requires_full_rebuild(self):
        self.variant['assignments']['P'] = 'OP20'
        with self.assertRaisesRegex(ValueError, 'Only bend'):
            map_material_states(self.viewer, self.base, [self.variant])

    def test_ambiguous_signature_is_rejected(self):
        self.viewer['variants'].append(copy.deepcopy(self.viewer['variants'][0]))
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            map_material_states(self.viewer, self.base, [self.variant])

    def test_bend_material_event_requires_full_rebuild(self):
        self.viewer['variants'][-1]['executed_forming_ids'].append('B')
        with self.assertRaisesRegex(ValueError, 'full rebuild'):
            map_material_states(self.viewer, self.base, [self.variant])

    def test_kernel_overlap_separation_and_contact(self):
        result = kernel_controls()
        self.assertAlmostEqual(result['overlap_mm3'], 4.)
        self.assertAlmostEqual(result['touching_mm3'], 0.)


class P001IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.viewer = read(OUT / 'viewer_data.json')
        cls.context = load_inference_context(WORKBOOK)
        cls.base = next(p for p in cls.viewer['plans'] if p['plan_id'] == 'NP2_001')
        cls.plans, cls.rejected = enumerate_neighbourhood(cls.context, cls.viewer, cls.base)

    def test_complete_neighbourhood_retains_better_and_worse_p1(self):
        self.assertEqual(len(self.plans), 32)
        self.assertEqual(len(self.rejected) + len(self.plans) - 1, 59 * 6)
        counts = [p['area_audit']['inversion_count'] for p in self.plans]
        self.assertEqual((min(counts), counts[0], max(counts)), (13, 14, 50))
        self.assertTrue(all(p['hard']['feasible'] for p in self.plans))

    def test_h6_direct_overlap_is_explicit(self):
        audit = audit_h6(self.context)
        self.assertEqual((audit['count'], audit['direct_H7_duplicate_count']), (32, 32))

    def test_new_allocation_really_changes_poses_not_material_events(self):
        states = map_material_states(self.viewer, self.base, self.plans)
        self.assertEqual(len(states), 32 * 7 * 2)
        schedule = lambda pid: [(r['station_id'], r['phase'], r['material_id']) for r in states if r['plan_id'] == pid]
        self.assertEqual(schedule('EXP_BASE'), schedule('EXP_001'))
        orders = {s['station_id']: s['order'] for s in self.viewer['stations']}
        model = self.viewer['model']
        def poses(plan):
            return state_transforms(model, station_fractions(model, plan['assignments'], orders, 'OP50', .5))
        a, b = poses(self.plans[0]), poses(self.plans[1])
        self.assertTrue(any(not np.allclose(a[r], b[r]) for r in a))


if __name__ == '__main__':
    unittest.main()
