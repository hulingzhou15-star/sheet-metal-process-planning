"""Fill a partial P002 pool using only schedules covered by the relief model."""
import argparse
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
import shutil
import time

from ortools.sat.python import cp_model
from src.candidate.distance import plan_hash
from src.inference.generation import assignment_from_solution, derived_seed, solver_audit, solver_for
from src.neighborhood_penalty.generation import compact_score, ranking_key

from information_extract.scripts import regenerate_p002_candidates as workflow
from information_extract.three_d.allocation_policy import AllocationEngine
from information_extract.three_d.artifacts import read, source_manifest, verify_sources
from information_extract.three_d.local_forming import load_shape
from information_extract.three_d.p002_regeneration import RELIEF_TIMINGS, Screen, unsupported_relief
from information_extract.three_d.p003_rescreen_search import _locked_model


def complete(context,catalogue,primary,config,scorer,bend_ids,signatures,excluded,count):
    allowed,ranks,objective = _locked_model(context,catalogue,config,primary,scorer,'secondary')
    station_ranks = context.station_ranks
    allowed.add_allowed_assignments([ranks[op] for op in bend_ids],
        [[station_ranks[s] for s in signature] for signature in sorted(signatures)])
    for relief,folds in RELIEF_TIMINGS.items():
        for fold in folds:
            allowed.add(ranks[relief] < ranks[fold])
    ops = sorted(ranks)
    variables = [ranks[o] for o in ops]

    def vector(assignment):
        return [station_ranks[assignment[o]] for o in ops]

    allowed.add_forbidden_assignments(variables,[vector(a) for a in excluded])
    allowed.minimize(objective.expression)
    engine = AllocationEngine(context)
    rows, diagnostics = [], []
    for attempt in range(count):
        seed = derived_seed(context,config['generation']['seed'],'P002 covered relief completion',attempt)
        solver = solver_for(config,seed)
        status = solver.solve(allowed)
        if status == cp_model.MODEL_INVALID:
            raise ValueError(allowed.validate())
        diagnostics.append(solver_audit(solver,status))
        if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
            break
        assignment = assignment_from_solution(context,ranks,solver)
        engine.require_feasible(assignment)
        score = scorer.evaluate(assignment)
        if Fraction(score['p5']['exact']) != Fraction(primary['minimum_exact']) or (
            Fraction(objective.integer_value(solver),objective.scale) != Fraction(score['secondary_exact'])
        ):
            raise ValueError('P002 supplementary solver and score disagree')
        rows.append(dict(plan_hash=plan_hash(assignment),assignments=assignment,score=compact_score(score),
            generation=dict(channel='secondary',scope='P002_DXF_RELIEF_MODEL_COVERAGE',**diagnostics[-1]),
            hard_feasible=True,preference_label=None))
        allowed.add_forbidden_assignments(variables,[vector(assignment)])
    return rows, diagnostics


def previous_checker(directory,model,plan,pair_cache):
    data = read(directory/'material_states.json')
    variants = {row['material_id']:row for row in data['variants']}
    material = {(tuple(r['executed_removal_ids']),tuple(r['executed_forming_ids'])):variants[r['material_id']]
                for r in data['records'] if r['plan_id']==plan['plan_id']}
    geom_ids = {gid for row in material.values() for gid in row['cells'].values()}
    geometry = {gid:workflow.mesh(load_shape(directory/'brep'/f'{gid}.brep'),
                                 workflow.previous.CHECK_CONFIG) for gid in geom_ids}
    return Screen(model,material,directory,geometry,pair_cache)


def synchronized_key(context,plan,bend_ids):
    groups = defaultdict(list)
    for op in bend_ids:
        groups[plan['assignments'][op]].append(op)
    return tuple(tuple(groups[s]) for s in sorted(groups,key=context.station_ranks.__getitem__))


def choose_distinct(context,plans,bend_ids,config,wanted):
    representatives = {}
    for plan in sorted(plans,key=ranking_key):
        representatives.setdefault(synchronized_key(context,plan,bend_ids),plan)
    picked = workflow.choose(context,list(representatives.values()),bend_ids,config,wanted)
    if len(picked)<wanted:
        seen = {p['plan_hash'] for p in picked}
        picked.extend(p for p in workflow.choose(context,plans,bend_ids,config,wanted) if p['plan_hash'] not in seen)
    return picked[:wanted]


