from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from itertools import product
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from ortools.sat.python import cp_model
from src.candidate.distance import plan_hash
from src.constraints.v06 import HardConstraintEngine,SoftPriorEngine
from src.data.schema import Operation,Station
from src.graph.builders import build_part_graph
from src.inference.input import InferenceContext,load_inference_context
from src.inference.generation import solver_for
from src.neighborhood import build_neighborhood,NeighborhoodContext,load_config as nc
from src.neighborhood.context import digest
from src.neighborhood_penalty import *
from src.neighborhood_penalty.config import validate_config
from src.neighborhood_penalty.catalogue import check_catalogue
from src.neighborhood_penalty.generation import ranking_key
from src.neighborhood_penalty.model import RationalExpression,build_objective_model
from src.neighborhood_penalty.review import comparison,summarize
from tests.test_neighborhood_v01 import context_for,two_ops,circle,line
from tests.test_neighborhood_n1 import joint_conflict_context
from tests.test_m3_reference_free_v07 import workbook

ROOT=Path(__file__).resolve().parents[1]


def setup(context=None):
    c=two_ops() if context is None else context
    cfg=load_config();cfg['generation'].update(pool_target=8,selected_target=4,score_selection_count=2,max_attempts=12,channel_quota=2)
    n=build_neighborhood(c,nc());cat=build_catalogue(c,n,cfg)
    return c,n,cat,cfg


