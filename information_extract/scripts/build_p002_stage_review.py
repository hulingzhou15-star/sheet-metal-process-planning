"""Publish a P001-style audit page using the delivered P002 replay artifacts."""
from collections import defaultdict
import gzip
import json
import shutil

import numpy as np

from information_extract.scripts.check_p002_curve_motion import ASSET, DEST, M, PREP, WEB
from information_extract.three_d.artifacts import read, relative, sha, verify_sources, write
from information_extract.three_d.p002_curve_motion import bind_vertices, frame, sweep
from information_extract.three_d.p002_staged_material import apply, charts

OUT = DEST/'stage'


def segment_points(s):
    if s['segment_type'] in ('LINE','BEND_LINE'):
        return [[s['start_x'],s['start_y']],[s['end_x'],s['end_y']]]
    center = np.array([s['center_x'],s['center_y']])
    if s['segment_type']=='CIRCLE':
        start, span = 0., 2*np.pi
    elif s['segment_type']=='ARC':
        start = np.arctan2(s['start_y']-center[1],s['start_x']-center[0])
        span = np.deg2rad(s['sweep_angle'])
    else:
        raise ValueError('Unsupported feature outline')
    angles = np.linspace(start,start+span,max(5,min(129,int(abs(span)*s['radius']/.4)+2)))
    return center+s['radius']*np.column_stack((np.cos(angles),np.sin(angles)))


def pack_group(cid, paths, normal, curves):
    group = dict(cell=cid,paths=[np.asarray(p).round(7).tolist() for p in paths],normal=np.asarray(normal).tolist())
    if cid in curves:
        curve = curves[cid]
        points = np.vstack(paths)
        origins,normals,_ = bind_vertices(points,curve['guides'])
        group.update(sweep_sign=curve['z_sign'], origin=origins.round(8).tolist(), outward=normals.round(8).tolist())
    return group


def feature_data(model, plan, asset, reg, audit, all_paths):
    curves = model['curve_guides']
    curve_ids = {r['operation_id']:cid for cid,r in curves.items()}
    joint_by_op = defaultdict(list)
    for j in model['joints']:
        joint_by_op[j['operation_id']].append(j)
    chart_by_cell = defaultdict(list)
    for chart in charts(model,plan,asset):
        chart_by_cell[chart['cell']].append(chart)
    matrix, translation = np.array(reg['matrix']),np.array(reg['translation_mm'])
    straight = {r['joint_id']:r for r in read(PREP/'straight_branch_proposals.json')['skeleton']['joints']}
    out = {}
    for op in model['operations']:
        oid = op['operation_id']
        path_groups = [[np.asarray(segment_points(seg))@matrix.T+translation for seg in path['segments']]
                       for path in all_paths[oid]['paths']]
        flat = [p for group in path_groups for p in group]
        groups = []
        if oid in curve_ids:
            source_edges = [np.asarray(asset['edges'][g['step_edge_id']-1]['points']) for g in curves[curve_ids[oid]]['guides']]
            groups.append(pack_group('C001',source_edges,[0,0,1],curves))
        elif oid in joint_by_op:
            for j in joint_by_op[oid]:
                jr = straight[j['joint_id']]
                axis = np.array(j['axis_direction'])
                pivot = np.array(j['hinge_point_mm'])
                evidence = asset['faces'][jr['surface_evidence'][0]['cylinder_face_id']-1]
                bounds = np.array(evidence['bounds_mm'])
                # The face bounds are exact along each finite straight hinge axis.
                extrema = [np.where(axis>0,bounds[3:],bounds[:3]),np.where(axis>0,bounds[:3],bounds[3:])]
                ends = [pivot+((p-pivot)@axis)*axis for p in extrema]
                cid = 'C007' if oid=='O094' else j['parent_cell_id']
                normal = jr['reference_surface_normals']['parent']
                groups.append(pack_group(cid,[ends],normal,curves))
        else:
            audit_row = audit[oid]
            source_parent = audit.get(op.get('cad_parent_operation_id'),{})
            edge_id = audit_row.get('match',{}).get('step_edge_id') or source_parent.get('match',{}).get('step_edge_id')
            edge = asset['edges'][edge_id-1] if edge_id else None
            if audit_row.get('parts'):
                owners = [(part['cell'],int(part['wire'].split('/')[0])) for part in audit_row['parts']]
            else:
                owners = [(audit_row['cell'],None)]
            for i,(cid,fid) in enumerate(owners):
                paths = path_groups[i] if len(owners)==len(path_groups) else flat
                points = np.vstack(paths)
                center = (points.min(0)+points.max(0))/2
                candidates = chart_by_cell[cid]
                chart = next((c for c in candidates if fid in c['faces']),None)
                if chart is None:
                    def distance(c):
                        p = (edge.get('circle',{}).get('center_mm') if edge else None) or asset['faces'][c['faces'][0]-1]['centroid_mm']
                        return np.linalg.norm(apply(c['mat'],p)[:2]-center)
                    chart = min(candidates,key=distance)
                mat = chart['mat']
                shift = np.zeros(2)
                if edge and 'circle' in edge:
                    shift = apply(mat,edge['circle']['center_mm'])[:2]-center
                inv = np.linalg.inv(mat)
                normal = inv[:3,2]
                paths_3d = [apply(inv,np.column_stack((p+shift,np.full(len(p),chart['mid']+.4)))) for p in paths]
                groups.append(pack_group(cid,paths_3d,normal,curves))
        out[oid] = dict(flat=[p.round(6).tolist() for p in flat],groups=groups)
    return out


