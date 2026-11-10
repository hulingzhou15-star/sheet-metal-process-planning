"""Post-generation explanations and descriptive audits, never supervision."""
from fractions import Fraction
from itertools import combinations

from src.candidate.distance import PlanDistance
from src.constraints.v06 import HardConstraintEngine
from src.neighborhood_n1.analysis import distribution
from .generation import compact_score,ranking_key


def score_historical(scorer,rows):
    result=[]
    for r in rows:
        hard=HardConstraintEngine(scorer.context).evaluate(r['assignments'])
        if not hard.feasible:raise ValueError('Historical cohort contains hard-invalid candidate')
        result.append({**r,'score':compact_score(scorer.evaluate(r['assignments']))})
    return result


def summarize(context,rows):
    distance=PlanDistance.from_context(context)
    return {'plan_count':len(rows),'hard_pass_count':sum(HardConstraintEngine(context).evaluate(r['assignments']).feasible for r in rows),
            'p5':distribution([r['score']['p5']['value'] for r in rows]),'secondary':distribution([r['score']['secondary'] for r in rows]),
            'components':{k:distribution([r['score']['components'][k]['value'] for r in rows]) for k in ('area','extra_gap','group_fragmentation')},
            'raw_hamming':distribution([distance.raw(a['assignments'],b['assignments']) for a,b in combinations(rows,2)]),
            'station_loads':{s.station_id:distribution([sum(v==s.station_id for v in r['assignments'].values()) for r in rows]) for s in context.ordered_stations},
            'fragmentation_groups':{g['group_id']:distribution([next(x['K_actual'] for x in r['score']['fragmentation']['groups'] if x['group_id']==g['group_id']) for r in rows]) for g in rows[0]['score']['fragmentation']['groups']} if rows else {}}


def comparison(context,neighborhood,a,b,identifier,kind,reason,focus=None):
    distance=PlanDistance.from_context(context)
    changes=[{'operation_id':o,'operation_type':context.operations[o].operation_type,'before_station':a['assignments'][o],'after_station':b['assignments'][o]} for o in sorted(context.operations) if a['assignments'][o]!=b['assignments'][o]]
    external=[]
    for r in neighborhood.external_relations:
        x,y=r['operation_ids'];old=a['assignments'][x]==a['assignments'][y];new=b['assignments'][x]==b['assignments'][y]
        if old!=new:external.append({'pair_id':r['pair_id'],'before_same_station':old,'after_same_station':new})
    old_p5,new_p5=(Fraction(r['score']['p5']['exact']) for r in (a,b))
    old_s,new_s=(Fraction(r['score']['secondary_exact']) for r in (a,b))
    if new_p5<old_p5:text='所选方案邻域罚分更低，按分层目标优先。'
    elif new_p5>old_p5:text='注意：对照方案邻域罚分更低，不能声称所选方案按分层目标更好。'
    elif new_s<old_s:text='邻域罚分相同，所选方案次级分数更低。'
    elif new_s>old_s:text='邻域罚分相同，所选方案次级分数更高；入选依据是代表性或多样性，不能声称分数更优。'
    else:text='两级分数相同，差别来自稳定排序或多样性选择，未证明工艺优劣。'
    return {'comparison_id':identifier,'kind':kind,'selection_reason':reason,'explanation':text,
            'before_plan_id':a['plan_id'],'after_plan_id':b['plan_id'],'before_plan_hash':a['plan_hash'],'after_plan_hash':b['plan_hash'],
            'focus_pair_id':focus,'raw_hamming':distance.raw(a['assignments'],b['assignments']),'all_changes':changes,'external_same_station_changes':external,
            'p5_delta':float(new_p5-old_p5),'secondary_delta':float(new_s-old_s),
            'component_contribution_changes':{k:b['score']['components'][k]['weighted_contribution']-a['score']['components'][k]['weighted_contribution'] for k in b['score']['components']},
            'answer':None,'causal_single_change_claim':False}


def build_comparisons(context,neighborhood,pool,selected,selection,baseline,history,known_cases):
    distance=PlanDistance.from_context(context);chosen={r['plan_hash'] for r in selected};unselected=[r for r in pool if r['plan_hash'] not in chosen]
    reasons={r['plan_hash']:r['reason'] for r in selection['steps']};out=[]
    for kind,source in [('selected_vs_unselected',unselected),('selected_vs_old_n1',baseline)]:
        for row in selected:
            if not source:continue
            a=min(source,key=lambda r:(distance.raw(r['assignments'],row['assignments']),ranking_key(r)))
            out.append(comparison(context,neighborhood,a,row,f'NP2_CMP_{len(out)+1:03d}',kind,reasons[row['plan_hash']]))
    for case in known_cases:
        ids=case['pair_id'].split('__');bad=[r for r in history if r['assignments'][ids[0]]==r['assignments'][ids[1]]]
        if not bad or not selected:continue
        a,b=min(((a,b) for a in bad for b in selected),key=lambda p:(distance.raw(p[0]['assignments'],p[1]['assignments']),ranking_key(p[1]),p[0]['plan_hash']))
        out.append(comparison(context,neighborhood,a,b,f'NP2_CMP_{len(out)+1:03d}','known_case_posthoc',reasons[b['plan_hash']],case['pair_id']))
    return out
