"""Partition the completed STEP at actual cylindrical/toroidal bend bands.

Remaining connected solids retain attached draw/dimple/flange endpoints exactly.
This compiles carried-volume evidence, not an unfolding or motion simulation.
"""
import math
from itertools import permutations
from pathlib import Path
import numpy as np
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_FACE,TopAbs_SOLID
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder,BRepPrimAPI_MakeTorus
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Ax2,gp_Pnt,gp_Dir
from information_extract.three_d.artifacts import ROOT,read,write,sha
from information_extract.three_d.assets import indexed,xyz,bounds
from information_extract.three_d.local_forming import subtract,intersect,save_shape

MODULE=ROOT/'information_extract';PREP=MODULE/'outputs/p002_generation_v01';OUT=MODULE/'outputs/p002_material_cells_v01'


def volume(shape):
    props=GProp_GProps()
    BRepGProp.VolumeProperties_s(shape,props,1e-10)
    return abs(float(props.Mass()))


def axes(position,u,offset=0.):
    d=np.array(xyz(position.Direction()));x=np.array(xyz(position.XDirection()));y=np.array(xyz(position.YDirection()))
    origin=np.array(xyz(position.Location()))+d*offset;x=math.cos(u)*x+math.sin(u)*y
    return gp_Ax2(gp_Pnt(*map(float,origin)),gp_Dir(*map(float,d)),gp_Dir(*map(float,x)))


def correspondence_audit():
    """Unique geometry identities, separate from quantitative unfolding accuracy."""
    straight=read(PREP/'straight_branch_proposals.json');paths=read(PREP/'geometry_paths.json')
    cfg=read(MODULE/'configs/material_state_v01.json')['local_association']
    reg=read(MODULE/'outputs/multi_part_step_v01/P002/base_registration.json')
    assoc=straight['association']
    if assoc['all_candidate_count']!=12 or len(assoc['matches'])!=12 or assoc['unmatched_bend_operation_ids']:
        raise ValueError('Straight correspondence no longer uniquely covers all 12 actions')
    refs=[]
    for p in paths['O136']['paths']:
        if len(p['segments'])!=1:raise ValueError('Invalid Z component reference')
        s=p['segments'][0]
        points=np.array([np.array(reg['matrix'])@np.array([s[e+'_x'],s[e+'_y']])+reg['translation_mm'] for e in ('start','end')])
        refs.append((p['component_operation_id'],points))
    joints=[j for j in straight['skeleton']['joints'] if j['joint_id'] in ('J531','J64')]
    candidates=[]
    for order in permutations(joints):
        rows=[]
        for (oid,points),j in zip(refs,order):
            ends=np.array(j['developed_axis_endpoints_mm'])[:,:2]
            error=min(np.max(np.linalg.norm(points-ends,axis=1)),np.max(np.linalg.norm(points-ends[::-1],axis=1)))
            if error>cfg['axis_offset_search_mm'] or abs(np.linalg.norm(points[1]-points[0])-np.linalg.norm(ends[1]-ends[0]))>cfg['length_tolerance_mm']:break
            rows.append(dict(component_operation_id=oid,joint_id=j['joint_id'],endpoint_residual_mm=float(error)))
        if len(rows)==2:candidates.append(rows)
    if len(candidates)!=1:raise ValueError('Z two-reference correspondence is ambiguous')
    return dict(straight=assoc['matches'],z_composite=candidates[0],
        method='Unique geometric matching of length, direction and position; no H7 or expert station used',
        position_search_tolerance_mm=cfg['axis_offset_search_mm'],
        quantitative_unfolding_tolerance_mm=cfg['quantitative_match_tolerance_mm'],
        quantitative_unfolding_confirmed=False,
        scope='Action/material identity for carried-volume preference only; finite bend compensation and trajectories not calibrated')


