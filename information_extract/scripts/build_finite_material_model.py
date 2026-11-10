"""All 59 bends from independent DXF strips and CAD tangent frames."""
from pathlib import Path
import sys,math
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
MODULE=ROOT/'information_extract'
import ezdxf
from ezdxf.disassemble import recursive_decompose
from information_extract.three_d.artifacts import read,write,sha
from information_extract.three_d.intervals import coverage,merged_intervals
from information_extract.three_d.artifacts import source_manifest
from information_extract.three_d.local_motion import poses
from information_extract.three_d.state import transform_point
from information_extract.three_d.band_material import state_transforms,strip_point,band_volume_ratio


def build():
    out=MODULE/'outputs/finite_material_v02/P001';out.mkdir(parents=True,exist_ok=True)
    stage_path=MODULE/'outputs/stage_review_v01/P001/stage_data.json';stage=read(stage_path)
    asset=read(MODULE/'outputs/three_d_state_v01/P001/asset.json')
    # Current dependencies only. Historical audit hashes stay in the archived baseline.
    protected=source_manifest([stage_path,MODULE/'outputs/three_d_state_v01/P001/asset.json',
        MODULE/'outputs/process_drawing_assistance/P001/evidence.json',
        MODULE/'sources/P001/process_drawing.dxf',MODULE/'sources/P001/formed.step',
        ROOT/'data/parts/P001/钣金加工数据表_P001.xlsx',
        ROOT/'baselines/P001/plans/all_16_complete_assignments.json',
        MODULE/'configs/P001_confirmed_material_parameters.json'])
    for path,h in protected.items():
        if sha(Path(path))!=h:raise ValueError('Stale source: '+path)
    drawing=read(MODULE/'outputs/process_drawing_assistance/P001/evidence.json');view=next(v for v in drawing['views'] if v['id']=='unfold')
    if not view['registration']['independently_validated']:raise ValueError('Unverified drawing')
    doc=ezdxf.readfile(drawing['source']);mapping=view['drawing_to_cad_base_xy'];lines=[]
    def xy(p):return np.array(mapping['matrix'])@np.array(list(p)[:2])+mapping['translation_mm']
    for handle in view['top_level_source_handles']:
        for i,e in enumerate(recursive_decompose([doc.entitydb[handle]])):
            if e.dxftype()=='LINE':lines.append({'source_id':f'{handle}:{i}','a':xy(e.dxf.start),'b':xy(e.dxf.end)})
    sk=stage['skeleton'];regions={r['region_id']:r for r in sk['regions']};ops={o['operation_id']:o for o in stage['operations']}
    old=poses(sk,{j['joint_id']:0 for j in sk['joints']});flat={sk['root_region_id']:np.eye(4)};reg=stage['display_outline'];bends=[]
    for j in sk['joints']:
        oid=sk['joint_to_operation'][j['joint_id']];seg=ops[oid]['segments'][0]
        p=np.array(reg['registration_matrix'])@[seg['start_x'],seg['start_y']]+reg['registration_translation_mm']
        q=np.array(reg['registration_matrix'])@[seg['end_x'],seg['end_y']]+reg['registration_translation_mm']
        axis=q-p;length=float(np.linalg.norm(axis));axis/=length;n=np.array([-axis[1],axis[0]])
        groups={}
        for line in lines:
            a,b=line['a'],line['b']
            if abs((b-a)@n)>1e-6:continue
            off=float((a-p)@n)
            # Sheet bend center lines have 0.001mm rounded coordinates; use
            # only boundaries outside that rounding scale, never a near-center line.
            if abs(off)<.001 or abs(off)>.6:continue
            lo,hi=sorted([float((a-p)@axis),float((b-p)@axis)])
            if min(length,hi)-max(0.,lo)>1e-6:groups.setdefault(round(off,9),[]).append({'source_id':line['source_id'],'interval_mm':[lo,hi]})
        cad_intervals=merged_intervals([sorted(float((np.array(v)[:2]-p)@axis) for v in e['endpoints']) for e in j['developed_axis_segment_evidence_mm']],length)
        candidates=[]
        for off,rows in groups.items():
            ints=merged_intervals([r['interval_mm'] for r in rows],length)
            exact=bool(ints.size) and ints.shape==cad_intervals.shape and np.max(np.abs(ints-cad_intervals))<1e-5
            cov=coverage([r['interval_mm'] for r in rows],length)
            if cov>=.95 or exact:candidates.append({'offset_mm':off,'source_segments':rows,'coverage':cov,'exact_cad_intervals':bool(exact)})
        lower=sorted([c for c in candidates if c['offset_mm']<0],key=lambda c:-c['offset_mm'])
        upper=sorted([c for c in candidates if c['offset_mm']>0],key=lambda c:c['offset_mm'])
        if not lower or not upper:raise ValueError('No independent strip for '+oid)
        lo,hi=lower[0],upper[0];width=hi['offset_mm']-lo['offset_mm']
        rid,parent=j['child_region_id'],j['parent_region_id'];fp=flat[parent]
        child_face=asset['faces'][regions[rid]['front_face_id']-1]
        child_center=np.array(transform_point(old[rid],child_face['centroid_mm']))[:2]
        sign=1 if float((child_center-p)@n)>0 else -1
        forward=np.r_[sign*n,0.];axis3=np.r_[axis,0.];near=lo['offset_mm'] if sign>0 else hi['offset_mm']
        start=np.r_[p+n*near,0.]
        cy=asset['faces'][j['surface_evidence'][0]['cylinder_face_id']-1]['cylinder'];center=np.array(cy['point_mm'])
        def tangent(region):
            plane=asset['faces'][regions[region]['front_face_id']-1]['plane'];norm=np.array(plane['normal']);norm/=np.linalg.norm(norm)
            return center-norm*float((center-plane['point_mm'])@norm)
        a,b=tangent(parent),tangent(rid);developed_a=np.array(transform_point(fp,a))
        parent_error=abs(float((developed_a-start)@forward))
        if parent_error>1e-5 or abs(developed_a[2])>1e-5:raise ValueError('Parent strip tangent mismatch '+oid)
        target_b=developed_a+forward*width;fc=old[rid].copy();fc[:3,3]=target_b-fc[:3,:3]@b;flat[rid]=fc
        child_normal=fp[:3,:3]@np.array(j['reference_surface_normals']['child']);z=np.array([0.,0.,1.])
        alpha=math.atan2(float(-child_normal@forward),float(child_normal@z))
        if abs(abs(math.degrees(alpha))-abs(j['angle_deg']))>1e-6:raise ValueError('Angle mismatch')
        delta=fp[:3,:3]@(b-a);sn,cs=math.sin(alpha),1-math.cos(alpha)
        radius=width/alpha;coefficient=(float(delta@forward)*sn+float(delta@z)*cs)/(sn*sn+cs*cs)
        h=radius-coefficient
        if not -1e-6<=h<=.8+1e-6:raise ValueError('Geometric offset outside thickness '+oid)
        # Preserve actual CAD axial intervals in the NEW parent material frame.
        intervals=[]
        for ev in j['surface_evidence']:
            f=asset['faces'][ev['cylinder_face_id']-1];pts=[v for eid in f['edge_ids'] for v in asset['edges'][eid-1]['points']]
            values=[float((np.array(transform_point(fp,v))-start)@axis3) for v in pts]
            intervals.append([min(values),max(values)])
        ints=merged_intervals(intervals,length).tolist()
        bend={'joint_id':j['joint_id'],'operation_id':oid,'parent_region_id':parent,'child_region_id':rid,
            'start_flat_mm':start.tolist(),'forward_flat':forward.tolist(),'axis_flat':axis3.tolist(),
            'width_mm':width,'target_angle_deg':math.degrees(alpha),'cad_signed_angle_deg':j['angle_deg'],
            'geometric_offset_from_front_mm':h,'axis_intervals_mm':ints,
            'lower_boundary':lo,'upper_boundary':hi,'cad_cylinder_face_ids':[e['cylinder_face_id'] for e in j['surface_evidence']],
            'cad_radii_mm':sorted(set(asset['faces'][e['cylinder_face_id']-1]['cylinder']['radius_mm'] for e in j['surface_evidence'])),
            'parent_tangent_error_mm':parent_error,
            'offset_interpretation':'GEOMETRIC_EQUIVALENT_NOT_MATERIAL_K'}
        end=strip_point(bend,start+forward*width,1.)
        target=np.array(transform_point(fp,b))
        # Compare at the same axial location (CAD axis origins may lie outside the strip).
        end+=axis3*float((target-end)@axis3)
        if np.linalg.norm(end-target)>1e-6:raise ValueError('Bend endpoint mismatch '+oid)
        bend['final_band_volume_ratio']=band_volume_ratio(bend,1.,.8);bends.append(bend)
    model={'version':'FINITE_MATERIAL_V02','part_id':'P001','thickness_mm':.8,'root_region_id':sk['root_region_id'],
        'regions':[{**r,'flat_from_cad':flat[r['region_id']].tolist()} for r in sk['regions']],
        'bends':bends,'source_sha256':protected,'physical_forming_validated':False,'candidate_filter_enabled':False,
        'initial_blank_recovered':False,'unconnected_regions':sk['unconnected_region_proposal_ids']}
    end=state_transforms(model,{b['joint_id']:1. for b in bends})
    errors={rid:float(np.max(np.abs(end[rid]@flat[rid]-np.eye(4)))) for rid in flat}
    if max(errors.values())>1e-6:raise ValueError('Final CAD pose did not close')
    model['validation']={'panel_count':len(flat),'bend_count':len(bends),'max_final_transform_error':max(errors.values()),
        'ninety_degree_count':sum(abs(abs(b['target_angle_deg'])-90)<1e-6 for b in bends),
        'small_angle_count':sum(abs(b['target_angle_deg'])<80 for b in bends),
        'ninety_widths_mm':sorted(set(round(b['width_mm'],9) for b in bends if abs(b['target_angle_deg'])>80)),
        'small_angle_widths_mm':sorted(set(round(b['width_mm'],9) for b in bends if abs(b['target_angle_deg'])<80)),
        'volume_conservation_claimed':False}
    for path,h in protected.items():
        if sha(Path(path))!=h:raise ValueError('Source modified')
    write(out/'model.json',model);print(model['validation'],flush=True)
    return model


if __name__=='__main__':build()
