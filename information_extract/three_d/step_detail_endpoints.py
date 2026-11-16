"""P001 dimple/marking endpoints taken directly from the supplied STEP solid."""
from pathlib import Path
import numpy as np
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_SOLID, TopAbs_FACE
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from .artifacts import ROOT, read, sha
from .local_forming import box, intersect, fuse, load_shape, save_shape, subtract
from .local_collision import indexed, volume, transformed
from ..extract_app.drawing import load_drawing
from ..extract_app.geometry import segment_bbox


def extract_details(viewer, source, destination):
    module=ROOT/'information_extract'
    cfg=read(module/'configs/three_d_state_v01.json')
    step=module/cfg['step']
    if sha(step)!=cfg['expected_step_sha256']:raise ValueError('STEP source changed')
    reader=STEPControl_Reader()
    if reader.ReadFile(str(step))!=IFSelect_RetDone or not reader.TransferRoots():raise ValueError('STEP import failed')
    cad=reader.OneShape();faces=indexed(cad,TopAbs_FACE)
    stage=read(module/'outputs/stage_review_v01/P001/stage_data.json')
    project=read(module/'projects/P001/project.json');dcfg=read(module/'configs/app.json')
    drawing=load_drawing(module/dcfg['source_file'],dcfg)
    segs={s['id']:s for s in drawing['segments']}
    ops={o['operation_id']:o for o in project['operations']}
    reg=stage['display_outline'];matrix=np.array(reg['registration_matrix']);translation=np.array(reg['registration_translation_mm'])
    domains={r['region_id']:load_shape(Path(source)/'brep'/f'domain_{r["region_id"]}.brep') for r in viewer['model']['regions']}
    regions={r['region_id']:r for r in viewer['model']['regions']}
    patches={};rows=[]
    for op in stage['operations']:
        if op['operation_type'] not in ('DIMPLE','MARKING'):continue
        oid=op['operation_id']
        if op['operation_type']=='DIMPLE':
            pts=np.array([p for path in op['flat_paths'] for p in path]);lo=pts.min(axis=0);hi=pts.max(axis=0)
        else:
            boxes=np.array([segment_bbox(segs[s]) for s in ops[oid]['source_segment_ids']])
            lo0,hi0=boxes[:,:2].min(axis=0),boxes[:,2:].max(axis=0)
            pts=np.array([matrix@p+translation for p in (lo0,hi0,np.array([lo0[0],hi0[1]]),np.array([hi0[0],lo0[1]]))])
            lo,hi=pts.min(axis=0),pts.max(axis=0)
        mask=box([*(lo-.5),-3],[*(hi+.5),3])
        if op['operation_type']=='MARKING' and len(ops[oid]['source_segment_ids'])==6:
            # L-shaped embosses: a bounding rectangle would capture unrelated
            # neighbouring material. Union narrow collars around the bound edges.
            strips=[]
            for sid in ops[oid]['source_segment_ids']:
                s=segs[sid]
                if s['segment_type']!='LINE':raise ValueError('Expected bound L edges')
                q=np.array([matrix@np.array([s[e+'_x'],s[e+'_y']])+translation for e in ('start','end')])
                strips.append(box([*(q.min(axis=0)-.5),-3],[*(q.max(axis=0)+.5),3]))
            mask=strips[0]
            for strip in strips[1:]:mask=fuse(mask,strip)
        hits={rid:volume(intersect(body,mask)) for rid,body in domains.items()}
        owners=[rid for rid,v in hits.items() if v>1e-6]
        if len(owners)!=1:raise ValueError(f'{oid}: ambiguous material owners {owners}')
        rid=owners[0];r=regions[rid];transform=np.array(r['flat_from_cad'])
        crop=intersect(cad,transformed(mask,np.linalg.inv(transform)));solids=indexed(crop,TopAbs_SOLID)
        matches=[]
        # Identify the crop by its known host STEP face, not spatial proximity.
        for i in range(1,solids.Extent()+1):
            body=solids.FindKey(i);props=GProp_GProps()
            BRepGProp.SurfaceProperties_s(intersect(body,faces.FindKey(r['front_face_id'])),props)
            if props.Mass()>1e-6:matches.append(body)
        if len(matches)!=1:raise ValueError(f'{oid}: {len(matches)} STEP host matches')
        formed=transformed(matches[0],transform);flat=intersect(domains[rid],mask)
        host=subtract(domains[rid],[mask])
        overlap=volume(intersect(host,formed))
        joined=indexed(fuse(host,formed),TopAbs_SOLID).Extent()
        if overlap>1e-6 or joined>indexed(host,TopAbs_SOLID).Extent():raise ValueError(f'{oid}: STEP patch not attached cleanly')
        delta=volume(subtract(flat,[formed]))+volume(subtract(formed,[flat]))
        patches[oid]=dict(mask=mask,flat=flat,formed=formed,region_id=rid)
        row=dict(operation_id=oid,operation_type=op['operation_type'],region_id=rid,
            host_front_face_id=r['front_face_id'],flat_bounds_mm=[lo.tolist(),hi.tolist()],
            selection='UNIQUE_MATERIAL_DOMAIN_AND_MATCHED_STEP_HOST_FACE',
            step_sha256=sha(step),unformed_volume_mm3=volume(flat),formed_volume_mm3=volume(formed),
            endpoint_symmetric_difference_mm3=delta,host_overlap_mm3=overlap,attached_to_host=True,
            endpoint_status='STEP_RELIEF' if delta>1e-6 else 'STEP_HAS_NO_VOLUME_RELIEF',continuous_forming_trajectory='NOT_MODELLED')
        rows.append(row)
        for kind in ('mask','flat','formed'):save_shape(Path(destination)/'detail_brep'/f'{oid}_{kind}.brep',patches[oid][kind])
        print('STEP DETAIL',row,flush=True)
    return patches,rows
