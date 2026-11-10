"""STEP-derived local end states attached to the existing material tree.

These are endpoint replacements, not a claimed continuous forming simulation.
All coordinates stored here are in the owning panel's material frame.
"""
from pathlib import Path
import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Fuse
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeBox
from OCP.BRepTools import BRepTools
from OCP.TopoDS import TopoDS_Shape
from OCP.BRep import BRep_Builder
from OCP.gp import gp_Pnt
from .band_material_solids import checked, subtract, compound, prism, common_volume
from .local_collision import volume, transformed, indexed
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopAbs import TopAbs_FACE
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps


def intersect(a,b):
    op=BRepAlgoAPI_Common(a,b)
    if not op.IsDone():raise ValueError('Local intersection failed')
    return checked(op.Shape(),True)


def fuse(a,b):
    op=BRepAlgoAPI_Fuse(a,b)
    if not op.IsDone():raise ValueError('Local union failed')
    op.SimplifyResult()
    return checked(op.Shape())


def box(lo,hi):
    return BRepPrimAPI_MakeBox(gp_Pnt(*map(float,lo)),gp_Pnt(*map(float,hi))).Shape()


def cylinder(x,y,r,zlo,zhi):
    s=BRepPrimAPI_MakeCylinder(r,zhi-zlo).Shape()
    t=np.eye(4);t[:3,3]=[x,y,zlo]
    return transformed(s,t)


def save_shape(path,shape):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    BRepTools.Write_s(shape,str(path))


def load_shape(path):
    s=TopoDS_Shape()
    if not BRepTools.Read_s(s,str(path),BRep_Builder()):raise ValueError(f'Cannot load {path}')
    return checked(s,True)


def restore_punch_material(model,domains,components,tools):
    """Fill only the five exact punch regions, split at the long wall tangent lines.

    Their neighbouring independent tabs are retained material, excluded from the
    original cutter. The restored waste belongs to the two main skins and their
    intervening bend; it is not assigned to a nearby tab by bounding-box proximity.
    """
    bend=next(b for b in model['bends'] if b['operation_id']=='O025')
    a=np.array(bend['start_flat_mm']);d=np.array(bend['forward_flat']);e=np.array(bend['axis_flat'])
    def strip(low,high):return prism([a-e*1500+d*low,a+e*1500+d*low,a+e*1500+d*high,a-e*1500+d*high],model['thickness_mm'])
    masks=[('parent',bend['parent_region_id'],strip(-1500,0)),('band',None,strip(0,bend['width_mm'])),('child',bend['child_region_id'],strip(bend['width_mm'],1500))]
    rows=[]
    for oid in ['O196','O197','O198','O199','O200']:
        tool=tools[oid];parts=[]
        # This assertion prevents filling already supported, retained tongues.
        assert sum(common_volume(tool,s) for s in domains.values())<1e-6,oid
        for kind,rid,mask in masks:
            patch=intersect(tool,mask);v=volume(patch)
            if v<1e-8:continue
            key=f'RESTORE:{oid}:{kind}'
            c={'component_id':key,'kind':'band' if kind=='band' else 'panel','restored_by_operation_id':oid}
            if kind=='band':c.update(joint_id=bend['joint_id'],operation_id=bend['operation_id'])
            else:c['region_id']=rid
            domains[key]=patch;components.append(c)
            parts.append({'component_id':key,'kind':c['kind'],'region_id':rid,'joint_id':c.get('joint_id'),'volume_mm3':v})
        error=abs(sum(p['volume_mm3'] for p in parts)-volume(tool))
        assert error<1e-5,(oid,error)
        rows.append({'operation_id':oid,'status':'RESTORED_EXACT_PUNCH_REGION','source_volume_mm3':volume(tool),'parts':parts,'volume_error_mm3':error,'partition_basis':'O025 parent tangent / finite-width strip / child tangent; independent retained tongues excluded by exact cutter'})
    return rows


def extract_local_patches(model,stage,step_shape,flanges,bridges,domains):
    regions={r['region_id']:r for r in model['regions']};ops={o['operation_id']:o for o in stage['operations']}
    patches={};rows=[];source_faces=indexed(step_shape,TopAbs_FACE)
    for feature in [*bridges,*flanges]:
        oid=feature['operation_id'];is_bridge=ops[oid]['operation_type']=='BRIDGE'
        rid=feature['contour_owner'] if is_bridge else feature['region_id']
        transform=np.array(regions[rid]['flat_from_cad']);t=model['thickness_mm']
        if is_bridge:
            pts=np.array([p for path in ops[oid]['flat_paths'] for p in path]);lo=pts.min(axis=0);hi=pts.max(axis=0)
            # Source footprint includes the bridge cut sides; add a short collar
            # at the two connection ends, never beyond its slit sides.
            mask=box([lo[0],lo[1]-.5,-8],[hi[0],hi[1]+.5,8])
        else:
            center=transform@np.r_[feature['center_cad_mm'],1]
            radius=max(feature['outer_radius_mm'],*[b['major_radius_mm']+b['minor_radius_mm'] for b in feature['root_blends']])+.5
            mask=cylinder(center[0],center[1],radius,-3,3)
        cad_mask=transformed(mask,np.linalg.inv(transform))
        clipped=checked(intersect(step_shape,cad_mask));solids=indexed(clipped,TopAbs_SOLID)
        source_ids=feature['face_ids'] if is_bridge else [c['face_id'] for c in feature['cylinder_candidates']]
        # Other folded panels can lie inside the same spatial crop. Select by
        # the already-matched STEP faces, never by volume or nearest centroid.
        matches=[]
        for i in range(1,solids.Extent()+1):
            solid=solids.FindKey(i);areas=[]
            for fid in source_ids:
                props=GProp_GProps();BRepGProp.SurfaceProperties_s(intersect(solid,source_faces.FindKey(fid)),props)
                areas.append(props.Mass())
            if all(a>1e-6 for a in areas):matches.append(solid)
        if len(matches)!=1:raise ValueError(f'{oid}: {len(matches)} local solids match source faces')
        exact_cad=checked(matches[0])
        formed=checked(transformed(exact_cad,transform))
        flat=checked(intersect(domains[rid],mask))
        count=indexed(formed,TopAbs_SOLID).Extent()
        if count!=1:raise ValueError(f'{oid}: local STEP patch contains {count} solids')
        patches[oid]={'mask':mask,'flat':flat,'formed':formed,'cad':exact_cad,'region_id':rid}
        rows.append({'operation_id':oid,'operation_type':ops[oid]['operation_type'],'region_id':rid,
                     'source_face_ids':feature['face_ids'] if is_bridge else [c['face_id'] for c in feature['cylinder_candidates']]+[feature['rim_face_id']]+[b['face_id'] for b in feature['root_blends']],
                     'unformed_volume_mm3':volume(flat),'formed_volume_mm3':volume(formed),'formed_solid_count':count,
                     'spatial_crop_solids':solids.Extent(),'selection_basis':'CONTAINS_BOTH_MATCHED_STEP_SKIN_OR_WALL_FACES',
                     'local_volume_change_mm3':volume(formed)-volume(flat),'geometry_source':'EXACT_STEP_INTERSECTION',
                     'continuous_forming_trajectory':'UNKNOWN_ENDPOINTS_ONLY'})
    return patches,rows
