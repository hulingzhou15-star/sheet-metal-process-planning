"""Build independent material proposals + a plan-driven local motion example."""
from pathlib import Path
import sys
import json
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from information_extract.three_d.material import infer_material
from information_extract.three_d.local_motion import oriented_normals,build_skeleton,associate_bends,poses,local_schedule
from information_extract.three_d.state import transform_point
from information_extract.three_d.facts import sha256,fingerprint

MODULE=ROOT/'information_extract'


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def clip_triangle(triangle,low,high):
    """Clip actual triangle geometry to a display crop, not a new material edge."""
    polygon=[np.array(p) for p in triangle]
    for axis in range(3):
        for limit,sign in [(low[axis],1),(high[axis],-1)]:
            clipped=[]
            for i,a in enumerate(polygon):
                b=polygon[(i+1)%len(polygon)];inside_a=sign*(a[axis]-limit)>=0;inside_b=sign*(b[axis]-limit)>=0
                if inside_a:clipped.append(a)
                if inside_a!=inside_b:clipped.append(a+(b-a)*(limit-a[axis])/(b[axis]-a[axis]))
            polygon=clipped
            if not polygon:return []
    return [np.array([polygon[0],polygon[i],polygon[i+1]]).tolist() for i in range(1,len(polygon)-1)]


def face_triangles(asset,face_ids):
    positions=np.asarray(asset['mesh']['positions']).reshape(-1,3,3)
    return np.concatenate([positions[f['triangle_start']:f['triangle_start']+f['triangle_count']]
                           for f in asset['faces'] if f['face_id'] in face_ids]).tolist()


def choose_example(skeleton,associations):
    match={r['joint_id']:r for r in associations['matches']}
    by_parent={}
    for j in skeleton['joints']:by_parent.setdefault(j['parent_region_id'],[]).append(j)
    chains=[]
    for first in by_parent.get(skeleton['root_region_id'],[]):
        children=by_parent.get(first['child_region_id'],[])
        if len(children)!=1:continue
        second=children[0]
        if second['child_region_id'] in by_parent or first['joint_id'] not in match or second['joint_id'] not in match:continue
        chains.append((match[first['joint_id']]['operation_id'],first,second))
    if not chains:raise ValueError('No complete two-hinge geometric proposal; no guessed example created')
    _,first,second=min(chains,key=lambda row:row[0])
    ids=[skeleton['root_region_id'],first['child_region_id'],second['child_region_id']]
    return {'root_region_id':ids[0],'region_order':ids,'regions':[r for r in skeleton['regions'] if r['region_id'] in ids],
            'joints':[first,second],'joint_to_operation':{j['joint_id']:match[j['joint_id']]['operation_id'] for j in [first,second]},
            'association_evidence':[match[j['joint_id']] for j in [first,second]]}


def attached_feature(example,asset,facts,registration,config):
    terminal=example['regions'][-1]
    flat=poses(example,{j['joint_id']:0 for j in example['joints']})[terminal['region_id']]
    circles=[c for c in asset['circles'] if terminal['front_face_id'] in c['face_ids'] and abs(c['parameter_span_rad']-2*np.pi)<1e-5]
    choices=[]
    for c in circles:
        reference=transform_point(flat,c['center_mm'])
        for op in facts['operations']:
            if op['operation_type']!='FLANGING' or not op['process_group_id'] or not op['segments']:continue
            segments=op['segments']
            if any(s['segment_type']!='CIRCLE' for s in segments):continue
            s=segments[0];target=np.array(registration['matrix'])@[s['center_x'],s['center_y']]+registration['translation_mm']
            residual=float(np.linalg.norm(target-np.array(reference[:2])))
            if residual>config['local_association']['feature_search_radius_mm']:continue
            group=[o for o in facts['operations'] if o['process_group_id']==op['process_group_id']]
            related=[e for e in asset['circles'] if np.linalg.norm(np.array(e['center_mm'][:2])-c['center_mm'][:2])<config['plane_pairing']['distance_tolerance_mm']]
            choices.append({'region_id':terminal['region_id'],'process_group_id':op['process_group_id'],
                            'operation_ids':[o['operation_id'] for o in group],
                            'source_center_xy_mm':target.tolist(),'formed_surface_center_mm':c['center_mm'],
                            'developed_surface_center_mm':reference,'center_residual_mm':residual,
                            'source_flange_radii_mm':[s['radius'] for s in segments],
                            'cad_observed_radii_mm':sorted({round(e['radius_mm'],8) for e in related}),
                            'cad_edge_id':c['edge_id'],'status':'LOCATION_PROPOSAL_DIMENSIONS_UNCONFIRMED'})
            choices[-1]['cad_front_edge_radius_mm']=c['radius_mm']
    choices.sort(key=lambda c:c['center_residual_mm'])
    if len(choices)>1 and choices[1]['center_residual_mm']-choices[0]['center_residual_mm']<config['local_association']['minimum_first_vs_second_margin_mm']:
        return None
    return choices[0] if choices else None


