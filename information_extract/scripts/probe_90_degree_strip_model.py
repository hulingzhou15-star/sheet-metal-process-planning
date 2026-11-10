"""Drawing/CAD-tangency-driven static unfold experiment; no hole fitting.

Uses only the already identified unfolded DXF view. No process assignments or
intermediate process views supply any bend parameter. Not a production model.
"""
from pathlib import Path
import sys,json
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
MODULE=ROOT/'information_extract'

import ezdxf
from ezdxf.disassemble import recursive_decompose
from information_extract.scripts.check_whole_panel_collision import read,write,sha
from information_extract.three_d.local_motion import poses
from information_extract.three_d.state import transform_point


def coverage(intervals,length):
    end=0.;total=0.
    for a,b in sorted(intervals):
        a=max(0.,a);b=min(length,b)
        if b>max(end,a):total+=b-max(end,a)
        end=max(end,b)
    return total/length


def merged_intervals(intervals,length):
    result=[]
    for a,b in sorted(intervals):
        a=max(0.,a);b=min(length,b)
        if b-a<=1e-6:continue
        if result and a<=result[-1][1]+1e-6:result[-1][1]=max(result[-1][1],b)
        else:result.append([a,b])
    return np.array(result)


def main():
    out=MODULE/'outputs/hole_offset_90_audit_v01/P001';audit=read(out/'audit.json')
    stage_path=MODULE/'outputs/stage_review_v01/P001/stage_data.json';stage=read(stage_path)
    asset=read(MODULE/'outputs/three_d_state_v01/P001/asset.json')
    drawing_path=MODULE/'outputs/process_drawing_assistance/P001/evidence.json';drawing=read(drawing_path)
    unfold=next(v for v in drawing['views'] if v['id']=='unfold')
    dxf=Path(drawing['source'])
    if sha(dxf)!=drawing['source_sha256'] or not unfold['registration']['independently_validated']:raise ValueError('Unvalidated DXF view')
    protected={**audit['source_sha256'],str(dxf):sha(dxf),str(drawing_path.resolve()):sha(drawing_path)}
    doc=ezdxf.readfile(dxf);mapping=unfold['drawing_to_cad_base_xy'];matrix=np.array(mapping['matrix']);translation=np.array(mapping['translation_mm'])
    def xy(p):return matrix@np.array(list(p)[:2])+translation
    lines=[]
    for handle in unfold['top_level_source_handles']:
        for i,e in enumerate(recursive_decompose([doc.entitydb[handle]])):
            if e.dxftype()=='LINE':lines.append({'source_id':f'{handle}:{i}','a':xy(e.dxf.start).tolist(),'b':xy(e.dxf.end).tolist()})
    sk=stage['skeleton'];ops={o['operation_id']:o for o in stage['operations']};regions={r['region_id']:r for r in sk['regions']}
    old=poses(sk,{j['joint_id']:0 for j in sk['joints']});joint_audit={j['joint_id']:j for j in audit['joint_rows']}
    reg=stage['display_outline'];rm=np.array(reg['registration_matrix']);rt=np.array(reg['registration_translation_mm'])
    strip_rows=[]
    for j in sk['joints']:
        if abs(abs(j['angle_deg'])-90)>1e-6:continue
        oid=sk['joint_to_operation'][j['joint_id']];op=ops[oid];seg=op['segments'][0]
        p=rm@[seg['start_x'],seg['start_y']]+rt;q=rm@[seg['end_x'],seg['end_y']]+rt
        d=q-p;length=float(np.linalg.norm(d));d/=length;n=np.array([-d[1],d[0]])
        groups={}
        for line in lines:
            a,b=np.array(line['a']),np.array(line['b'])
            if abs(float((b-a)@n))>1e-6:continue
            dist=float((a-p)@n)
            if abs(dist)<1e-6 or abs(dist)>.6:continue
            aa,bb=sorted([float((a-p)@d),float((b-p)@d)])
            if min(length,bb)-max(0.,aa)<=1e-6:continue
            key=round(dist,6);groups.setdefault(key,[]).append({'source_id':line['source_id'],'interval_mm':[aa,bb]})
        cad_intervals=merged_intervals([sorted(float((np.array(v)[:2]-p)@d) for v in e['endpoints']) for e in j['developed_axis_segment_evidence_mm']],length)
        candidates=[]
        for k,v in groups.items():
            intervals=merged_intervals([x['interval_mm'] for x in v],length)
            exact_segments=intervals.shape==cad_intervals.shape and bool(intervals.size) and np.max(np.abs(intervals-cad_intervals))<=1e-5
            candidates.append({'signed_offset_mm':k,'coverage_fraction':coverage([x['interval_mm'] for x in v],length),
                'exact_physical_cylinder_intervals_match':bool(exact_segments),'source_segments':v})
        lower=sorted([c for c in candidates if c['signed_offset_mm']<0 and (c['coverage_fraction']>=.95 or c['exact_physical_cylinder_intervals_match'])],key=lambda c:-c['signed_offset_mm'])
        upper=sorted([c for c in candidates if c['signed_offset_mm']>0 and (c['coverage_fraction']>=.95 or c['exact_physical_cylinder_intervals_match'])],key=lambda c:c['signed_offset_mm'])
        row={'operation_id':oid,'joint_id':j['joint_id'],'candidates':candidates,'recorded_center_start_xy_mm':p.tolist(),'recorded_axis_direction_xy':d.tolist(),'normal_xy':n.tolist()}
        if lower and upper:
            lo,hi=lower[0],upper[0];row.update(status='PAIRED_DRAWING_BOUNDARY_EVIDENCE',
                lower=lo,upper=hi,developed_strip_width_mm=hi['signed_offset_mm']-lo['signed_offset_mm'],
                evidence_coverage='FULL_SPAN' if min(lo['coverage_fraction'],hi['coverage_fraction'])>=.95 else 'EXACT_FRAGMENTED_CAD_INTERVALS')
        else:row['status']='NO_FULL_SPAN_PAIRED_BOUNDARIES'
        strip_rows.append(row)
    strip_byjoint={r['joint_id']:r for r in strip_rows};new={sk['root_region_id']:np.eye(4)};model_rows=[]
    for j in sk['joints']:
        rid=j['child_region_id'];parent=j['parent_region_id'];row={'joint_id':j['joint_id'],'operation_id':sk['joint_to_operation'][j['joint_id']],'region_id':rid}
        if j['joint_id'] not in strip_byjoint or parent not in new:
            row['status']='UNSUPPORTED_NON90_OR_ANCESTOR';model_rows.append(row);continue
        strip=strip_byjoint[j['joint_id']]
        if strip['status']!='PAIRED_DRAWING_BOUNDARY_EVIDENCE':
            row['status']='MISSING_DRAWING_STRIP';model_rows.append(row);continue
        if not all(e['sharp_inner_and_radius_equals_thickness'] for e in joint_audit[j['joint_id']]['section_evidence']):
            row['status']='UNSUPPORTED_CAD_SECTION';model_rows.append(row);continue
        # Reference direction comes from the CAD plate, NOT hole observations.
        child_face=asset['faces'][regions[rid]['front_face_id']-1]
        child_center=np.array(transform_point(old[rid],child_face['centroid_mm']))[:2]
        p=np.array(strip['recorded_center_start_xy_mm']);n=np.array(strip['normal_xy'])
        sign=1 if float((child_center-p)@n)>0 else -1
        direction=sign*n;lo,hi=strip['lower']['signed_offset_mm'],strip['upper']['signed_offset_mm']
        near=lo if sign>0 else hi;far=hi if sign>0 else lo;width=strip['developed_strip_width_mm']
        targets=[];errors=[]
        for ev in j['surface_evidence']:
            cy=asset['faces'][ev['cylinder_face_id']-1]['cylinder'];center=np.array(cy['point_mm'])
            def tangent(region):
                pl=asset['faces'][regions[region]['front_face_id']-1]['plane'];normal=np.array(pl['normal']);normal/=np.linalg.norm(normal)
                return center-normal*float((center-pl['point_mm'])@normal)
            a,b=tangent(parent),tangent(rid)
            developed_a=np.array(transform_point(new[parent],a))
            error=abs(float((developed_a[:2]-p)@n)-near)
            target_b=developed_a+np.r_[direction*width,0.]
            rotation=old[rid][:3,:3];translation_b=target_b-rotation@b
            targets.append(translation_b);errors.append(error)
        row.update(developed_strip_width_mm=width,reference_direction_xy=direction.tolist(),
            parent_tangent_vs_drawing_boundary_error_mm=max(errors),
            multi_segment_translation_spread_mm=max(float(np.linalg.norm(t-targets[0])) for t in targets))
        if max(errors)>1e-5 or row['multi_segment_translation_spread_mm']>1e-5:
            row['status']='TANGENCY_OR_STRIP_INCONSISTENT';model_rows.append(row);continue
        T=old[rid].copy();T[:3,3]=np.mean(targets,axis=0);new[rid]=T
        row.update(status='INDEPENDENT_STATIC_UNFOLD_PROPOSAL',
            flat_translation_change_mm=(T[:3,3]-old[rid][:3,3]).tolist(),
            current_holes_used_to_fit=0)
        model_rows.append(row)
    # Validate on all hole observations only AFTER deriving plate placement.
    validations=[]
    for r in audit['hole_rows']:
        if r['status']!='UNIQUE_TRANSLATION_CONGRUENT_PROPOSAL' or r['region_id'] not in new:continue
        rid=r['region_id'];cad=np.array(r['cad_centroid_xyz_mm']);source=np.array(r['source_centroid_xyz_mm'])
        recorded=np.array(transform_point(old[rid],source));predicted=np.array(transform_point(new[rid],cad))
        validations.append({'operation_id':r['operation_id'],'region_id':rid,
            'pure_90_path':r['ninety_degree_path'],'old_error_mm':float(np.linalg.norm(r['flat_delta_xyz_mm'])),
            'new_error_mm':float(np.linalg.norm(predicted-recorded)),
            'new_delta_xyz_mm':(predicted-recorded).tolist(),'used_to_fit_model':False})
    supplementary=[]
    for r in audit['hole_rows']:
        rid=r['region_id']
        if rid not in new or r['status']=='UNIQUE_TRANSLATION_CONGRUENT_PROPOSAL':continue
        c=r.get('nearest_candidate')
        if not c:continue
        cad=np.array(c['cad_centroid_xyz_mm']);source=np.array(c['source_centroid_xyz_mm'])
        recorded=np.array(transform_point(old[rid],source));predicted=np.array(transform_point(new[rid],cad))
        supplementary.append({'operation_id':r['operation_id'],'operation_type':r['operation_type'],'region_id':rid,
            'pure_90_path':r['ninety_degree_path'],'status':r['status'],
            'nearest_candidate_wire_id':c['wire_id'],'old_centroid_delta_xyz_mm':c['flat_delta_xyz_mm'],
            'new_centroid_delta_xyz_mm':(predicted-recorded).tolist(),
            'target_is_circle':bool(c.get('cad_circle')),
            'same_panel_candidate_count':len(r.get('candidates',[])),
            'warning':'CENTROID_ONLY_FOR_NONCONGRUENT_SHAPES_NOT_A_HOLE_CENTER_OR_FULL_SHAPE_MATCH'})
    summary={'ninety_degree_bends':len(strip_rows),'paired_drawing_boundary_count':sum(r['status']=='PAIRED_DRAWING_BOUNDARY_EVIDENCE' for r in strip_rows),
        'strip_widths_mm':sorted(set(round(r['developed_strip_width_mm'],6) for r in strip_rows if 'developed_strip_width_mm' in r)),
        'supported_static_flat_panels':len(new),'model_joint_status_counts':dict(__import__('collections').Counter(r['status'] for r in model_rows)),
        'pure90_validated_hole_count':sum(v['pure_90_path'] for v in validations),
        'max_pure90_hole_error_mm':max((v['new_error_mm'] for v in validations if v['pure_90_path']),default=None),
        'holes_used_to_fit':0,'motion_model_implemented':False,'bend_solid_implemented':False,'production_geometry_changed':False,
        'dxf_scope':'ALREADY_IDENTIFIED_UNFOLDED_VIEW_ONLY; offline drawing-assisted geometry audit, not official inference input'}
    for path,h in protected.items():
        if sha(Path(path))!=h:raise ValueError('Protected source changed')
    result={'summary':summary,'source_sha256':protected,'strip_rows':strip_rows,'model_rows':model_rows,
        'flat_transforms':{rid:T.tolist() for rid,T in new.items()},'independent_hole_validation':validations,
        'noncongruent_centroid_comparisons':supplementary,
        'inputs_unchanged':True}
    write(out/'strip_model_probe.json',result);print(json.dumps(summary,ensure_ascii=False,indent=2))
    for row in model_rows:
        if row['status'] not in ['UNSUPPORTED_NON90_OR_ANCESTOR','INDEPENDENT_STATIC_UNFOLD_PROPOSAL']:print(row)


if __name__=='__main__':main()
