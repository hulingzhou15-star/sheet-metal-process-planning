from copy import deepcopy
from fractions import Fraction
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from information_extract.three_d.geometric_p1 import compile_basis, check_basis, geometry_tree, GeometryScorer, ordering
from information_extract.three_d.allocation_policy import load_context
from information_extract.scripts.run_geometry_allocation import prepare, verify_optimizer
from information_extract.scripts.experiment_bend_allocation_3d import WORKBOOK, OUT, FROZEN, read
from information_extract.tests import test_allocation_policy as policy_tests
from src.neighborhood.context import digest
from src.neighborhood_penalty import load_config


class GeometryP1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context=load_context(WORKBOOK)
        cls.viewer=read(OUT/'viewer_data.json')
        cls.basis=compile_basis(cls.context,cls.viewer,OUT)
        cls.config=load_config()
        cls.scorer,cls.catalogue=prepare(cls.context,cls.basis,cls.config)
        cls.plans=read(FROZEN)['plans']

    def test_all_frozen_p1_values_and_optimizer_match(self):
        from src.inference.input import load_inference_context
        from src.constraints.v06.soft_priors import area_ordering
        old=load_inference_context(WORKBOOK)
        for p in self.plans:
            result=self.scorer.evaluate(p['assignments'])
            expected=area_ordering(old,p['assignments'],1.15)
            self.assertEqual(Fraction(result['components']['area']['exact']),
                             Fraction(expected['inversion_count'],expected['comparable_pair_count']))
            self.assertEqual(result['components']['area']['weight'],.30)
            self.assertEqual(result['disabled_hard_constraints'],['H6'])
            self.assertEqual(result['area_audit']['status'],'ACTIVE_USER_ADOPTED')
        check=verify_optimizer(self.context,self.catalogue,self.config,self.scorer,self.plans[0]['assignments'])
        self.assertEqual(check['geometric_pair_count'],1451)

    def test_sheet5_and_expert_cannot_affect_formal_score_or_basis_reuse(self):
        helper=policy_tests.AllocationPolicyTests()
        expected=self.scorer.evaluate(self.plans[0]['assignments'])
        with tempfile.TemporaryDirectory() as folder:
            for mode in ('missing','corrupt','expert'):
                context=load_context(helper.workbook_variant(folder,mode))
                check_basis(context,self.basis)
                scorer=GeometryScorer(context,self.catalogue,self.config,self.basis)
                actual=scorer.evaluate(self.plans[0]['assignments'])
                self.assertEqual(actual['secondary_exact'],expected['secondary_exact'])
                self.assertEqual(actual['p5']['exact'],expected['p5']['exact'])
                self.assertEqual(actual['basis_fingerprint'],expected['basis_fingerprint'])

    def test_other_components_and_full_frozen_scores_remain_identical(self):
        from src.inference.input import load_inference_context
        from src.neighborhood import build_neighborhood, load_config as nc_load, measure_geometry
        from src.neighborhood_penalty import build_catalogue
        from src.neighborhood_penalty.scoring import Scorer
        from information_extract.three_d.artifacts import ROOT
        old=load_inference_context(WORKBOOK)
        nc=nc_load(ROOT/self.config['neighborhood_config'])
        neighborhood=build_neighborhood(old,nc,evidence=measure_geometry(old,nc))
        catalogue=build_catalogue(old,neighborhood,self.config)
        scorer=Scorer(old,catalogue,self.config)
        for p in self.plans:
            a,b=scorer.evaluate(p['assignments']),self.scorer.evaluate(p['assignments'])
            self.assertEqual(a['p5']['exact'],b['p5']['exact'])
            for k in ('area','extra_gap','group_fragmentation'):
                self.assertEqual(a['components'][k]['exact'],b['components'][k]['exact'])
                self.assertEqual(a['components'][k]['weight'],b['components'][k]['weight'])
            self.assertEqual(a['secondary_exact'],b['secondary_exact'])

    def test_actual_geometry_changes_drive_score_and_optimizer(self):
        # Equal hypothetical domains eliminate P1 pairs. If legacy areas still
        # drive either path, independent evaluation or the CP objective differs.
        basis=deepcopy(self.basis)
        for d in basis['domains']:d['carried_volume_mm3']=1.
        basis['fingerprint']=digest({k:v for k,v in basis.items() if k!='fingerprint'})
        scorer=GeometryScorer(self.context,self.catalogue,self.config,basis)
        score=scorer.evaluate(self.plans[0]['assignments'])
        self.assertEqual(score['components']['area']['exact'],'0')
        self.assertEqual(score['area_audit']['applicability'],'no_comparable_pairs')
        self.assertEqual(scorer.area_template['comparable_pair_count'],0)
        verify_optimizer(self.context,self.catalogue,self.config,scorer,self.plans[0]['assignments'])

    def test_missing_tampered_and_foreign_geometry_fails_instead_of_area_fallback(self):
        for mode in ('missing','tampered','foreign'):
            basis=deepcopy(self.basis)
            if mode=='missing':
                basis['domains'].pop()
                basis['fingerprint']=digest({k:v for k,v in basis.items() if k!='fingerprint'})
            elif mode=='tampered':basis['domains'][0]['carried_volume_mm3']*=2
            else:
                basis['facts_fingerprint']='foreign'
                basis['fingerprint']=digest({k:v for k,v in basis.items() if k!='fingerprint'})
            with self.assertRaises(ValueError):GeometryScorer(self.context,self.catalogue,self.config,basis)

    def test_infeasible_plan_rejected_before_scoring(self):
        a=dict(self.plans[0]['assignments']);r=self.context.feature_tree_relations[0]
        a[r.predecessor_id]=a[r.operation_id]
        with self.assertRaises(ValueError):self.scorer.evaluate(a)

    def test_material_tree_rejects_disconnected_or_multiple_parents(self):
        model=deepcopy(self.viewer['model'])
        model['bends'].pop()
        with self.assertRaises(ValueError):geometry_tree(model)
        model=deepcopy(self.viewer['model']);model['bends'].append(deepcopy(model['bends'][0]))
        with self.assertRaises(ValueError):geometry_tree(model)


if __name__=='__main__':unittest.main()