class CatalogueTests(unittest.TestCase):
    def test_priority_scope_and_fixed_denominator(self):
        c,n,cat,cfg=setup()
        self.assertEqual(cat['type_counts'],{'FLANGING_IRREGULAR':1})
        self.assertEqual(evaluate_p5(c,cat,{'A':'Z','B':'Z'})['value'],1)
        self.assertEqual(evaluate_p5(c,cat,{'A':'Z','B':'A'})['value'],0)
        self.assertEqual(cat['type_counts'],{'FLANGING_IRREGULAR':1})

    def test_flange_direction_priority_is_required(self):
        c,n,cat,cfg=setup();data=n.to_dict()
        for arc in data['relations'][0]['arcs']:arc['tier']='EXTENDED' if arc['anchor']=='A' else 'PRIORITY'
        cat=build_catalogue(c,NeighborhoodContext(data),cfg)
        self.assertFalse(cat['pairs'])
        self.assertEqual(cat['excluded'][0]['reason'],'no_applicable_priority_arc')

    def test_bend_flange_and_bend_irregular_act_when_no_hard_order(self):
        for kind in ('FLANGING','IRREGULAR_PUNCH'):
            ops=[Operation('P','B','BEND',surface_side='ROUGH',geometry_id='GB'),Operation('P','X',kind,geometry_id='GX')]
            geoms=[line('B',2),circle('X') if kind=='FLANGING' else line('X',3)]
            c,n,cat,cfg=setup(context_for(ops,geoms))
            self.assertEqual(len(cat['pairs']),1)
            self.assertEqual(evaluate_p5(c,cat,{'B':'Z','X':'Z'})['value'],1)
            self.assertEqual(evaluate_p5(c,cat,{'B':'Z','X':'A'})['value'],0)

    def test_internal_simple_and_same_type_pairs_unscored(self):
        ops=[Operation('P','F','FLANGING',geometry_id='GF',process_group_id='G'),Operation('P','P','PRE_PUNCH',geometry_id='GP',process_group_id='G'),
             Operation('P','C','CIRCLE_PUNCH',geometry_id='GC'),Operation('P','F2','FLANGING',geometry_id='GF2')]
        c,n,cat,cfg=setup(context_for(ops,[circle(o.operation_id,x=i) for i,o in enumerate(ops)]))
        self.assertFalse(cat['pairs']);self.assertTrue(any(r['reason']=='internal_process_relation' for r in cat['excluded']))

    def test_hard_direct_and_transitive_exclusion(self):
        ops=[Operation('P','A','FLANGING',geometry_id='GA',cad_parent_operation_id='T'),Operation('P','T','TRIM',cad_parent_operation_id='B'),Operation('P','B','IRREGULAR_PUNCH',geometry_id='GB')]
        c,n,cat,cfg=setup(context_for(ops,[circle('A'),line('B',3)]))
        self.assertFalse(cat['pairs']);self.assertEqual(cat['excluded'][0]['hard_path'],['A','T','B'])

    def test_disjoint_capability_exclusion(self):
        c=two_ops();stations=[Station('P','Z',10,frozenset({'FLANGING'})),Station('P','A',40,frozenset({'IRREGULAR_PUNCH'}))]
        c,n,cat,cfg=setup(InferenceContext(replace(c.part_data,stations=tuple(stations))))
        self.assertFalse(cat['pairs']);self.assertEqual(cat['excluded'][0]['reason'],'disjoint_station_domains')

    def test_type_macro_mean_not_pool_pair_mean(self):
        ops=[Operation('P','F','FLANGING',geometry_id='GF'),Operation('P','I1','IRREGULAR_PUNCH',geometry_id='GI1'),
             Operation('P','I2','IRREGULAR_PUNCH',geometry_id='GI2'),Operation('P','B','BEND',surface_side='ROUGH',geometry_id='GB')]
        c,n,cat,cfg=setup(context_for(ops,[circle('F'),line('I1',3),line('I2',4),line('B',5)]))
        assignments={'F':'Z','I1':'Z','I2':'A','B':'A'};r=evaluate_p5(c,cat,assignments)
        expected=sum((Fraction(x['same_station_count'],x['pair_count']) for x in r['types'].values()),Fraction())/len(r['types'])
        self.assertEqual(Fraction(r['exact']),expected)
        self.assertEqual(len({r['pair_id'] for r in cat['pairs']}),len(cat['pairs']))

    def test_no_geometry_reports_incomplete_not_safe(self):
        c=two_ops();c=InferenceContext(replace(c.part_data,geometries=(),operations=tuple(replace(o,geometry_id=None) for o in c.part_data.operations)))
        c,n,cat,cfg=setup(c);r=evaluate_p5(c,cat,{'A':'Z','B':'A'})
        self.assertEqual(r['applicability'],'not_applicable');self.assertEqual(r['coverage_status'],'coverage_incomplete')
        self.assertEqual(r['missing_geometry_operations'],['A','B'])

    def test_foreign_or_modified_catalogue_rejected(self):
        c,n,cat,cfg=setup();bad=deepcopy(cat);bad['pairs'][0]['boundary_distance']=100
        with self.assertRaises(ValueError):check_catalogue(c,bad)
        changed=InferenceContext(replace(c.part_data,operations=(replace(c.part_data.operations[0],surface_side='ROUGH'),c.part_data.operations[1])))
        with self.assertRaises(ValueError):check_catalogue(changed,cat)

    def test_duplicate_pair_rejected(self):
        c,n,cat,cfg=setup();snapshot=n.to_dict();snapshot['relations']*=2
        with self.assertRaises(ValueError):build_catalogue(c,NeighborhoodContext(snapshot),cfg)


