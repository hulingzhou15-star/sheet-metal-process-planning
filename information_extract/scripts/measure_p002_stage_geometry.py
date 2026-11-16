"""Compare actual STEP edge samples with DXF stage projections and O094 carrier."""
from pathlib import Path
import numpy as np
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Curve
from information_extract.three_d.assets import indexed,xyz
from information_extract.three_d.artifacts import ROOT,read,write,sha,verify_sources
from information_extract.three_d.p002_stage_evidence import distances,registered_geometry
from information_extract.three_d.state import rotation_about_axis

M=ROOT/'information_extract';OUT=M/'outputs/p002_stage_evidence_v01'
TARGETS={'O069':(8,'C005'),'O088':(9,'C004'),'O094':(6,'C007'),'O096':(9,'C003'),'O104':(7,'C002')}


def transform(points,m):
    return np.array(points)@m[:3,:3].T+m[:3,3]


def run():
    from information_extract.three_d.local_forming import load_shape
    ev=read(M/'outputs/p002_expert_dxf_v01/evidence.json');reg=read(M/'outputs/multi_part_step_v01/P002/base_registration.json')
    asset=read(M/'outputs/multi_part_step_v01/P002/asset.json');project=read(M/'projects/P002/project.json')
    cells=read(M/'outputs/p002_material_cells_v01/cells.json');straight=read(M/'outputs/p002_generation_v01/straight_branch_proposals.json')
    sides=read(M/'outputs/p002_generation_v01/curved_side_evidence.json');notes=read(OUT/'annotations.json')
    files=[M/'sources/P002/originals/P002.dxf',M/'sources/P002/originals/P002.stp',M/'sources/P002/P002_1.dxf',M/'projects/P002/project.json',
           M/'outputs/p002_expert_dxf_v01/evidence.json',M/'outputs/multi_part_step_v01/P002/base_registration.json',
           M/'outputs/multi_part_step_v01/P002/asset.json',M/'outputs/p002_material_cells_v01/cells.json',
           M/'outputs/p002_generation_v01/straight_branch_proposals.json',M/'outputs/p002_generation_v01/curved_side_evidence.json',
           OUT/'annotations.json',Path(__file__),M/'three_d/p002_stage_evidence.py',M/'scripts/probe_p002_stage_evidence.py']
    files += [M/'outputs/p002_material_cells_v01'/c['brep_file'] for c in cells['cells']]
    hashes={f.relative_to(ROOT).as_posix():sha(f) for f in files}
    if ev['source_sha256']!=sha(files[0]) or ev['flat_source_sha256']!=sha(files[2]) or asset['asset_id']!=sha(files[1]):raise ValueError('Stale sources')
    current={o['operation_id']:o for o in project['operations']};old={o['operation_id']:o for o in ev['operations']}
    for oid in TARGETS:
        if current[oid]['source_segment_ids']!=old[oid]['source_segment_ids']:raise ValueError('Changed target bindings')
    reader=STEPControl_Reader()
    if reader.ReadFile(str(files[1]))!=IFSelect_RetDone:raise ValueError('STEP read failed')
    reader.SetSystemLengthUnit(1.);reader.TransferRoots();edges=indexed(reader.OneShape(),TopAbs_EDGE)
    frames={int(f['text'].split('：')[1].split('/')[0]):f for i,f in enumerate(ev['frames']) if i not in (5,11)}
    geom={s:registered_geometry(f,reg) for s,f in frames.items()}
    cell_points={};cell_edge_points={}
    for cid in {v[1] for v in TARGETS.values()}:
        c=next(c for c in cells['cells'] if c['cell_id']==cid);es=indexed(load_shape(M/'outputs/p002_material_cells_v01'/c['brep_file']),TopAbs_EDGE)
        curves=[]
        for k in range(1,es.Extent()+1):
            a=BRepAdaptor_Curve(TopoDS.Edge_s(es.FindKey(k)))
            curves.append(np.array([xyz(a.Value(float(u))) for u in np.linspace(a.FirstParameter(),a.LastParameter(),17)]))
        cell_edge_points[cid]=curves;cell_points[cid]=np.vstack(curves)
    results=[]
    for oid,(station,cid) in TARGETS.items():
        p=cell_points[cid];row=dict(operation_id=oid,cell_id=cid,expert_station_for_validation_only=f'OP{station*10}',
            source_segment_ids=current[oid]['source_segment_ids'],bindings_unchanged_from_revision15=True,step_bounds_mm=[*p.min(axis=0),*p.max(axis=0)],projections=[])
        for s in (station-1,station,min(station+1,10)):
            d=distances(p[:,:2],geom[s]);row['projections'].append(dict(station=f'OP{s*10}',projection='XY',sample_count=len(d),
                within_0_001_mm=int(sum(d<=.001)),within_0_08_mm=int(sum(d<=.08)),max_residual_mm=float(d.max()),median_residual_mm=float(np.median(d)),
                interpretation='One-way projected edge proximity; occlusion/overlapping lines prevent treating this as a complete stage-shape proof'))
        if oid!='O094':
            side=next(r for r in sides['rows'] if r['operation_id']==oid)
            row.update(base_boundary_matches=side['matches'],surface_side=side['surface_side'],attached_wall_face_ids=side['adjacent_step_face_ids'])
            handle,sign={'O069':('1107B',-1),'O088':('11243',1),'O096':('11243',1),'O104':('10EC7',-1)}[oid]
            thickness=next(r for r in notes['rows'] if r['source_path']==[handle])
            ys=[thickness['points'][k][1] for k in ('defpoint2','defpoint3')]
            zero=max(ys) if sign==1 else min(ys)
            shift_x=frames[station]['translation'][0]-reg['translation_mm'][0]
            projection=np.column_stack([p[:,0]+shift_x,zero+sign*p[:,2]])
            d=distances(projection,frames[station]['targets'])
            extreme=p[:,2].max() if side['base_protrusion_sign']==1 else p[:,2].min()
            rim=np.abs(p[:,2]-extreme)<1e-6
            row['side_projection']=dict(projection='XZ',stage=f'OP{station*10}',z_sign=sign,z_zero_dxf_y=zero,
                x_translation=shift_x,base_thickness_dimension=thickness,
                sample_count=len(d),within_0_001_mm=int(sum(d<=.001)),max_residual_mm=float(d.max()),
                farthest_wall_height_z_mm=float(extreme),farthest_wall_samples=int(sum(rim)),
                farthest_wall_max_residual_mm=float(d[rim].max()),farthest_wall_matched_samples=int(sum(d[rim]<=.001)),
                interpretation='Projection support only; overlapping/hidden contours do not determine intermediate deformation')
        results.append(row)
    joints={j['joint_id']:j for j in straight['skeleton']['joints']};parent=joints['J540'];child=joints['J364']
    carrier=rotation_about_axis(parent['hinge_point_mm'],parent['axis_direction'],-90.)
    # Independent DXF endpoint alignment, measured before interpreting the correction.
    sources=[g for g in ev['flat_primitives'] if g['source_id'] in current['O094']['source_segment_ids']]
    srcpts=np.array([p for g in sources for p in g['points']])@np.array(reg['matrix']).T+np.array(reg['translation_mm'])
    proposed=np.array(child['developed_axis_endpoints_mm'])
    shift=float(srcpts[:,0].mean()-proposed[:,0].mean())
    if abs(shift-.4)>1e-6:raise ValueError('O094 measured carrier compensation changed')
    carrier[0,3]+=shift
    developed=transform(cell_points['C007'],carrier)
    local_unbend=rotation_about_axis(child['hinge_point_mm'],child['axis_direction'],-90.)
    initial=transform(cell_points['C007'],carrier@local_unbend)
    # Use only the free rim, not the artificial cut through the omitted bend band.
    free_curves=[p for p in cell_edge_points['C007'] if np.max(p[:,0])>cell_points['C007'][:,0].min()+1e-5 and np.ptp(p[:,1])<1e-6]
    free=np.vstack(free_curves)
    tip=initial[np.abs(initial[:,1]-initial[:,1].min())<1e-6]
    tip_span=float(np.ptp(tip[:,0]));tip_x=float((tip[:,0].max()+tip[:,0].min())/2)
    tip_matches=[]
    for g in geom[5]:
        q=np.array(g['points'])
        if g['kind']=='LINE' and np.ptp(q[:,1])<1e-6 and abs(np.ptp(q[:,0])-tip_span)<1e-6 and abs(q[:,0].mean()-tip_x)<1e-6 and abs(q[:,1].mean()-tip[:,1].mean())<1.:
            tip_matches.append(g)
    if len(tip_matches)!=1:raise ValueError('Ambiguous pre-fold free rim evidence')
    y_shift=float(np.array(tip_matches[0]['points'])[:,1].mean()-tip[:,1].mean())
    flat_correction=np.array([0.,y_shift,0.])
    initial+=flat_correction
    c094=dict(operation_id='O094',cell_id='C007',parent_operation_id='O088',parent_region_id='P425_465',
        carrier_joint=parent,local_joint=child,unformed_parent_transform=carrier.tolist(),
        measured_x_compensation_mm=shift,calibration_source_ids=current['O094']['source_segment_ids'],
        calibration_mean_x_residual_mm=float(abs(transform(proposed,np.eye(4))[:,0].mean()+shift-srcpts[:,0].mean())),
        folded_before_parent_bounds_mm=[*developed.min(axis=0),*developed.max(axis=0)],
        unformed_bounds_mm=[*initial.min(axis=0),*initial.max(axis=0)],
        evidence_scope='Local rigid-wall reference while O088 has not started; O088 transition remains UNKNOWN',
        initial_world_translation_correction_mm=flat_correction.tolist(),
        flat_rim_calibration_entity=tip_matches[0],
        trajectory_assumption='O094 rigid hinge with linear angle and linear endpoint compensation; not a measured continuous deformation',stage_comparisons=[])
    for s in (5,6,7,8,9):
        pp=initial if s==5 else developed;d=distances(pp[:,:2],geom[s])
        c094['stage_comparisons'].append(dict(station=f'OP{s*10}',sample_count=len(d),within_0_001_mm=int(sum(d<=.001)),max_residual_mm=float(d.max()),median_residual_mm=float(np.median(d))))
    c094['free_rim_validation']=[]
    for s in (5,7,8):
        pp=transform(free,carrier@local_unbend)+flat_correction if s==5 else transform(free,carrier)
        d=distances(pp[:,:2],geom[s])
        c094['free_rim_validation'].append(dict(station=f'OP{s*10}',sample_count=len(d),max_residual_mm=float(d.max()),
            within_0_001_mm=int(sum(d<=.001)),excluded='Artificial bend-band cuts and thickness cross edges which project inside the rim'))
    dim=next(r for r in notes['rows'] if r['source_path']==['10E59'])
    c094['independent_dimension']=dict(source=dim,height_after_local_fold_mm=float(np.ptp(developed[:,2])),residual_mm=abs(float(np.ptp(developed[:,2]))-dim['measurement']))
    if max(r['max_residual_mm'] for r in c094['free_rim_validation'])>1e-6 or c094['independent_dimension']['residual_mm']>1e-6:raise ValueError('O094 stage endpoint validation failed')
    # A short carrier reference does NOT certify the connected curved wall.
    report=dict(part_id='P002',annotation_revision=project['revision'],source_evidence_revision=ev['annotation_revision'],operations=results,o094_carrier=c094,
        angular_text_findings=[r for r in notes['rows'] if r['kind'] in ('TEXT','MTEXT') and ('80%%d' in r['text'] or r['text']=='90%%d')],
        inputs=hashes,expert_schedule_used_by_planner=False,full_curve_deformation_implemented=False,manufacturing_safety_proved=False)
    verify_sources(hashes);write(OUT/'stage_geometry.json',report)
    render(report,geom,cell_edge_points,carrier,local_unbend)
    print(json_summary(report),flush=True)
    return report


