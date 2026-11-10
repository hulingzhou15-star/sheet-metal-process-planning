from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import unittest

from scripts.evaluate_expert_penalty_v02 import evaluate,markdown
from src.constraints.v06 import load_rule_context
from src.inference.input import load_inference_context
from src.neighborhood_penalty import load_config
from tests.test_neighborhood_penalty_v02 import setup

ROOT=Path(__file__).resolve().parents[1]


class OfflineExpertEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.output=ROOT/'outputs/neighborhood_penalty_v02'
        cls.context=load_inference_context(ROOT/'data/parts/P001/钣金加工数据表_P001.xlsx')
        cls.expert=load_rule_context(ROOT/'data/parts/P001/钣金加工数据表_P001.xlsx').expert_reference_plan
        cls.cat=json.loads((cls.output/'p5_catalogue.json').read_text(encoding='utf-8'))
        cls.primary=json.loads((cls.output/'primary_optimization.json').read_text(encoding='utf-8'))
        cls.pool=[json.loads(s) for s in (cls.output/'candidate_pool.jsonl').read_text(encoding='utf-8').splitlines()]
        cls.config=load_config()
        cls.result=evaluate(cls.context,cls.expert,cls.cat,cls.config,cls.pool,cls.primary)

    def test_expert_hard_and_primary_admission(self):
        r=self.result
        self.assertTrue(r['hard']['feasible']);self.assertTrue(all(v==0 for v in r['hard']['violation_breakdown'].values()))
        self.assertTrue(r['primary_admitted']);self.assertEqual(r['full_score_audit']['p5']['same_station_count'],0)
        self.assertEqual(r['full_score_audit']['p5']['types']['FLANGING_IRREGULAR']['pair_count'],22)

    def test_expert_score_and_main_contribution(self):
        r=self.result;s=r['full_score_audit']
        self.assertEqual(s['components']['area']['exact'],'48/1451')
        self.assertEqual(s['components']['extra_gap']['exact'],'281/1122')
        self.assertEqual(s['components']['group_fragmentation']['exact'],'2/3')
        expected=Fraction(3,10)*Fraction(48,1451)+Fraction(1,5)*Fraction(281,1122)+Fraction(1,2)*Fraction(2,3)
        self.assertEqual(Fraction(s['secondary_exact']),expected)
        self.assertAlmostEqual(r['fragmentation_share_of_secondary'],(1/3)/float(expected))

    def test_offline_rank_not_misreported_as_hard_rejection(self):
        r=self.result
        self.assertEqual(r['pool_comparison']['expert_rank_interval'],[33,33])
        self.assertEqual(r['pool_comparison']['strictly_better_by_heuristic'],32)
        self.assertFalse(r['counterfactual_selection']['expert_selected'])
        self.assertTrue(r['counterfactual_selection']['offline_counterfactual'])
        self.assertFalse(r['counterfactual_selection']['original_pool_modified'])
        self.assertIn('当前软筛选确实可能漏掉专家方案',markdown(r))

    def test_no_weights_data_or_labels_modified(self):
        r=self.result
        self.assertFalse(r['used_for_generation'] or r['weights_changed'] or r['labels_generated'])
        self.assertEqual(self.config,load_config())
        self.assertEqual(self.pool,[json.loads(s) for s in (self.output/'candidate_pool.jsonl').read_text(encoding='utf-8').splitlines()])
        self.assertEqual(r['fragmentation_station_assignments']['CIRCLE_PUNCH']['OP10'],['O191','O192'])

    def test_hard_failure_stops_soft_scoring(self):
        c,n,cat,cfg=setup()
        r=evaluate(c,{'A':'Z'},cat,cfg,[],{'minimum_proven':True,'minimum_exact':'0'})
        self.assertEqual(r['state'],'stopped_expert_hard_failure');self.assertNotIn('full_score_audit',r)

    def test_expert_identity_does_not_force_exclusion_or_duplicate(self):
        best=min(self.pool,key=lambda r:Fraction(r['score']['secondary_exact']))
        r=evaluate(self.context,best['assignments'],self.cat,self.config,self.pool,self.primary)
        self.assertTrue(r['counterfactual_selection']['expert_selected'])
        self.assertEqual(r['counterfactual_selection']['augmented_unique_pool_size'],64)
        self.assertEqual(r['pool_comparison']['other_unique_plans'],63)

    def test_expert_counterexample_to_false_optimum_stops(self):
        primary=deepcopy(self.primary);primary['minimum_exact']='1/2'
        with self.assertRaisesRegex(ValueError,'contradicts'):
            evaluate(self.context,self.expert,self.cat,self.config,self.pool,primary)


if __name__=='__main__':unittest.main()
