"""Recheck all rigid material regions (including local STEP bodies) of NP2_001.

Finite bend bands are not included in this panel-pair pass. This is a sampled
diagnostic, not a continuous forming or tooling feasibility verdict.
"""
from information_extract.scripts.build_local_forming_states import OUT,MODULE,read,write,sha
from information_extract.three_d.local_forming import load_shape,intersect
from information_extract.three_d.local_collision import volume,transformed
from information_extract.three_d.assets import bounds
from information_extract.three_d.band_material import state_transforms,station_fractions
from information_extract.three_d.panel_collision import transformed_bounds,aabb_gap
from collections import defaultdict,Counter
from itertools import combinations
from pathlib import Path
import json
import numpy as np


def make_samples(viewer, plan, steps=24):
    if steps < 2:
        raise ValueError('At least two motion subdivisions required')
    moving = {plan['assignments'][b['operation_id']] for b in viewer['model']['bends']}
    samples = []
    for station in sorted(viewer['stations'], key=lambda s: s['order']):
        sid = station['station_id']
        samples.append((sid, 'entry', 0.))
        if sid in moving:
            samples.extend((sid, 'motion', k / steps) for k in range(1, steps))
        samples.append((sid, 'completed', 1.))
    return samples


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def checked_checkpoint(checkpoint, signature, samples):
    if checkpoint.get('signature') != signature:
        raise ValueError('Checkpoint inputs/code differ; use a new output directory')
    runs = checkpoint['runs']
    actual = [(r['station_id'], r['phase'], r['tau']) for r in runs]
    if len(runs) > len(samples) or actual != samples[:len(runs)]:
        raise ValueError('Checkpoint sample sequence differs')
    return runs, checkpoint['kernel_calls']


