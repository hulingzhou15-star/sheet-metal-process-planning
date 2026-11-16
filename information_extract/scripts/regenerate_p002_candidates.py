"""Regenerate complete P002 allocations and check their actual material schedules."""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
import shutil
import time

import numpy as np

from src.neighborhood_penalty import load_config
from src.neighborhood_penalty.generation import ranking_key, select_candidates

from information_extract.scripts import check_p002_curve_motion as previous
from information_extract.scripts.experiment_bend_allocation_3d import protected_hashes
from information_extract.scripts.regenerate_p003_candidates import write_checkpoint
from information_extract.three_d import p002_regeneration
from information_extract.three_d.artifacts import read, relative, source_manifest, verify_sources, write
from information_extract.three_d.local_forming import save_shape
from information_extract.three_d.meshing import mesh
from information_extract.three_d.p003_rescreen_search import BendSearch, build_passing_pool
from information_extract.three_d.project_geometry_scoring import ProjectGeometryScorer, geometry_context


SOURCE = previous.M/'outputs/geometry_allocation_v1_generated/P002'
OUT = previous.M/'outputs/p002_motion_regeneration_v01'
COARSE, FINE = 24, 96


def setup():
    model,_,asset,solids,sources = previous.setup()
    model['limitations'][-1] = 'Swept shapes use mesh preflight and closed faceted BRep Common on mesh indications'
    policy_files = read(SOURCE/'inputs.json')['files']
    verify_sources(policy_files)
    sources.update(policy_files)
    sources.update(source_manifest([Path(__file__),Path(p002_regeneration.__file__),
        SOURCE/'pool.json',SOURCE/'catalogue.json',SOURCE/'primary.json',SOURCE/'material_basis.json',
        previous.M/'three_d/p003_rescreen_search.py',previous.M/'three_d/p004_collision.py']))
    paths = read(previous.PREP/'geometry_paths.json')
    context,_ = geometry_context(read(previous.PREP/'facts.json'),paths,read(previous.PREP/'curved_side_evidence.json'))
    config = load_config()
    catalogue = read(SOURCE/'catalogue.json')
    primary = read(SOURCE/'primary.json')
    scorer = ProjectGeometryScorer(context,catalogue,config,read(SOURCE/'material_basis.json'))
    verify_sources(sources)
    return model,asset,solids,sources,paths,context,catalogue,primary,config,scorer


def make_batch(directory, model,asset,solids,paths,plans,pair_cache):
    geometries = {cid:mesh(shape,previous.CHECK_CONFIG) for cid,shape in solids.items()}
    for cid, shape in solids.items():
        save_shape(directory/'brep'/f'{cid}.brep',shape)
    material = previous.material_states(model,plans,asset,solids,paths,read(previous.ASSET/'base_registration.json'),
                                        directory,geometries)
    if read(directory/'material_audit.json')['unapplied_operation_ids']:
        raise ValueError('P002 material audit has unresolved actions in the supported-time batch')
    checker = p002_regeneration.Screen(model,material,directory,geometries,pair_cache)
    return checker


def publish_plan(out,plan,checker,asset,result):
    pid = plan['plan_id']
    mesh_dir = out/'meshes'
    mesh_dir.mkdir(parents=True,exist_ok=True)
    report = checker.run(plan,FINE,output=mesh_dir)
    if any(report.get(k)!=result.get(k) for k in ('status','counts','sample_count')):
        raise ValueError(f'Dense results changed during publishing for {pid}')
    final = report['runs'][-1]
    if any(gid!=checker.replay_id(cid) or not np.allclose(final['transforms'][cid],np.eye(4),rtol=0,atol=1e-10)
           for cid,gid in final['geometry_ids'].items()):
        raise ValueError('P002 final stage material and pose differ from STEP')
    checker.store_mesh(mesh_dir,'COMPLETED_STEP',np.asarray(asset['mesh']['positions']).reshape(-1,3,3))
    path = out/f'{pid}/collision_report.json'
    write(path,dict(plan_id=pid,assignments=plan['assignments'],runs=report['runs'],subdivisions=FINE,
        sample_count=report['sample_count'],summary=dict(counts=report['counts']),
        motion_reference='P002_DXF_CALIBRATED_O094_AND_STEP_CURVED_SWEEP',
        manufacturing_safety_proved=False))
    shutil.copyfile(previous.STEP,path.parent/'completed.step')
    plan['motion_verification'] = dict(report=relative(path),material_directory=relative(checker.directory),
        sample_count=report['sample_count'],pair_states=sum(report['counts'].values()))


