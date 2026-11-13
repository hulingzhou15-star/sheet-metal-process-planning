"""Verify O069's curved flange in STEP, then optionally correct its action class."""
import argparse
from collections import defaultdict
from copy import deepcopy
import json
import math
from pathlib import Path

import numpy as np
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
from information_extract.three_d.assets import indexed, xyz, bounds
from information_extract.three_d.facts import sha256
from information_extract.extract_app.drawing import load_drawing
from information_extract.extract_app.model import ProjectStore, validate_project
from information_extract.scripts.extract_p002_expert_dxf import source_primitive, key, transformed

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/p002_o069_step_v01'


def run(apply=False):
    cfg=json.loads((ROOT/'configs/P002.json').read_text(encoding='utf-8'))
    drawing=load_drawing(ROOT/cfg['source_file'],cfg)
    store=ProjectStore(ROOT,drawing,'P002');project=store.load()
    operations={o['operation_id']:o for o in project['operations']};op=operations['O069']
    segs={s['id']:s for s in drawing['segments']}
    folder=ROOT/'outputs/multi_part_step_v01/P002'
    asset=json.loads((folder/'asset.json').read_text(encoding='utf-8'))
    reg=json.loads((folder/'base_registration.json').read_text(encoding='utf-8'))
    source=ROOT/'sources/P002/originals/P002.stp'
    if sha256(source)!=asset['asset_id'] or asset['asset_id']!=reg['step_sha256']:raise ValueError('Stale STEP asset')
    reader=STEPControl_Reader()
    if reader.ReadFile(str(source))!=IFSelect_RetDone:raise ValueError('STEP read failed')
    reader.SetSystemLengthUnit(1.0)
    if not reader.TransferRoots():raise ValueError('STEP transfer failed')
    shape=reader.OneShape();edges=indexed(shape,TopAbs_EDGE);faces=indexed(shape,TopAbs_FACE)
    if faces.Extent()!=asset['face_count'] or edges.Extent()!=asset['edge_count']:raise ValueError('Topology index mismatch')
    m,t=np.array(reg['matrix']),np.array(reg['translation_mm'])
    anchors=[]
    for anchor in reg['anchors']:
        if anchor['operation_id']=='O069':raise ValueError('Target must not be an anchor')
        aop=operations[anchor['operation_id']]
        s=next(segs[sid] for sid in aop['source_segment_ids'] if segs[sid]['segment_type']=='CIRCLE')
        curve=BRepAdaptor_Curve(TopoDS.Edge_s(edges.FindKey(anchor['edge_id'])))
        center=xyz(curve.Circle().Location())
        error=float(np.linalg.norm(m@np.array([s['center_x'],s['center_y']])+t-np.array(center[:2])))
        radius_error=abs(curve.Circle().Radius()-s['radius'])
        if error>1e-6 or radius_error>reg['policy']['radius_tolerance_mm']:raise ValueError('Independent anchor changed')
        anchors.append(dict(operation_id=anchor['operation_id'],center_residual_mm=error,
                            radius_difference_mm=radius_error,radius_tolerance_mm=reg['policy']['radius_tolerance_mm']))
    flat=[dict(transformed(source_primitive(segs[sid]),m,t),source_id=sid) for sid in op['source_segment_ids']]
    edge_lookup=defaultdict(list)
    for i in range(1,edges.Extent()+1):
        a=BRepAdaptor_Curve(TopoDS.Edge_s(edges.FindKey(i)));kind=str(a.GetType()).split('.')[-1]
        if kind not in ('GeomAbs_Line','GeomAbs_Circle'):continue
        lo,hi=a.FirstParameter(),a.LastParameter();pts=[xyz(a.Value(u)) for u in (lo,hi,(lo+hi)/2)]
        if np.ptp(np.array(pts)[:,2])>1e-6:continue
        g={'kind':'LINE' if kind=='GeomAbs_Line' else 'ARC','points':[p[:2] for p in pts[:2 if kind=='GeomAbs_Line' else 3]],
           'radius':None if kind=='GeomAbs_Line' else a.Circle().Radius()}
        edge_lookup[key(g)].append({'edge_id':i,'z_mm':pts[0][2]})
    boundary=[]
    for s in flat:
        matches=[e for e in edge_lookup[key(s)] if abs(e['z_mm']+.8)<1e-6]
        if matches:boundary.append({'source_id':s['source_id'],'matches':matches})
    if len(flat)!=14 or len(boundary)!=7 or any(len(r['matches'])!=1 for r in boundary):raise ValueError('Expected exact seven-part outer wall boundary')
    face_by={f['face_id']:f for f in asset['faces']}
    edgefaces=defaultdict(list)
    for f in asset['faces']:
        for eid in f['edge_ids']:edgefaces[eid].append(f['face_id'])
    wall_ids=[]
    for r in boundary:
        touching=edgefaces[r['matches'][0]['edge_id']]
        if 468 not in touching:raise ValueError('Boundary not attached to base sheet')
        others=[i for i in touching if i!=468]
        if len(others)!=1:raise ValueError('Unexpected wall adjacency')
        wall_ids.extend(others)
    wall_ids=sorted(set(wall_ids))
    if len(wall_ids)!=7:raise ValueError('Expected four planar and three cylindrical wall faces')
    wall_rows=[]
    base=BRepAdaptor_Surface(TopoDS.Face_s(faces.FindKey(468)),True).Plane()
    normal=np.array(xyz(base.Axis().Direction()))
    for i in wall_ids:
        a=BRepAdaptor_Surface(TopoDS.Face_s(faces.FindKey(i)),True);kind=str(a.GetType()).split('.')[-1]
        b=bounds(faces.FindKey(i));row={'face_id':i,'surface':kind,'bounds_mm':b,'wall_height_mm':b[5]-b[2]}
        if abs(row['wall_height_mm']-3)>1e-6:raise ValueError('Wall height changed')
        if kind=='GeomAbs_Plane':
            angle=math.degrees(math.acos(float(np.clip(abs(normal@np.array(xyz(a.Plane().Axis().Direction()))),-1,1))))
            if abs(angle-90)>1e-6:raise ValueError('Wall not perpendicular')
            row['base_wall_angle_deg']=angle
        elif kind=='GeomAbs_Cylinder':
            if abs(abs(normal@np.array(xyz(a.Cylinder().Axis().Direction())))-1)>1e-8:raise ValueError('Corner wall not vertical')
            row['path_radius_mm']=a.Cylinder().Radius()
        else:raise ValueError('Unexpected wall surface')
        wall_rows.append(row)
    # Traverse from the opposite base surface through its rounded root faces to the inner wall.
    root_rows=[];inner_wall_ids=set()
    for i in face_by[466]['neighbor_face_ids']:
        a=BRepAdaptor_Surface(TopoDS.Face_s(faces.FindKey(i)),True);kind=str(a.GetType()).split('.')[-1]
        if kind not in ('GeomAbs_Cylinder','GeomAbs_Torus'):continue
        b=bounds(faces.FindKey(i))
        if abs(b[2]+.8)>1e-6 or abs(b[5])>1e-6:continue
        downstream=[j for j in face_by[i]['neighbor_face_ids'] if j!=466 and abs(face_by[j]['bounds_mm'][2]+3.8)<1e-6 and abs(face_by[j]['bounds_mm'][5]+.8)<1e-6 and 452 in face_by[j]['neighbor_face_ids']]
        if not downstream:continue
        radius=a.Cylinder().Radius() if kind=='GeomAbs_Cylinder' else a.Torus().MinorRadius()
        sweep=math.degrees((a.LastUParameter()-a.FirstUParameter()) if kind=='GeomAbs_Cylinder' else (a.LastVParameter()-a.FirstVParameter()))
        if abs(radius-.8)>1e-6 or abs(sweep-90)>1e-6:raise ValueError('Root bend geometry changed')
        root_rows.append({'face_id':i,'surface':kind,'round_radius_mm':radius,'cross_section_turn_deg':sweep,'inner_wall_faces':downstream})
        inner_wall_ids.update(downstream)
    if len(root_rows)!=7 or len(inner_wall_ids)!=7:raise ValueError('Expected complete seven-part curved root and inner wall')
    result={'part_id':'P002','operation_id':'O069','status':'CURVED_BEND_90_VERIFIED','step_sha256':sha256(source),
            'annotation_revision':project['revision'],'independent_anchors':anchors,'outer_boundary_exact_matches':boundary,
            'wall_faces':wall_rows,'inner_wall_faces':sorted(inner_wall_ids),'root_faces':root_rows,
            'sheet_top_z_mm':0,'sheet_bottom_z_mm':-.8,'wall_end_z_mm':-3.8,'sheet_thickness_mm':.8,
            'straight_wall_height_mm':3,'total_depth_from_sheet_top_mm':3.8,'turn_deg':90,
            'interpretation':'沿四直线三圆弧路径连续向STEP负Z折出的立边；根部四圆柱面加三环面均90度过渡。不是可直接去料的平面异形孔。',
            'angle_note':'路径拐角的90度与横截面折弯90度独立核验，不混同。',
            'surface_side_policy':'STEP负Z仅表示模型方向，不推断光面/毛面；保留原NOT_APPLICABLE，与当前其它曲线折弯一致。',
            'limitation':'确认局部成品几何和动作分类；不证明成形轨迹、展开补偿或工具可行性，也未实现曲线路径逐序实体成形。'}
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'measurement.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    plot(flat,asset,wall_ids+sorted(inner_wall_ids)+[r['face_id'] for r in root_rows]+[452])
    page='''<!doctype html><meta charset="utf-8"><title>P002 O069 STEP对应</title><style>body{font:17px system-ui;max-width:1100px;margin:30px auto;color:#24384a;background:#f5f7fa}img{width:100%}aside{background:#fff0d6;padding:16px}</style><h1>P002 · O069：90°曲线折弯</h1><p>6个独立圆孔锚点定位，二维外侧7段与STEP板面交界逐段精确对应。轮廓为4条直线＋3段圆弧；STEP保留连续立边和弯曲根部，不能按平面冲孔去料。</p><img src="comparison.png"><p>板厚0.8mm；直壁高3mm；相对主板上表面总深3.8mm。4段直壁与主板垂直；根部4个圆柱面＋3个环面的横截面转角均90°、圆角半径0.8mm。</p><aside>确认90°曲线折弯，专家工序保持OP80；STEP负Z不是光面/毛面的判据。此次完成局部对应及分类，尚未接入该曲线折弯的逐序实体成形。</aside><p><a href="measurement.json">完整STEP几何证据</a></p>'''
    (OUT/'review.html').write_text(page,encoding='utf-8')
    if apply:
        if project['revision']!=14 or op['operation_type']!='IRREGULAR_PUNCH':raise ValueError('Expected rev14 original classification')
        updated=deepcopy(project);target=next(o for o in updated['operations'] if o['operation_id']=='O069')
        target.update(operation_type='BEND',feature_type='BEND_CURVED',geometry_shape='CURVED',bend_angle_deg=90,name='曲线折弯4')
        updated.pop('geometry_batch',None)
        updated.setdefault('user_decisions',[]).append({'id':'P002_O069_CURVED_BEND_90_20261113',
            'quote':'O069规则抽孔看起来像曲线折弯，应当是90度；根据STEP找到对应关系。',
            'old_name':op['name'],'old_type':op['operation_type'],'new_name':target['name'],'new_type':'BEND',
            'new_feature_type':'BEND_CURVED','bend_angle_deg':90,'expert_station':'OP80',
            'source_segment_ids_preserved':op['source_segment_ids'],'step_sha256':result['step_sha256'],
            'measurement_file':'outputs/p002_o069_step_v01/measurement.json','measurement_sha256':sha256(OUT/'measurement.json'),
            'surface_side_policy':result['surface_side_policy']})
        report=validate_project(updated,drawing,annotation_only=True)
        if report['errors'] or report['process_config']['reference_issues']:raise ValueError(json.dumps({'errors':report['errors'],'reference_issues':report['process_config']['reference_issues']},ensure_ascii=False))
        for name,value in [('before_project',project),('validation',report)]:
            (OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        saved=store.save(updated,project['revision'])['project']
        print('Saved revision',saved['revision'])
    print(json.dumps({'anchors':len(anchors),'exact_boundary_parts':len(boundary),'wall_faces':wall_ids,'root_faces':[r['face_id'] for r in root_rows],'turn_deg':90},ensure_ascii=False))


def plot(flat,asset,local_ids):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    plt.rcParams['font.sans-serif']=['Microsoft YaHei','SimHei','DejaVu Sans'];plt.rcParams['axes.unicode_minus']=False
    fig=plt.figure(figsize=(13,6),layout='constrained');ax=fig.add_subplot(121);bx=fig.add_subplot(122,projection='3d')
    for s in flat:
        pts=np.array(s['points'])
        if s['kind']=='ARC':
            # Circumcircle through endpoint/midpoint; select the arc passing through the stored midpoint.
            a,b,c=pts;center=np.linalg.solve(2*np.array([b-a,c-a]),np.array([b@b-a@a,c@c-a@a]));angs=np.arctan2(pts[:,1]-center[1],pts[:,0]-center[0]);sweep=(angs[1]-angs[0])%(2*np.pi)
            if (angs[2]-angs[0])%(2*np.pi)>sweep:sweep-=2*np.pi
            theta=angs[0]+np.linspace(0,sweep,50);pts=center+s['radius']*np.array([np.cos(theta),np.sin(theta)]).T
        ax.plot(pts[:,0],pts[:,1],color='#2586b7',lw=1)
    ax.set(aspect='equal',title='二维绑定：沿曲线路径的两条线',xlabel='STEP配准 X / mm',ylabel='Y / mm');ax.grid(alpha=.15)
    triangles=np.array(asset['mesh']['positions']).reshape(-1,3,3);ids=np.array(asset['mesh']['triangle_face_ids'])
    selected=triangles[np.isin(ids,local_ids)]
    bx.add_collection3d(Poly3DCollection(selected,facecolors='#91c7df',edgecolors='none',alpha=1))
    # Place the local model in an honest equal-unit aspect; exaggeration would misstate its height.
    pts=selected.reshape(-1,3);lo=pts.min(axis=0);hi=pts.max(axis=0)
    bx.set(xlim=(lo[0]-3,hi[0]+3),ylim=(lo[1]-3,hi[1]+3),zlim=(-4.3,.5),xlabel='X / mm',ylabel='Y / mm',zlabel='Z / mm',title='STEP实际立边及弯曲根部（局部抽出）')
    bx.set_box_aspect((hi[0]-lo[0]+6,hi[1]-lo[1]+6,4.8));bx.view_init(elev=25,azim=-65)
    bx.set_zticks([])
    bx.text2D(.03,.1,'等比例显示：直壁高 3 mm，板厚 0.8 mm',transform=bx.transAxes,fontsize=11)
    fig.savefig(OUT/'comparison.png',dpi=160);plt.close(fig)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');run(parser.parse_args().apply)
