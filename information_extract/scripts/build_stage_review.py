"""Plan-driven stage inspection; schematic panels and action wires, NOT formed B-reps."""
from pathlib import Path
import sys
import json
import hashlib
from collections import Counter
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / 'information_extract'
sys.path.insert(0, str(ROOT))
from information_extract.three_d.local_motion import poses
from information_extract.three_d.artifacts import portable
from information_extract.three_d.state import transform_point
from information_extract.three_d.assets import indexed, xyz
from src.visualization.outline_adapter import load_display_outline, flatten_shape
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopoDS import TopoDS
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRep import BRep_Tool
from OCP.TopLoc import TopLoc_Location


def read(p):
    return json.loads(p.read_text(encoding='utf-8'))


def write(p, value):
    p.write_text(json.dumps(portable(value), ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')


def polygon_distance(point, polygon):
    """Zero inside, otherwise distance to the sampled outer boundary."""
    p = np.asarray(point); poly = np.asarray(polygon)
    a, b = poly, np.roll(poly, -1, axis=0)
    v = b-a; denom = (v*v).sum(axis=1)
    t = np.clip(((p-a)*v).sum(axis=1)/np.maximum(denom, 1e-20), 0, 1)
    distance = float(np.linalg.norm(p-a-t[:, None]*v, axis=1).min())
    inside = False
    for x, y in zip(a, b):
        if (x[1] > p[1]) != (y[1] > p[1]):
            if p[0] < (y[0]-x[0])*(p[1]-x[1])/(y[1]-x[1])+x[0]:
                inside = not inside
    return 0.0 if inside else distance


def support(face, cfg):
    # Remove ALL inner wires. No pre-existing final holes leak into early stages.
    # The outer contour is still final-CAD support, NOT a recovered original blank.
    outer = BRepTools.OuterWire_s(face)
    explorer = BRepTools_WireExplorer(outer)
    boundary = []
    while explorer.More():
        edge = explorer.Current(); c = BRepAdaptor_Curve(edge)
        n = 2 if 'Line' in str(c.GetType()) else cfg['boundary_curve_samples']
        ts = np.linspace(c.FirstParameter(), c.LastParameter(), n)
        if edge.Orientation() == TopAbs_REVERSED:
            ts = ts[::-1]
        boundary.extend(xyz(c.Value(float(t))) for t in ts[:-1])
        explorer.Next()
    made = BRepBuilderAPI_MakeFace(outer, True)
    if not made.IsDone():
        raise ValueError('Cannot build planar outer-wire support')
    filled = made.Face()
    mesher = BRepMesh_IncrementalMesh(filled, cfg['mesh_linear_deflection_mm'], False, cfg['mesh_angular_deflection_rad'], True)
    if not mesher.IsDone():
        raise ValueError('Support meshing failed')
    loc = TopLoc_Location(); mesh = BRep_Tool.Triangulation_s(filled, loc)
    if mesh is None:
        raise ValueError('Support mesh missing')
    triangles = [[xyz(mesh.Node(i).Transformed(loc.Transformation())) for i in mesh.Triangle(j).Get()]
                 for j in range(1, mesh.NbTriangles()+1)]
    return boundary, triangles


def main():
    cfg = read(MODULE/'configs/P001_stage_review_v01.json')
    cadcfg = read(MODULE/'configs/three_d_state_v01.json')
    source = MODULE/'outputs/three_d_state_v01/P001'
    facts, asset, registration = [read(source/(x+'.json')) for x in ['facts', 'asset', 'registration']]
    step = MODULE/cadcfg['step']
    if hashlib.sha256(step.read_bytes()).hexdigest() != asset['asset_id']:
        raise ValueError('STEP asset mismatch')
    material = MODULE/'outputs/material_state_v01/P001'
    skeleton = read(material/'kinematic_proposals.json')
    matches = read(material/'bend_associations.json')['matches']
    skeleton['joint_to_operation'] = {m['joint_id']: m['operation_id'] for m in matches}
    if len(skeleton['joint_to_operation']) != len(skeleton['joints']):
        raise ValueError('Full joint assignment not available')
    flat = poses(skeleton, {j['joint_id']: 0 for j in skeleton['joints']})
    reader = STEPControl_Reader()
    if reader.ReadFile(str(step)) != IFSelect_RetDone or not reader.TransferRoots():
        raise ValueError('STEP load failed')
    face_map = indexed(reader.OneShape(), TopAbs_FACE)
    if face_map.Extent() != asset['face_count']:
        raise ValueError('STEP topology mismatch')
    panels = []
    for r in skeleton['regions']:
        boundary, triangles = support(TopoDS.Face_s(face_map.FindKey(r['front_face_id'])), cfg)
        flat_boundary = [transform_point(flat[r['region_id']], p) for p in boundary]
        panels.append({'region_id': r['region_id'], 'boundary': boundary+[boundary[0]], 'triangles': triangles,
                       'flat_boundary': flat_boundary, 'flat_z': float(np.mean(np.array(flat_boundary)[:, 2])),
                       'interpretation': 'Outer-wire-only reference support; final cutouts retained; not blank.'})
    m, t = np.array(registration['matrix']), np.array(registration['translation_mm'])
    # Reuse the audited display boundary, never recover a blank from the folded skins.
    outline_config = read(ROOT/'configs/m3_visualization.yaml')
    outline, outline_audit = load_display_outline(outline_config, ROOT, 'P001')
    if not outline or outline_audit['status'] != 'available':
        raise ValueError('Display outline unavailable: '+str(outline_audit))
    outline_paths = []
    for loop in outline['loops']:
        points = [p for shape in loop['shapes'] for p in flatten_shape(shape, outline_config['outline']['containment_flatten_tolerance'])[:-1]]
        # Stored outline uses SVG (x,-y); restore material XY, then apply the same
        # registration as operation paths. This layer has no operation/station ID.
        path = [(m@np.array([p[0], -p[1]])+t).tolist() for p in points]
        outline_paths.append(path+[path[0]])
    bend_map = {v['operation_id']: v for v in matches}
    joints = {j['joint_id']: j for j in skeleton['joints']}
    operations = []
    for op in facts['operations']:
        paths = [[(m@np.array(p)+t).tolist() for p in path] for path in op['paths']]
        row = {**op, 'flat_paths': paths, 'region_id': None, 'formed_paths': [], 'mapping_status': 'MISSING_2D' if not paths else 'UNRESOLVED'}
        if op['operation_id'] in bend_map:
            j = joints[bend_map[op['operation_id']]['joint_id']]
            row.update(region_id=j['parent_region_id'], mapping_status='BEND_AXIS_PROPOSAL', joint_id=j['joint_id'],
                       formed_paths=[[transform_point(np.linalg.inv(flat[j['parent_region_id']]), p) for p in e['endpoints']]
                                     for e in j['developed_axis_segment_evidence_mm']])
        elif paths:
            points = [p for path in paths for p in path]
            candidates = []
            for panel in panels:
                poly = np.array(panel['flat_boundary'])[:, :2]
                low, high = poly.min(axis=0)-cfg['panel_membership_search_tolerance_mm'], poly.max(axis=0)+cfg['panel_membership_search_tolerance_mm']
                if any(np.any(np.array(p)<low) or np.any(np.array(p)>high) for p in points):
                    continue
                error = max(polygon_distance(p, poly) for p in points)
                if error <= cfg['panel_membership_search_tolerance_mm']:
                    candidates.append((panel, error))
            row['region_candidates'] = [{'region_id': p['region_id'], 'max_outside_distance_mm': e} for p, e in candidates]
            if len(candidates) == 1:
                panel, error = candidates[0]; rid = panel['region_id']
                inv = np.linalg.inv(flat[rid])
                row.update(region_id=rid, mapping_status='CONTAINMENT_PROPOSAL', max_outside_distance_mm=error,
                           formed_paths=[[transform_point(inv, [*p, panel['flat_z']]) for p in path] for path in paths])
        operations.append(row)
    plans_path = MODULE/cfg['plans']; plans = read(plans_path)['plans']
    ids = {o['operation_id'] for o in operations}
    stations = sorted(facts['stations'], key=lambda s:s['order'])
    station_map = {s['station_id']: s for s in stations}
    for plan in plans:
        if set(plan['assignments']) != ids:
            raise ValueError('Incomplete/extra plan operations')
        for op in operations:
            s = station_map[plan['assignments'][op['operation_id']]]
            if op['operation_type'] not in s['allowed_operation_types'].split(','):
                raise ValueError('Capability violation')
    data = {'part_id':'P001', 'operations':operations, 'stations':stations, 'plans':plans, 'skeleton':skeleton, 'panels':panels,
            'display_outline':{'paths':outline_paths,'audit':outline_audit,'display_only':True,
                               'registration_matrix':m.tolist(),'registration_translation_mm':t.tolist()},
            'config':cfg, 'asset_id':asset['asset_id'], 'facts_fingerprint':facts['facts_fingerprint'],
            'plans_sha256':hashlib.sha256(plans_path.read_bytes()).hexdigest(),
            'summary':dict(Counter(o['mapping_status'] for o in operations)),
            'physical_simulation':False, 'collision_checked':False, 'is_blank':False, 'sheet5_used':False}
    out=MODULE/'outputs/stage_review_v01/P001';out.mkdir(parents=True,exist_ok=True)
    write(out/'stage_data.json',data)
    orders={s['station_id']:s['order'] for s in stations}
    audit=[]
    for plan in plans:
        rows=[]
        for station in stations:
            active=[o for o in operations if orders[plan['assignments'][o['operation_id']]]<=station['order']]
            rows.append({'station_id':station['station_id'],
                         'new':sum(plan['assignments'][o['operation_id']]==station['station_id'] for o in operations),
                         'cumulative':len(active), 'wire_3d':sum(o['region_id'] is not None for o in active),
                         'unmapped':[o['operation_id'] for o in active if o['region_id'] is None]})
        audit.append({'plan_id':plan['plan_id'],'stages':rows})
    write(out/'stage_coverage.json',audit)
    unresolved=[o['operation_id'] for o in operations if o['mapping_status']=='UNRESOLVED']
    missing=[o['operation_id'] for o in operations if o['mapping_status']=='MISSING_2D']
    report=f'''# P001 按工序检视 v0.1

入口：[review.html](review.html)。单 HTML 可离线打开；成品检视和局部模型的相对链接需保留相邻输出目录。

## 已实现

从原有完整分配文件读取 16 个候选，保留 ID、顺序和分配；每个方案呈现 OP10—OP70 的 7 张完成态示意，共 112 个阶段。动作按照 Station.order，在所属工序及以后累计显示。没有同序动作内部执行顺序；59 个折弯采用统一的本序完成参数。空工序仍保留上一序状态。

点击清单、二维或三维线框可同步选中；只允许点选当前已执行动作。支持筛选、缩放、平移、旋转、聚焦、方案切换、单序 PNG 导出。后序动作不会加入绘制及点击命中列表。清单“仅本序”筛选不删除模型中此前已完成的动作。

二维板料轮廓为默认开启的灰色背景层，复用 information_extract/sources/P001/legacy_outline.html 的既有可审计轮廓解析器。恢复 SVG 的 Y 轴后，使用与动作相同的配准矩阵映射。轮廓位于动作下方，不参与点击、动作计数或工序显示判定；各序均显示作定位参考，并不表示本序已完成切边，也不是据此重建的真实初始毛坯。保留原来源的多个闭合轮廓，不造边、不做物理合并。来源与解析统计见 stage_data.json 的 display_outline.audit。

## 几何实现及覆盖

使用既有 CAD 材料连接树的 60 个板面。所有内孔从参考板面中填平，动作轮廓按分配另行出现。板面由外轮廓构成，外部切口仍属于成品外边界，因此参考板面不是原始毛坯，也不代表逐序切边实体。

全部 224 个动作中，{data['summary'].get('BEND_AXIS_PROPOSAL',0)} 个折弯显示 CAD 轴线随动建议，{data['summary'].get('CONTAINMENT_PROPOSAL',0)} 个动作有唯一板面几何包含建议，共 {sum(o['region_id'] is not None for o in operations)} 个可显示三维线框。新的包含建议没有写回已确认的二维—三维绑定。

关联方法：在零宽逆折叠得到的参考平面上，比较动作的全部已采样轮廓点与各板面的外边界；只有唯一候选才采用。配置允许 {cfg['panel_membership_search_tolerance_mm']} mm 的边界外搜索距离，用于容纳既有零宽展开误差，**不是定位精度、工艺间距或确认阈值**。不使用 CAD 父动作或 Sheet5 强制消除空间歧义，不把没有匹配解释为没有风险。此方法只提供位置建议。

- 有二维但三维待关联（{len(unresolved)}）：{'、'.join(unresolved)}。
- 缺二维（{len(missing)}）：{'、'.join(missing)}。
- 每个方案/工序的新增、累计、三维覆盖详见 stage_coverage.json；逐动作依据见 stage_data.json。

## 尚未完成的真实实体建模

全件使用既有零宽转轴近似，不将 O009/O010 局部有限宽参数推广至其他折弯。因此它是阶段几何示意，不能视为有限宽全件仿真；最终角度闭合也不是独立物理验证。

冲孔/预孔只显示二维来源轮廓，并未在板面上做布尔切孔；翻孔、凸米、折桥等尚未生成局部高度和曲面。冲孔与翻孔即使同位置，仍是分别按工序出现的两个动作。未建立完整原始毛坯、冲切材料去除、过渡带、塑性变形、工具与装夹、碰撞检测。未覆盖的动作不能完成三维高亮。

局部有限宽页面保留为固定示例，链接文字明确说明不随当前方案切换。没有把局部示例冒充当前候选的计算结果。

## 来源与边界

- 方案：{plans_path.resolve()}，SHA256 `{data['plans_sha256']}`。
- 成品 STEP SHA256 `{data['asset_id']}`，构建前与原资产校验。
- Sheet5 不参与构建；不读取专家分配；不改变 M1/M2/M3 业务规则及候选结果。
- 配置入口：information_extract/configs/P001_stage_review_v01.json。
- 构建入口：information_extract/scripts/build_stage_review.py。
- 本版所有开发文件位于 information_extract 子目录。

## 验证入口

test_stage_review.cjs 检查全部 112 个阶段，包含累计可见性、后序隔离、Station.order、同步折弯、有限坐标等；test_stage_geometry.py 检查填平内孔后的面积、歧义不强制关联、来源和范围；test_stage_review_browser.mjs 验证实际点击、方案/工序切换、缩放与 PNG 导出。浏览器结果见 browser_qa.json。

首个方案的 OP10.png—OP70.png 为逐序预览；O009_selected.png 为联动选中示例。
'''
    (out/'REPORT.md').write_text(report,encoding='utf-8')
    web=MODULE/'three_d/web';html=(web/'stage_review.html').read_text(encoding='utf-8')
    for marker,value in [('DATA',json.dumps(data,ensure_ascii=False,allow_nan=False).replace('<','\\u003c')),
                         ('CORE',(web/'core.js').read_text(encoding='utf-8')),
                         ('MOTION',(web/'motion_core.js').read_text(encoding='utf-8')),
                         ('STAGE',(web/'stage_core.js').read_text(encoding='utf-8')),
                         ('APP',(web/'stage_review.js').read_text(encoding='utf-8'))]:
        html=html.replace('/*__'+marker+'__*/',value)
    (out/'review.html').write_text(html,encoding='utf-8')
    print(json.dumps({'output':str(out),'coverage':data['summary'],'plan_count':len(plans)},ensure_ascii=False))


if __name__=='__main__':main()
