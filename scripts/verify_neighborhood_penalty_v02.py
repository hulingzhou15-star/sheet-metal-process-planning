"""Recompute geometry, score arithmetic, selection evidence and frozen file hashes."""
from collections import Counter,defaultdict
from fractions import Fraction
from itertools import combinations
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.inference.input import load_inference_context
from src.inference.generation import solver_for
from src.constraints.v06 import HardConstraintEngine
from src.constraints.v06.feasibility import build_feasibility_model
from src.candidate.distance import PlanDistance,plan_hash
from src.neighborhood import build_neighborhood,load_config as neighborhood_config,measure_geometry
from src.neighborhood_penalty import load_config,build_catalogue
from src.neighborhood_penalty.review import summarize
from ortools.sat.python import cp_model


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def readl(path):return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s.strip()]


def main():
    out=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else ROOT/'outputs/neighborhood_penalty_v02'
    meta=read(out/'run_metadata.json');cfg=load_config();context=load_inference_context(Path(meta['source_workbook']))
    assert meta['state']=='complete' and context.facts_fingerprint==meta['facts_fingerprint']
    assert context.part_data.source_sha256==meta['source_sha256_provenance_only']
    def progress(stage,**details):print(json.dumps({'stage':stage,**details}),flush=True)
    nc=neighborhood_config(ROOT/cfg['neighborhood_config'])
    neighborhood=build_neighborhood(context,nc,evidence=measure_geometry(context,nc))
    cat=read(out/'p5_catalogue.json');assert cat==build_catalogue(context,neighborhood,cfg)
    progress('fresh_geometry_and_catalogue',pairs=neighborhood.snapshot['summary']['all_geometry_pairs'])
    primary=read(out/'primary_optimization.json');assert primary['minimum_proven'] and primary['minimum_exact']=='0'
    assert HardConstraintEngine(context).evaluate(primary['witness']['assignments']).feasible
    assert all(primary['witness']['assignments'][r['operation_ids'][0]]!=primary['witness']['assignments'][r['operation_ids'][1]] for r in cat['pairs'])
    bounds=read(out/'fragmentation_bounds.json')
    # Independently construct station occupancy with OR/reification rather than
    # call the production occupancy helper or use its cached result.
    for row in bounds:
        for bound,maximize in [('K_min',False),('K_max',True)]:
            if not row['operation_ids']:assert row[bound]==0;continue
            model,ranks=build_feasibility_model(context);uses=[]
            for station in context.ordered_stations:
                flags=[]
                for op in row['operation_ids']:
                    f=model.new_bool_var('verify_at:'+op+':'+station.station_id)
                    model.add(ranks[op]==context.station_ranks[station.station_id]).only_enforce_if(f)
                    model.add(ranks[op]!=context.station_ranks[station.station_id]).only_enforce_if(f.Not())
                    flags.append(f)
                use=model.new_bool_var('verify_used:'+station.station_id)
                model.add_bool_or(flags).only_enforce_if(use)
                model.add_bool_and([f.Not() for f in flags]).only_enforce_if(use.Not())
                uses.append(use)
            (model.maximize if maximize else model.minimize)(sum(uses))
            solver=solver_for({'solver':cfg['bounds_solver']},0);status=solver.solve(model)
            assert status==cp_model.OPTIMAL and sum(solver.value(v) for v in uses)==row[bound],row['group_id']
    progress('independent_occupancy_bounds',groups=len(bounds))
    family_types={frozenset(['FLANGING','IRREGULAR_PUNCH']):'FLANGING_IRREGULAR',frozenset(['BEND','FLANGING']):'BEND_FLANGING',frozenset(['BEND','IRREGULAR_PUNCH']):'BEND_IRREGULAR'}
    eligible=[]
    for r in neighborhood.external_relations:
        a,b=r['operation_ids'];family=family_types.get(frozenset(context.operations[o].operation_type for o in (a,b)))
        if not family:continue
        arcs=[x for x in r['arcs'] if x['tier']=='PRIORITY' and (family!='FLANGING_IRREGULAR' or context.operations[x['anchor']].operation_type=='FLANGING')]
        if not arcs:continue
        if b in context.precedence_reachability[a] or a in context.precedence_reachability[b]:continue
        if not ({s.station_id for s in context.allowed_stations(a)} & {s.station_id for s in context.allowed_stations(b)}):continue
        eligible.append((a,b,family))
    assert {frozenset((a,b)) for a,b,_ in eligible}=={frozenset(r['operation_ids']) for r in cat['pairs']}
    counts=Counter(t for _,_,t in eligible)
    def independent_score(row):
        p=row['assignments'];assert HardConstraintEngine(context).evaluate(p).feasible
        p5=sum((Fraction(sum(p[a]==p[b] for a,b,t in eligible if t==kind),n) for kind,n in counts.items()),Fraction())/len(counts) if counts else Fraction()
        inversions=comparable=0
        for a,b in combinations(context.bend_influences,2):
            x,y=a.bend_operation_id,b.bend_operation_id
            if a.influence_region_id in context.region_ancestors[b.influence_region_id] or b.influence_region_id in context.region_ancestors[a.influence_region_id]:continue
            if y in context.precedence_reachability[x] or x in context.precedence_reachability[y]:continue
            av,bv=a.affected_area_proxy,b.affected_area_proxy
            if av>0 and (bv==0 or av/bv>cfg['area_significance_ratio']):large,small=x,y
            elif bv>0 and (av==0 or bv/av>cfg['area_significance_ratio']):large,small=y,x
            else:continue
            comparable+=1;inversions+=context.stations[p[large]].order<context.stations[p[small]].order
        area=Fraction(inversions,comparable) if comparable else Fraction()
        relations=sorted({(r.predecessor_id,r.operation_id) for r in context.precedence_relations});gaps=[]
        for a,b in relations:
            available=[context.station_ranks[y.station_id]-context.station_ranks[x.station_id] for x in context.allowed_stations(a) for y in context.allowed_stations(b) if y.order>x.order]
            actual=context.station_ranks[p[b]]-context.station_ranks[p[a]]
            gaps.append(Fraction(actual-min(available),max(available)-min(available)) if max(available)>min(available) else Fraction())
        gap=sum(gaps,Fraction())/len(gaps) if gaps else Fraction()
        types=defaultdict(list)
        for g in bounds:
            actual=len({p[o] for o in g['operation_ids']})
            assert g['K_min']<=actual<=g['K_max']
            saved=next(r for r in row['score']['fragmentation']['groups'] if r['group_id']==g['group_id'])
            assert saved['K_actual']==actual and saved['actual_used_stations']==sorted({p[o] for o in g['operation_ids']},key=context.station_ranks.get)
            if g['participating']:types[g['operation_type']].append(Fraction(actual-g['K_min'],g['K_max']-g['K_min']))
        fragment=sum((sum(v,Fraction())/len(v) for v in types.values()),Fraction())/len(types) if types else Fraction()
        values={'area':area,'extra_gap':gap,'group_fragmentation':fragment}
        secondary=sum((Fraction(str(cfg['secondary_weights'][k]))*v for k,v in values.items()),Fraction())
        assert Fraction(row['score']['p5']['exact'])==p5 and row['score']['p5']['value']==float(p5)
        assert all(Fraction(row['score']['components'][k]['exact'])==v and row['score']['components'][k]['value']==float(v) for k,v in values.items())
        assert Fraction(row['score']['secondary_exact'])==secondary and row['score']['secondary']==float(secondary)
        assert plan_hash(p)==row['plan_hash']
    pool=readl(out/'candidate_pool.jsonl');selected=readl(out/'selected_candidates.jsonl');old=read(out/'historical_rescoring.json')
    for row in [*pool,*old['old_n1'],*old['old_original']]:independent_score(row)
    assert len(pool)==64 and len(selected)==16 and len({r['plan_hash'] for r in pool})==64
    poolmap={r['plan_hash']:r for r in pool}
    assert all(poolmap[r['plan_hash']]==r for r in selected)
    selection=read(out/'selection_report.json');chosen=[];distance=PlanDistance.from_context(context)
    ranking=lambda r:(Fraction(r['score']['p5']['exact']),Fraction(r['score']['secondary_exact']),r['plan_hash'])
    expected_score=sorted(pool,key=ranking)[:cfg['generation']['score_selection_count']]
    assert [s['plan_hash'] for s in selection['steps'][:8]]==[r['plan_hash'] for r in expected_score]
    for step in selection['steps']:
        remaining=[r for r in pool if r['plan_hash'] not in chosen];row=poolmap[step['plan_hash']]
        if step['reason']=='diversity':
            near=lambda r:min(distance.raw(r['assignments'],poolmap[h]['assignments']) for h in chosen)
            assert row==min(remaining,key=lambda r:(-near(r),ranking(r)))
            assert {r['plan_hash']:r['minimum_raw_distance'] for r in step['competition']}=={r['plan_hash']:near(r) for r in remaining}
        chosen.append(step['plan_hash'])
    assert chosen==[r['plan_hash'] for r in selected]
    assert len(selection['rejections'])==48 and {r['plan_hash'] for r in selection['rejections']}==set(poolmap)-set(chosen)
    cohorts=read(out/'cohort_summary.json')
    for name,rows in [('old_original_pool',old['old_original']),('old_n1_pool',old['old_n1']),('new_pool',pool),('new_selected',selected)]:assert cohorts[name]==summarize(context,rows)
    allplans={r['plan_hash']:r for r in [*pool,*old['old_n1'],*old['old_original']]};comps=read(out/'comparisons.json')
    for c in comps:
        a,b=(allplans[c[k]] for k in ['before_plan_hash','after_plan_hash'])
        expected=[{'operation_id':o,'operation_type':context.operations[o].operation_type,'before_station':a['assignments'][o],'after_station':b['assignments'][o]} for o in sorted(context.operations) if a['assignments'][o]!=b['assignments'][o]]
        assert c['all_changes']==expected and c['raw_hamming']==distance.raw(a['assignments'],b['assignments'])
        assert c['answer'] is None and not c['causal_single_change_claim']
    known=read(out/'known_case_coverage.json')
    assert len(known)==4 and all(r['covered'] and r['new_pool_same_count']==r['selected_same_count']==0 for r in known)
    progress('scores_selection_comparisons',independent_scores=len(pool)+len(old['old_n1'])+len(old['old_original']),comparisons=len(comps))
    tests=(out/'test_results.txt').read_text(encoding='utf-8');special=(out/'penalty_test_results.txt').read_text(encoding='utf-8')
    assert re.search(r'Ran 96 tests',tests) and tests.rstrip().endswith('OK')
    assert re.search(r'Ran 36 tests',special) and special.rstrip().endswith('OK')
    browser=read(out/'browser_qa.json');assert browser['passed'] and not browser['page_errors']
    for path,expected in meta['baseline_files_first_read_after_generation'].items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==expected
    manifest=read(out/'baseline_manifest.json');changed=[]
    progress('frozen_hashes_start',files=len(manifest))
    for rel,sha in manifest.items():
        p=ROOT/rel
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=sha:changed.append(rel)
    assert not changed,changed
    result={'passed':True,'prior_files_verified':len(manifest),'prior_files_changed':changed,'source_sha256':context.part_data.source_sha256,
            'fresh_geometry_pairs':neighborhood.snapshot['summary']['all_geometry_pairs'],'p5_pairs':len(cat['pairs']),
            'bounds_independently_resolved':len(bounds),'pool':64,'selected':16,'independent_scores':len(pool)+len(old['old_n1'])+len(old['old_original']),
            'comparisons':len(comps),'selection_and_rejections_verified':True,'tests_passed':96,'new_tests_passed':36,'browser_verified':True,
            'entire_historical_suite_rerun':False,'expert_used':False,'training_labels_generated':False}
    (out/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
