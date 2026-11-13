import unittest
from information_extract.scripts.extract_full_expert_drawings import classify, apply_confirmed_steps, apply_p004_variant_confirmation
from information_extract.scripts.split_p003_o069 import revise
from information_extract.tests import test_p003_two_step_bend as two_step_fixture


class FullExpertTests(unittest.TestCase):
    def frame(self,station,ids,color='#00ff00',**kwargs):
        return dict(frame_id=station,station_id=station,ancillary=False,alignment_supported=True,
                    matches={sid:[{'color':color}] for sid in ids})|kwargs

    def test_full_partial_absent_multiple(self):
        op={'operation_id':'O1','name':'孔','feature_type':'IRREGULAR_PUNCH','source_segment_ids':['a','b']}
        for frames,status,station in [([self.frame('OP10',['a','b'])],'UNIQUE_FULL_HIGHLIGHT','OP10'),
                                     ([self.frame('OP10',['a'])],'PARTIAL_HIGHLIGHT',None),
                                     ([self.frame('OP10',['a','b'],'#808080')],'NO_HIGHLIGHT_MATCH',None),
                                     ([self.frame('OP10',['a','b']),self.frame('OP20',['a'])],'MULTIPLE_STATION_HIGHLIGHTS',None)]:
            row=classify(op,frames);self.assertEqual(row['status'],status);self.assertEqual(row['proposed_expert_station'],station)

    def test_ancillary_and_unregistered_frames_cannot_assign(self):
        op={'operation_id':'O1','name':'孔','feature_type':'CIRCLE_PUNCH','source_segment_ids':['a']}
        row=classify(op,[self.frame('OP10',['a'],ancillary=True),self.frame('OP20',['a'],alignment_supported=False)])
        self.assertIsNone(row['proposed_expert_station']);self.assertEqual(row['evidence'],[])

    def test_confirmed_split_preserves_raw_multiple_highlights(self):
        project = revise(two_step_fixture.TwoStepBendTests().fixture())
        rows = []
        for op in project['operations']:
            if op['operation_id'] in ('O069', 'O120'):
                op['feature_type'] = 'BEND_STRAIGHT'
                rows.append(classify(op, [self.frame('OP40', ['a']), self.frame('OP50', ['a', 'b'])]))
        apply_confirmed_steps(rows, project)
        self.assertEqual([r['proposed_expert_station'] for r in rows], ['OP40', 'OP50'])
        for row in rows:
            self.assertEqual(row['raw_matching_status'], 'MULTIPLE_STATION_HIGHLIGHTS')
            self.assertEqual([e['highlighted_source_count'] for e in row['evidence']], [1, 2])
        project['operations'][-1]['bend_angle_deg'] = 90
        with self.assertRaises(ValueError): apply_confirmed_steps(rows, project)

    def test_resolved_variant_preserves_partial_evidence_and_rejects_geometry_change(self):
        op = dict(operation_id='O077', name='刻印', feature_type='MARKING', source_segment_ids=['a','b'], expert_station='OP30')
        decision = dict(id='P004_DRAWING_VARIANTS_RESOLVED_20261112', evidence_input_hashes={},
                        expert_assignments={'O077':'OP30'}, preserved_source_ids={'O077':['a','b']},
                        explanations={'O077':'灰色材料边界不是本序刻印高亮线'})
        project = dict(operations=[op], user_decisions=[decision])
        rows = [classify(op, [self.frame('OP30',['a'])])]
        apply_p004_variant_confirmation(rows, project)
        self.assertEqual(rows[0]['raw_matching_status'], 'PARTIAL_HIGHLIGHT')
        self.assertEqual(rows[0]['evidence'][0]['highlighted_source_count'], 1)
        self.assertEqual(rows[0]['proposed_expert_station'], 'OP30')
        op['source_segment_ids'] = ['a']
        with self.assertRaises(ValueError): apply_p004_variant_confirmation(rows, project)


if __name__=='__main__':unittest.main()
