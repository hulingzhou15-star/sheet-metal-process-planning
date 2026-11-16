"""Analytic path, STEP ownership and optimizer regressions for new P002 flow."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from itertools import combinations
import math
import json
import unittest

from information_extract.three_d.artifacts import ROOT,read
from information_extract.three_d.project_geometry_scoring import (geometry_context,compile_cell_basis,
    point_to_reference,mid_reference,curved_references,recorded_circle_profile,ProjectGeometryScorer)
from information_extract.three_d.geometric_p1 import ordering
from information_extract.three_d.allocation_policy import AllocationContext,AllocationEngine
from information_extract.three_d.local_forming import load_shape,intersect
from information_extract.scripts.build_p002_material_cells import volume
from information_extract.scripts.run_geometry_allocation import verify_optimizer
from src.data.schema import Operation,Station
from src.inference.input import InferenceFacts
from src.graph.geometry_primitives import Arc,Line,geometry_primitives
from src.neighborhood.context import digest
from src.neighborhood_penalty import load_config

M=ROOT/'information_extract';P=M/'outputs/p002_generation_v01';C=M/'outputs/p002_material_cells_v01';G=M/'outputs/geometry_allocation_v1_generated/P002'


class AnalyticPathTests(unittest.TestCase):
    def test_arc_projection_is_finite(self):
        arc=Arc((0,0),2,0,math.pi/2)
        self.assertAlmostEqual(point_to_reference((3,3),[arc])['measurement'],math.sqrt(18)-2)
        self.assertAlmostEqual(point_to_reference((-3,0),[arc])['measurement'],math.sqrt(13))

    def test_disconnected_paths_have_no_implicit_connector(self):
        refs=[Line((0,0),(0,1)),Line((10,0),(10,1))]
        self.assertEqual(point_to_reference((5,0),refs)['measurement'],5)

    def test_midcurve_requires_true_parallel_or_concentric_pair(self):
        self.assertEqual(mid_reference(Line((0,0),(0,3)),Line((.4,3),(.4,0))),Line((.2,0),(.2,3)))
        self.assertIsNone(mid_reference(Line((0,0),(0,3)),Line((.4,0),(.5,3))))
        self.assertIsNone(mid_reference(Arc((0,0),2,0,1),Arc((0.01,0),2.4,0,1)))

    def test_center_roundoff_does_not_hide_different_circles(self):
        profile=recorded_circle_profile([Arc((1,2),1),Arc((1,2+1e-12),2)])
        self.assertEqual(profile['diameter'],4)
        self.assertIsNone(recorded_circle_profile([Arc((1,2),1),Arc((1,2.001),2)]))

    def test_draw_is_a_real_fragmentation_group_in_score_and_cp(self):
        data=InferenceFacts(tuple(Operation('T',x,'DRAW_FORM') for x in ('A','B')),(),
            tuple(Station('T',f'S{i}',i,frozenset(['DRAW_FORM'])) for i in (1,2)),())
        c=AllocationContext(data,(),(),'test');cfg=load_config()
        body=dict(facts_fingerprint=c.facts_fingerprint,type_counts={},pairs=[],coverage_status='covered_for_declared_types',missing_geometry_operations=[])
        catalogue={**body,'fingerprint':digest(body)}
        b=dict(schema_version='geometry_p1.v1',facts_fingerprint=c.facts_fingerprint,domains=[])
        basis={**b,'fingerprint':digest(b)};scorer=ProjectGeometryScorer(c,catalogue,cfg,basis)
        together={'A':'S1','B':'S1'};apart={'A':'S1','B':'S2'}
        self.assertEqual(scorer.evaluate(together)['secondary_exact'],'0')
        self.assertEqual(Fraction(scorer.evaluate(apart)['secondary_exact']),Fraction(1,2))
        verify_optimizer(c,catalogue,cfg,scorer,apart)


class P002GenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facts=read(P/'facts.json');cls.cells=read(C/'cells.json')
        cls.context,cls.audit=geometry_context(cls.facts,read(P/'geometry_paths.json'),read(P/'curved_side_evidence.json'))
        cls.basis=compile_cell_basis(cls.context,cls.cells)

    def test_complete_geometry_and_compound_actions(self):
        self.assertEqual(len(self.context.operations),132)
        self.assertEqual(len(self.context.part_data.geometries),132)
        self.assertEqual(len(self.basis['domains']),17)
        self.assertEqual(len(self.cells['cells']),19)
        self.assertEqual(self.audit['O136']['component_ids'],['O042','O043'])
        self.assertEqual(len(geometry_primitives(self.context.part_data.geometry_for(self.context.operations['O136']))),2)
        self.assertEqual({o:len(a['pairs']) for o,a in self.audit.items() if 'pairs' in a},{'O069':7,'O088':8,'O096':3,'O104':3})

    def test_material_tree_ownership_and_local_draw_cap(self):
        owned=[c for n in self.basis['material_tree'] for c in n['owned_cell_ids']]
        self.assertEqual(len(owned),len(set(owned)))
        self.assertEqual(set(owned),{c['cell_id'] for c in self.cells['cells']})
        domain=next(d for d in self.basis['domains'] if d['operation_id']=='O024')
        self.assertIn('P63_256',domain['region_ids'])
        self.assertEqual(len(next(d for d in self.basis['domains'] if d['operation_id']=='O136')['cell_ids']),2)

    def test_material_domains_do_not_depend_on_h7_tree(self):
        data=replace(self.context.part_data,operations=tuple(replace(o,cad_parent_operation_id=None) for o in self.context.operations.values()))
        other=AllocationContext(data,(),(),'test')
        b=compile_cell_basis(other,self.cells)
        self.assertEqual(self.basis['material_tree'],b['material_tree'])
        self.assertEqual(self.basis['domains'],b['domains'])

    def test_non_nested_material_domain_is_rejected(self):
        cells=deepcopy(self.cells)
        a,b=next(d for d in cells['domains'] if d['operation_id']=='O015'),next(d for d in cells['domains'] if d['operation_id']=='O020')
        b['cell_ids']+=a['cell_ids'][:1]
        with self.assertRaisesRegex(ValueError,'nested tree'):compile_cell_basis(self.context,cells)

    def test_saved_cells_are_disjoint_valid_positive_step_solids(self):
        from OCP.BRepCheck import BRepCheck_Analyzer
        shapes=[]
        for c in self.cells['cells']:
            s=load_shape(C/c['brep_file']);self.assertTrue(BRepCheck_Analyzer(s).IsValid())
            self.assertAlmostEqual(volume(s),c['volume_mm3'],places=7);shapes.append(s)
        self.assertLessEqual(max(volume(intersect(a,b)) for a,b in combinations(shapes,2)),1e-6)
        self.assertTrue(self.cells['all_partition_faces_owned_by_solids'])
        self.assertLess(self.cells['cell_sum_relative_error'],1e-5)

    def test_generated_pool_and_selected_scores_are_independently_valid(self):
        pool=read(G/'pool.json')['plans'];payload=read(G/'plans.json');cfg=load_config()
        scorer=ProjectGeometryScorer(self.context,read(G/'catalogue.json'),cfg,self.basis)
        self.assertEqual(len(pool),64);self.assertEqual(len(payload['plans']),16)
        self.assertEqual(len({tuple(sorted(p['assignments'].items())) for p in pool}),64)
        for p in pool:
            self.assertTrue(AllocationEngine(self.context).evaluate(p['assignments']).feasible)
            self.assertEqual(set(p['assignments']),set(self.context.operations))
            for o in ('O063','O105','O108'):self.assertEqual(p['assignments'][o],'OP10')
        for p in payload['plans']:
            score=scorer.evaluate(p['assignments'])
            # Solver wall-clock measurements are provenance, not score values.
            for key in ('p5','secondary_exact','components','area_audit','gap_audit'):
                self.assertEqual(json.loads(json.dumps(score[key])),p['score'][key])
            self.assertEqual([(g['group_id'],g['K_min'],g['K_max'],g['K_actual']) for g in score['fragmentation']['groups']],
                [(g['group_id'],g['K_min'],g['K_max'],g['K_actual']) for g in p['score']['fragmentation']['groups']])
            self.assertEqual(score['p5']['exact'],payload['primary']['minimum_exact'])
        best=next(p for p in payload['plans'] if p['plan_id']==payload['ranking'][0])
        flat=deepcopy(self.basis)
        for d in flat['domains']:d['carried_volume_mm3']=1
        flat['fingerprint']=digest({k:v for k,v in flat.items() if k!='fingerprint'})
        alternative=ProjectGeometryScorer(self.context,read(G/'catalogue.json'),cfg,flat)
        self.assertEqual(alternative.area_template['comparable_pair_count'],0)
        # Check a plan with a nonzero real P1, not an accidental zero-score plan.
        p=next(p for p in pool if scorer.evaluate(p['assignments'])['area_audit']['inversion_count']>0)
        self.assertNotEqual(scorer.evaluate(p['assignments'])['secondary_exact'],alternative.evaluate(p['assignments'])['secondary_exact'])
        verify_optimizer(self.context,read(G/'catalogue.json'),cfg,alternative,p['assignments'])


if __name__=='__main__':unittest.main()