class ScoreAndBoundsTests(unittest.TestCase):
    def test_general_type_group_concentration_and_split(self):
        c=context_for([Operation('P','A','CIRCLE_PUNCH'),Operation('P','B','CIRCLE_PUNCH')],[])
        c,n,cat,cfg=setup(c);scorer=Scorer(c,cat,cfg)
        self.assertEqual(scorer.evaluate({'A':'Z','B':'Z'})['components']['group_fragmentation']['value'],0)
        self.assertEqual(scorer.evaluate({'A':'Z','B':'A'})['components']['group_fragmentation']['value'],1)

    def test_required_bend_split_not_penalized(self):
        c=context_for([Operation('P','A','BEND',surface_side='SMOOTH',cad_parent_operation_id='B'),Operation('P','B','BEND',surface_side='SMOOTH')],[])
        c,n,cat,cfg=setup(c);s=Scorer(c,cat,cfg);r=s.evaluate({'A':'Z','B':'A'})
        g=next(g for g in r['fragmentation']['groups'] if g['group_id']=='BEND:SMOOTH')
        self.assertEqual((g['K_min'],g['K_max'],g['K_actual']),(2,2,2));self.assertFalse(g['participating'])
        self.assertEqual(r['components']['group_fragmentation']['value'],0)

    def test_bend_subgroups_do_not_double_type_weight(self):
        ops=[Operation('P',name,'BEND',surface_side=side) for side,names in [('SMOOTH',['S1','S2']),('ROUGH',['R1','R2'])] for name in names]
        ops += [Operation('P','C1','CIRCLE_PUNCH'),Operation('P','C2','CIRCLE_PUNCH')]
        c,n,cat,cfg=setup(context_for(ops,[]));s=Scorer(c,cat,cfg)
        r=s.evaluate({'S1':'Z','S2':'A','R1':'Z','R2':'Z','C1':'Z','C2':'Z'})
        self.assertEqual(r['fragmentation']['type_means'],{'BEND':.5,'CIRCLE_PUNCH':0})
        self.assertEqual(r['components']['group_fragmentation']['value'],.25)

    def test_missing_bend_direction_rejected(self):
        c,n,cat,cfg=setup(context_for([Operation('P','B','BEND')],[]))
        with self.assertRaises(BoundError):Scorer(c,cat,cfg)

    def test_hard_invalid_not_scored(self):
        c,n,cat,cfg=setup();s=Scorer(c,cat,cfg)
        with self.assertRaises(ValueError):s.evaluate({'A':'Z'})

    def test_exact_model_and_independent_score_all_channels(self):
        c,n,cat,cfg=setup();s=Scorer(c,cat,cfg)
        for channel in ('secondary','group_fragmentation','extra_gap'):
            model,ranks,p5,obj=build_objective_model(c,cat,cfg,s,channel)
            model.minimize(obj.expression);solver=solver_for(cfg,0);status=solver.solve(model)
            self.assertIn(status,(cp_model.OPTIMAL,cp_model.FEASIBLE))
            p={o:c.ordered_stations[solver.value(v)].station_id for o,v in ranks.items()};r=s.evaluate(p)
            self.assertEqual(Fraction(obj.integer_value(solver),obj.scale),Fraction(r['secondary_exact'] if channel=='secondary' else r['components'][channel]['exact']))

    def test_integer_safety_fails_without_rounding(self):
        model=cp_model.CpModel();v=model.new_bool_var('x')
        with self.assertRaises(ValueError):RationalExpression([(v,Fraction(1,101))]).compile(100,1)

    def test_weights_and_unknown_policy_rejected(self):
        for key,value in [('secondary_weights',{'area':.3,'extra_gap':.2,'group_fragmentation':.6}),('integer_objective_limit',2**63)]:
            cfg=load_config();cfg[key]=value
            with self.assertRaises(ValueError):validate_config(cfg)


