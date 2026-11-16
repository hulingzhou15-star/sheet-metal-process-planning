"""Generate P004 plans with synchronized fold screening and staged-material checks."""
import argparse
from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path
import shutil
import time

import numpy as np

from information_extract.scripts.check_p003_candidate_motion import protected_hashes
from information_extract.scripts.generate_p004_allocation import MODULE, OUT as GENERATED, PREP, load_scoring
from information_extract.scripts.regenerate_p003_candidates import write_checkpoint
from information_extract.three_d.artifacts import ROOT, read, relative, sha, source_manifest, verify_sources, write
from information_extract.three_d.local_forming import load_shape
from information_extract.three_d.meshing import mesh
from information_extract.three_d.p002_motion_reference import CHECK_CONFIG
from information_extract.three_d.p003_rescreen_search import BendSearch, build_passing_pool
from information_extract.three_d.p004_collision import P004VariantChecker
from information_extract.three_d.p004_motion import Schedule, build_model, cylinder_axis_model, motion
from src.neighborhood_penalty.generation import ranking_key, select_candidates

OUT = MODULE/'outputs/p004_motion_regeneration_v01'
ASSET = MODULE/'outputs/multi_part_step_v01/P004/asset.json'
STEP = MODULE/'sources/P004/originals/P004.stp'
COARSE, FINE = 24, 96
FOLD_START = 'OP40'


def prove_material_switch(context):
    for op,row in context.operations.items():
        allowed = {s.station_id for s in context.allowed_stations(op)}
        if row.operation_type=='BEND':
            valid = allowed <= {'OP40','OP50','OP60'}
        elif row.operation_type=='DIMPLE':
            valid = allowed == {'OP40'}
        else:
            valid = allowed <= {'OP10','OP20','OP30'}
        if not allowed or not valid:
            raise ValueError(f'Unverified P004 material stage for {op}')


