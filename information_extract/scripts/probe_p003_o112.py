"""Compare the known O112 view discrepancy after independent base-circle registration."""
import json
import math
from pathlib import Path
from collections import Counter
import numpy as np
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE, TopAbs_WIRE, TopAbs_EDGE
from OCP.BRepTools import BRepTools
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.TopoDS import TopoDS
from information_extract.three_d.assets import indexed, bounds
from information_extract.three_d.facts import sha256

MODULE=Path(__file__).resolve().parents[1]


def main():
    read=lambda p:json.loads(p.read_text(encoding='utf-8'))
    folder=MODULE/'outputs/multi_part_step_v01/P003'
    reg=read(folder/'base_registration.json')
    source=MODULE/'sources/P003/originals/P003.stp'
    assert reg['status']=='BASE_FRAME_SUPPORTED' and sha256(source)==reg['step_sha256']
    assert all(a['operation_id']!='O112' for a in reg['anchors'])
    audit=read(MODULE/'outputs/multi_part_readiness_v01/audit.json')
    part=next(p for p in audit['parts'] if p['part_id']=='P003')
    for name,digest in part['source_sha256'].items():
        assert sha256(MODULE.parent/name)==digest,name
    op=next(o for o in part['operations'] if o['operation_id']=='O112')
    model_decision=op['model_geometry_decision']
    assert model_decision['decision']=='MODEL_FROM_FLAT_CIRCLE'
    segment=next(s for s in part['drawing']['segments'] if s['id']==op['source_segment_ids'][0])
    assert segment['segment_type']=='CIRCLE'
    xy=np.array(reg['matrix'])@np.array([segment['center_x'],segment['center_y']])+reg['translation_mm']
    reader=STEPControl_Reader();reader.ReadFile(str(source));reader.SetSystemLengthUnit(1.);reader.TransferRoots()
    faces=indexed(reader.OneShape(),TopAbs_FACE);face=TopoDS.Face_s(faces.FindKey(reg['base_face_id']))
    outer=BRepTools.OuterWire_s(face);wires=indexed(face,TopAbs_WIRE);rows=[]
    for i in range(1,wires.Extent()+1):
        wire=wires.FindKey(i)
        if wire.IsSame(outer):continue
        box=bounds(wire);center=np.array([(box[0]+box[3])/2,(box[1]+box[4])/2])
        edges=indexed(wire,TopAbs_EDGE);curves=[]
        for j in range(1,edges.Extent()+1):
            curve=BRepAdaptor_Curve(TopoDS.Edge_s(edges.FindKey(j)))
            kind=str(curve.GetType()).split('.')[-1]
            n=1 if kind=='GeomAbs_Line' else 48
            lo,hi=curve.FirstParameter(),curve.LastParameter()
            points=[]
            for k in range(n+1):
                point=curve.Value(lo+(hi-lo)*k/n);points.append([point.X(),point.Y(),point.Z()])
            curves.append({'type':kind,'points_mm':points,'radius_mm':curve.Circle().Radius() if kind=='GeomAbs_Circle' else None})
        rows.append({'wire_index':i,'bounds_mm':box,'center_distance_mm':float(np.linalg.norm(center-xy)),
                     'curve_types':dict(Counter(c['type'] for c in curves)),'curves':curves})
    rows.sort(key=lambda r:r['center_distance_mm']);best=rows[0]
    result={'operation_id':'O112','status':'GEOMETRIC_CORRESPONDENCE_PROPOSAL_SHAPE_DISCREPANCY',
            'model_geometry_decision':model_decision,
            'step_sha256':reg['step_sha256'],'annotation_sha256':reg['annotation_sha256'],
            'dxf_sha256':reg['dxf_sha256'],'base_face_id':reg['base_face_id'],
            'independent_anchor_count':reg['anchor_count'],'excluded_from_anchors':'O112',
            'flat_circle_radius_mm':segment['radius'],'base_frame_target_xy_mm':xy.tolist(),
            'candidate_wires':rows[:5],
            'limitation':'Identity proposal supported by independent base registration and loop location; cause of shape difference and source revision equality not established.'}
    (folder/'O112_base_wire_candidates.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    def curves_svg():
        return ''.join('<polyline points="'+' '.join(f'{p[0]-xy[0]},{-(p[1]-xy[1])}' for p in c['points_mm'])+'"/>' for c in best['curves'])
    circle=f'<circle cx="0" cy="0" r="{segment["radius"]}"/>'
    views=[]
    for label,content,color in [('二维原图：圆孔',circle,'#298bc8'),('STEP 基准面内环',curves_svg(),'#4d5966'),('同中心叠加',f'<g stroke="#298bc8">{circle}</g><g stroke="#4d5966">{curves_svg()}</g>','#4d5966')]:
        views.append(f'<section><h2>{label}</h2><svg viewBox="-5 -5 10 10"><g fill="none" stroke="{color}" stroke-width=".035">{content}</g></svg></section>')
    width,height=best['bounds_mm'][3]-best['bounds_mm'][0],best['bounds_mm'][4]-best['bounds_mm'][1]
    page=f'''<!doctype html><html lang="zh"><meta charset="utf-8"><title>P003 O112 · 二维/STEP 差异</title>
<style>body{{font:17px system-ui;background:#f4f7fa;color:#263d50;margin:30px}}main{{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}}section{{background:white;padding:20px;border:1px solid #d6dfe7;border-radius:8px}}h2{{font-size:19px}}svg{{width:100%;height:360px}}aside{{padding:16px;background:#fff0d2}}</style>
<h1>P003 · O112 形状差异</h1><p>用其他 {reg['anchor_count']} 个圆孔独立配准，O112 不参与拟合。三图采用相同比例与中心，尺寸单位 mm。</p>
<main>{''.join(views)}</main><p>二维直径：{2*segment['radius']:.3f} mm；STEP 内环范围：{width:.3f} × {height:.3f} mm；内环为四直线＋四圆弧。中心残差：{best['center_distance_mm']:.3g} mm（计算一致性，非测量精度）。</p>
<aside>2026-11-11 用户确认：建模采用二维直径 7 mm 圆孔。原始 STEP 的圆角矩形保留作为来源差异，不作为此孔建模依据。差异原因仍未查明，不推断圆孔到圆角矩形的成形映射。</aside>
<p><a href="O112_base_wire_candidates.json">几何证据</a> · <a href="../../multi_part_readiness_v01/review.html">返回三件核查页</a></p></html>'''
    (folder/'O112_comparison.html').write_text(page,encoding='utf-8')
    print('O112 independent anchors',reg['anchor_count'],'center residual',best['center_distance_mm'],'size',width,height,'curve types',best['curve_types'])


if __name__=='__main__':main()
