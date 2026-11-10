from information_extract.three_d.artifacts import relative
"""Clip display-only feature outlines to their actual reference material domains."""
from pathlib import Path
import sys,json
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT));MODULE=ROOT/'information_extract'
from information_extract.three_d.artifacts import read,sha,write
from information_extract.scripts.build_finite_material_states import build_domains
from information_extract.three_d.local_collision import indexed
from information_extract.three_d.assets import bounds
from information_extract.three_d.band_material_solids import compound,boxes_intersect
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_FACE,TopAbs_EDGE
from OCP.TopoDS import TopoDS
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.gp import gp_Pnt


def build():
    out=MODULE/'outputs/finite_material_v02/P001';model_path=out/'model.json';data_path=out/'viewer_data.json'
    stage_path=MODULE/'outputs/stage_review_v01/P001/stage_data.json';model,stage=read(model_path),read(stage_path)
    for path,h in model['source_sha256'].items():
        if sha(Path(path))!=h:raise ValueError('Source changed: '+path)
    asset=read(MODULE/'outputs/three_d_state_v01/P001/asset.json');cad=read(MODULE/'configs/three_d_state_v01.json')
    reader=STEPControl_Reader()
    if reader.ReadFile(str(MODULE/cad['step']))!=IFSelect_RetDone or not reader.TransferRoots():raise ValueError('STEP failed')
    domains,components=build_domains(model,stage,asset,indexed(reader.OneShape(),TopAbs_FACE))
    boxes={key:bounds(shape) for key,shape in domains.items()};metadata={c['component_id']:c for c in components}
    bend_ops={b['operation_id']:b for b in model['bends']};features=[]
    coverage_path=out/'removal_coverage.json';coverage={r['operation_id']:r for r in read(coverage_path)}
    for op in stage['operations']:
        oid=op['operation_id'];row={k:op[k] for k in ['operation_id','operation_type','source_feature_ref','flat_paths']};row['parts']=[]
        if oid in bend_ops:
            row['display_kind']='BEND_BAND';features.append(row);continue
        wires=[]
        for path in op['flat_paths']:
            points=[]
            for p in path:
                if not points or np.linalg.norm(np.array(p)-points[-1])>1e-9:points.append(p)
            if len(points)<2:continue
            polygon=BRepBuilderAPI_MakePolygon()
            for p in points:polygon.Add(gp_Pnt(float(p[0]),float(p[1]),-.4))
            if polygon.IsDone():wires.append(polygon.Wire())
        if not wires:
            row['display_kind']='NO_2D_LOCATION';features.append(row);continue
        wire=compound(wires);box=bounds(wire)
        for cid,domain in domains.items():
            if not boxes_intersect(box,boxes[cid]):continue
            common=BRepAlgoAPI_Common(wire,domain)
            if not common.IsDone():raise ValueError('Outline/material intersection failed '+oid+' '+cid)
            edges=indexed(common.Shape(),TopAbs_EDGE);paths=[]
            for i in range(1,edges.Extent()+1):
                curve=BRepAdaptor_Curve(TopoDS.Edge_s(edges.FindKey(i)))
                points=[]
                for t in np.linspace(curve.FirstParameter(),curve.LastParameter(),2):
                    p=curve.Value(float(t));points.append([p.X(),p.Y(),0.])
                if np.linalg.norm(np.array(points[0])-points[1])>1e-8:paths.append(points)
            if paths:row['parts'].append({'component':metadata[cid],'paths':paths})
        row['display_kind']='PUNCH_CONTOUR' if op['operation_type'] in {'CIRCLE_PUNCH','IRREGULAR_PUNCH','PRE_PUNCH'} else 'LOCATION_ONLY'
        if oid in coverage and coverage[oid]['status']!='FULL_SUPPORTED_DOMAIN':row['parts']=[]
        if not row['parts']:row['display_kind']='OUTSIDE_SUPPORTED_REFERENCE'
        features.append(row)
    result={'source_sha256':{relative(p):sha(p) for p in [model_path,data_path,stage_path,coverage_path]},'operations':features,
        'interpretation':'DISPLAY_ONLY: sampled source outlines clipped at sheet mid-thickness, then displayed on the front skin; not added physical forming solids.'}
    (out/'feature_highlights.json').write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'),allow_nan=False),encoding='utf-8')
    print(dict(__import__('collections').Counter(r['display_kind'] for r in features)),flush=True)
    return result


if __name__=='__main__':build()
