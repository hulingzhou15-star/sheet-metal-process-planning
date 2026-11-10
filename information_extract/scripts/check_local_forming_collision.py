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
import numpy as np


def run():
    v=read(OUT/'viewer_data.json');model=v['model'];plan=next(p for p in v['plans'] if p['plan_id']=='NP2_001')
    variants={x['material_id']:x for x in v['variants']};orders={s['station_id']:s['order'] for s in v['stations']}
    old=read(MODULE/'baselines/P001/previous_panel_issues.json')['issues']
    shapes={};boxes={};cache={};runs=[];kernel_calls=0
    def shape(gid):
        if gid not in shapes:
            shapes[gid]=load_shape(OUT/v['geometries'][gid]['brep_file']);boxes[gid]=bounds(shapes[gid])
        return shapes[gid]
    samples=[]
    for s in v['stations']:
        samples.append((s['station_id'],'entry',0.))
        if s['station_id'] in ['OP40','OP50','OP60']:
            samples.extend((s['station_id'],'motion',k/24) for k in range(1,24))
        samples.append((s['station_id'],'completed',1.))
    for index,(sid,phase,tau) in enumerate(samples):
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
        write(OUT/'collision_checkpoint.json',{'runs':runs,'kernel_calls':kernel_calls})
        if index%4==0 or phase=='completed':print(f'{index+1}/{len(samples)} {sid} {phase} {tau:.3f} {dict(counts)} kernels={kernel_calls}',flush=True)
    recheck=[]
    for issue in old:
        matches=[{'phase':r['phase'],'tau':r['tau'],**p} for r in runs if r['station_id']==issue['station_id'] for p in r['issues'] if p['region_ids']==issue['region_ids']]
        recheck.append({'station_id':issue['station_id'],'region_ids':issue['region_ids'],'status':'UNKNOWN_KERNEL_FAILURE' if any(p['status']=='UNKNOWN_KERNEL_FAILURE' for p in matches) else 'RESIDUAL_MODEL_OVERLAP' if matches else 'NO_OVERLAP_AT_RECHECK_SAMPLES','samples':matches})
    local_hits=[{'station_id':r['station_id'],'phase':r['phase'],'tau':r['tau'],'region_ids':p['region_ids'],**c} for r in runs for p in r['issues'] for c in p['component_overlaps'] if c['local_forming_operations']]
    result={'plan_id':plan['plan_id'],'source_sha256':{str(OUT/'viewer_data.json'):sha(OUT/'viewer_data.json')},'scope':'ALL_60_RIGID_REGION_PAIRS_INCLUDING_STEP_LOCAL_PATCHES; finite bands excluded; sampled states, no continuous forming/tooling verdict',
            'sample_count':len(runs),'kernel_calls':kernel_calls,'old_62_recheck_summary':dict(Counter(r['status'] for r in recheck)),'old_62_recheck':recheck,'local_forming_overlap_samples':local_hits,'runs':runs,'candidate_filter_enabled':False,'same_station_local_process':'UNKNOWN_ENTRY_GEOMETRY_DURING_MOTION_COMPLETED_ENDPOINT_SWITCH'}
    write(OUT/'collision_report.json',result);print(result['old_62_recheck_summary'], 'local hits',len(local_hits),flush=True)


if __name__=='__main__':run()
