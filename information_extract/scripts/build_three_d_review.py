"""Build the self-contained, reference/Sheet5-free geometry review pack."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from information_extract.three_d.facts import read_facts, frozen_snapshot, sha256
from information_extract.three_d.assets import build_asset
from information_extract.three_d.association import register, bend_proposals
from information_extract.three_d.state import readiness
from information_extract.three_d.artifacts import portable, relative


def write_json(path, value):
    path.write_text(json.dumps(portable(value), ensure_ascii=False, allow_nan=False, separators=(',', ':')), encoding='utf-8')


def main():
    module = ROOT/'information_extract'
    config = json.loads((module/'configs/three_d_state_v01.json').read_text(encoding='utf-8'))
    output = module/config['output']
    output.mkdir(parents=True, exist_ok=True)
    if not (output/'frozen_before.json').exists():
        write_json(output/'frozen_before.json', frozen_snapshot(ROOT))
    step, workbook = module/config['step'], module/config['workbook']
    if sha256(step) != config['expected_step_sha256']:
        raise ValueError('STEP version changed; rebaseline explicitly')
    facts = read_facts(workbook, config['part_id'])
    write_json(output/'facts.json', facts)
    print('Facts loaded', facts['coverage'], flush=True)
    asset = build_asset(step, config)
    write_json(output/'asset.json', asset)
    print('CAD mesh ready', {k: asset[k] for k in ('face_count','edge_count','surface_counts')}, flush=True)
    registration = register(facts, asset, config)
    proposals = bend_proposals(facts, asset, registration, config)
    write_json(output/'registration.json', registration)
    write_json(output/'bend_proposals.json', proposals)
    print('Registration', {k: registration.get(k) for k in ('status','anchor_count','runner_up_anchor_count','max_residual_mm')}, flush=True)
    inventory = {
        'schema_version': 1, 'inputs': [
            {'path': relative(step), 'sha256': sha256(step), 'role': 'formed_geometry'},
            {'path': relative(workbook), 'sha256': sha256(workbook), 'role': 'permitted_fact_columns_only'}],
        'coverage': facts['coverage'], 'read_scope': facts['read_scope'],
        'frozen_verification': 'Run verify_three_d_review.py against frozen_before.json',
        'unknown': ['initial_blank_and_cut_state', 'thickness_confirmation', 'material_moving_regions',
                    'bend_angle_convention_and_values', 'forming_trajectories', 'tools_and_fixtures'],
        'stage_status': {'inventory': 'COMPLETE', 'material_and_motion': 'NOT_YET_SOLVED'}}
    write_json(output/'inventory.json', inventory)
    write_json(output/'readiness.json', readiness(facts, {}))
    page_data = {'facts':facts,'asset':asset,'registration':registration,'proposals':proposals}
    web = module/'three_d/web'
    html = (web/'review.html').read_text(encoding='utf-8')
    for marker, content in [('/*__STYLE__*/',(web/'style.css').read_text(encoding='utf-8')),
                            ('/*__DATA__*/',json.dumps(page_data, ensure_ascii=False, allow_nan=False).replace('<','\\u003c')),
                            ('/*__CORE__*/',(web/'core.js').read_text(encoding='utf-8')),
                            ('/*__APP__*/',(web/'app.js').read_text(encoding='utf-8'))]:
        html = html.replace(marker,content)
    (output/'review.html').write_text(html,encoding='utf-8')
    rows = ['# P001 三维关联构建报告','','## 输入与覆盖','',
            f'- 动作 {len(facts["operations"])}，二维几何 {facts["coverage"]["with_2d_geometry"]}。',
            '- 缺失二维：'+', '.join(facts['coverage']['missing_2d']),
            f'- STEP：1 个有效实体，{asset["face_count"]} 个面、{asset["edge_count"]} 条边。',
            '- 只读取 Sheet1 非专家列、Sheet2、Sheet3；未读取 Sheet4/5。','',
            '## 配准证据','', f'- 状态：{registration["status"]}。',
            f'- 基准面 F{registration.get("base_face_id")}；匹配孔 {registration.get("anchor_count",0)}；次优方向匹配孔 {registration.get("runner_up_anchor_count",0)}。',
            f'- 最大数值残差 {registration.get("max_residual_mm")} mm；仅表示基准孔吻合，不是测量精度。',
            f'- 线性映射：{registration.get("matrix")}；平移 mm：{registration.get("translation_mm")}。',
            '- 搜索范围为保持单位的 90° 旋转/反射；不是任意空间姿态的通用配准器。',
            '- 基准配准不将所有展开动作投影到成品平面。','',
            '| 动作 | CAD 边 | 关联面（含基准面） | 残差 mm |','|---|---:|---|---:|']
    for a in registration.get('anchors',[]):
        rows.append(f'| {a["operation_id"]} | {a["edge_id"]} | {a["face_ids"]} | {a["residual_mm"]:.3g} |')
    supported = [p for p in proposals if p['candidates']]
    rows += ['','## 折弯关联建议','',f'- {len(supported)}/{sum(o["operation_type"]=="BEND" for o in facts["operations"])} 个折弯具有局部候选面。',
             '- 方法：配准后轴方向、投影重叠及真实拓扑邻接路径筛选；门限在配置文件。',
             '- 这些是未确认几何建议。轴偏移可能涉及展开补偿，不能作为测量误差或工艺罚分。',
             '- 可能同一候选对应两个展开动作；没有自动合并动作，也没有指定移动侧、目标角度或运动。',
             '- 已提取 CAD 面邻接；完整材料分区与运动连接尚未建立。','',
             '## 状态与阻塞','',
             '- 直线铰链状态内核单独实现，需用人工算例验证；尚未给 P001 赋予任何推测运动。',
             '- 成品模型未证明初始毛坯形状、切孔状态、折弯参数及移动区域；工具和轨迹未知。',
             '- 当前三维候选过滤准备度为 UNKNOWN，不能把未完成检查视为通过。',
             '- 原 WKF 特征历史尚未原生解析。','',
             '## 使用','',
             '- 打开 review.html 可离线旋转、点选、关联；无需本地服务或网络。',
             '- 二维原工程坐标不变，显示旋转到已配准方向；三维坐标来自 STEP。',
             '- 关联按零件 + CAD 哈希 + 动作指纹隔离保存在浏览器本机。下载 JSON 用于持久备份。',
             '- CAD 面关联可多选；确认关联不等于确认材料移动区域。',
             '- 浏览器备份尚未由本报告自动视为已确认事实。','']
    (output/'REPORT.md').write_text('\n'.join(rows),encoding='utf-8')
    print('Offline review page:',output/'review.html',flush=True)


if __name__ == '__main__':
    main()