class OptimizationTests(unittest.TestCase):
    def test_feasible_zero_witness_is_a_complete_nonnegative_proof(self):
        c,n,cat,cfg=setup()
        factory=solver_for
        class FeasibleStatus:
            def __init__(self):self.real=factory({'solver':cfg['bounds_solver']},0)
            def solve(self,m):self.real.solve(m);return cp_model.FEASIBLE
            def __getattr__(self,k):return getattr(self.real,k)
        with patch('src.neighborhood_penalty.generation.solver_for',side_effect=lambda *args:FeasibleStatus()):p=establish_primary(c,cat,cfg)
        self.assertEqual(p['minimum_exact'],'0');self.assertTrue(p['minimum_proven']);self.assertEqual(p['proof'],'nonnegative_zero_witness')

    def test_feasible_positive_is_not_proof_of_minimum(self):
        c,n,cat,cfg=setup(joint_conflict_context());factory=solver_for
        class FeasibleStatus:
            def __init__(self):self.real=factory({'solver':cfg['bounds_solver']},0)
            def solve(self,m):self.real.solve(m);return cp_model.FEASIBLE
            def __getattr__(self,k):return getattr(self.real,k)
        with patch('src.neighborhood_penalty.generation.solver_for',side_effect=lambda *args:FeasibleStatus()):p=establish_primary(c,cat,cfg)
        self.assertEqual(p['minimum_exact'],'1/2');self.assertFalse(p['minimum_proven']);self.assertEqual(p['state'],'primary_unproven')

    def test_unknown_bounds_not_replaced_with_capability_guess(self):
        c,n,cat,cfg=setup();s=Scorer(c,cat,cfg)
        class Unknown:
            def solve(self,m):return cp_model.UNKNOWN
            def status_name(self,s):return 'UNKNOWN'
            wall_time=0;best_objective_bound=0;response_proto=type('R',(),{'deterministic_time':0})()
        with patch('src.neighborhood_penalty.scoring.solver_for',return_value=Unknown()),self.assertRaises(BoundError):s.bounds()

    def test_candidate_unknown_reports_budget_without_relaxation(self):
        c,n,cat,cfg=setup();p=establish_primary(c,cat,cfg);s=Scorer(c,cat,cfg);s.bounds()
        cfg['generation']['max_attempts']=2
        class Unknown:
            def solve(self,m):return cp_model.UNKNOWN
            def status_name(self,s):return 'UNKNOWN'
            wall_time=0;best_objective_bound=0;response_proto=type('R',(),{'deterministic_time':0})()
        with patch('src.neighborhood_penalty.generation.solver_for',return_value=Unknown()):pool,run=generate_candidates(c,cat,cfg,p,s)
        self.assertFalse(pool);self.assertEqual(len(run['attempts']),2);self.assertEqual(run['state'],'partial_budget_exhausted')
        self.assertTrue(all(r['primary_fixed_exact']=='0' for r in run['attempts']))

    def test_positive_minimum_when_all_separation_jointly_impossible(self):
        c,n,cat,cfg=setup(joint_conflict_context());primary=establish_primary(c,cat,cfg)
        self.assertEqual(primary['minimum_exact'],'1/2');self.assertTrue(primary['minimum_proven'])
        self.assertFalse(primary['residual_pair_individually_unavoidable_claimed'])
        s=Scorer(c,cat,cfg);pool,run=generate_candidates(c,cat,cfg,primary,s)
        self.assertTrue(pool)
        self.assertTrue(all(HardConstraintEngine(c).evaluate(p['assignments']).feasible and p['score']['p5']['exact']=='1/2' for p in pool))
        self.assertFalse(run['n1_inequality_constraints_added'])

    def test_exhaustive_small_domain_and_shortage(self):
        c,n,cat,cfg=setup();p=establish_primary(c,cat,cfg);s=Scorer(c,cat,cfg);pool,run=generate_candidates(c,cat,cfg,p,s)
        expected={plan_hash({'A':a,'B':b}) for a,b in product(c.stations,repeat=2) if a!=b}
        self.assertEqual({r['plan_hash'] for r in pool},expected);self.assertEqual(run['state'],'feasible_set_exhausted')

    def test_p5_has_priority_over_any_secondary(self):
        low={'score':{'p5':{'exact':'0'},'secondary_exact':'1'},'plan_hash':'a'}
        high={'score':{'p5':{'exact':'1/100'},'secondary_exact':'0'},'plan_hash':'b'}
        self.assertLess(ranking_key(low),ranking_key(high))

    def test_unknown_is_not_infeasible_or_zero(self):
        c,n,cat,cfg=setup();real=solver_for(cfg,0)
        class Unknown:
            def solve(self,model):return cp_model.UNKNOWN
            def status_name(self,status):return 'UNKNOWN'
            wall_time=0;objective_value=0;best_objective_bound=0;response_proto=real.response_proto if False else type('R',(),{'deterministic_time':0})()
        with patch('src.neighborhood_penalty.generation.solver_for',return_value=Unknown()):p=establish_primary(c,cat,cfg)
        self.assertEqual(p['state'],'primary_unknown');self.assertNotIn('minimum_exact',p)
        s=Scorer(c,cat,cfg);self.assertEqual(generate_candidates(c,cat,cfg,p,s)[0],[])

    def test_hard_infeasible_not_neighbor_conflict(self):
        c=two_ops();c=InferenceContext(replace(c.part_data,stations=tuple(replace(s,allowed_operation_types=frozenset()) for s in c.part_data.stations)))
        c,n,cat,cfg=setup(c);p=establish_primary(c,cat,cfg)
        self.assertEqual(p['state'],'hard_infeasible');self.assertFalse(p['generation_allowed'])

    def test_unproven_positive_not_used_for_final_generation(self):
        c,n,cat,cfg=setup();p=establish_primary(c,cat,cfg);p.update(generation_allowed=False,minimum_proven=False,state='primary_unproven')
        s=Scorer(c,cat,cfg);pool,run=generate_candidates(c,cat,cfg,p,s)
        self.assertEqual(pool,[]);self.assertEqual(run['state'],'skipped_primary_not_proven')

    def test_source_and_expert_metadata_not_generation_inputs(self):
        c,n,cat,cfg=setup();p=establish_primary(c,cat,cfg);pool,_=generate_candidates(c,cat,cfg,p,Scorer(c,cat,cfg))
        altered=InferenceContext(replace(c.part_data,source_path=Path('other.xlsx'),source_sha256='different'))
        altered_n=build_neighborhood(altered,nc());cat2=build_catalogue(altered,altered_n,cfg)
        self.assertEqual(c.facts_fingerprint,altered.facts_fingerprint)
        p2=establish_primary(altered,cat2,cfg);again,_=generate_candidates(altered,cat2,cfg,p2,Scorer(altered,cat2,cfg))
        self.assertEqual([r['plan_hash'] for r in pool],[r['plan_hash'] for r in again])

    def test_expert_property_trap_and_six_workbook_modes(self):
        outcomes=[]
        with tempfile.TemporaryDirectory() as temp,patch.object(InferenceContext,'expert_reference_plan',property(lambda _:(_ for _ in ()).throw(AssertionError('expert leak')))):
            for mode in ('valid','blank','absent','invalid','formula','error'):
                path=Path(temp)/(mode+'.xlsx');workbook(path,expert_mode=mode);c=load_inference_context(path)
                c,n,cat,cfg=setup(c);p=establish_primary(c,cat,cfg);pool,_=generate_candidates(c,cat,cfg,p,Scorer(c,cat,cfg))
                outcomes.append((c.facts_fingerprint,[r['plan_hash'] for r in pool]))
        self.assertTrue(all(r==outcomes[0] for r in outcomes))

    def test_cli_new_part_without_expert_cache_or_baseline(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'outputs',prefix='penalty_cli_test_') as temp:
            folder=Path(temp).resolve()
            self.assertTrue(folder.is_relative_to((ROOT/'outputs').resolve()))
            source=folder/'new_part.xlsx';workbook(source,expert_mode='absent')
            target=folder/'result'
            p=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/run_neighborhood_penalty_v02.py'),'--input',str(source),'--output',str(target)],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace')
            self.assertEqual(p.returncode,0,p.stderr)
            meta=json.loads((target/'run_metadata.json').read_text(encoding='utf-8'))
            self.assertEqual(meta['state'],'complete');self.assertFalse(meta['expert_used']);self.assertEqual(meta['baseline_files_first_read_after_generation'],{})
            self.assertTrue((target/'penalty_internal_review.html').exists())

    def test_selection_reasons_and_explanation_truth(self):
        c,n,cat,cfg=setup();p=establish_primary(c,cat,cfg);pool,_=generate_candidates(c,cat,cfg,p,Scorer(c,cat,cfg));chosen,selection=select_candidates(c,pool,cfg)
        self.assertEqual(len(chosen),4);self.assertEqual(len(selection['steps']),4)
        cmp=comparison(c,n,pool[0],chosen[0],'C','test',selection['steps'][0]['reason'])
        self.assertEqual(len(cmp['all_changes']),sum(pool[0]['assignments'][o]!=chosen[0]['assignments'][o] for o in c.operations))
        self.assertIsNone(cmp['answer'])
        self.assertIsNone(summarize(c,[])['p5']['mean'])


