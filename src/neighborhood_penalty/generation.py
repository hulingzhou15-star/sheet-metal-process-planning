"""Neighborhood-first optimization, multiple exploration channels, auditable selection."""
from fractions import Fraction
import hashlib
import json
import random

from ortools.sat.python import cp_model
from src.candidate.distance import PlanDistance,plan_hash
from src.constraints.v06 import HardConstraintEngine
from src.inference.generation import solver_for,solver_audit,assignment_from_solution,derived_seed
from .catalogue import check_catalogue,evaluate_p5
from .config import validate_config
from .model import build_objective_model


def establish_primary(context,catalogue,config):
    config=validate_config(config)
    model,ranks,p5,_=build_objective_model(context,catalogue,config)
    model.minimize(p5.expression)
    seed=derived_seed(context,config['generation']['seed'],'P5:primary:'+catalogue['fingerprint'])
    solver=solver_for({'solver':config['bounds_solver']},seed);status=solver.solve(model)
    result={'facts_fingerprint':context.facts_fingerprint,'catalogue_fingerprint':catalogue['fingerprint'],
            'solver':{'seed':seed,**solver_audit(solver,status)},'integer_objective':p5.audit(),
            'minimum_proven':False,'generation_allowed':False,'witness_used_as_candidate_or_hint':False}
    if status==cp_model.MODEL_INVALID:raise ValueError(model.validate())
    if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE):
        return {**result,'state':'hard_infeasible' if status==cp_model.INFEASIBLE else 'primary_unknown'}
    assignments=assignment_from_solution(context,ranks,solver)
    if not HardConstraintEngine(context).evaluate(assignments).feasible:raise RuntimeError('Hard witness disagreement')
    value=p5.integer_value(solver)
    score=evaluate_p5(context,catalogue,assignments)
    if Fraction(value,p5.scale)!=Fraction(score['exact']):raise RuntimeError('Primary objective/scorer disagreement')
    proven=status==cp_model.OPTIMAL or value==0
    return {**result,'state':'primary_proven' if proven else 'primary_unproven','minimum_proven':proven,
            'generation_allowed':proven,'minimum_integer_value':value,'minimum_exact':score['exact'],
            'minimum':score['value'],'proof':'nonnegative_zero_witness' if value==0 else 'solver_OPTIMAL' if proven else None,
            'witness':{'assignments':assignments,'plan_hash':plan_hash(assignments),'p5':score},
            'residual_pair_individually_unavoidable_claimed':False}


def compact_score(score):
    return {k:v for k,v in score.items() if k not in {'area_audit','gap_audit'}}