def flat_geometry(model,plan,variants):
    start = frame(model,plan,model['stations'][0]['station_id'],'entry',0.)
    flat_dir = OUT/'flat'
    flat_dir.mkdir(parents=True,exist_ok=True)
    seen = set()
    for variant in variants:
        for cid,gid in variant['cells'].items():
            if gid in seen:
                continue
            seen.add(gid)
            with gzip.open(DEST/'meshes'/f'{gid}.json.gz','rt',encoding='utf8') as stream:
                tri = np.asarray(json.load(stream)['triangles'])
            points = tri.reshape(-1,3)
            if cid in model['curve_guides']:
                c = model['curve_guides'][cid]
                mapped = sweep(points,bind_vertices(points,c['guides']),0.,c['z_sign'])
            else:
                mapped = apply(np.asarray(start['transforms'][cid]),points)
            xy = mapped[:,:2].reshape(-1,3,2).round(5)
            ab,ac = xy[:,1]-xy[:,0],xy[:,2]-xy[:,0]
            area = ab[:,0]*ac[:,1]-ab[:,1]*ac[:,0]
            xy[area<0] = xy[area<0,::-1]
            xy = xy[abs(area)>1e-8]
            with gzip.open(flat_dir/f'{gid}.json.gz','wt',encoding='utf8',compresslevel=3) as stream:
                json.dump(xy.reshape(-1).tolist(),stream,separators=(',',':'))
    return len(seen)


def main():
    verify_sources(read(DEST/'output_manifest.json')['files'])
    model = read(DEST/'model.json')
    plans = read(DEST/'plans.json')
    asset = read(ASSET/'asset.json')
    staging = read(DEST/'material_states.json')
    audit = read(DEST/'material_audit.json')['operations']
    plan = plans['plans'][0]
    templates = WEB/'local_forming_review.html'
    template = templates.read_text(encoding='utf8')
    css = template.split('<style>',1)[1].split('</style>',1)[0]
    theme_path = M/'configs/local_forming_display_v03.json'
    OUT.mkdir(exist_ok=True)
    (OUT/'review.css').write_text(css,encoding='utf8')
    (OUT/'theme.js').write_text('globalThis.FiniteMaterialTheme='+json.dumps(read(theme_path))+';\n',encoding='utf8')
    for name,target in [('p002_stage_workspace.html','index.html'),('p002_stage_workspace.js','review.js')]:
        shutil.copyfile(WEB/name,OUT/target)
    features = feature_data(model,plan,asset,read(ASSET/'base_registration.json'),audit,read(PREP/'geometry_paths.json'))
    count = flat_geometry(model,plan,staging['variants'])
    write(OUT/'data.json',dict(model=model,features=features,variants=staging['variants'],ranking=plans['ranking'],
        assignments={p['plan_id']:p['assignments'] for p in plans['plans']}))
    sources = [templates,theme_path,WEB/'p002_stage_workspace.html',WEB/'p002_stage_workspace.js',
               DEST/'output_manifest.json',ASSET/'asset.json',ASSET/'base_registration.json',
               PREP/'geometry_paths.json',PREP/'straight_branch_proposals.json']
    write(OUT/'verification.json',dict(sources={relative(p):sha(p) for p in sources},
        feature_count=len(features),material_projection_count=count,plan_count=len(plans['plans']),
        source_replay_unchanged=True))
    print('Published',len(features),'features and',count,'flat projections')


if __name__=='__main__':
    main()
