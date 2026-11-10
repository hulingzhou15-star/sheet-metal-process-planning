import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
import numpy as np
from information_extract.three_d.h6_h7_audit import (relation_coverage, find_path, descendants,
    fractions_for_stage, representative_sources, pair_changes)

OUT = Path(__file__).resolve().parents[1]/'outputs/h6_h7_audit_v01/P001'


def relation(kind,a,b):
    return SimpleNamespace(constraint_type=kind,predecessor_id=a,operation_id=b,origin_ref='test')


class H6AuditTests(unittest.TestCase):
    def test_h7_direct_transitive_and_uncovered(self):
        rows = relation_coverage([relation('H7','a','b'),relation('H7','b','c'),
            relation('H6','a','b'),relation('H6','a','c'),relation('H6','c','d')])
        self.assertEqual([r['coverage'] for r in rows],['H7_DIRECT','H7_TRANSITIVE','NOT_COVERED_BY_H7'])
        self.assertEqual(rows[1]['h7_path'],['a','b','c'])
        self.assertIsNone(rows[2]['path_without_h6'])

    def test_h6_cannot_prove_itself(self):
        rows = relation_coverage([relation('H6','a','b'),relation('H6','b','c'),relation('H6','a','c')])
        self.assertTrue(all(r['path_without_h6'] is None for r in rows))

    def test_other_rule_coverage_is_not_mislabeled_h7(self):
        rows = relation_coverage([relation('H3','a','b'),relation('H6','a','b')])
        self.assertEqual(rows[0]['coverage'],'NOT_COVERED_BY_H7')
        self.assertEqual(rows[0]['path_without_h6'],['a','b'])

    def test_path_direction_is_preserved(self):
        self.assertIsNone(find_path([('a','b')],'b','a'))

    def test_descendants_do_not_include_siblings(self):
        sk={'joints':[{'parent_region_id':a,'child_region_id':b} for a,b in [('r','a'),('r','b'),('a','c')]]}
        self.assertEqual(descendants(sk,'a'),['a','c'])

    def test_same_station_is_simultaneous_and_orders_are_numeric(self):
        sk={'joints':[{'joint_id':s} for s in ['j1','j2','j3']], 'joint_to_operation':{'j1':'a','j2':'b','j3':'c'}}
        fr=fractions_for_stage(sk,{'a':'Z','b':'Z','c':'A'},{'Z':3,'A':9},'Z',.25)
        self.assertEqual(fr,{'j1':.25,'j2':.25,'j3':0})
        fr=fractions_for_stage(sk,{'a':'Z','b':'Z','c':'A'},{'Z':3,'A':9},'A',1)
        self.assertEqual(fr,{'j1':1,'j2':1,'j3':1})

    def test_missing_geometry_not_classified_unaffected(self):
        rows,missing=representative_sources([{'operation_id':'a','formed_paths':[], 'region_id':None}])
        self.assertEqual(rows,[]);self.assertEqual(missing,['a'])

    def test_pair_examples_exclude_future_and_same_panel(self):
        sources=[{'operation_id':a,'region_id':r} for a,r in [('a','r'),('b','r'),('c','s'),('future','s')]]
        before=np.array([[0,0,0],[1,0,0],[2,0,0],[3,0,0]])
        after=np.array([[0,0,0],[1,0,0],[0,3,0],[0,10,0]])
        rows=pair_changes(sources,before,after,{'a','b','c'},{'c','future'},8)
        self.assertEqual(len(rows),2)
        self.assertTrue(all('future' not in r['operation_ids'] for r in rows))

    def test_actual_relation_proof_and_candidate_scope(self):
        summary=json.loads((OUT/'summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['h6_coverage'],{'H7_DIRECT':32})
        self.assertEqual(summary['h6_with_cad_motion_descendant_path'],32)
        self.assertEqual(summary['candidate_count'],16)
        self.assertEqual(summary['sampled_stage_states'],560)
        self.assertFalse(summary['h7_modified']);self.assertFalse(summary['whole_part_collision_checked'])
        plans=json.loads((OUT/'candidate_motion_diagnostics.json').read_text(encoding='utf-8'))
        self.assertTrue(all(p['hard_feasible'] and p['without_h6_in_memory_feasible'] for p in plans))
        self.assertTrue(all(p['local_collision_evidence']['trajectory']=='child_first' for p in plans))
        self.assertTrue(all(p['local_collision_evidence']['manufacturing_feasibility']=='UNKNOWN' for p in plans))

    def test_sources_unchanged(self):
        summary=json.loads((OUT/'summary.json').read_text(encoding='utf-8'))
        for path,expected in summary['protected_sha256'].items():
            self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(),expected,path)


if __name__=='__main__':unittest.main()