def coarse_batch(directory,rows,model,asset,solids,paths,first_id,bend_ids,pair_cache):
    for i,plan in enumerate(rows,first_id):
        plan['plan_id'] = f'P002_R{i:03d}'
        plan['bend_stations'] = list(workflow.bend_key(plan,bend_ids))
        if unsupported_relief(model,plan['assignments']):
            raise ValueError('P002 supplementary proposal violates modeled cut timing')
    workflow.write_checkpoint(directory/'proposals.json',dict(plans=rows))
    checker = workflow.make_batch(directory,model,asset,solids,paths,rows,pair_cache)
    classified = []
    for plan in rows:
        pid = plan['plan_id']
        result = checker.run(plan,workflow.COARSE,progress=workflow.progress(pid,workflow.COARSE,checker))
        classified.append(dict(**plan,status=result['status'],coarse=result,refined=None))
        print(pid,result['status'],flush=True)
    return classified,checker


def prepare():
    """Run only the new coarse batch while the primary run refines its selection."""
    out = workflow.OUT
    model,asset,solids,sources,paths,context,catalogue,primary,config,scorer = workflow.setup()
    baseline = read(out/'screening.json')['attempts']
    target = config['generation']['pool_target']
    accepted = [p for p in baseline if p['coarse']['status']=='passed']
    if len(accepted)>=target:
        return
    batches = sorted((out/'staged').glob('batch_*'))
    recorded = [plan for batch in batches for plan in read(batch/'proposals.json')['plans']]
    if [p['plan_hash'] for p in recorded] != [p['plan_hash'] for p in baseline]:
        raise ValueError('P002 primary coarse proposals are still in progress')
    old = read(workflow.SOURCE/'pool.json')['plans']
    bend_ids = tuple(sorted(o for o,r in context.operations.items() if r.operation_type=='BEND'))
    paths_in_scope = {workflow.bend_key(p,bend_ids) for p in [*old,*accepted]}
    rows,audit = complete(context,catalogue,primary,config,scorer,bend_ids,paths_in_scope,
        [p['assignments'] for p in [*old,*baseline]],target-len(accepted))
    if not rows:
        return
    directory = out/'staged'/f'batch_{len(batches)+1:02d}'
    checks,_ = coarse_batch(directory,rows,model,asset,solids,paths,len(baseline)+1,bend_ids,{})
    sources.update(source_manifest([Path(__file__),directory/'proposals.json',directory/'material_states.json',
        directory/'material_audit.json',*sorted((directory/'brep').glob('*.brep'))]))
    verify_sources(sources)
    workflow.write_checkpoint(directory/'covered_plan_checks.json',dict(attempts=checks,solver=audit,sources=sources,
        preceding_hashes=[p['plan_hash'] for p in baseline]))


