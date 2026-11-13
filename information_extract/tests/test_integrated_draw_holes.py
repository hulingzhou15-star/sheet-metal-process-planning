from copy import deepcopy
import unittest

from information_extract.tests.test_composite_allocation import CompositeAllocationTests
from information_extract.scripts.revise_p002_z_bend import revise
from information_extract.scripts.integrate_p002_draw_holes import integrate
from information_extract.extract_app.process_config import inspect_process, planning_operations, not_later_relations
from information_extract.extract_app.exporter import _rows
from information_extract.extract_app.workflow import compile_operation
from information_extract.scripts.build_p002_expert_review import apply_user_decisions


class IntegratedDrawTests(unittest.TestCase):
    def previous(self):
        return revise(CompositeAllocationTests().fixture())

    def test_scope_and_original_holes_preserved(self):
        previous=self.previous();p=integrate(previous)
        by={o['operation_id']:o for o in p['operations']}
        old={o['operation_id']:o for o in previous['operations']}
        self.assertEqual(len(p['operations']),136)
        self.assertEqual(len(planning_operations(p)),132)
        self.assertEqual(not_later_relations(p),[])
        self.assertEqual(by['O105']['expert_station'],'OP10')
        self.assertEqual(set(by['O105']['source_segment_ids']),{'s105','s106','s107'})
        for oid in ('O106','O107'):
            actual=deepcopy(by[oid]);actual.pop('superseded_by_operation_id');actual['not_later_than_operation_ids']=['O105']
            self.assertEqual(actual,old[oid])
        for station in p['station_rows']:
            station['allowed_operation_types']='DRAW_FORM' if station['order']==1 else 'PUNCH, BEND'
        checked=inspect_process(p)
        self.assertEqual(checked['station_errors'],[])
        self.assertEqual(checked['relation_capacity_issues'],[])
        self.assertEqual(checked['reference_issues'],[])
        ids={r['operation_id'] for r in _rows(p,{'geometries':{},'regions':[]})['Sheet1']}
        self.assertEqual(len(ids),132)
        self.assertFalse(ids & {'O042','O043','O106','O107'})
        with self.assertRaises(ValueError):integrate(p)
        by['O105']['source_segment_ids'].remove('s106')
        self.assertTrue(inspect_process(p)['station_errors'])

    def test_compile_keeps_hole_paths_and_radii(self):
        p=self.previous();by={o['operation_id']:o for o in p['operations']}
        points=[(0,0),(8,0),(8,8),(0,8)]
        sources=[{'id':f'e{i}','segment_type':'LINE','start_x':a[0],'start_y':a[1],'end_x':b[0],'end_y':b[1]} for i,(a,b) in enumerate(zip(points,points[1:]+points[:1]))]
        sources += [{'id':f'h{i}','segment_type':'CIRCLE','center_x':x,'center_y':3,'radius':r} for i,x,r in [(0,2,.5),(1,6,1)]]
        by['O105']['source_segment_ids']=[f'e{i}' for i in range(4)]
        by['O106']['source_segment_ids']=['h0'];by['O107']['source_segment_ids']=['h1']
        merged=next(o for o in integrate(p)['operations'] if o['operation_id']=='O105')
        record=compile_operation(merged,sources,{'geometry_tolerance':1e-6})
        self.assertFalse(record['legacy_supported'])
        self.assertEqual(len(record['paths']),3)
        holes=[p for p in record['paths'] if p['role']=='INTEGRATED_HOLE']
        self.assertEqual(sorted(p['segments'][0]['radius'] for p in holes),[.5,1])
        self.assertEqual(sum(p['role']=='DRAW_FORM_PROFILE' for p in record['paths']),1)

    def test_expert_override_does_not_forge_full_geometry_match(self):
        op={'operation_id':'O105','operation_type':'DRAW_FORM','composite_policy':'INTEGRATED_DRAW_FORM',
            'composite_operation_ids':['O106','O107'],'primary_source_segment_ids':['body']}
        row={'operation_id':'O105','status':'MULTIPLE_STATION_HIGHLIGHTS','source_count':3,
             'evidence':[{'station_id':'OP10','highlighted_source_count':1,'highlighted_source_ids':['body']},
                         {'station_id':'OP40','highlighted_source_count':2,'highlighted_source_ids':['hole1','hole2']}]}
        decision={'decisions':[{'id':'user','operation_ids':['O105'],'component_operation_ids':['O106','O107'],
                               'operation_type':'DRAW_FORM','station_id':'OP10','resolution_method':'USER_INTEGRATED_DRAW_HOLES','quote':'两孔视作拉包一部分'}]}
        before=deepcopy(row['evidence']);apply_user_decisions([row],[op],decision)
        self.assertEqual(row['proposed_expert_station'],'OP10')
        self.assertEqual(row['geometric_status'],'MULTIPLE_STATION_HIGHLIGHTS')
        self.assertEqual(row['evidence'],before)
        op['primary_source_segment_ids']=['unmatched']
        with self.assertRaises(ValueError):apply_user_decisions([row],[op],decision)


if __name__=='__main__':unittest.main()
