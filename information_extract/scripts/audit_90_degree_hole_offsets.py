"""P001 90-degree bend and hole audit. Writes a new diagnostic pack only."""
from pathlib import Path
import sys,json
from collections import Counter,defaultdict
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
MODULE=ROOT/'information_extract'
from information_extract.scripts.check_whole_panel_collision import read,write,sha
from information_extract.three_d.local_motion import poses
from information_extract.three_d.local_collision import indexed,valid_solid
from information_extract.three_d.material_removal import source_prism
from information_extract.three_d.hole_offset_audit import compare_translation,offset_family,leave_one_out,centroid
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_FACE,TopAbs_WIRE,TopAbs_FORWARD,TopAbs_EDGE
from OCP.TopoDS import TopoDS
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.gp import gp_Vec
from OCP.BRepAdaptor import BRepAdaptor_Curve


def main():
    cfg_path=MODULE/'configs/P001_whole_panel_collision_v01.json';cfg=read(cfg_path)
    stage_path=MODULE/cfg['stage_data'];asset_path=MODULE/cfg['asset']
    audit_path=MODULE/'outputs/staged_removal_v01/P001/mapping_audit.json'
    old_path=MODULE/'outputs/whole_panel_collision_v01/P001/report.json'
    stage,asset,audit,old=map(read,[stage_path,asset_path,audit_path,old_path])
    parameter_path=MODULE/cfg['material_config'];thickness=read(parameter_path)['stock_thickness_mm']
    step=MODULE/read(MODULE/cfg['cad_config'])['step']
    if sha(step)!=asset['asset_id'] or sha(step)!=stage['asset_id']:raise ValueError('STEP provenance mismatch')
    sources=[cfg_path,stage_path,asset_path,audit_path,old_path,parameter_path,step,MODULE/cfg['candidates'],MODULE/cfg['workbook']]
    sources+=list((ROOT/'src/constraints').rglob('*.py'))
    protected={str(p.resolve()):sha(p) for p in sources}
    if sha(MODULE/cfg['candidates'])!=stage['plans_sha256']:raise ValueError('Candidate source mismatch')
    sk=stage['skeleton'];flat=poses(sk,{j['joint_id']:0 for j in sk['joints']})
    regions={r['region_id']:r for r in sk['regions']};panels={r['region_id']:r for r in stage['panels']}
    joints={j['child_region_id']:j for j in sk['joints']}
    def path_for(rid):
        path=[]
        while rid in joints:
            j=joints[rid];path.insert(0,j);rid=j['parent_region_id']
        return path
    reader=STEPControl_Reader()
    if reader.ReadFile(str(step))!=IFSelect_RetDone or not reader.TransferRoots():raise ValueError('STEP failed')
    faces=indexed(reader.OneShape(),TopAbs_FACE)
    if faces.Extent()!=asset['face_count']:raise ValueError('Topology mismatch')
    evidence={e['region_id']:e for e in old['panel_evidence']}
    wire_shapes={};wire_rows=[]
    for w in audit['inner_wires']:
        face=TopoDS.Face_s(faces.FindKey(w['front_face_id']));wire=TopoDS.Wire_s(indexed(face,TopAbs_WIRE).FindKey(w['wire_index']))
        made=BRepBuilderAPI_MakeFace(TopoDS.Wire_s(wire.Oriented(TopAbs_FORWARD)),True)
        if not made.IsDone():raise ValueError('Wire face failed')
        body=valid_solid(BRepPrimAPI_MakePrism(made.Face(),gp_Vec(*evidence[w['region_id']]['extrusion_vector_mm'])).Shape())
        circle=None;emap=indexed(wire,TopAbs_EDGE);circles=[]
        for ei in range(1,emap.Extent()+1):
            curve=BRepAdaptor_Curve(TopoDS.Edge_s(emap.FindKey(ei)))
            if 'Circle' not in str(curve.GetType()):break
            c=curve.Circle();location=c.Location();circles.append({'center_xyz_mm':[location.X(),location.Y(),location.Z()],'radius_mm':c.Radius()})
        else:
            if circles and all(abs(c['radius_mm']-circles[0]['radius_mm'])<1e-6 and np.linalg.norm(np.array(c['center_xyz_mm'])-circles[0]['center_xyz_mm'])<1e-6 for c in circles):circle=circles[0]
        wire_shapes[w['wire_id']]=body;wire_rows.append({**w,'centroid_xyz_mm':centroid(body).tolist(),'circle':circle})
    hole_rows=[]
    for op in stage['operations']:
        if op['operation_type'] not in {'CIRCLE_PUNCH','IRREGULAR_PUNCH','PRE_PUNCH'}:continue
        oid=op['operation_id'];rid=op['region_id']
        row={k:op.get(k) for k in ['operation_id','operation_type','source_feature_ref','geometry_id','region_id']}
        if not rid:
            row.update(status='UNMAPPED_PANEL_NO_OFFSET_CLAIM',ninety_degree_path=None)
            hole_rows.append(row);continue
        path=path_for(rid);row.update(bend_path=[{'operation_id':sk['joint_to_operation'][j['joint_id']],'angle_deg':j['angle_deg']} for j in path],
            ninety_degree_path=bool(path) and all(abs(abs(j['angle_deg'])-90)<=1e-6 for j in path),
            has_ninety_degree_ancestor=any(abs(abs(j['angle_deg'])-90)<=1e-6 for j in path))
        try:
            source=source_prism(op,stage['display_outline'],panels[rid]['flat_z'],np.linalg.inv(flat[rid]),evidence[rid]['extrusion_vector_mm'])
            cs=centroid(source);fid=regions[rid]['front_face_id'];n=asset['faces'][fid-1]['plane']['normal'];candidates=[]
            for w in wire_rows:
                if w['region_id']!=rid:continue
                gap=float(np.linalg.norm(np.array(w['centroid_xyz_mm'])-cs))
                if gap>2.0:continue
                result=compare_translation(source,wire_shapes[w['wire_id']],n,flat[rid])
                candidates.append({'wire_id':w['wire_id'],'cad_circle':w['circle'],**result})
            candidates.sort(key=lambda c:(not c['same_shape_after_translation'],c['in_plane_delta_mm']))
            row['candidates']=candidates
            matching=[c for c in candidates if c['same_shape_after_translation']]
            if len(matching)==1:
                row.update(status='UNIQUE_TRANSLATION_CONGRUENT_PROPOSAL',**matching[0])
                row['offset_family']=offset_family(row['in_plane_delta_mm'],thickness)
            elif len(matching)>1:row['status']='AMBIGUOUS_TRANSLATION_MATCH'
            elif candidates:
                row.update(status='SHAPE_OR_FORMING_DIFFERENCE_NOT_PURE_TRANSLATION',nearest_candidate=candidates[0])
            else:row['status']='NO_SAME_PANEL_WIRE_WITHIN_2MM'
        except Exception as exc:row.update(status='UNKNOWN_GEOMETRY_OR_KERNEL',error=str(exc))
        hole_rows.append(row)
    # Audit mutual uniqueness rather than silently selecting two actions for one wire.
    used=Counter(r['wire_id'] for r in hole_rows if r['status']=='UNIQUE_TRANSLATION_CONGRUENT_PROPOSAL')
    for r in hole_rows:
        if r['status']=='UNIQUE_TRANSLATION_CONGRUENT_PROPOSAL' and used[r['wire_id']]!=1:r['status']='WIRE_ASSOCIATION_NOT_MUTUALLY_UNIQUE'
    by_panel=defaultdict(list)
    for r in hole_rows:
        if r['status']=='UNIQUE_TRANSLATION_CONGRUENT_PROPOSAL':by_panel[r['region_id']].append(r)
    panel_rows=[]
    for rid,region in regions.items():
        path=path_for(rid);holes=[r for r in hole_rows if r['region_id']==rid]
        panel_rows.append({'region_id':rid,'front_face_id':region['front_face_id'],
            'bend_path':[sk['joint_to_operation'][j['joint_id']] for j in path],
            'ninety_degree_path':bool(path) and all(abs(abs(j['angle_deg'])-90)<=1e-6 for j in path),
            'hole_ids':[r['operation_id'] for r in holes],
            'congruent_hole_ids':[r['operation_id'] for r in by_panel[rid]],
            'translation_fit':leave_one_out(by_panel[rid])})
    # Independent bend cross-section evidence: outer cylindrical surface and
    # the opposite skins' shared sharp edge, not hole-fitted translations.
    joint_rows=[]
    for j in sk['joints']:
        path=path_for(j['child_region_id']);evs=[]
        for ev in j['surface_evidence']:
            f=asset['faces'][ev['cylinder_face_id']-1];cy=f['cylinder'];axis=np.array(cy['direction']);origin=np.array(cy['point_mm'])
            ra,rb=j['parent_region_id'],j['child_region_id']
            opposite=[next(fid for fid in regions[r]['skin_face_ids'] if fid!=ev['contact_skin_faces'][r]) for r in [ra,rb]]
            shared=set(asset['faces'][opposite[0]-1]['edge_ids'])&set(asset['faces'][opposite[1]-1]['edge_ids'])
            sharp=[]
            for eid in shared:
                edge=asset['edges'][eid-1]
                if edge['curve_type']!='Line':continue
                error=max(float(np.linalg.norm(np.cross(np.array(p)-origin,axis))) for p in edge['points'])
                if error<=1e-6:sharp.append({'edge_id':eid,'axis_distance_mm':error})
            evs.append({'cylinder_face_id':f['face_id'],'cylinder_radius_mm':cy['radius_mm'],
                'cylinder_axis_point_mm':origin.tolist(),'opposite_skin_face_ids':opposite,
                'sharp_inner_shared_edge_evidence':sharp,
                'sharp_inner_and_radius_equals_thickness':bool(sharp) and abs(cy['radius_mm']-thickness)<=1e-6})
        descendants=[]
        for rid in regions:
            if any(k['joint_id']==j['joint_id'] for k in path_for(rid)):descendants.append(rid)
        joint_rows.append({'operation_id':sk['joint_to_operation'][j['joint_id']], 'joint_id':j['joint_id'],
            'angle_deg':j['angle_deg'],'is_90_degrees':abs(abs(j['angle_deg'])-90)<=1e-6,
            'parent_region_id':j['parent_region_id'],'child_region_id':j['child_region_id'],
            'midplane_intersection_axis_mm':j['hinge_point_mm'],'section_evidence':evs,
            'descendant_hole_ids':[r['operation_id'] for r in hole_rows if r.get('region_id') in descendants]})
    ordinary=[r for r in hole_rows if r['operation_type']!='PRE_PUNCH']
    pure=[r for r in ordinary if r.get('ninety_degree_path')]
    congruent=[r for r in pure if r['status']=='UNIQUE_TRANSLATION_CONGRUENT_PROPOSAL']
    ninety=[j for j in joint_rows if j['is_90_degrees']]
    summary={'all_bend_count':len(joint_rows),'ninety_degree_bend_count':len(ninety),
        'ordinary_punch_count':len(ordinary),'pre_punch_count':len(hole_rows)-len(ordinary),
        'ordinary_punches_on_all_90_paths':len(pure),'ordinary_90_path_status_counts':dict(Counter(r['status'] for r in pure)),
        'ordinary_90_path_offset_families':dict(Counter(r['offset_family'] for r in congruent)),
        'all_ordinary_status_counts':dict(Counter(r['status'] for r in ordinary)),
        'ninety_degree_joints_with_descendant_punch_geometry':sum(bool(j['descendant_hole_ids']) for j in ninety),
        'ninety_degree_joints_without_descendant_punch_geometry':sum(not j['descendant_hole_ids'] for j in ninety),
        'ninety_degree_joints_with_sharp_inner_Router_t_evidence':sum(all(e['sharp_inner_and_radius_equals_thickness'] for e in j['section_evidence']) for j in ninety),
        'unknown_kernel_rows':sum(r['status']=='UNKNOWN_GEOMETRY_OR_KERNEL' for r in hole_rows),
        'translated_matches_are_production_associations':False,'candidate_filter_enabled':False,
        'all_holes_proven_04_or_08':False,'manufacturing_feasibility':'UNKNOWN'}
    out=MODULE/'outputs/hole_offset_90_audit_v01/P001';out.mkdir(parents=True,exist_ok=True)
    result={'summary':summary,'parameters':{'angle_tolerance_deg':1e-6,'shape_volume_tolerance_mm3':1e-5,
        'offset_family_tolerance_mm':1e-5,'center_search_radius_mm':2.0,
        'search_scope':'same proposed panel only; no rotation or rescaling; PRE_PUNCH radius difference is not collapsed'},
        'source_sha256':protected,'hole_rows':hole_rows,'panel_rows':panel_rows,'joint_rows':joint_rows}
    for p,h in protected.items():
        if sha(Path(p))!=h:raise ValueError('Protected source changed')
    result['inputs_unchanged']=True
    write(out/'audit.json',result)
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    for r in hole_rows:
        if r['status']=='UNIQUE_TRANSLATION_CONGRUENT_PROPOSAL':print(r['operation_id'],r['operation_type'],r['region_id'],r.get('ninety_degree_path'),r['offset_family'],np.round(r['flat_delta_xyz_mm'],6).tolist())


if __name__=='__main__':main()
