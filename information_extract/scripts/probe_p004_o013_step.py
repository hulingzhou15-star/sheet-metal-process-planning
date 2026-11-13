"""Read STEP directly and locate O013 by its four unique axial spans."""
from collections import defaultdict
import json
import math
from pathlib import Path

import numpy as np
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Surface
from information_extract.three_d.assets import indexed, bounds, xyz
from information_extract.three_d.facts import sha256
from information_extract.extract_app.drawing import load_drawing

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/p004_o013_step_v01'


def run():
    manifest = json.loads((ROOT/'configs/multi_part_step_v01.json').read_text(encoding='utf-8'))['parts']['P004']
    path = ROOT/manifest['step']
    if sha256(path) != manifest['sha256']: raise ValueError('STEP source changed')
    project_path = ROOT/'projects/P004/project.json'
    before = sha256(project_path)
    project = json.loads(project_path.read_text(encoding='utf-8'))
    op = next(o for o in project['operations'] if o['operation_id']=='O013')
    cfg = json.loads((ROOT/'configs/P004.json').read_text(encoding='utf-8'))
    drawing = load_drawing(ROOT/cfg['source_file'],cfg)
    selected = [s for s in drawing['segments'] if s['id'] in op['source_segment_ids']]
    spans = sorted({tuple(sorted([s['start_x'],s['end_x']])) for s in selected})
    if len(spans)!=4 or len(selected)!=8: raise ValueError('O013 source inventory changed')
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone: raise ValueError('STEP read failed')
    reader.SetSystemLengthUnit(1.0)
    if not reader.TransferRoots(): raise ValueError('STEP transfer failed')
    shape = reader.OneShape(); faces = indexed(shape,TopAbs_FACE)
    groups = defaultdict(list); adaptors={}; planes={}
    for i in range(1,faces.Extent()+1):
        f=TopoDS.Face_s(faces.FindKey(i));a=BRepAdaptor_Surface(f,True);adaptors[i]=a
        kind=str(a.GetType()).split('.')[-1].replace('GeomAbs_','')
        if kind=='Plane': planes[i]={'normal':xyz(a.Plane().Axis().Direction()),'point':xyz(a.Plane().Location())}
        if kind!='Cylinder': continue
        c=a.Cylinder();d=xyz(c.Axis().Direction());p=xyz(c.Axis().Location())
        if abs(d[0])<1-1e-8: continue
        b=bounds(f)
        row={'face_id':i,'radius_mm':c.Radius(),'axis_point_mm':p,'axis_direction':d,
             'x_span_mm':[b[0],b[3]],'sweep_deg':math.degrees(a.LastUParameter()-a.FirstUParameter())}
        groups[(round(p[1],6),round(p[2],6),round(c.Radius(),6))].append(row)
    # Compare complete four-span patterns, both X directions, without a preselected face ID.
    matches=[]
    for group,rows in groups.items():
        if len(rows)!=4: continue
        rows=sorted(rows,key=lambda r:r['x_span_mm'][0]);target=np.array([r['x_span_mm'] for r in rows])
        for sign in (1,-1):
            source=np.array(sorted(tuple(sorted(sign*np.array(s))) for s in spans))
            shift=float(np.mean(target-source));error=float(np.max(np.abs(target-source-shift)))
            if error<1e-6:matches.append({'group':group,'rows':rows,'x_sign':sign,'x_translation_mm':shift,'max_endpoint_error_mm':error})
    if len(matches)!=2: raise ValueError('Expected one inner/outer four-span pair')
    matches.sort(key=lambda m:m['group'][2]);inner,outer=matches
    if inner['group'][:2]!=outer['group'][:2] or inner['x_sign']!=outer['x_sign']:raise ValueError('Not coaxial')
    angle=float(np.mean([r['sweep_deg'] for m in matches for r in m['rows']]))
    if max(abs(r['sweep_deg']-angle) for m in matches for r in m['rows'])>1e-6:raise ValueError('Unequal sweeps')
    # Independently check adjacent planar normals and thickness from the direct import.
    asset=json.loads((ROOT/'outputs/multi_part_step_v01/P004/asset.json').read_text(encoding='utf-8'))
    if asset['asset_id']!=sha256(path) or asset['face_count']!=faces.Extent():raise ValueError('Stale topology index')
    by={f['face_id']:f for f in asset['faces']}
    planar_checks=[]
    for row in inner['rows']:
        ns=[i for i in by[row['face_id']]['neighbor_face_ids'] if i in planes]
        xplanes=[i for i in ns if abs(planes[i]['normal'][0])<1e-8]
        if len(xplanes)!=2:raise ValueError('Expected two tangent panels')
        normals=[np.array(planes[i]['normal']) for i in xplanes]
        turn=math.degrees(math.acos(float(np.clip(abs(normals[0]@normals[1]),-1,1))))
        if abs(turn-angle)>1e-6:raise ValueError('Cylinder angle disagrees with planes')
        planar_checks.append({'bend_face':row['face_id'],'adjacent_panel_faces':xplanes,'turn_deg':turn})
    result={'part_id':'P004','operation_id':'O013','step_source':manifest['step'],'step_sha256':sha256(path),
            'annotation_revision':project['revision'],'annotation_sha256':before,'units':'mm',
            'mapping_basis':'Unique four-span X pattern across all X-parallel cylinders; independent paired inner/outer surfaces and adjacent-plane angle checks.',
            'flat_spans_mm':spans,'inner':inner,'outer':outer,'coaxial':True,
            'axis_point_mm':[0,*inner['rows'][0]['axis_point_mm'][1:]],'axis_direction':[1,0,0],
            'inner_radius_mm':inner['group'][2],'outer_radius_mm':outer['group'][2],
            'thickness_mm':outer['group'][2]-inner['group'][2],
            'physical_turn_deg':angle,'included_angle_deg':180-angle,'recorded_bend_angle_deg':op['bend_angle_deg'],
            'plane_crosschecks':planar_checks,'status':'STEP_LOCAL_GEOMETRY_VERIFIED',
            'modeling_policy':'Use STEP axis, radial surfaces, span boundaries and physical turn as the O013 final-geometry target. Preserve annotation 170 as the measured included-angle interpretation here; no global angle reinterpretation.',
            'limitations':['Only O013 local correspondence established; no full P004 DXF/STEP material registration.',
                          'No neutral layer/K factor known: cannot uniquely derive a flat bend centerline or prove which of the two DXF Y positions is exact.',
                          'Existing 0.060/0.056025mm drawing double lines are not accepted as the physical bend-band width.',
                          'No P004 sequential solid builder integrated by this read-only probe; expert OP60, H7 and annotations unchanged.']}
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'measurement.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    plot(result,adaptors)
    rows=''.join(f'<tr><td>{i+1}</td><td>{s[1]-s[0]:.2f}</td><td>{r["x_span_mm"][0]:.2f} ～ {r["x_span_mm"][1]:.2f}</td></tr>' for i,(s,r) in enumerate(zip(spans,inner['rows'])))
    page=f'''<!doctype html><meta charset="utf-8"><title>P004 O013 STEP核验</title><style>body{{font:17px system-ui;max-width:1100px;margin:30px auto;background:#f5f7fa;color:#23384b}}img{{width:100%;background:white}}td,th{{padding:8px 18px;border:1px solid #ccd5de}}table{{border-collapse:collapse}}aside{{background:#fff0d5;padding:15px}}</style><h1>P004 · O013 的 STEP 结构</h1><p>四段共轴，内半径0.5mm、外半径1.5mm、板厚1mm；实际转折10°，内夹角170°。四段长度及间隔与录入图逐一吻合。</p><img src="comparison.png"><table><tr><th>段</th><th>长度 mm</th><th>STEP X范围 mm</th></tr>{rows}</table><p>STEP共轴线：Y={result['axis_point_mm'][1]:.9f}，Z={result['axis_point_mm'][2]:.9f}，沿X方向。内外圆柱面各4段，合计8个面；不是四段错位。</p><aside>采用STEP作为此处成品几何目标；不把170直接作为旋转量。不凭成品STEP直接裁定两份二维图约0.04176mm的中线差，仍需展开/中性层依据。本次为局部核验，未改人工几何和专家序，也未接入P004逐序实体模型。</aside><p><a href="measurement.json">完整坐标、面编号与来源指纹</a></p>'''
    (OUT/'review.html').write_text(page,encoding='utf-8')
    if sha256(project_path)!=before or sha256(path)!=manifest['sha256']:raise ValueError('Source changed during probe')
    print(json.dumps({k:result[k] for k in ['coaxial','inner_radius_mm','outer_radius_mm','thickness_mm','physical_turn_deg','included_angle_deg']},ensure_ascii=False))
    print('inner faces', [r['face_id'] for r in inner['rows']], 'outer faces', [r['face_id'] for r in outer['rows']])
    return result