def json_summary(report):
    return dict(curves=[(r['operation_id'],r['projections']) for r in report['operations']],O094=report['o094_carrier'])


def render(report,geom,curves,carrier,local):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    # Render source entities densely enough to show ARC, with the actual STEP edge traces.
    from matplotlib.patches import Circle
    fig,axs=plt.subplots(1,3,figsize=(13,5))
    for ax,s,m in zip(axs,[5,7,10],[carrier@local,carrier,np.eye(4)]):
        for g in geom[s]:
            q=np.array(g['points'])
            if g['kind']=='LINE':ax.plot(*q.T,color='.65',lw=.8)
            elif g['kind']=='CIRCLE':ax.add_patch(Circle(q[0],g['radius'],fill=False,color='.7',lw=.6))
        for p in curves['C007']:
            q=transform(p,m)
            if s==5:q+=np.array(report['o094_carrier']['initial_world_translation_correction_mm'])
            ax.plot(*q[:,:2].T,color='#e85d04',lw=1.2)
        ax.set(xlim=(-114,-87),ylim=(-101,-76),title=f'O094 / OP{s*10} projection',xlabel='STEP X / mm',ylabel='STEP Y / mm');ax.set_aspect('equal')
    fig.suptitle('Gray: DXF. Orange: STEP local rigid reference. Finite bend bands excluded.')
    fig.tight_layout();fig.savefig(OUT/'o094_stage_projection.png',dpi=160);plt.close(fig)


if __name__=='__main__':run()
