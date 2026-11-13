from copy import deepcopy
import unittest

from information_extract.scripts.split_p003_o069 import revise


class TwoStepBendTests(unittest.TestCase):
    def fixture(self):
        ops = [dict(uid=str(i), operation_id=f'O{i:03d}', name='original', operation_type='BEND',
                    surface_side='SMOOTH', bend_angle_deg=90, source_segment_ids=[str(i)],
                    cad_parent_operation_id=None, required_predecessor_ids=[]) for i in range(1, 120)]
        ops[68].update(name='光面一般折弯4', source_segment_ids=['a', 'b'], required_predecessor_ids=['O109', 'O110'])
        ops[69]['cad_parent_operation_id'] = 'O069'
        return dict(part_id='P003', operations=ops, expected_operation_count=None)

    def test_shared_geometry_increment_and_preserved_relations(self):
        previous = self.fixture(); snapshot = deepcopy(previous); changed = revise(previous)
        self.assertEqual(previous, snapshot)
        by = {o['operation_id']: o for o in changed['operations']}
        self.assertEqual(len(by), 120)
        self.assertEqual(by['O069']['bend_angle_deg'], 135)
        self.assertEqual((by['O120']['name'], by['O120']['surface_side'], by['O120']['bend_angle_deg']), ('光面一般折弯4', 'ROUGH', 45))
        self.assertEqual(by['O069']['required_predecessor_ids'], ['O109', 'O110'])
        self.assertEqual(by['O120']['required_predecessor_ids'], ['O069'])
        self.assertIsNone(by['O120']['cad_parent_operation_id'])
        for op in previous['operations']:
            if op['operation_id'] != 'O069': self.assertEqual(by[op['operation_id']], op)
        for oid in ('O069', 'O120'):
            self.assertEqual(by[oid]['source_segment_ids'], ['a', 'b'])
            self.assertEqual(by[oid]['shared_source_segment_ids'], ['a', 'b'])
        steps = changed['user_decisions'][-1]['steps']
        self.assertEqual([s['expert_station'] for s in steps], ['OP40', 'OP50'])
        self.assertEqual(sum(s['signed_increment_deg'] for s in steps), 90)
        with self.assertRaises(ValueError): revise(changed)


if __name__ == '__main__': unittest.main()