def build():
    inputs={p.relative_to(ROOT).as_posix():sha(p) for p in [Path(__file__),
        PREP/'material_proposals.json',PREP/'carried_region_proposals.json',PREP/'curved_side_evidence.json',
        PREP/'straight_branch_proposals.json',PREP/'geometry_paths.json',MODULE/'configs/material_state_v01.json',
        MODULE/'outputs/multi_part_step_v01/P002/base_registration.json',
        MODULE/'outputs/multi_part_step_v01/P002/asset.json',MODULE/'sources/P002/originals/P002.stp']}
    correspondence=correspondence_audit()
    source=MODULE/'sources/P002/originals/P002.stp';asset=read(MODULE/'outputs/multi_part_step_v01/P002/asset.json')
    if sha(source)!=asset['asset_id']:raise ValueError('STEP changed')
    r=STEPControl_Reader()
    if r.ReadFile(str(source))!=IFSelect_RetDone:raise ValueError('STEP read failure')
    r.SetSystemLengthUnit(1.);r.TransferRoots();cad=r.OneShape();faces=indexed(cad,TopAbs_FACE)
    material=read(PREP/'material_proposals.json');proposals=read(PREP/'carried_region_proposals.json')['rows'];sides=read(PREP/'curved_side_evidence.json')
    thickness=material['thickness_estimate']['value_mm'];joints={j['joint_id']:j for j in material['joints']}
    bands=[];records=[]
    used=set(j for p in proposals for j in p['joint_ids'])
    cylinder_sources={max(joints[j]['surface_evidence'],key=lambda e:e['outer_or_inner_radius_mm'])['cylinder_face_id']:j for j in used}
    # Short tangent strips can end at a curved skin rather than a paired
    # planar region, so the planar-joint proposal list alone is incomplete.
    for row in sides['rows']:
        for fid in row['adjacent_step_face_ids']:
            a=BRepAdaptor_Surface(TopoDS.Face_s(faces.FindKey(fid)),True)
            if str(a.GetType()).split('.')[-1]=='GeomAbs_Cylinder' and abs(a.Cylinder().Radius()-thickness)<1e-6:
                cylinder_sources.setdefault(fid,None)
    for fid,jid in sorted(cylinder_sources.items()):
        a=BRepAdaptor_Surface(TopoDS.Face_s(faces.FindKey(fid)),True);cy=a.Cylinder()
        outer=cy.Radius();inner=max(0.,outer-thickness)
        u0,u1,v0,v1=a.FirstUParameter(),a.LastUParameter(),a.FirstVParameter(),a.LastVParameter()
        ax=axes(cy.Position(),u0,v0)
        outer_shape=BRepPrimAPI_MakeCylinder(ax,outer,v1-v0,u1-u0).Shape()
        shell=subtract(outer_shape,[BRepPrimAPI_MakeCylinder(ax,inner,v1-v0,u1-u0).Shape()]) if inner>1e-7 else outer_shape
        band=intersect(cad,shell)
        if volume(band)<=1e-8:raise ValueError('Empty cylindrical band '+str(fid))
        bands.append(band);records.append(dict(kind='CYLINDER',joint_id=jid,source_face_id=fid,inner_radius=inner,outer_radius=outer,volume_mm3=volume(band)))
        print('BAND',records[-1],flush=True)
    torus_ids=set()
    for row in sides['rows']:
        for fid in row['adjacent_step_face_ids']:
            a=BRepAdaptor_Surface(TopoDS.Face_s(faces.FindKey(fid)),True)
            if str(a.GetType()).split('.')[-1]!='GeomAbs_Torus':continue
            if fid in torus_ids:continue
            torus_ids.add(fid);t=a.Torus()
            if abs(t.MinorRadius()-thickness)>1e-6:raise ValueError('Unsupported torus paired thickness')
            u0,u1,v0,v1=a.FirstUParameter(),a.LastUParameter(),a.FirstVParameter(),a.LastVParameter()
            # Canonicalize periodic quarter-circle endpoints; STEP seam noise
            # at pi otherwise creates degenerate OCCT parameter edges.
            if any(abs(v-round(v/(math.pi/2))*(math.pi/2))>1e-7 for v in (v0,v1)) or abs(v1-v0-math.pi/2)>1e-7:
                raise ValueError('Only exact quarter-section toroidal bands supported')
            v0=round(v0/(math.pi/2))*(math.pi/2);v1=round(v1/(math.pi/2))*(math.pi/2)
            if v0>=math.pi:v0-=2*math.pi;v1-=2*math.pi
            ax=axes(t.Position(),u0)
            tool=BRepPrimAPI_MakeTorus(ax,t.MajorRadius(),t.MinorRadius(),u1-u0).Shape()
            middle=(v0+v1)/2;minor=t.MinorRadius();major=t.MajorRadius()
            axial=axes(t.Position(),u0,-minor if math.sin(middle)<0 else 0.)
            half=BRepPrimAPI_MakeCylinder(axial,major+minor+1.,minor).Shape()
            tool=intersect(tool,half)
            radial=BRepPrimAPI_MakeCylinder(axes(t.Position(),u0,-minor),major,2*minor).Shape()
            tool=intersect(tool,radial) if math.cos(middle)<0 else subtract(tool,[radial])
            print('TORUS TOOL',fid,v0,v1,flush=True)
            band=intersect(cad,tool)
            if volume(band)<=1e-8:raise ValueError('Empty torus band')
            bands.append(band);records.append(dict(kind='TORUS',source_face_id=fid,volume_mm3=volume(band)))
            print('BAND',records[-1],flush=True)
    print('CUT',len(bands),'bands',flush=True)
    remaining=subtract(cad,bands);solids=indexed(remaining,TopAbs_SOLID);rows=[]
    for i in range(1,solids.Extent()+1):
        solid=solids.FindKey(i);ids=[]
        for reg in material['regions']:
            areas=[]
            for fid in reg['skin_face_ids']:
                props=GProp_GProps();BRepGProp.SurfaceProperties_s(intersect(solid,faces.FindKey(fid)),props);areas.append(props.Mass())
            if min(areas)>1e-6:ids.append(reg['region_id'])
        row=dict(cell_id=f'C{i:03d}',region_ids=ids,volume_mm3=volume(solid),bounds_mm=bounds(solid),brep_file=f'brep/C{i:03d}.brep')
        save_shape(OUT/row['brep_file'],solid);rows.append(row);print('CELL',row['cell_id'],ids,row['volume_mm3'],flush=True)
    domains=[]
    for p in proposals:
        cells=[c for c in rows if set(c['region_ids'])&set(p['region_ids'])]
        outside=set(r for c in cells for r in c['region_ids'])-set(p['region_ids'])
        # Local cap P63 is part of the side-wall draw and stays in its STEP cell.
        if outside-{'P63_256'}:raise ValueError('Unseparated domain '+p['operation_id']+': '+str(outside))
        if not set(p['region_ids'])<=set(r for c in cells for r in c['region_ids']):raise ValueError('Missing domain material')
        domains.append(dict(operation_id=p['operation_id'],cell_ids=[c['cell_id'] for c in cells],
            region_ids=sorted(set(r for c in cells for r in c['region_ids'])),carried_volume_mm3=math.fsum(c['volume_mm3'] for c in cells)))
    if any(not c['region_ids'] and c['volume_mm3']>1e-6 for c in rows):raise ValueError('Unassigned residual material')
    removed=volume(cad)-volume(remaining)
    error=abs(math.fsum(c['volume_mm3'] for c in rows)-volume(remaining))
    save_shape(OUT/'partition.brep',remaining)
    print('MASS CHECK',volume(remaining),math.fsum(c['volume_mm3'] for c in rows),error,flush=True)
    # Reject non-solid scraps independently of numerical mass integration.
    solid_faces=set()
    for i in range(1,solids.Extent()+1):
        fs=indexed(solids.FindKey(i),TopAbs_FACE)
        solid_faces.update(fs.FindKey(k) for k in range(1,fs.Extent()+1))
    all_faces=indexed(remaining,TopAbs_FACE)
    if len(solid_faces)!=all_faces.Extent():raise ValueError('Residual faces outside solids')
    if error>1e-5*volume(remaining):raise ValueError('Partition mass integration mismatch: '+str(error))
    if any(sha(ROOT/p)!=h for p,h in inputs.items()):raise ValueError('Inputs changed during material partition')
    inputs.update({(OUT/c['brep_file']).relative_to(ROOT).as_posix():sha(OUT/c['brep_file']) for c in rows})
    write(OUT/'cells.json',dict(step_sha256=sha(source),cells=rows,domains=domains,bands=records,
        original_volume_mm3=volume(cad),remaining_volume_mm3=volume(remaining),removed_band_union_volume_mm3=removed,
        cell_sum_error_mm3=error,cell_sum_relative_error=error/volume(remaining),
        all_partition_faces_owned_by_solids=True,volume_integration_relative_tolerance=1e-10,
        compound_vs_solid_integration_tolerance=1e-5,inputs=inputs,correspondence=correspondence,
        source='Exact completed STEP minus explicitly identified finite bend bands; no local endpoint substitution or inferred mass',
        manufacturing_safety_proved=False))
    print('COMPLETE',len(rows),len(domains),flush=True)


if __name__=='__main__':build()