def main():
    out = workflow.OUT
    summary = read(out/'summary.json')
    verify_sources(summary['sources'])
    model,asset,initial_solids,sources,paths,context,catalogue,primary,config,scorer = workflow.setup()
    target = config['generation']['pool_target']
    wanted = config['generation']['selected_target']
    bend_ids = tuple(sorted(o for o,r in context.operations.items() if r.operation_type=='BEND'))
    accepted = read(out/'pool.json')['plans']
    selected_before = {p['plan_id']:p for p in read(out/'plans.json')['plans']}
    pool_distinct = len({synchronized_key(context,p,bend_ids) for p in accepted})
    chosen_distinct = len({synchronized_key(context,p,bend_ids) for p in selected_before.values()})
    if summary['pool_size']>=target and chosen_distinct>=min(wanted,pool_distinct):
        print('P002 pool already contains',summary['pool_size'],'screened plans')
        return
    started = time.monotonic()
    protected = workflow.protected_hashes()
    snapshot = out/'before_supplement'
    snapshot.mkdir(exist_ok=True)
    for name in ('summary.json','plans.json','pool.json','screening.json'):
        if not (snapshot/name).exists():
            shutil.copyfile(out/name,snapshot/name)
    sources.update(source_manifest([Path(__file__),*sorted(snapshot.glob('*.json'))]))
    old = read(workflow.SOURCE/'pool.json')['plans']
    attempts = read(out/'screening.json')['attempts']
    records = {p['plan_id']:p for p in attempts}
    batches = sorted((out/'staged').glob('batch_*'))
    directory_by_id = {row['plan_id']:b for b in batches
                       for row in read(b/'proposals.json')['plans']}
    for batch in batches:
        packet = batch/'covered_plan_checks.json'
        if not packet.is_file():
            continue
        proof = read(packet)
        if proof['preceding_hashes'] != [p['plan_hash'] for p in attempts]:
            continue
        verify_sources(proof['sources'])
        sources.update(proof['sources'])
        sources.update(source_manifest([packet]))
        attempts.extend(proof['attempts'])
        records.update((p['plan_id'],p) for p in proof['attempts'])
        keys = {'status','coarse','refined'}
        accepted.extend({k:v for k,v in p.items() if k not in keys}
                        for p in proof['attempts'] if p['status']=='passed')
        summary['generation'].append(dict(scope='P002_DXF_RELIEF_MODEL_COVERAGE',attempts=proof['solver'],
                                          actual_pool=len(proof['attempts'])))
    signatures = {workflow.bend_key(plan,bend_ids) for plan in [*old,*accepted]}
    pair_cache, new_checkers = {}, {}
    selected = []
    for turn in range(1,4):
        exhausted = False
        if len(accepted)<target:
            rows,audit = complete(context,catalogue,primary,config,scorer,bend_ids,signatures,
                [p['assignments'] for p in [*old,*attempts]],target-len(accepted))
            summary['generation'].append(dict(scope='P002_DXF_RELIEF_MODEL_COVERAGE',attempts=audit,
                                               actual_pool=len(rows)))
            exhausted = not rows
            if rows:
                batch = out/'staged'/f'batch_{len(batches)+turn:02d}'
                solids = initial_solids if turn==1 else {
                    c['cell_id']:load_shape(workflow.previous.CELLS/c['brep_file']) for c in model['cells']}
                checks,checker = coarse_batch(batch,rows,model,asset,solids,paths,len(attempts)+1,bend_ids,pair_cache)
                for plan,result in zip(rows,checks):
                    pid = plan['plan_id']
                    records[pid] = result
                    attempts.append(records[pid])
                    directory_by_id[pid] = batch
                    new_checkers[pid] = checker
                    if result['status']=='passed':
                        accepted.append(plan)
        while True:
            selected = choose_distinct(context,accepted,bend_ids,config,wanted)
            unchecked = [p for p in selected if 'motion_verification' not in p]
            if not unchecked:
                break
            for plan in unchecked:
                pid = plan['plan_id']
                if pid in selected_before:
                    plan['motion_verification'] = selected_before[pid]['motion_verification']
                    continue
                checker = new_checkers.get(pid) or previous_checker(directory_by_id[pid],model,plan,pair_cache)
                full = checker.run(plan,workflow.FINE,progress=workflow.progress(pid,workflow.FINE,checker))
                records[pid].update(status=full['status'],refined=full)
                if full['status']=='passed':
                    workflow.publish_plan(out,plan,checker,asset,full)
                else:
                    accepted.remove(plan)
        if len(accepted)>=target or exhausted:
            break
    verify_sources(sources)
    if workflow.protected_hashes()!=protected:
        raise ValueError('Protected P002 inputs changed during supplement')
    workflow.write_checkpoint(out/'pool.json',dict(part_id='P002',plans=accepted))
    workflow.write_checkpoint(out/'plans.json',dict(part_id='P002',plans=selected,
        ranking=[p['plan_id'] for p in sorted(selected,key=ranking_key)]))
    workflow.write_checkpoint(out/'screening.json',dict(attempts=attempts))
    summary.update(termination='target_reached' if len(accepted)>=target else 'covered_completion_budget_reached',
        checked_path_counts=dict(Counter(p['status'] for p in attempts)),
        refined_counts=dict(Counter(p['refined']['status'] for p in attempts if p['refined'])),
        pool_size=len(accepted),selected_count=len(selected),sources=sources,
        passing_motion_sequences=len(workflow.representatives(accepted,bend_ids)),
        selected_motion_sequences=len({workflow.bend_key(p,bend_ids) for p in selected}),
        selected_synchronous_bend_groups=len({synchronized_key(context,p,bend_ids) for p in selected}),
        selected_sample_count=sum(p['motion_verification']['sample_count'] for p in selected),
        selected_pair_states=sum(p['motion_verification']['pair_states'] for p in selected),
        supplemental_geometry_pair_cache_entries=len(pair_cache),
        supplemental_elapsed_seconds=round(time.monotonic()-started,2))
    workflow.write_checkpoint(out/'summary.json',summary)
    print('P002 COMPLETE',len(accepted),'passing pool',len(selected),'selected',flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only',action='store_true',help='Precheck only the additional coarse candidates')
    args = parser.parse_args()
    prepare() if args.prepare_only else main()
