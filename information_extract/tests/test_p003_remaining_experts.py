from copy import deepcopy
import unittest
from information_extract.scripts.confirm_p003_remaining_experts import revise
from information_extract.scripts.extract_full_expert_drawings import transitive_parent_conflicts, apply_remaining_expert_confirmation


class RemainingExpertsTests(unittest.TestCase):
    def test_explicit_assignment_does_not_fabricate_geometric_matches(self):
        decision = {'id':'P003_REMAINING_EXPERTS_AND_PARENT_REMOVALS_20261112', 'removed_cad_parent_links':[],
                    'expert_assignments':{'O070':'OP40','O099':'OP20','O113':'OP10'}}
        project = {'operations':[dict(operation_id=k,expert_station=v) for k,v in decision['expert_assignments'].items()], 'user_decisions':[decision]}
        rows = [dict(operation_id=k,status='NO_HIGHLIGHT_MATCH' if k=='O070' else 'MULTIPLE_STATION_HIGHLIGHTS',evidence=[]) for k in decision['expert_assignments']]
        apply_remaining_expert_confirmation(rows, project)
        self.assertEqual([r['proposed_expert_station'] for r in rows], ['OP40','OP20','OP10'])
        self.assertEqual(rows[0]['raw_matching_status'], 'NO_HIGHLIGHT_MATCH')
        self.assertEqual(rows[0]['preferred_evidence_frame_id'], 'view_05')
        self.assertTrue(all(r['evidence']==[] for r in rows))
        project['operations'][0]['expert_station']='OP50'
        with self.assertRaises(ValueError): apply_remaining_expert_confirmation(rows, project)

    def test_only_authorized_edges_and_assignments_change(self):
        ops = [dict(operation_id=f'O{i:03d}', cad_parent_operation_id=None, required_predecessor_ids=[],
                    source_segment_ids=[str(i)], bend_angle_deg=90) for i in range(1, 121)]
        by = {o['operation_id']: o for o in ops}
        by['O070']['cad_parent_operation_id'] = 'O069'
        for oid in ('O071', 'O072', 'O073', 'O074'): by[oid]['cad_parent_operation_id'] = 'O070'
        by['O075']['cad_parent_operation_id'] = 'O074'; by['O076']['cad_parent_operation_id'] = 'O075'
        by['O120']['required_predecessor_ids'] = ['O069']
        p = dict(part_id='P003', revision=8, operations=ops, station_rows=[dict(station_id=f'OP{i}0', order=i) for i in range(1, 8)])
        before = deepcopy(p); changed = revise(p); actual = {o['operation_id']: o for o in changed['operations']}
        self.assertEqual(p, before)
        for oid in ('O070','O071','O072','O073','O074'): self.assertIsNone(actual[oid]['cad_parent_operation_id'])
        self.assertEqual(actual['O075']['cad_parent_operation_id'], 'O074')
        self.assertEqual(actual['O076']['cad_parent_operation_id'], 'O075')
        self.assertEqual(actual['O120']['required_predecessor_ids'], ['O069'])
        for oid, station in [('O113','OP10'),('O099','OP20'),('O070','OP40')]: self.assertEqual(actual[oid]['expert_station'], station)
        for old in ops:
            a=deepcopy(actual[old['operation_id']]); b=deepcopy(old)
            for k in ('cad_parent_operation_id','expert_station'): a.pop(k,None); b.pop(k,None)
            self.assertEqual(a,b)
        self.assertEqual(transitive_parent_conflicts(changed, {'O074':'OP60','O075':'OP20','O076':'OP10','O069':'OP40','O070':'OP40'}), [])
        with self.assertRaises(ValueError): revise(changed)


if __name__ == '__main__': unittest.main()