def load_material(model, plans, asset, destination):
    from information_extract.three_d.p004_staged_material import material_states

    solids = {}
    (destination/'brep').mkdir(parents=True,exist_ok=True)
    for row in model['cells']:
        cid = row['cell_id']
        source = PREP/'brep'/f'{cid}.brep'
        solids[cid] = load_shape(source)
        shutil.copyfile(source,destination/'brep'/source.name)
    geometries = {cid:mesh(shape,CHECK_CONFIG) for cid,shape in solids.items()}
    material = material_states(model,plans,asset,solids,read(PREP/'geometry_paths.json'),
                               read(PREP/'registration.json'),destination,geometries)
    geometries['COMPLETED_STEP'] = dict(triangles=np.asarray(asset['mesh']['positions']).reshape(-1,3,3).tolist())
    (destination/'meshes').mkdir(exist_ok=True)
    for gid,data in geometries.items():
        with gzip.open(destination/'meshes'/f'{gid}.json.gz','wt',encoding='utf8',compresslevel=3) as stream:
            json.dump(data,stream,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    return material


class SampleChecks:
    def __init__(self, model, axis_model, material, directory):
        self.model,self.axis_model,self.material = model,axis_model,material
        self.checker = P004VariantChecker(axis_model,directory)
        self.checker.adjacent.extend(
            (row['parent_region_id'],row['child_region_id'])
            for group in model.get('parallel_supports',{}).values() for row in group.get('supports',[])
        )
        self.pair_count = len(model['cells'])*(len(model['cells'])-1)//2
        self.poses = {}
        self.results = {}

    def sample(self, schedule, station, phase, tau):
        progress = schedule.progress(station,tau)
        in_fold_stages = schedule.order[station]>=schedule.order[FOLD_START]
        key = in_fold_stages, progress
        if key not in self.poses:
            pose_model = self.axis_model if in_fold_stages else self.model
            self.poses[key] = motion(pose_model,dict(zip(schedule.bends,progress)))
        pose = self.poses[key]
        variant = self.material[schedule.material_key(station,phase)]
        cached = variant['material_id'],key
        if cached not in self.results:
            result = self.checker.check(variant['cells'],pose['transforms'])
            if result['pair_count']!=self.pair_count or sum(result['counts'].values())!=self.pair_count:
                raise ValueError('Incomplete P004 material-pair coverage')
            self.results[cached] = result
        return pose,variant,self.results[cached]

    def screen(self, plan, steps, *, folds):
        schedule = Schedule(self.model,plan)
        first_unknown = None
        counts = Counter()
        sample_count = 0
        for station,phase,tau in schedule.samples(steps):
            if (schedule.order[station]>=schedule.order[FOLD_START])!=folds:
                continue
            pose,material,result = self.sample(schedule,station,phase,tau)
            counts.update(result['counts'])
            sample_count += 1
            state = dict(station_id=station,phase=phase,tau=tau,material_id=material['material_id'],
                         joint_angles_deg=pose['joint_angles_deg'])
            overlap = max((p for p in result['narrow_phase_pairs'] if p['status']=='OVERLAP'),
                          key=lambda p:p['intersection_volume_mm3'],default=None)
            if overlap:
                return dict(status='collision',witness={**state,**overlap},counts=dict(counts),
                            sample_count=sample_count,subdivisions=steps)
            if not first_unknown and (result['counts'].get('UNKNOWN_KERNEL_FAILURE') or material['pending_unmodelled']):
                first_unknown = {**state,'pending_unmodelled':material['pending_unmodelled'],
                                 'kernel_unknown_pair_count':result['counts'].get('UNKNOWN_KERNEL_FAILURE',0)}
        return dict(status='unknown' if first_unknown else 'passed',witness=first_unknown,
                    counts=dict(counts),sample_count=sample_count,subdivisions=steps)

    def report(self, plan, steps):
        schedule = Schedule(self.model,plan)
        runs,counts = [],Counter()
        issues = defaultdict(lambda:dict(samples=0,max_volume_mm3=0.))
        for station,phase,tau in schedule.samples(steps):
            pose,material,result = self.sample(schedule,station,phase,tau)
            state = schedule.frame(station,phase,tau,pose)
            state.update(material_id=material['material_id'],geometry_ids=material['cells'],
                         unapplied_material_event_ids=material['pending_unmodelled'],reference_geometry=result)
            runs.append(state)
            counts.update(result['counts'])
            for pair in result['narrow_phase_pairs']:
                if pair['status'] not in {'OVERLAP','UNKNOWN_KERNEL_FAILURE'}:
                    continue
                key = station,pair['status'],*pair['region_ids']
                issue = issues[key]
                issue['samples'] += 1
                volume = pair.get('intersection_volume_mm3',0.)
                if issue['samples']==1 or issue['max_volume_mm3']<volume:
                    issue.update(max_volume_mm3=volume,phase=phase,tau=tau,
                                 intersection_world_aabb_mm=pair.get('intersection_world_aabb_mm'))
        if any(cid!=geom for cid,geom in runs[-1]['geometry_ids'].items()) or any(
            not np.allclose(m,np.eye(4),atol=1e-10,rtol=0) for m in runs[-1]['transforms'].values()
        ):
            raise ValueError('Completed P004 material/pose differs from STEP partition')
        return dict(plan_id=plan['plan_id'],assignments=plan['assignments'],sample_count=len(runs),runs=runs,
            summary=dict(counts=dict(counts),issues=[dict(station_id=sid,status=status,cells=[c1,c2],**item)
                for (sid,status,c1,c2),item in sorted(issues.items())],
                samples_with_unapplied_material_events=sum(bool(s['unapplied_material_event_ids']) for s in runs)))


def motion_key(stations, ranks):
    # The OP40 material switch distinguishes paths across the same bend order.
    orbit = {s:i for i,s in enumerate(sorted({*stations,FOLD_START},key=ranks.get))}
    return tuple(orbit[s] for s in stations),orbit[FOLD_START]


def screen_paths(checker, seed_pool, context, catalogue, config, primary, sources, limit, root, axis):
    bend_ids = sorted(o['operation_id'] for o in checker.model['operations'] if o['operation_type']=='BEND')
    cache_path = root/'path_checks'/f'{axis}.json'
    if cache_path.exists():
        history = read(cache_path)
        if history['sources']!=sources:
            raise ValueError('Motion checkpoint differs from current P004 inputs')
        checked = {tuple(item['bend_stations']):item for item in history['paths']}
    else:
        checked = {}

    def test(plan):
        signature = tuple(plan['assignments'][op] for op in bend_ids)
        if signature in checked:
            return
        coarse = checker.screen(plan,COARSE,folds=True)
        fine = checker.screen(plan,FINE,folds=True) if coarse['status']=='passed' else None
        row = dict(bend_stations=signature, status=(fine or coarse)['status'],
                   example_plan_id=plan['plan_id'],coarse=coarse,refined=fine,assignments=plan['assignments'])
        checked[signature] = row
        write_checkpoint(cache_path,dict(sources=sources,bend_operations=bend_ids,paths=list(checked.values())))
        if row['status']=='passed' or len(checked)%20==0:
            print('P004 PATHS',len(checked), dict(Counter(x['status'] for x in checked.values())),flush=True)

    for plan in seed_pool:
        test(plan)
    passed = [x for x in checked.values() if x['status']=='passed']
    accepted_paths = {motion_key(x['bend_stations'],context.station_ranks) for x in passed}
    seed = passed[0]['assignments'] if passed else seed_pool[0]['assignments']
    search = BendSearch(context,catalogue,config,primary,seed,checked.keys())
    target = config['generation']['selected_target']
    stop = 'path_budget_reached'
    while len(accepted_paths)<target and len(checked)<limit:
        proposal = search.next()
        if proposal['assignments'] is None:
            stop = 'projection_exhausted' if proposal['status']=='INFEASIBLE' else 'solver_incomplete'
            break
        plan = dict(plan_id=f'fold_trial_{len(checked)+1:04d}',assignments=proposal['assignments'])
        test(plan)
        signature = search.signature(plan['assignments'])
        if checked[signature]['status']=='passed':
            accepted_paths.add(motion_key(signature,context.station_ranks))
    if len(accepted_paths)>=target:
        stop = 'passing_motion_target_reached'
    return checked,stop


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-paths',type=int,default=160)
    parser.add_argument('--axis',choices=('midplane','step-cylinder'),default='midplane')
    parser.add_argument('--probe-only',action='store_true')
    args = parser.parse_args()
    if args.max_paths<1:
        parser.error('Positive motion search limit required')
    began = time.monotonic()
    protection = protected_hashes()
    context,catalogue,config,primary,scorer = load_scoring()
    prove_material_switch(context)
    model = build_model(read(PREP/'facts.json'),read(PREP/'skeleton.json'),read(PREP/'material_basis.json'))
    model['parallel_supports'] = read(PREP/'partition.json')['parallel_supports']
    asset = read(ASSET)
    if asset['asset_id']!=sha(STEP):
        raise ValueError('Stale P004 STEP asset')
    seed_pool = sorted(read(GENERATED/'pool.json')['plans'],key=ranking_key)
    alternate,offsets = cylinder_axis_model(model,asset)
    axis_model = alternate if args.axis=='step-cylinder' else model
    root = (OUT/'axis_probes') if args.probe_only else OUT
    from information_extract.three_d import p004_collision, p004_motion, p004_staged_material
    sources = dict(read(GENERATED/'inputs.json')['files'])
    sources.update(source_manifest([Path(__file__),Path(p004_collision.__file__),
        Path(p004_motion.__file__),Path(p004_staged_material.__file__),
        PREP/'skeleton.json', PREP/'registration.json', PREP/'partition.json',
        GENERATED/'pool.json', GENERATED/'plans.json',ASSET,STEP]))
    verify_sources(sources)
    write(root/'model.json',model)
    material_path = root/'screen_material'
    material = load_material(model,[seed_pool[0]],asset,material_path)
    early = SampleChecks(model,model,material,material_path).screen(seed_pool[0],COARSE,folds=False)
    write(root/'early_material_probe.json',early)
    versions = [('midplane',model),('step-cylinder',alternate)] if args.probe_only else [(args.axis,axis_model)]
    for label,version in versions:
        checker = SampleChecks(model,version,material,material_path)
        result = checker.screen(seed_pool[0],COARSE,folds=True)
        write(root/f'{label}_probe.json',result)
        print('P004 AXIS PROBE',label,result['status'],result['witness'],flush=True)
    if args.probe_only:
        return
    # Axes deliberately enter the checkpoint identity, including when sources are identical.
    checked,stop = screen_paths(checker,seed_pool,context,catalogue,config,primary,
                               sources,args.max_paths,root,args.axis)
    allowed = [key for key,row in checked.items() if row['status']=='passed']
    base = dict(part_id='P004', axis=args.axis, axis_offsets_mm=offsets, termination=stop,
        coarse_subdivisions=COARSE, refined_subdivisions=FINE, source_pool_count=len(seed_pool),
        checked_path_counts=dict(Counter(row['status'] for row in checked.values())), sources=sources,
        manufacturing_safety_proved=False)
    if not allowed:
        write(root/'summary.json',dict(base,pool_size=0,selected_count=0,elapsed_seconds=round(time.monotonic()-began,2)))
        print('P004 no passing motion path',stop,flush=True)
        return
    passed = []
    early_failures = []
    banned = []
    stage_sources = {}
    generated_ids = {}
    while len(passed)<config['generation']['pool_target']:
        pool,audit = build_passing_pool(context,catalogue,config,primary,scorer,allowed,
                                       seed_plans=passed,excluded_assignments=banned)
        fresh = [row for row in pool if row['plan_hash'] not in stage_sources]
        if not fresh:
            break
        for row in fresh:
            row['plan_id'] = generated_ids.setdefault(row['plan_hash'],f'P004_R{len(generated_ids)+1:03d}')
            row['bend_stations'] = [row['assignments'][op] for op in sorted(
                r['operation_id'] for r in model['operations'] if r['operation_type']=='BEND')]
        stage_path = root/'staged'/f'batch_{len({item[0] for item in stage_sources.values()})+1:02d}'
        stage_material = load_material(model,fresh,asset,stage_path)
        stage_checker = SampleChecks(model,axis_model,stage_material,stage_path)
        previous_failures = len(early_failures)
        for row in fresh:
            result = stage_checker.screen(row,COARSE,folds=False)
            stage_sources[row['plan_hash']] = stage_path,stage_checker
            if result['status']=='passed':
                passed.append(row)
            else:
                banned.append(row['assignments'])
                early_failures.append(dict(plan_id=row['plan_id'],**result))
        if len(early_failures)==previous_failures:
            break
    reps = {}
    for row in sorted(passed,key=ranking_key):
        reps.setdefault(motion_key(row['bend_stations'],context.station_ranks),row)
    selected, selection = select_candidates(context,list(reps.values()),config)
    if len(selected)<config['generation']['selected_target']:
        supplement,_ = select_candidates(context,passed,config)
        seen = {row['plan_hash'] for row in selected}
        selected.extend(row for row in supplement if row['plan_hash'] not in seen)
        selected = selected[:config['generation']['selected_target']]
    for row in selected:
        stage_path,staged = stage_sources[row['plan_hash']]
        report = staged.report(row,FINE)
        counts = report['summary']['counts']
        if counts.get('OVERLAP') or counts.get('UNKNOWN_KERNEL_FAILURE') or report['summary']['samples_with_unapplied_material_events']:
            raise ValueError(f"Staged geometry did not reproduce passage for {row['plan_id']}")
        report['motion_reference'] = dict(before_op40='P004_DXF_REGISTERED_FLAT',op40_and_later=args.axis)
        report_path = root/'selected'/row['plan_id']/'collision_report.json'
        write(report_path,report)
        row['motion_verification'] = dict(report=relative(report_path),material_directory=relative(stage_path),
            sample_count=report['sample_count'],pair_states=sum(counts.values()))
    verify_sources(sources)
    if protected_hashes()!=protection:
        raise ValueError('Protected P004 sources changed')
    write(root/'pool.json',dict(part_id='P004',plans=passed))
    write(root/'plans.json',dict(part_id='P004',plans=selected,ranking=[r['plan_id'] for r in sorted(selected,key=ranking_key)]))
    write(root/'summary.json',dict(base,pool_size=len(passed),selected_count=len(selected),
        selection=selection, passing_motion_sequences=len(reps), selected_motion_sequences=len(
            {motion_key(row['bend_stations'],context.station_ranks) for row in selected}),
        selected_sample_count=sum(row['motion_verification']['sample_count'] for row in selected),
        selected_pair_states=sum(row['motion_verification']['pair_states'] for row in selected),
        rejected_early_material=early_failures,pool_audit=audit,elapsed_seconds=round(time.monotonic()-began,2)))
    print('P004 COMPLETE',len(passed),'pool;',len(selected),'selected',flush=True)


if __name__=='__main__':
    main()
