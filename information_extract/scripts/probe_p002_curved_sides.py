"""Calibrate curved bends against user-confirmed O069 on the same material base."""
from collections import defaultdict
import numpy as np
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Curve
from information_extract.three_d.artifacts import ROOT,read,write,sha
from information_extract.three_d.assets import indexed,xyz
from information_extract.extract_app.drawing import load_drawing
from information_extract.scripts.extract_p002_expert_dxf import source_primitive,transformed,key

MODULE=ROOT/'information_extract'


def probe(project,drawing,asset,registration):
    path=MODULE/'sources/P002/originals/P002.stp'
    if sha(path)!=asset['asset_id'] or sha(path)!=registration['step_sha256']:raise ValueError('Stale STEP registration')
    reader=STEPControl_Reader()
    if reader.ReadFile(str(path))!=IFSelect_RetDone:raise ValueError('STEP import failed')
    reader.SetSystemLengthUnit(1.)
    if not reader.TransferRoots():raise ValueError('STEP transfer failed')
    edges=indexed(reader.OneShape(),TopAbs_EDGE)
    if edges.Extent()!=asset['edge_count']:raise ValueError('Topology differs')
    lookup=defaultdict(list)
    for i in range(1,edges.Extent()+1):
        row=asset['edges'][i-1]
        if not set(row['face_ids'])&{466,468}:continue
        c=BRepAdaptor_Curve(TopoDS.Edge_s(edges.FindKey(i)));kind=str(c.GetType()).split('.')[-1]
        if kind not in ('GeomAbs_Line','GeomAbs_Circle'):continue
        lo,hi=c.FirstParameter(),c.LastParameter();pts=[xyz(c.Value(u)) for u in (lo,hi,(lo+hi)/2)]
        if np.ptp(np.array(pts)[:,2])>1e-6:continue
        g=dict(kind='LINE' if kind=='GeomAbs_Line' else 'ARC',points=[p[:2] for p in pts[:2 if kind=='GeomAbs_Line' else 3]],radius=None if kind=='GeomAbs_Line' else c.Circle().Radius())
        lookup[key(g)].append(row)
    segs={s['id']:s for s in drawing['segments']};ops={o['operation_id']:o for o in project['operations']}
    m,t=np.array(registration['matrix']),np.array(registration['translation_mm']);rows=[]
    for oid in ('O069','O088','O096','O104'):
        matches=[];wall_ids=set()
        for sid in ops[oid]['source_segment_ids']:
            g=transformed(source_primitive(segs[sid]),m,t)
            hits=lookup.get(key(g),[])
            if not hits:continue
            matches.append(dict(source_segment_id=sid,step_edge_ids=[r['edge_id'] for r in hits]))
            for e in hits:wall_ids.update(set(e['face_ids'])-{466,468})
        if not matches:raise ValueError('No exact base boundary match: '+oid)
        extents=[asset['faces'][i-1]['bounds_mm'] for i in sorted(wall_ids)]
        signs=set()
        for bb in extents:
            if bb[5]>1e-5:signs.add(1)
            if bb[2]<-.8-1e-5:signs.add(-1)
        if len(signs)!=1:raise ValueError('Ambiguous signed base protrusion: '+oid)
        rows.append(dict(operation_id=oid,base_skin_faces=[466,468],matched_source_count=len(matches),
            source_segment_count=len(ops[oid]['source_segment_ids']),matches=matches,adjacent_step_face_ids=sorted(wall_ids),
            adjacent_face_bounds_mm=extents,base_protrusion_sign=signs.pop()))
    reference=rows[0]
    if ops['O069']['surface_side']!='ROUGH':raise ValueError('O069 calibration changed')
    for r in rows:
        r['surface_side']='ROUGH' if r['base_protrusion_sign']==reference['base_protrusion_sign'] else 'SMOOTH'
        r['basis']='User instruction: same direction as O069 means same material side, on the same base'
    return dict(step_sha256=sha(path),project_sha256=sha(MODULE/'projects/P002/project.json'),
        reference_operation_id='O069',reference_surface_side='ROUGH',rows=rows,
        limitations='Exact matched boundary subset and attached STEP faces establish side only; not complete material domain/trajectory.')


def main():
    cfg=read(MODULE/'configs/P002.json');p=read(MODULE/'projects/P002/project.json')
    d=load_drawing(MODULE/cfg['source_file'],cfg);base=MODULE/'outputs/multi_part_step_v01/P002'
    result=probe(p,d,read(base/'asset.json'),read(base/'base_registration.json'))
    write(MODULE/'outputs/p002_generation_v01/curved_side_evidence.json',result)
    print([(r['operation_id'],r['surface_side'],r['matched_source_count']) for r in result['rows']])


if __name__=='__main__':main()