def main():
    config=json.loads((MODULE/'configs/material_state_v01.json').read_text(encoding='utf-8'))
    source=MODULE/config['source_pack'];out=MODULE/config['output'];out.mkdir(parents=True,exist_ok=True)
    asset=json.loads((source/'asset.json').read_text(encoding='utf-8'))
    facts=json.loads((source/'facts.json').read_text(encoding='utf-8'))
    registration=json.loads((source/'registration.json').read_text(encoding='utf-8'))
    source_config=json.loads((MODULE/'configs/three_d_state_v01.json').read_text(encoding='utf-8'))
    step=MODULE/source_config['step']
    if sha256(step)!=asset['asset_id']:raise ValueError('Source STEP version mismatch')
    material=infer_material(asset,config)
    normals=oriented_normals(step,asset)
    skeleton=build_skeleton(material,asset,registration,normals)
    associations=associate_bends(skeleton,facts,registration,config)
    # Independent semantic cross-check AFTER geometry-only matching. These
    # relations never enter pairing, graph construction or match selection.
    operations={o['operation_id']:o for o in facts['operations']}
    joint_operations={m['joint_id']:m['operation_id'] for m in associations['matches']}
    joint_by_child={j['child_region_id']:j for j in skeleton['joints']}
    crosscheck=[]
    for j in skeleton['joints']:
        operation=joint_operations.get(j['joint_id'])
        if not operation:continue
        cad_parent=operations[operation]['cad_parent_operation_id']
        if not cad_parent or operations[cad_parent]['operation_type']!='BEND':continue
        parent_joint=joint_by_child.get(j['parent_region_id'])
        geometric_parent=joint_operations.get(parent_joint['joint_id']) if parent_joint else None
        crosscheck.append({'operation_id':operation,'cad_parent_operation_id':cad_parent,
                           'geometry_parent_operation_id':geometric_parent,'consistent':cad_parent==geometric_parent})
    write(out/'semantic_crosscheck.json',{'stage':'POST_ASSOCIATION_ONLY_NO_SELECTION_FEEDBACK',
                'comparable_relation_count':len(crosscheck),'consistent_count':sum(r['consistent'] for r in crosscheck),'relations':crosscheck})
    example=choose_example(skeleton,associations)
    feature=attached_feature(example,asset,facts,registration,config)
    material['source_asset_id']=asset['asset_id'];material['source_topology_fingerprint']=asset['topology_fingerprint']
    write(out/'material_proposals.json',material);write(out/'kinematic_proposals.json',skeleton);write(out/'bend_associations.json',associations)
    write(out/'local_example.json',{'skeleton':example,'feature':feature})
    print('Material',len(material['regions']),'regions',len(material['joints']),'joints; associations',len(associations['matches']),flush=True)
    # A local test fixture chosen from station capability, not expert columns.
    bend_stations=[s for s in sorted(facts['stations'],key=lambda s:s['order']) if 'BEND' in s['allowed_operation_types'].split(',')]
    if len(bend_stations)<2:raise ValueError('Two bend-capable stations required for this local test fixture')
    operation_ids=[example['joint_to_operation'][j['joint_id']] for j in example['joints']]
    assignment={operation_ids[1]:bend_stations[0]['station_id'],operation_ids[0]:bend_stations[1]['station_id']}
    orders={s['station_id']:s['order'] for s in facts['stations']}
    snapshots=[local_schedule(example,example['joint_to_operation'],assignment,orders,station,tau)
               for station in assignment.values() for tau in [0,.5,1]]
    write(out/'local_schedule_example.json',{'scope':'LOCAL_TEST_FIXTURE_NOT_COMPLETE_CANDIDATE','assignment':assignment,'snapshots':snapshots})
    flat=poses(skeleton,{j['joint_id']:0 for j in skeleton['joints']})
    flat_z=[transform_point(flat[r['region_id']],r['midplane']['point_mm'])[2] for r in skeleton['regions']]
    root=example['regions'][0];moving=example['regions'][1:]
    points=[asset['faces'][i-1]['bounds_mm'] for r in moving for i in r['skin_face_ids']]
    low=np.min(np.array([b[:3] for b in points]),axis=0)-config['display']['base_crop_margin_mm']
    high=np.max(np.array([b[3:] for b in points]),axis=0)+config['display']['base_crop_margin_mm']
    base_triangles=[t for triangle in face_triangles(asset,root['skin_face_ids']) for t in clip_triangle(triangle,low,high)]
    surfaces=[{'region_id':r['region_id'],'face_ids':r['skin_face_ids'],
               'triangles':base_triangles if i==0 else face_triangles(asset,r['skin_face_ids']),
               'front_edges':[] if i==0 else [asset['edges'][e-1]['points'] for e in asset['faces'][r['front_face_id']-1]['edge_ids']]}
              for i,r in enumerate(example['regions'])]
    summary={'schema_version':1,'source_asset_id':asset['asset_id'],'facts_fingerprint':facts['facts_fingerprint'],
             'panel_proposal_count':len(material['regions']),'joint_proposal_count':len(material['joints']),
             'kinematic_region_count':len(skeleton['regions']),
             'unconnected_region_proposal_ids':skeleton['unconnected_region_proposal_ids'],
             'bend_operation_count':sum(o['operation_type']=='BEND' for o in facts['operations']),
             'unique_bend_proposal_count':len(associations['matches']),
             'quantitative_bend_matches':sum(m['quantitative_match'] for m in associations['matches']),
             'semantic_crosscheck_count':len(crosscheck),'semantic_consistent_count':sum(r['consistent'] for r in crosscheck),
             'unmatched_bends':associations['unmatched_bend_operation_ids'],
             'flat_midplane_z_spread_mm':max(flat_z)-min(flat_z),
             'thickness_estimate_mm':material['thickness_estimate']['value_mm'],
             'example_operations':operation_ids,'feature':feature,
             'full_plan_simulation_ready':False,'collision_checked':False,
             'limitations':['Geometric correspondences remain proposals; unfolded offsets uncalibrated.',
                            'Panel skins only; transition/edge faces are not a closed material solid.',
                            'Final mesh holes reused; no punching/flanging formation events simulated.',
                            'Initial blank, bend allowance, process trajectories, tools and global process state remain unknown.']}
    region_depth={r['region_id']:r['depth'] for r in skeleton['regions']}
    joint_depth={j['joint_id']:region_depth[j['child_region_id']] for j in skeleton['joints']}
    summary['unfolded_residuals_by_depth']={str(depth):{'count':len(values),'minimum_mm':min(values),
             'maximum_mm':max(values),'mean_mm':float(np.mean(values))}
             for depth in sorted(set(joint_depth.values()))
             for values in [[m['endpoint_residual_mm'] for m in associations['matches'] if joint_depth[m['joint_id']]==depth]] if values}
    write(out/'summary.json',summary)
    payload={'summary':summary,'example':example,'feature':feature,'surfaces':surfaces,'default_assignment':assignment,
             'stations':facts['stations'],'local_operations':[o for o in facts['operations'] if o['operation_id'] in operation_ids],
             'config':config,'scope':'LOCAL_KINEMATIC_EXPERIMENT_NOT_FULL_PART_PROCESS'}
    write(out/'viewer_data.json',payload)
    web=MODULE/'three_d/web'
    html=(web/'material_review.html').read_text(encoding='utf-8')
    for marker,value in [('/*__DATA__*/',json.dumps(payload,ensure_ascii=False,allow_nan=False).replace('<','\\u003c')),
                          ('/*__CORE__*/',(web/'core.js').read_text(encoding='utf-8')),
                          ('/*__MOTION__*/',(web/'motion_core.js').read_text(encoding='utf-8')),
                          ('/*__RENDERER__*/',(web/'motion_renderer.js').read_text(encoding='utf-8')),
                          ('/*__APP__*/',(web/'material_review.js').read_text(encoding='utf-8'))]:html=html.replace(marker,value)
    (out/'material_review.html').write_text(html,encoding='utf-8')
    residuals=[r['endpoint_residual_mm'] for r in associations['matches']]
    lines=['# P001 材料关联与局部工序回放','','## 实际完成','',
           f'- 从 CAD 几何得到 {len(material["regions"])} 组板面配对建议和 {len(material["joints"])} 个连接建议；其中 {len(skeleton["regions"])} 组位于基准连接树。',
           f'- 另有 {len(skeleton["unconnected_region_proposal_ids"])} 组板面没有当前支持的圆柱铰链连接，保留未知；不表示材料真实脱离。',
           '- 配对使用平行、间距、投影范围及面积证据；连接来自共同圆柱面和面邻接，不使用 CAD 动作父子关系。',
           f'- 几何厚度估计 {summary["thickness_estimate_mm"]:.3f} mm，{len(material["thickness_estimate"]["large_plane_support"])} 组大板面支持；不等同于已确认的原材料规格。',
           '- 区域表示为两侧平面表皮，未完成厚度边界与过渡带的实体分区。',
           f'- 59 个折弯中，{len(associations["matches"])} 个有互为唯一最佳的几何对应建议。',
           f'- 展开线端点残差范围 {min(residuals):.6f}–{max(residuals):.6f} mm，中位数 {float(np.median(residuals)):.6f} mm。',
           f'- 在配置的 {config["local_association"]["quantitative_match_tolerance_mm"]} mm 定量匹配容差下，通过数为 {summary["quantitative_bend_matches"]}；没有将搜索门限当成确认门限。',
           '- 尚未对应的折弯：'+(', '.join(summary['unmatched_bends']) or '无（仅指有唯一建议，不等于定量验证通过）'),
           f'- 关联完成后独立比较 CAD 折弯父子关系：{sum(r["consistent"] for r in crosscheck)}/{len(crosscheck)} 一致。语义关系未反馈到匹配选择，也没有生成新工艺边。',
           '- 同一折弯的多段圆柱面已共同用于轴向范围，保留每段原始区间，不把断开的过渡带补成连续材料。',
           '- 原有工艺硬约束、软偏好和候选池均未修改。','',
           '## 局部案例与分配驱动','',
           f'- 示例由几何树上已匹配的两连接叶分支选出：{operation_ids[0]} → 连接板面，{operation_ids[1]} → 末端板面；对应关系仍为未确认建议。',
           '- 默认局部测试分配：'+', '.join(f'{op} → {station}' for op,station in assignment.items())+'。工序取自合法 BEND capability；不是完整候选，也未读取专家分配。',
           '- 可导入包含 assignments 的完整方案 JSON，页面只提取并计算这两个折弯。',
           '- 同序参数先共同求值，再按材料连接组合位姿；未按动作编号串行求解。',
           '- 局部输入检查包含合法 Station/capability 与这两个动作已存在的 child → parent 顺序，不宣称完成 H1–H7 全方案检查。',
           '- O011/O012 的加工组用于保持两个动作身份；通过末端板面圆边与展开位置得到附属位置建议。',
           '- 它们的位置会随末端及父板面运动，不是固定的世界坐标点。',
           '- 成品孔与表面网格被复用为几何参照；尚未实现冲孔/翻孔自己的事件或变形。','',
           '## 需要保留的差异','',
           '| 对象 | 中面交线模型展开位置与既有二维差值 |','|---|---:|',
           *[f'| {r["operation_id"]} | {r["endpoint_residual_mm"]:.6f} mm |' for r in example['association_evidence']],
           f'| O011/O012 位置建议 | {feature["center_residual_mm"]:.6f} mm |' if feature else '| 附属特征 | 无唯一建议 |',
           '', '- 差异可能涉及未建模的过渡带、展开补偿与图纸参数含义；当前不能据此断言原始数据错误。',
           '- 本模型把中面交线当作零宽铰链，没有真实中性层模型或已知折弯展开参数。',
           '- O011 二维圆环半径 1.375/1.9 mm；对应位置 CAD 可见圆边半径 1.2/1.6/2.0 mm。尚需核对这些参数各自指孔壁、外缘还是工艺参考，不能直接认定同一尺寸。',
           '- 回到成品位置是可逆构造的闭合检查，不是独立工艺验证。','',
           '## 输出与边界','',
           '- material_review.html：离线页面，无需本地服务；滑块、播放、工序切换、分配导入、旋转/平移/缩放。',
           '- material_proposals.json、kinematic_proposals.json：几何连接与运动参考证据。',
           '- bend_associations.json、local_example.json：关联候选、差值及局部样例。',
           '- local_schedule_example.json：仅局部测试分配及状态快照。',
           '- summary.json、viewer_data.json：构建摘要和页面数据。',
           '- 初始真实毛坯、切除阶段、曲线折弯/拉包、实际轨迹及工具均未建模；未做干涉判定、候选筛选或评分。',
           '- 所有新增内容位于 information_extract；未改动原始 CAD、Excel、reference、Graph0/1/2 或旧候选。','']
    (out/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