def bend_key(plan, bend_ids):
    return tuple(plan['assignments'][oid] for oid in bend_ids)


def representatives(plans,bend_ids):
    reps = {}
    for plan in sorted(plans,key=ranking_key):
        reps.setdefault(bend_key(plan,bend_ids),plan)
    return reps


def choose(context,plans,bend_ids,config,target):
    selected,_ = select_candidates(context,list(representatives(plans,bend_ids).values()),config)
    if len(selected)<target:
        supplement,_ = select_candidates(context,plans,config)
        seen = {p['plan_hash'] for p in selected}
        selected.extend(p for p in supplement if p['plan_hash'] not in seen)
    return selected[:target]


def progress(pid,steps,checker):
    def report(sid,phase,samples):
        print(pid,steps,sid,phase,samples,'samples',checker.volume_calls,'solid',
              checker.mesh_calls,'mesh',flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot',type=int,default=0,help='Run this many proposals in a separate pilot directory')
    parser.add_argument('--rounds',type=int,default=3,help='Maximum batches of generator/checker feedback')
    args = parser.parse_args()
    if not 0 <= args.pilot <= 64 or args.rounds<1:
        parser.error('Expected pilot between 0 and 64 and a positive rounds limit')
    out = OUT if not args.pilot else OUT.with_name(f'{OUT.name}_pilot{args.pilot}')
    started = time.monotonic()
    protected = protected_hashes()
    model,asset,solids,sources,paths,context,catalogue,primary,config,scorer = setup()
    inputs_path = out/'inputs.json'
    if inputs_path.exists() and read(inputs_path)['files']!=sources:
        raise ValueError(f'{out.name} already uses a different input contract')
    write(inputs_path,dict(files=sources))
    write(out/'model.json',model)
    original = sorted(read(SOURCE/'pool.json')['plans'],key=ranking_key)
    bend_ids = tuple(sorted(op for op,o in context.operations.items() if o.operation_type=='BEND'))
    signatures = {bend_key(plan,bend_ids) for plan in original}
    explored_signatures = set(signatures)
    pair_cache = {}
    attempts, accepted, evidence, audits = [], [], {}, []
    dense_passes = {}
    target = args.pilot or config['generation']['pool_target']
    selected_target = min(target,config['generation']['selected_target'])
    if args.pilot:
        config = deepcopy(config)
        options = config['generation']
        options['pool_target'] = target
        options['selected_target'] = selected_target
        options['score_selection_count'] = min(options['score_selection_count'],selected_target)
    exhausted = False
    for round_no in range(1,args.rounds+1):
        excluded = [p['assignments'] for p in original]
        excluded.extend(p['assignments'] for p in attempts if p['plan_hash'] not in evidence)
        proposals, audit = build_passing_pool(context,catalogue,config,primary,scorer,signatures,
                                             seed_plans=accepted,excluded_assignments=excluded)
        audits.append(audit)
        already = {p['plan_hash'] for p in attempts}
        new = [p for p in proposals if p['plan_hash'] not in already][:target-len(accepted)]
        if not new and len(accepted)<target:
            exhausted = audit['state']=='passing_completions_exhausted'
        for index,plan in enumerate(new,len(attempts)+1):
            plan['plan_id'] = f'P002_R{index:03d}'
            plan['bend_stations'] = list(bend_key(plan,bend_ids))
        directory = out/'staged'/f'batch_{round_no:02d}'
        timings = {p['plan_id']:p002_regeneration.unsupported_relief(model,p['assignments']) for p in new}
        supported = [p for p in new if not timings[p['plan_id']]]
        write(directory/'proposals.json',dict(plans=new))
        print('P002 batch',round_no,len(new),'proposals,',len(supported),'with supported cut timing',flush=True)
        checker = make_batch(directory,model,asset,solids,paths,supported,pair_cache) if supported else None
        for plan in new:
            pid = plan['plan_id']
            if timings[pid]:
                coarse = dict(status='unknown',reason='DXF relief restoration does not cover this fold/cut order',
                              operations=timings[pid])
            else:
                coarse = checker.run(plan,COARSE,progress=progress(pid,COARSE,checker))
            decision = coarse['status']
            attempts.append(dict(**plan,status=decision,coarse=coarse,refined=None))
            if decision=='passed':
                accepted.append(plan)
                evidence[plan['plan_hash']] = checker,attempts[-1]
            write_checkpoint(out/'screening.json',dict(attempts=attempts))
            print(pid,decision,'passes',len(accepted),
                  'unique pairs',len(pair_cache),'seconds',round(time.monotonic()-started,1),flush=True)
        if len(accepted)>=target or exhausted or round_no==args.rounds:
            while True:
                selected = choose(context,accepted,bend_ids,config,selected_target)
                unchecked = [p for p in selected if p['plan_hash'] not in dense_passes]
                if not unchecked:
                    break
                for plan in unchecked:
                    key = plan['plan_hash']
                    checker,attempt = evidence[key]
                    full = checker.run(plan,FINE,progress=progress(plan['plan_id'],FINE,checker))
                    attempt.update(status=full['status'],refined=full)
                    if full['status']=='passed':
                        dense_passes[key] = full
                    else:
                        accepted.remove(plan)
                        del evidence[key]
                    write_checkpoint(out/'screening.json',dict(attempts=attempts))
                    print(plan['plan_id'],'96 subdivisions',full['status'],flush=True)
            if len(accepted)>=target or exhausted:
                break
        if len({bend_key(p,bend_ids) for p in accepted})<selected_target:
            finder = BendSearch(context,catalogue,config,primary,
                                (accepted or original)[0]['assignments'],explored_signatures)
            for _ in range(selected_target):
                proposal = finder.next()['assignments']
                if proposal is None:
                    break
                key = tuple(proposal[oid] for oid in bend_ids)
                signatures.add(key)
                explored_signatures.add(key)

    # Because cuts and forming can change which path passes, only the full
    # assignment is excluded from future completions after a failed trial.
    reps = representatives(accepted,bend_ids)
    selected = choose(context,accepted,bend_ids,config,selected_target)
    for plan in selected:
        key = plan['plan_hash']
        checker,_ = evidence[key]
        publish_plan(out,plan,checker,asset,dense_passes[key])
    verify_sources(sources)
    if protected_hashes()!=protected:
        raise ValueError('Protected P002 inputs changed during regeneration')
    write(out/'pool.json',dict(part_id='P002',plans=accepted))
    write(out/'plans.json',dict(part_id='P002',plans=selected,
        ranking=[p['plan_id'] for p in sorted(selected,key=ranking_key)]))
    write(out/'summary.json',dict(part_id='P002',termination='target_reached' if len(accepted)>=target else
        'passing_completions_exhausted' if exhausted else 'batch_budget_reached',
        checked_path_counts=dict(Counter(p['status'] for p in attempts)),pool_size=len(accepted),
        refined_counts=dict(Counter(p['refined']['status'] for p in attempts if p['refined'])),
        selected_count=len(selected),passing_motion_sequences=len(reps),coarse_subdivisions=COARSE,
        refined_subdivisions=FINE,selected_motion_sequences=len({bend_key(p,bend_ids) for p in selected}),
        selected_sample_count=sum(p['motion_verification']['sample_count'] for p in selected),
        selected_pair_states=sum(p['motion_verification']['pair_states'] for p in selected),
        geometry_pair_cache_entries=len(pair_cache),generation=audits,sources=sources,
        elapsed_seconds=round(time.monotonic()-started,2),manufacturing_safety_proved=False))
    print('P002 COMPLETE',len(accepted),'passing pool',len(selected),'selected',flush=True)


if __name__=='__main__':
    main()