def generate_candidates(context,catalogue,config,primary,scorer,*,progress=None):
    config=validate_config(config);check_catalogue(context,catalogue)
    if primary.get('facts_fingerprint')!=context.facts_fingerprint or primary.get('catalogue_fingerprint')!=catalogue['fingerprint']:
        raise ValueError('Foreign primary proof')
    if not primary.get('generation_allowed') or not primary.get('minimum_proven'):
        return [],{'state':'skipped_primary_not_proven','actual_pool':0,'attempts':[]}
    expected=Fraction(primary['minimum_exact'])
    pool=[];logs=[];counts={c:0 for c in ('secondary','group_fragmentation','extra_gap','random')}
    opts=config['generation'];channels=list(counts)
    for index in range(opts['max_attempts']):
        eligible=[c for c in channels if counts[c]<opts['channel_quota']]
        channel=eligible[index%len(eligible)] if eligible else 'random'
        model,ranks,p5,objective=build_objective_model(context,catalogue,config,scorer,channel)
        target=expected*p5.scale
        if target.denominator!=1:raise ValueError('Nonintegral primary lock')
        model.add(p5.expression==int(target))
        ordered=sorted(ranks)
        if pool:model.add_forbidden_assignments([ranks[o] for o in ordered],[[context.station_ranks[p['assignments'][o]] for o in ordered] for p in pool])
        seed=derived_seed(context,opts['seed'],'P5:candidate:'+channel+':'+catalogue['fingerprint'],index)
        costs=[]
        if channel=='random':
            rng=random.Random(seed);terms=[]
            for op in ordered:
                if len(context.allowed_stations(op))<=1:continue
                for station in context.allowed_stations(op):
                    flag=model.new_bool_var(f'random:{op}:{station.station_id}')
                    model.add(ranks[op]==context.station_ranks[station.station_id]).only_enforce_if(flag)
                    model.add(ranks[op]!=context.station_ranks[station.station_id]).only_enforce_if(flag.Not())
                    cost=rng.randint(-opts['random_cost_abs_max'],opts['random_cost_abs_max']);terms.append(cost*flag);costs.append([op,station.station_id,cost])
            model.minimize(sum(terms))
        else:model.minimize(objective.expression)
        solver=solver_for(config,seed);status=solver.solve(model)
        log={'attempt':index+1,'channel':channel,'seed':seed,**solver_audit(solver,status),'no_good_count':len(pool),
             'primary_fixed_exact':str(expected),'exact_objective':objective.audit() if objective else None,
             'random_cost_sha256':hashlib.sha256(json.dumps(costs,separators=(',',':')).encode()).hexdigest() if costs else None}
        if status==cp_model.MODEL_INVALID:raise ValueError(model.validate())
        if status in (cp_model.OPTIMAL,cp_model.FEASIBLE):
            assignments=assignment_from_solution(context,ranks,solver)
            score=scorer.evaluate(assignments)
            if Fraction(score['p5']['exact'])!=expected:raise RuntimeError('Primary lock independent audit failed')
            key=plan_hash(assignments)
            if any(p['plan_hash']==key for p in pool):raise RuntimeError('Duplicate under no-good')
            if objective:
                actual=Fraction(objective.integer_value(solver),objective.scale)
                wanted=Fraction(score['secondary_exact'] if channel=='secondary' else score['components'][channel]['exact'])
                if actual!=wanted:raise RuntimeError(f'{channel} model score disagreement: {actual} != {wanted}')
                log['independent_objective_exact']=str(actual)
            row={'plan_id':f'NP2_{len(pool)+1:03d}','plan_hash':key,'assignments':assignments,
                 'score':compact_score(score),'generation':log.copy(),'hard_feasible':True,'preference_label':None}
            pool.append(row);counts[channel]+=1
        logs.append(log)
        if progress:progress({'stage':'candidate','attempt':index+1,'channel':channel,'status':log['status'],'pool':len(pool)})
        if len(pool)>=opts['pool_target'] or status==cp_model.INFEASIBLE:break
    exhausted=bool(logs and logs[-1]['status']=='INFEASIBLE')
    return pool,{'state':'target_reached' if len(pool)>=opts['pool_target'] else 'feasible_set_exhausted' if exhausted else 'partial_budget_exhausted',
                 'requested_pool':opts['pool_target'],'actual_pool':len(pool),'channel_counts':counts,'attempts':logs,
                 'expert_used':False,'old_candidates_used':False,'n1_inequality_constraints_added':False,
                 'primary_witness_used_as_candidate_or_hint':False,'secondary_optimality_claim':'per attempt only when status OPTIMAL; no global top-64 claim'}


def ranking_key(row):
    return Fraction(row['score']['p5']['exact']),Fraction(row['score']['secondary_exact']),row['plan_hash']


def select_candidates(context,pool,config):
    opts=validate_config(config)['generation'];target=opts['selected_target']
    if len({r['plan_hash'] for r in pool})!=len(pool):raise ValueError('Duplicate selection pool')
    if len({r['score']['p5']['exact'] for r in pool})>1:raise ValueError('Selection requires one primary optimum level')
    remaining=sorted(pool,key=ranking_key);selected=[];steps=[]
    distance=PlanDistance.from_context(context)
    def add(row,reason):
        selected.append(row);remaining.remove(row)
        steps.append({'plan_id':row['plan_id'],'plan_hash':row['plan_hash'],'reason':reason})
    for row in remaining[:min(target,opts['score_selection_count'])]:add(row,'secondary_score')
    for name in ('group_fragmentation','extra_gap'):
        if not remaining or len(selected)>=target:break
        best=min(pool,key=lambda r:(Fraction(r['score']['components'][name]['exact']),ranking_key(r)))
        if best not in selected:add(best,'minimum_'+name+'_representative')
    while remaining and len(selected)<target:
        def nearest(row):return min((distance.raw(row['assignments'],s['assignments']) for s in selected),default=0)
        competition=[{'plan_hash':r['plan_hash'],'minimum_raw_distance':nearest(r)} for r in remaining]
        row=min(remaining,key=lambda r:(-nearest(r),ranking_key(r)))
        add(row,'diversity');steps[-1]['minimum_raw_distance_to_previously_selected']=min((distance.raw(row['assignments'],s['assignments']) for s in selected[:-1]),default=0)
        steps[-1]['competition']=competition
    ranks={r['plan_hash']:i+1 for i,r in enumerate(sorted(pool,key=ranking_key))}
    rejections=[{'plan_id':r['plan_id'],'plan_hash':r['plan_hash'],'secondary_rank_in_pool':ranks[r['plan_hash']],
                 'nearest_selected_raw_distance':min((distance.raw(r['assignments'],s['assignments']) for s in selected),default=None),
                 'reason':'Not in the score quota, not a reserved component representative, and not chosen by the logged farthest-first competitions before the quota was filled.'} for r in remaining]
    return selected,{'requested':target,'actual':len(selected),'steps':steps,'method':'score_then_representatives_then_farthest_first',
                     'rejections':rejections,'quality_guarantee':False,'movable_operation_count':len(distance.movable_operation_ids)}
