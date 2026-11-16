"""Independent later-view endpoint check, separate from motion calibration inputs."""
from pathlib import Path
import numpy as np
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Curve
from information_extract.three_d.artifacts import ROOT,read,write,sha,verify_sources
from information_extract.three_d.local_forming import load_shape
from information_extract.three_d.assets import indexed,xyz
from information_extract.three_d.p002_stage_evidence import distances

M=ROOT/'information_extract';OUT=M/'outputs/p002_stage_evidence_v01'


def main():
    ev=read(M/'outputs/p002_expert_dxf_v01/evidence.json');reg=read(M/'outputs/multi_part_step_v01/P002/base_registration.json')
    original=read(OUT/'stage_geometry.json');verify_sources(original['inputs'])
    asset=read(M/'outputs/multi_part_step_v01/P002/asset.json')
    band_faces={r['source_face_id'] for r in read(M/'outputs/p002_material_cells_v01/cells.json')['bands']}
    notes=read(OUT/'annotations.json')['rows'];f=ev['frames'][9]
    dim=next(r for r in notes if r['source_path']==['11243']);zero=max(dim['points'][k][1] for k in ('defpoint2','defpoint3'))
    rows=[]
    for oid,cid in [('O069','C005'),('O088','C004'),('O096','C003'),('O104','C002')]:
        op=next(r for r in original['operations'] if r['operation_id']==oid)
        es=indexed(load_shape(M/'outputs/p002_material_cells_v01/brep'/f'{cid}.brep'),TopAbs_EDGE);pts=[]
        for k in range(1,es.Extent()+1):
            a=BRepAdaptor_Curve(TopoDS.Edge_s(es.FindKey(k)))
            pts.extend(xyz(a.Value(float(u))) for u in np.linspace(a.FirstParameter(),a.LastParameter(),17))
        p=np.array(pts);q=np.column_stack([p[:,0]+f['translation'][0]-reg['translation_mm'][0],zero+p[:,2]])
        ds=distances(q,f['targets']);z=p[:,2].max() if op['surface_side']=='SMOOTH' else p[:,2].min();rim=abs(p[:,2]-z)<1e-6
        walls=[]
        for fid in op['attached_wall_face_ids']:
            face=asset['faces'][fid-1]
            if fid in band_faces:
                walls.append(dict(face_id=fid,kind='FiniteRootBand',surface_type=face['surface_type'],basis='Previously separated finite bend band; not a vertical corner wall'))
            elif face['surface_type']=='Plane':
                normal=face['plane']['normal'];angle=float(np.degrees(np.arccos(np.clip(abs(normal[2]),0,1))))
                if abs(angle-90)>1e-6:raise ValueError('Non-perpendicular wall')
                walls.append(dict(face_id=fid,kind='Plane',base_angle_deg=angle))
            elif face['surface_type']=='Cylinder':
                alignment=abs(face['cylinder']['direction'][2])
                if abs(alignment-1)<=1e-6:
                    walls.append(dict(face_id=fid,kind='Cylinder',axis_parallel_to_base_normal=True))
                elif fid in band_faces:
                    walls.append(dict(face_id=fid,kind='RootBendCylinder',basis='Previously separated finite bend band; not a vertical corner wall'))
                else:raise ValueError('Unclassified adjacent cylinder')
            else:raise ValueError('Unexpected root wall surface')
        if not np.all(ds[rim]<.001):raise ValueError('Later view does not support farthest rim')
        rows.append(dict(operation_id=oid,cell_id=cid,validation_view='view_10 / OP90',projection='XZ',
            side_view_thickness_dimension_handle='11243',z_zero_dxf_y=zero,x_translation=f['translation'][0]-reg['translation_mm'][0],
            z_extreme_mm=float(z),farthest_rim_samples=int(sum(rim)),farthest_rim_matched_samples=int(sum(ds[rim]<.001)),
            farthest_rim_max_residual_mm=float(ds[rim].max()),all_samples=len(ds),all_samples_matched=int(sum(ds<.001)),
            all_samples_max_residual_mm=float(ds.max()),step_root_wall_geometry=walls,
            interpretation='Later side view supports completed height and STEP root walls are perpendicular; earlier occluded/unshown side contours and continuous deformation are not reconstructed'))
    write(OUT/'later_side_validation.json',dict(rows=rows,stage_evidence_sha256=sha(OUT/'stage_geometry.json'),
        implementation_sha256=sha(Path(__file__)),continuous_curve_motion_verified=False,expert_schedule_used_by_planner=False))
    print([(r['operation_id'],r['farthest_rim_samples'],r['farthest_rim_max_residual_mm']) for r in rows])


if __name__=='__main__':main()
