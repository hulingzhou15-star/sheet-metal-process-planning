from copy import deepcopy
import unittest
from information_extract.scripts.reconcile_p003_expert_schedule import compare_windows, revise, revise_window_mapping, WINDOW_STATIONS
from information_extract.tests import test_p003_two_step_bend as fixture
from information_extract.scripts.split_p003_o069 import revise as split
from information_extract.scripts.extract_full_expert_drawings import transitive_parent_conflicts


class ScheduleReconciliationTests(unittest.TestCase):
    def test_explicit_window_map_supersedes_shifted_steps(self):
        p = split(fixture.TwoStepBendTests().fixture()); p['revision'] = 7
        by = {o['operation_id']: o for o in p['operations']}
        by['O069']['expert_station'] = 'OP30'; by['O120']['expert_station'] = 'OP40'
        updated = revise_window_mapping(p); result = {o['operation_id']: o for o in updated['operations']}
        self.assertEqual([WINDOW_STATIONS[str(i)] for i in (1,2,4,5,6,7)], ['OP10','OP20','OP30','OP40','OP50','OP60'])
        self.assertIsNone(WINDOW_STATIONS['3']); self.assertIsNone(WINDOW_STATIONS['8'])
        self.assertEqual(result['O069']['expert_station'], 'OP40'); self.assertEqual(result['O120']['expert_station'], 'OP50')
        for original in p['operations']:
            a = deepcopy(result[original['operation_id']]); b = deepcopy(original)
            a.pop('expert_station', None); b.pop('expert_station', None)
            self.assertEqual(a, b)
        self.assertFalse(any(o.get('expert_station')=='OP70' for o in updated['operations']))

    def test_h7_through_unassigned_intermediate(self):
        p = {'operations': [dict(operation_id='A', cad_parent_operation_id='B'), dict(operation_id='B', cad_parent_operation_id='C'), dict(operation_id='C', cad_parent_operation_id=None)],
             'station_rows': [dict(station_id=f'OP{i}0', order=i) for i in range(1, 8)]}
        self.assertEqual(transitive_parent_conflicts(p, {'A': 'OP10', 'C': 'OP30'}), [])
        self.assertEqual(transitive_parent_conflicts(p, {'A': 'OP60', 'C': 'OP30'})[0]['chain'], ['A', 'B', 'C'])
        self.assertEqual(len(transitive_parent_conflicts(p, {'A': 'OP10', 'C': 'OP20'})), 1)

    def test_window_subset_requires_geometry_and_highlight(self):
        def frame(fid, color):
            g = {'kind': 'CIRCLE', 'points': [[1, 2]], 'radius': 2, 'color': color}
            return dict(frame_id=fid, matrix=[[1, 0], [0, 1]], translation=[0, 0], targets=[g], matches={'s': [g]})
        project = {'operations': [dict(operation_id='O1', name='翻孔', operation_type='FLANGING', source_segment_ids=['s'])]}
        e = {'frames': [frame('view_02', '#ff00ff'), frame('view_03', '#ff00ff')]}
        a = compare_windows(e, project)
        self.assertTrue(a['all_same_highlight_present']); self.assertEqual(a['action_count'], 1)
        e['frames'][0]['targets'][0]['color'] = '#808080'
        a = compare_windows(e, project)
        self.assertTrue(a['all_geometry_present']); self.assertFalse(a['all_same_highlight_present'])
        e['frames'][0]['targets'][0]['radius'] = 3
        self.assertFalse(compare_windows(e, project)['all_geometry_present'])

    def test_schedule_update_preserves_geometry_angles_and_h7(self):
        p = split(fixture.TwoStepBendTests().fixture()); p['revision'] = 6
        p['station_rows'] = [dict(station_id=f'OP{i}0', order=i, allowed_operation_types='BEND') for i in range(1, 8)]
        before = deepcopy(p)
        assignments = {'operations': [dict(operation_id=o['operation_id'], proposed_expert_station=None) for o in p['operations']]}
        audit = dict(all_geometry_present=True, all_same_highlight_present=True, highlighted_primitive_count=58, action_count=29)
        after = revise(p, assignments, audit)
        self.assertEqual(p, before)
        self.assertEqual([s['allowed_operation_types'] for s in after['station_rows']], ['PUNCH', 'PUNCH, FLANGING, DIMPLE', 'BEND', 'BEND', 'BEND', 'BEND', 'NONE'])
        by = {o['operation_id']: o for o in after['operations']}
        for o in p['operations']:
            actual = deepcopy(by[o['operation_id']]); expected = deepcopy(o)
            actual.pop('expert_station', None); expected.pop('expert_station', None)
            self.assertEqual(actual, expected)
        self.assertEqual(by['O069']['expert_station'], 'OP30'); self.assertEqual(by['O120']['expert_station'], 'OP40')
        self.assertEqual(by['O071']['expert_station'], 'OP60')
        self.assertNotIn('expert_station', by['O070'])
        audit['all_same_highlight_present'] = False
        with self.assertRaises(ValueError): revise(p, assignments, audit)


if __name__ == '__main__': unittest.main()