def run(plan_id='NP2_001', viewer=None, report_dir=None, shared=None,
        signature=None, resume=False, steps=24):
    v=read(OUT/'viewer_data.json') if viewer is None else viewer
    model=v['model'];plan=next(p for p in v['plans'] if p['plan_id']==plan_id)
    destination=OUT if report_dir is None else Path(report_dir)
    checkpoint_path=destination/'collision_checkpoint.json' 
    variants={x['material_id']:x for x in v['variants']};orders={s['station_id']:s['order'] for s in v['stations']}
    old=read(MODULE/'baselines/P001/previous_panel_issues.json')['issues'] if plan_id=='NP2_001' else []
    shared={} if shared is None else shared
    shapes=shared.setdefault('shapes',{});boxes=shared.setdefault('boxes',{});cache=shared.setdefault('cache',{});runs=[];kernel_calls=0
    def shape(gid):
        if gid not in shapes:
            shapes[gid]=load_shape(OUT/v['geometries'][gid]['brep_file']);boxes[gid]=bounds(shapes[gid])
        return shapes[gid]
    samples=make_samples(v,plan,steps)
    if resume and checkpoint_path.exists():
        runs,kernel_calls=checked_checkpoint(read(checkpoint_path),signature,samples)
    for index,(sid,phase,tau) in enumerate(samples):
        if index < len(runs):
            continue
        mid=next(r['material_id'] for r in v['material_states'] if r['plan_id']==plan['plan_id'] and r['station_id']==sid and r['phase']==('completed' if phase=='completed' else 'entry'))
        material=variants[mid];fr=station_fractions(model,plan['assignments'],orders,sid,tau);ts=state_transforms(model,fr);groups=defaultdict(list)
        for c in material['components']:
            if c['kind']=='panel':groups[c['region_id']].append(c);shape(c['geometry_id'])
        world_boxes={c['component_id']:transformed_bounds(boxes[c['geometry_id']],ts[rid]) for rid,cs in groups.items() for c in cs}
        group_boxes={rid:[min(world_boxes[c['component_id']][i] for c in cs) for i in range(3)]+[max(world_boxes[c['component_id']][i+3] for c in cs) for i in range(3)] for rid,cs in groups.items()}
        rows=[];counts=Counter()
        for a,b in combinations(sorted(groups),2):
            if aabb_gap(group_boxes[a],group_boxes[b])>1e-6:counts['AABB_SEPARATED']+=1;continue
            relative=np.linalg.inv(ts[a])@ts[b];total=0.;unknown=[];overlap_parts=[]
            for ca in groups[a]:
                for cb in groups[b]:
                    if aabb_gap(world_boxes[ca['component_id']],world_boxes[cb['component_id']])>1e-6:continue
                    ga,gb=ca['geometry_id'],cb['geometry_id'];key=(ga,gb,*np.round(relative,10).flat)
                    if key not in cache:
                        try:cache[key]={'volume_mm3':volume(intersect(shape(ga),transformed(shape(gb),relative)))}
                        except Exception as exc:cache[key]={'error':str(exc)}
                        kernel_calls+=1
                    result=cache[key]
                    if 'error' in result:unknown.append({'components':[ca['component_id'],cb['component_id']],**result});continue
                    total+=result['volume_mm3']
                    if result['volume_mm3']>1e-6:overlap_parts.append({'components':[ca['component_id'],cb['component_id']],'local_forming_operations':[c['forming_operation_id'] for c in [ca,cb] if c.get('forming_operation_id')],'volume_mm3':result['volume_mm3']})
            status='UNKNOWN_KERNEL_FAILURE' if unknown else 'OVERLAP' if total>1e-6 else 'NO_POSITIVE_VOLUME_OVERLAP'
            counts[status]+=1
            if total>1e-6 or unknown:rows.append({'region_ids':[a,b],'intersection_volume_mm3':total,'status':status,'component_overlaps':overlap_parts,'errors':unknown})
        active=[o for o in material['executed_forming_ids']]
        runs.append({'station_id':sid,'phase':phase,'tau':tau,'material_id':mid,'executed_forming_ids':active,'pair_count':len(groups)*(len(groups)-1)//2,'counts':dict(counts),'issues':rows})
        atomic_write(checkpoint_path,{'signature':signature,'runs':runs,'kernel_calls':kernel_calls})
        if index%4==0 or phase=='completed':print(f'{plan_id} {index+1}/{len(samples)} {sid} {phase} {tau:.3f} {dict(counts)} kernels={kernel_calls}',flush=True)
    recheck=[]
    for issue in old:
        matches=[{'phase':r['phase'],'tau':r['tau'],**p} for r in runs if r['station_id']==issue['station_id'] for p in r['issues'] if p['region_ids']==issue['region_ids']]
        recheck.append({'station_id':issue['station_id'],'region_ids':issue['region_ids'],'status':'UNKNOWN_KERNEL_FAILURE' if any(p['status']=='UNKNOWN_KERNEL_FAILURE' for p in matches) else 'RESIDUAL_MODEL_OVERLAP' if matches else 'NO_OVERLAP_AT_RECHECK_SAMPLES','samples':matches})
    local_hits=[{'station_id':r['station_id'],'phase':r['phase'],'tau':r['tau'],'region_ids':p['region_ids'],**c} for r in runs for p in r['issues'] for c in p['component_overlaps'] if c['local_forming_operations']]
    result={'plan_id':plan['plan_id'],'signature':signature,'motion_subdivisions':steps,'source_sha256':{str(OUT/'viewer_data.json'):sha(OUT/'viewer_data.json')},'scope':'ALL_60_RIGID_REGION_PAIRS_INCLUDING_STEP_LOCAL_PATCHES; finite bands excluded; sampled states, no continuous forming/tooling verdict',
            'sample_count':len(runs),'kernel_calls':kernel_calls,'old_62_recheck_summary':dict(Counter(r['status'] for r in recheck)),'old_62_recheck':recheck,'local_forming_overlap_samples':local_hits,'runs':runs,'candidate_filter_enabled':False,'same_station_local_process':'UNKNOWN_ENTRY_GEOMETRY_DURING_MOTION_COMPLETED_ENDPOINT_SWITCH'}
    atomic_write(destination/'collision_report.json',result);print(plan_id,result['old_62_recheck_summary'], 'local hits',len(local_hits),flush=True)
    return result


if __name__=='__main__':run()