def plot(result,adaptors):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    plt.rcParams['font.sans-serif']=['Microsoft YaHei','SimHei','DejaVu Sans']
    plt.rcParams['axes.unicode_minus']=False
    fig,(ax,bx)=plt.subplots(2,1,figsize=(11,7),height_ratios=[1,2],layout='constrained')
    ax.axhline(0,color='#96a4b3',ls='--',lw=1)
    for i,r in enumerate(result['inner']['rows']):
        lo,hi=r['x_span_mm'];ax.add_patch(Rectangle((lo,-.12),hi-lo,.24,color='#74b5df'))
        ax.text((lo+hi)/2,.23,f'{i+1}：{hi-lo:.2f} mm',ha='center',fontsize=10)
    ax.set(xlim=(-210,230),ylim=(-.4,.6),yticks=[],xlabel='STEP X / mm',title='O013：四段材料，共用同一条折弯轴（示意厚度放大）')
    y0=result['axis_point_mm'][1]
    for group,color in [(result['inner'],'#2484b2'),(result['outer'],'#56616b')]:
        a=adaptors[group['rows'][-1]['face_id']];v=(a.FirstVParameter()+a.LastVParameter())/2
        pts=np.array([xyz(a.Value(float(u),v)) for u in np.linspace(a.FirstUParameter(),a.LastUParameter(),80)])
        pts=pts[np.argsort(pts[:,1])];yy=pts[:,1]-y0;zz=pts[:,2]
        bx.plot(yy,zz,color=color,lw=2)
        bx.plot([-2,yy[0]],[zz[0],zz[0]],color=color,lw=2)
        bx.plot([yy[-1],5],[zz[-1],zz[-1]+(5-yy[-1])*math.tan(math.radians(result['physical_turn_deg']))],color=color,lw=2)
    bx.plot(0,.5,'+',color='#c74632',ms=10);bx.annotate('共轴中心',xy=(0,.5),xytext=(-1.8,1.2),arrowprops={'arrowstyle':'->'},fontsize=11)
    bx.text(2.1,1.25,'板面转折 10°\n内夹角 170°',fontsize=12)
    bx.text(1,-1.2,'内 R0.5 / 外 R1.5 / 板厚 1 mm',fontsize=11)
    bx.set(xlim=(-2.2,5.2),ylim=(-1.5,1.8),xlabel='Y − 折弯轴Y / mm',ylabel='STEP Z / mm',title='STEP原始圆柱面截面 + 相切板面延长线（等比例）',aspect='equal')
    bx.grid(alpha=.18)
    fig.savefig(OUT/'comparison.png',dpi=160);plt.close(fig)


if __name__=='__main__':run()
