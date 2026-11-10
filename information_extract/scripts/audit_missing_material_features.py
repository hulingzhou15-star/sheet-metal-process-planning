from information_extract.three_d.artifacts import relative
"""Locate pre-existing punch voids and four bridge faces; read-only CAD audit."""
from information_extract.scripts.build_finite_material_states import *
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
import html

def main():
    out=MODULE/'outputs/finite_material_v02/P001'
    model=read(out/'model.json');viewer=read(out/'viewer_data.json')
    stage=read(MODULE/'outputs/stage_review_v01/P001/stage_data.json')
    asset=read(MODULE/'outputs/three_d_state_v01/P001/asset.json')
    highlights=read(out/'feature_highlights.json');cfg=read(MODULE/'configs/three_d_state_v01.json')
    reader=STEPControl_Reader();assert reader.ReadFile(str(MODULE/cfg['step']))==IFSelect_RetDone
    assert reader.TransferRoots();faces=indexed(reader.OneShape(),TopAbs_FACE)
    raw={}
    for region in model['regions']:
        f=TopoDS.Face_s(faces.FindKey(region['front_face_id']))
        filled=BRepBuilderAPI_MakeFace(BRepTools.OuterWire_s(f),True);assert filled.IsDone()
        flat=transformed(filled.Face(),region['flat_from_cad'])
        raw[region['region_id']]=BRepPrimAPI_MakePrism(TopoDS.Face_s(flat),gp_Vec(0,0,-model['thickness_mm'])).Shape()
    boxes={k:bounds(v) for k,v in raw.items()};punches=[]
    for oid in ['O196','O197','O198','O199','O200']:
        op=next(o for o in stage['operations'] if o['operation_id']==oid)
        tool=source_prism(op,stage['display_outline'],0,np.eye(4),[0,0,-model['thickness_mm']]);bb=bounds(tool);hits=[]
        for rid,body in raw.items():
            if not boxes_intersect(bb,boxes[rid]):continue
            distance=BRepExtrema_DistShapeShape(tool,body);distance.Perform();assert distance.IsDone()
            hits.append({'region_id':rid,'overlap_mm3':common_volume(tool,body),'distance_mm':distance.Value()})
        punches.append({'operation_id':oid,'source_volume_mm3':volume(tool),'raw_supports':hits,
                        'absent_before_any_punch':all(x['overlap_mm3']<1e-8 for x in hits)})
    bridges=[]
    for oid,rid in [('O027','P1102_1104'),('O028','P1096_1100'),('O029','P137_734'),('O030','P709_1070')]:
        feature=next(o for o in highlights['operations'] if o['operation_id']==oid)
        face_ids=list(map(int,rid[1:].split('_')));face=asset['faces'][face_ids[0]-1]
        region_id=feature['parts'][0]['component']['region_id'];region=next(r for r in model['regions'] if r['region_id']==region_id)
        pts=np.array([p for path in feature['flat_paths'] for p in path]);center=(pts.min(axis=0)+pts.max(axis=0))/2
        reference=np.linalg.inv(region['flat_from_cad'])@np.r_[center,0,1]
        c=np.array(face['centroid_mm']);assert np.max(np.abs(c[[0,2]]-reference[[0,2]]))<1e-7
        assert abs(face['bounds_mm'][3]-face['bounds_mm'][0]-3)<1e-7
        bridges.append({'operation_id':oid,'name':feature['source_feature_ref'],'region_id':rid,'face_ids':face_ids,
                        'cad_center_mm':c.tolist(),'flat_center_mm':center.tolist(),'unformed_surface_center_cad_mm':reference[:3].tolist(),
                        'contour_owner':region_id,'display_kind':feature['display_kind'],
                        'included_in_dynamic_regions':any(r['region_id']==rid for r in model['regions']),
                        'included_in_material_variants':any(p['component_id']==rid for v in viewer['variants'] for p in v['components'])})
    result={'punches':punches,'bridges':bridges,'cad_solid_count':asset['solid_count'],
            'interpretation':'Four U-bridge middle-face proposals excluded from ordinary-bend motion tree, not disconnected physical solids.',
            'source_sha256':{relative(p):sha(p) for p in [out/'model.json',out/'viewer_data.json',out/'feature_highlights.json']}}
    write(out/'missing_features_audit.json',result)
    def svg(xlo,xhi,zlo,zhi,selected=None,profile=False):
        selected=selected or bridges
        sx=(lambda p:p[1]) if profile else (lambda p:p[0])
        paths=[]
        for edge in asset['edges']:
            ps=edge['points']
            if profile and (max(p[0] for p in ps)<selected[0]['cad_center_mm'][0]-1.51 or min(p[0] for p in ps)>selected[0]['cad_center_mm'][0]+1.51):continue
            if max(sx(p) for p in ps)<xlo or min(sx(p) for p in ps)>xhi or max(p[2] for p in ps)<zlo or min(p[2] for p in ps)>zhi:continue
            paths.append('<polyline points="'+' '.join(f'{sx(p):.5f},{-p[2]:.5f}' for p in ps)+'"/>')
        marks=[]
        for i,b in enumerate(selected):
            f=asset['faces'][b['face_ids'][0]-1];bb=f['bounds_mm'];x=b['cad_center_mm'][0]
            if profile:marks.append(f'<line x1="{bb[1]}" x2="{bb[1]}" y1="{-bb[2]}" y2="{-bb[5]}" stroke="#db4935" stroke-width=".35"/>')
            else:
                marks.append(f'<rect x="{bb[0]}" y="{-bb[5]}" width="{bb[3]-bb[0]}" height="{bb[5]-bb[2]}" fill="#db4935"/>')
                size=min(5,(xhi-xlo)/18);marks.append(f'<text x="{x}" y="{-bb[5]-3}" text-anchor="middle" font-size="{size}" fill="#a52b1b">{b["operation_id"]}</text>')
        return f'<svg viewBox="{xlo} {-zhi} {xhi-xlo} {zhi-zlo}" xmlns="http://www.w3.org/2000/svg"><g fill="none" stroke="#89959e" stroke-width=".18">'+''.join(paths)+'</g>'+''.join(marks)+'</svg>'
    rows=''.join(f'<tr><td>{b["operation_id"]} · {html.escape(b["name"])}</td><td>{b["region_id"]}</td><td>{b["cad_center_mm"][0]:.1f}</td><td>仅定位轮廓；成形实体未接入</td></tr>' for b in bridges)
    cards=''.join(f'<section><h3>{b["operation_id"]} · {html.escape(b["name"])}</h3><p>正视定位（红色为中段面）</p>'+svg(b['cad_center_mm'][0]-12,b['cad_center_mm'][0]+12,23,53,[b])+ '<p>侧面剖向投影（保留背面边线）</p>'+svg(120,133,25,51,[b],True)+'</section>' for b in bridges)
    page='''<!doctype html><meta charset="utf-8"><title>P001 四个中桥定位核查</title><style>body{font:16px/1.6 system-ui;background:#f3f5f7;color:#243745;max-width:1280px;margin:24px auto;padding:0 20px}svg{display:block;width:100%;height:260px;background:white;border:1px solid #ccd5da}table{border-collapse:collapse;width:100%;background:white}td,th{padding:8px;border:1px solid #ccd5da}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}section{min-width:0}h3{font-size:16px}.note{background:#fff3dd;padding:12px}</style><h1>P001：四个“未连接区域”实际是 U 形中桥中段</h1><p>这是原始成品 CAD 的定位投影，红色面来自真实 CAD；本页不表示它们已接入逐序成形模型。</p><p class="note">原始 CAD 为一个实体。“未连接”指未接入普通折弯运动树。当前工序 review.html 只有 O027–O030 定位轮廓，没有这些桥部的成形实体。</p><h2>长侧壁上的四个位置 · CAD 正视投影</h2>'''+svg(65,290,15,65)+ '<table><tr><th>动作</th><th>原区域编号</th><th>CAD X / mm</th><th>当前工序 HTML</th></tr>'+rows+'</table><div class="cards">'+cards+'</div><p>O196–O200：原始成品外轮廓生成的支持材料在逐序冲孔前即不覆盖孔区。需要补建冲前材料，并按既有工序去料；不是孔位识别困难。</p><p><a href="missing_features_audit.json">数值核查记录</a> · <a href="review.html">返回工序检视</a></p>'
    (out/'missing_features_locator.html').write_text(page,encoding='utf-8')
    print(json.dumps({'punches':[{k:r[k] for k in ['operation_id','absent_before_any_punch']} for r in punches],'bridges':bridges},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
