from information_extract.three_d.artifacts import relative
"""Extract flange dimensions from original STEP faces in each panel frame."""
from information_extract.scripts.build_finite_material_states import *
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
import csv, math

def main():
    base=MODULE/'outputs/finite_material_v02/P001';out=MODULE/'outputs/local_forming_v03/P001';out.mkdir(parents=True,exist_ok=True)
    paths=[base/'model.json',base/'feature_highlights.json',MODULE/'outputs/three_d_state_v01/P001/asset.json',MODULE/'outputs/stage_review_v01/P001/stage_data.json']
    model,highlights,asset,stage=map(read,paths);cfg=read(MODULE/'configs/three_d_state_v01.json');step=MODULE/cfg['step']
    assert sha(step)==cfg['expected_step_sha256']
    reader=STEPControl_Reader();assert reader.ReadFile(str(step))==IFSelect_RetDone;assert reader.TransferRoots()
    faces=indexed(reader.OneShape(),TopAbs_FACE);assert faces.Extent()==asset['face_count']
    rows=[]
    for op in highlights['operations']:
        if op['operation_type']!='FLANGING':continue
        oid=op['operation_id'];row={'operation_id':oid,'status':'UNRESOLVED','integrated_into_stage_solids':False};rows.append(row)
        owners={p['component']['region_id'] for p in op['parts'] if p['component']['kind']=='panel'}
        if len(owners)!=1:row['reason']='Non-unique panel';continue
        rid=next(iter(owners));r=next(r for r in model['regions'] if r['region_id']==rid);T=np.linalg.inv(r['flat_from_cad'])
        ps=np.array([p for path in op['flat_paths'] for p in path]);xy=(ps.min(axis=0)+ps.max(axis=0))/2
        center=(T@np.r_[xy,0,1])[:3];normal=T[:3,2];candidates=[]
        for f in asset['faces']:
            cy=f.get('cylinder')
            if not cy:continue
            d=np.array(cy['direction']);delta=center-np.array(cy['point_mm']);error=float(np.linalg.norm(delta-(delta@d)*d))
            if abs(d@normal)<.999999 or error>.03:continue
            face=TopoDS.Face_s(faces.FindKey(f['face_id']));surface=BRepAdaptor_Surface(face,True)
            prop=GProp_GProps();BRepGProp.SurfaceProperties_s(face,prop);com=prop.CentreOfMass()
            assert np.linalg.norm(np.array([com.X(),com.Y(),com.Z()])-f['centroid_mm'])<1e-6
            ends=[surface.Value(surface.FirstUParameter(),v) for v in [surface.FirstVParameter(),surface.LastVParameter()]]
            levels=sorted(float((np.array([p.X(),p.Y(),p.Z()])-center)@normal) for p in ends)
            if min(abs(x) for x in levels)>10:continue
            length=levels[1]-levels[0];ratio=prop.Mass()/(2*math.pi*cy['radius_mm']*length)
            candidates.append({'face_id':f['face_id'],'radius_mm':cy['radius_mm'],'signed_axial_interval_mm':levels,'straight_wall_length_mm':length,'axis_center_error_mm':error,'full_cylinder_area_ratio':ratio})
        row.update(region_id=rid,front_skin_face_id=r['front_face_id'],center_cad_mm=center.tolist(),front_normal=normal.tolist(),cylinder_candidates=candidates)
        if len(candidates)!=2:row['reason']='Non-unique coaxial inner/outer wall pair';continue
        inner,outer=sorted(candidates,key=lambda c:c['radius_mm'])
        if any(abs(c['full_cylinder_area_ratio']-1)>1e-6 for c in candidates):row['reason']='Partial cylindrical wall';continue
        shared=set(asset['faces'][inner['face_id']-1]['neighbor_face_ids'])&set(asset['faces'][outer['face_id']-1]['neighbor_face_ids'])
        rims=[asset['faces'][i-1] for i in shared if asset['faces'][i-1]['surface_type']=='Plane' and abs(np.dot(asset['faces'][i-1]['plane']['normal'],normal))>.999999]
        if len(rims)!=1:row['reason']='Non-unique annular end face';continue
        rim=rims[0];level=float((np.array(rim['plane']['point_mm'])-center)@normal);t=model['thickness_mm']
        if not (level>1e-6 or level<-t-1e-6):row['reason']='Rim does not project beyond a sheet skin';continue
        sign=1 if level>0 else -1;protrusion=level if sign>0 else -level-t
        assert all(min(abs(level-x) for x in c['signed_axial_interval_mm'])<1e-6 for c in candidates)
        blends=[]
        for fid in asset['faces'][inner['face_id']-1]['neighbor_face_ids']:
            if asset['faces'][fid-1]['surface_type']=='Torus':
                tor=BRepAdaptor_Surface(TopoDS.Face_s(faces.FindKey(fid)),True).Torus()
                blends.append({'face_id':fid,'major_radius_mm':tor.MajorRadius(),'minor_radius_mm':tor.MinorRadius()})
        if len(blends)!=1:row['reason']='Root blend not uniquely located';continue
        source=next(o for o in stage['operations'] if o['operation_id']==oid)
        prep=[o['operation_id'] for o in stage['operations'] if o['operation_type']=='PRE_PUNCH' and o['process_group_id']==source['process_group_id']]
        assert len(prep)==1
        row.update(status='STEP_GEOMETRY_EXTRACTED',pre_punch_operation_id=prep[0],surface_side=source['surface_side'],inner_radius_mm=inner['radius_mm'],outer_radius_mm=outer['radius_mm'],rim_face_id=rim['face_id'],root_blends=blends,rim_from_front_skin_mm=level,geometric_direction_sign=sign,protrusion_beyond_exit_skin_mm=protrusion,total_plate_and_lip_height_mm=t+protrusion,inner_straight_wall_length_mm=inner['straight_wall_length_mm'],outer_straight_wall_length_mm=outer['straight_wall_length_mm'])
    assert len(rows)==46
    used=[c['face_id'] for r in rows if r['status']=='STEP_GEOMETRY_EXTRACTED' for c in r['cylinder_candidates']];assert len(set(used))==len(used)
    result={'version':'STEP_FLANGING_INVENTORY_V01','source_sha256':{relative(p):sha(p) for p in paths+[step]},'height_definition':'Front skin local z=0, opposite skin z=-thickness. Protrusion measured beyond the exit skin, not world Z or inner straight-wall length. Geometric direction does not override surface_side.','summary':dict(Counter(r['status'] for r in rows)),'operations':rows}
    write(out/'flanging_step_inventory.json',result)
    fields=['operation_id','pre_punch_operation_id','status','region_id','surface_side','inner_radius_mm','outer_radius_mm','rim_from_front_skin_mm','protrusion_beyond_exit_skin_mm','total_plate_and_lip_height_mm','inner_straight_wall_length_mm','outer_straight_wall_length_mm','rim_face_id']
    with (out/'flanging_step_inventory.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    for path,digest in model['source_sha256'].items():assert sha(Path(path))==digest,path
    print(result['summary']);print('protrusion_mm',sorted({round(r['protrusion_beyond_exit_skin_mm'],7) for r in rows if r['status']=='STEP_GEOMETRY_EXTRACTED'}))

if __name__=='__main__':main()
