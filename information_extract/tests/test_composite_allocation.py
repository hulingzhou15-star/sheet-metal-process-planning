from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from ortools.sat.python import cp_model

from information_extract.tests.test_exporter import project, report
from information_extract.extract_app.process_config import planning_operations, inspect_process, not_later_relations
from information_extract.extract_app.allocation_constraints import add_not_later_constraints, check_not_later_constraints
from information_extract.extract_app.exporter import _rows, export_workbook
from information_extract.extract_app.workflow import compile_operation
from information_extract.scripts.revise_p002_z_bend import revise


class CompositeAllocationTests(unittest.TestCase):
    def fixture(self):
        p=project();base=p['operations'][0]
        p['operations']=[dict(deepcopy(base),uid=f'u{i}',operation_id=f'O{i:03d}',source_segment_ids=[f's{i}'],
                              cad_parent_operation_id=None,required_predecessor_ids=[],geometry_mode='bound',no_geometry=False) for i in range(1,136)]
        by={o['operation_id']:o for o in p['operations']}
        for oid in ('O040','O042','O043'):
            by[oid].update(operation_type='BEND',feature_type='BEND_STRAIGHT',geometry_shape='STRAIGHT')
        by['O105'].update(operation_type='DRAW_FORM',feature_type='DRAW_FORM',geometry_shape='MULTI_CONTOUR')
        for oid in ('O106','O107'):
            by[oid].update(operation_type='CIRCLE_PUNCH',feature_type='CIRCLE_PUNCH',geometry_shape='CIRCULAR')
        for child,parent in [('O042','O040'),('O043','O042'),('O106','O105'),('O107','O105')]:
            by[child]['cad_parent_operation_id']=parent
        p['station_rows']=[{'station_id':f'OP{i}0','order':i,'allowed_operation_types':'PUNCH, BEND, DRAW_FORM'} for i in range(1,7)]
        return p

    def test_merge_scope_and_relationships(self):
        original=self.fixture();updated=revise(original)
        by={o['operation_id']:o for o in updated['operations']}
        self.assertEqual(len(updated['operations']),136)
        self.assertEqual(len(planning_operations(updated)),134)
        self.assertEqual(by['O136']['cad_parent_operation_id'],'O040')
        self.assertEqual(by['O136']['source_segment_ids'],['s42','s43'])
        for oid in ('O042','O043'):
            actual=deepcopy(by[oid]);actual.pop('superseded_by_operation_id')
            # annotation_project may normalize feature metadata; original facts remain.
            self.assertEqual(actual['source_segment_ids'],original['operations'][int(oid[1:])-1]['source_segment_ids'])
        self.assertEqual(by['O043']['cad_parent_operation_id'],'O042')
        self.assertIsNone(by['O106']['cad_parent_operation_id'])
        self.assertEqual(by['O107']['not_later_than_operation_ids'],['O105'])
        self.assertEqual(inspect_process(updated)['station_errors'],[])
        with self.assertRaises(ValueError):revise(updated)
        rows=_rows(updated,{'geometries':{},'regions':[]})
        ids={r['operation_id'] for r in rows['Sheet1']}
        self.assertEqual(len(ids),134)
        self.assertFalse(ids & {'O042','O043'})

    def test_same_stage_allowed_later_forbidden_and_h7_retained(self):
        p=revise(self.fixture());by={o['operation_id']:o for o in p['operations']}
        for oid in ('O105','O106','O107'):by[oid]['expert_station']='OP10'
        by['O040']['expert_station']='OP60'
        self.assertEqual(inspect_process(p)['reference_issues'],[])
        by['O106']['expert_station']='OP40'
        issues=inspect_process(p)['reference_issues']
        self.assertEqual(len(issues),1);self.assertIn('不晚于',issues[0])
        by['O040']['expert_station']='OP50'
        self.assertTrue(any('O136 → O040' in s and '严格先后' in s for s in inspect_process(p)['reference_issues']))

    def test_solver_and_independent_checker_enforce_non_strict_relation(self):
        relations=[{'operation_id':'hole','not_later_than':'draw'}]
        for hole,draw,feasible in [(0,0,True),(0,1,True),(1,0,False)]:
            model=cp_model.CpModel();ranks={k:model.new_int_var(0,1,k) for k in ('hole','draw')}
            model.add(ranks['hole']==hole);model.add(ranks['draw']==draw)
            add_not_later_constraints(model,ranks,relations)
            status=cp_model.CpSolver().solve(model)
            self.assertEqual(status in (cp_model.OPTIMAL,cp_model.FEASIBLE),feasible)
            check=check_not_later_constraints({'hole':f'OP{hole}','draw':f'OP{draw}'},{'OP0':0,'OP1':1},relations)
            self.assertEqual(check['feasible'],feasible)

    def test_capacity_conflict_is_independent_of_expert_assignment(self):
        p=revise(self.fixture())
        for station in p['station_rows']:
            station['allowed_operation_types']='DRAW_FORM' if station['order']==1 else 'PUNCH, BEND'
        issues=inspect_process(p)['relation_capacity_issues']
        self.assertEqual(len(issues),2)
        self.assertTrue(all('不存在满足不晚于关系的分配' in s for s in issues))
        p['station_rows'][0]['allowed_operation_types']='DRAW_FORM, PUNCH'
        self.assertEqual(inspect_process(p)['relation_capacity_issues'],[])

    def test_z_geometry_does_not_become_one_bend_line(self):
        p=revise(self.fixture());op=p['operations'][-1]
        sources=[{'id':sid,'segment_type':'LINE','start_x':0,'end_x':3,'start_y':i,'end_y':i} for i,sid in enumerate(op['source_segment_ids'])]
        record=compile_operation(op,sources,{'geometry_tolerance':1e-6})
        self.assertFalse(record['legacy_supported'])
        self.assertEqual(record['component_operation_ids'],['O042','O043'])
        self.assertEqual(len(record['paths'][0]['segments']),2)
        self.assertEqual(record['legacy_geometry'],[])

    def test_export_relations_as_required_facts_without_reference(self):
        p=project();other=dict(deepcopy(p['operations'][0]),uid='other',operation_id='O002')
        p['operations'].append(other);p['operations'][0]['not_later_than_operation_ids']=['O002']
        root=Path(__file__).resolve().parents[1]/'outputs/test_tmp';root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            result=export_workbook(p,report(),Path(directory)/'outputs',module_root=directory,mode='draft')
            folder=Path(result['directory'])
            facts=json.loads((folder/'allocation_relations.json').read_text(encoding='utf-8'))
            self.assertEqual(facts['not_later_than'],not_later_relations(p))
            self.assertFalse(facts['expert_reference_included'])
            manifest=json.loads((folder/'source_manifest.json').read_text(encoding='utf-8'))
            self.assertFalse(manifest['workbook_alone_is_complete_allocation_input'])

    def test_formal_solver_consumes_extra_relation_and_preserves_h7(self):
        p=project();p['operations'].append(dict(deepcopy(p['operations'][0]),uid='other',operation_id='O002'))
        p['operations'][0]['cad_parent_operation_id']='O002'
        p['station_rows'].append(dict(p['station_rows'][0],station_id='OP20',order=20))
        root=Path(__file__).resolve().parents[1]/'outputs/test_tmp'
        with tempfile.TemporaryDirectory(dir=root) as directory:
            p['operations'][0]['not_later_than_operation_ids']=['O002']
            result=export_workbook(p,report(),Path(directory)/'outputs',module_root=directory,mode='validated')
            self.assertTrue(result['ready'],result['report'])
            self.assertTrue(result['report']['hard_feasibility']['additional_relation_check']['feasible'])
            p['operations'][0]['not_later_than_operation_ids']=[]
            p['operations'][1]['not_later_than_operation_ids']=['O001']
            result=export_workbook(p,report(),Path(directory)/'outputs',module_root=directory,mode='validated')
            self.assertEqual(result['report']['hard_feasibility']['status'],'INFEASIBLE')
            self.assertFalse(result['ready'])


if __name__=='__main__':unittest.main()