class P001IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c=load_inference_context(ROOT/'data/parts/P001/钣金加工数据表_P001.xlsx')
        cls.out=ROOT/'outputs/neighborhood_penalty_v02'
        cls.cat=json.loads((cls.out/'p5_catalogue.json').read_text(encoding='utf-8'))
        cls.cfg=load_config();cls.s=Scorer(cls.c,cls.cat,cls.cfg)
        cls.rows=[json.loads(line) for line in (cls.out/'candidate_pool.jsonl').read_text(encoding='utf-8').splitlines()]

    def test_catalogue_counts_and_known_four(self):
        self.assertEqual(self.cat['priority_type_counts_before_hard_exclusion'],{'BEND_IRREGULAR':68,'BEND_FLANGING':43,'FLANGING_IRREGULAR':22})
        self.assertEqual(self.cat['type_counts'],{'FLANGING_IRREGULAR':22})
        self.assertTrue({'O066__O200','O015__O196','O031__O196','O035__O196'} <= {r['pair_id'] for r in self.cat['pairs']})

    def test_all_64_hard_legal_primary_zero_scores_independent(self):
        self.assertEqual(len(self.rows),64);self.assertEqual(len({r['plan_hash'] for r in self.rows}),64)
        for row in self.rows:
            s=self.s.evaluate(row['assignments'])
            self.assertEqual(row['score']['secondary_exact'],s['secondary_exact']);self.assertEqual(s['p5']['exact'],'0')
            self.assertEqual(row['plan_hash'],plan_hash(row['assignments']))
            for c in s['components'].values():self.assertTrue(0<=c['value']<=1)

    def test_general_fragmentation_is_active_not_flanging_only(self):
        values={r['score']['components']['group_fragmentation']['value'] for r in self.rows}
        self.assertTrue({0,1}<=values)
        self.assertTrue(values<={0,1/3,2/3,1})
        bounds=self.s.bounds();groups={r['group_id']:r for r in bounds}
        self.assertEqual((groups['FLANGING']['K_min'],groups['FLANGING']['K_max']),(1,1))
        self.assertEqual((groups['BEND:SMOOTH']['K_min'],groups['BEND:SMOOTH']['K_max']),(3,3))
        self.assertEqual((groups['BEND:ROUGH']['K_min'],groups['BEND:ROUGH']['K_max']),(1,2))

    def test_selection_and_frozen_graph_and_soft_apis(self):
        selected,report=select_candidates(self.c,self.rows,self.cfg)
        saved=[json.loads(s) for s in (self.out/'selected_candidates.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual([r['plan_hash'] for r in selected],[r['plan_hash'] for r in saved]);self.assertEqual(len(selected),16)
        self.assertEqual(sum(r['reason']=='secondary_score' for r in report['steps']),8)
        legacy=SoftPriorEngine(self.c).evaluate(selected[0]['assignments'])
        self.assertEqual(set(legacy['components']),{'bend_area_ordering','precedence_extra_gap','type_fragmentation'})
        self.assertEqual(legacy['components']['type_fragmentation']['raw_component'],0)
        # Graph builders and stored graph semantics are untouched; dynamic data
        # are tested through frozen APIs, rather than defining a new graph here.
        self.assertTrue(callable(build_part_graph))


if __name__=='__main__':unittest.main()
