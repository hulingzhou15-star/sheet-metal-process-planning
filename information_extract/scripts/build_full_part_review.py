"""Unify all P001 actions and current evidence; never invent unsupported motion."""
from pathlib import Path
import hashlib
import json
from collections import Counter

MODULE = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def main():
    source = MODULE / 'outputs/three_d_state_v01/P001'
    material = MODULE / 'outputs/material_state_v01/P001'
    finite = MODULE / 'outputs/finite_bend_v01/P001'
    out = MODULE / 'outputs/full_part_review_v01/P001'; out.mkdir(parents=True, exist_ok=True)
    facts, asset, registration = [read(source / (n + '.json')) for n in ['facts', 'asset', 'registration']]
    config = read(MODULE / 'configs/three_d_state_v01.json')
    if hashlib.sha256((MODULE / config['step']).read_bytes()).hexdigest() != asset['asset_id']:
        raise ValueError('STEP changed')
    skeleton = read(material / 'kinematic_proposals.json')
    matches = read(material / 'bend_associations.json')['matches']
    local = read(finite / 'model.json'); validation = read(finite / 'validation.json')
    if local['source_step_sha256'] != asset['asset_id']:
        raise ValueError('Local model / full STEP mismatch')
    joints = {j['joint_id']: j for j in skeleton['joints']}
    regions = {r['region_id']: r for r in skeleton['regions']}
    bend_map = {m['operation_id']: m for m in matches}
    anchors = {a['operation_id']: a for a in registration['anchors']}
    local_ids = {b['operation_id'] for b in local['bends']}
    attached = read(material / 'local_example.json')['feature']
    rows = {}
    for op in facts['operations']:
        oid = op['operation_id']
        row = {'operation_id': oid, 'operation_type': op['operation_type'], 'face_ids': [],
               'motion_model': False, 'local_example': False, 'automatically_confirmed': False}
        if oid in bend_map:
            m = bend_map[oid]; j = joints[m['joint_id']]
            faces = regions[j['child_region_id']]['skin_face_ids'] + [s['cylinder_face_id'] for s in j['surface_evidence']]
            row.update(status='BEND_PROPOSAL', label='折弯对应建议',
                       meaning='来自材料连接树的几何对应，尚未完成该动作的有限宽展开和运动核验。',
                       face_ids=sorted(set(faces)), joint_id=j['joint_id'], angle_deg=j['angle_deg'],
                       residual_mm=m['endpoint_residual_mm'], child_region_id=j['child_region_id'])
            if oid in local_ids:
                row.update(status='LOCAL_GEOMETRY', label='已有局部几何回放', motion_model=True, local_example=True,
                           meaning='已建立有限宽折弯带，局部几何检查通过；恒厚插值不守恒，物理成形与碰撞未验证。')
        elif oid in anchors:
            row.update(status='BASE_ANCHOR', label='基准孔对应证据',
                       meaning='通过基准孔几何配准找到成品孔壁；没有模拟冲孔事件。',
                       face_ids=[i for i in anchors[oid]['face_ids'] if i != registration['base_face_id']])
        elif oid in attached['operation_ids']:
            edge = asset['edges'][attached['cad_edge_id'] - 1]
            row.update(status='ATTACHED_LOCATION', label='附属位置可随动', local_example=True,
                       meaning='在双折弯局部例子中定位并随板面移动；自身预孔/翻孔过程与轮廓尺寸尚未核验。高亮是成品位置参照。',
                       face_ids=list(edge['face_ids']))
        elif op['paths']:
            row.update(status='TWO_D_ONLY', label='已有二维，三维待关联',
                       meaning='保留动作完整二维来源；没有把展开坐标直接投影到成品，也没有猜测加工移动区域。')
        else:
            row.update(status='MISSING_TWO_D', label='缺少二维几何',
                       meaning='动作仍列入覆盖统计；当前缺少二维来源与自动三维对应，不能按已完成或无风险处理。')
        rows[oid] = row
    counts = dict(Counter(r['status'] for r in rows.values()))
    inspection = {'operations': rows, 'counts': counts, 'operation_count': len(rows),
                  'type_counts': dict(Counter(o['operation_type'] for o in facts['operations'])),
                  'full_part_simulation_ready': False, 'full_part_collision_checked': False,
                  'local_model_url': '../../finite_bend_v01/P001/review.html',
                  'local_model_validation': validation,
                  'source_asset_id': asset['asset_id'], 'facts_fingerprint': facts['facts_fingerprint']}
    write(out / 'coverage.json', inspection)
    data = {'facts': facts, 'asset': asset, 'registration': registration, 'proposals': [], 'inspection': inspection}
    web = MODULE / 'three_d/web'; page = (web / 'review.html').read_text(encoding='utf-8')
    replacements = {
        'P001 · 二维—三维动作关联': 'P001 · 全零件特征检视',
        'P001 · 几何与动作关联': 'P001 · 全零件特征检视',
        '折弯建议尚未确定移动侧与轨迹。': '全件为成品检视；只有 O009/O010 有局部几何回放。',
        '<div id="list"></div>': '<label class="footnote"><input id="autofocus" type="checkbox">选择动作时自动聚焦已有证据</label><div id="list"></div>',
        '<option value="PROPOSAL">有折弯建议</option>': '<option value="PROPOSAL">有三维证据 / 建议</option>'
    }
    for before, after in replacements.items():
        if before not in page: raise ValueError('Base review template changed: ' + before)
        page = page.replace(before, after)
    extra_options = ''.join(f'<option value="TYPE:{t}">{t} · {n} 个</option>' for t, n in inspection['type_counts'].items())
    labels = {r['status']: r['label'] for r in rows.values()}
    extra_options += ''.join(f'<option value="STATUS:{s}">{labels[s]} · {n}</option>' for s, n in counts.items())
    page = page.replace('<option value="BOUND">已有三维关联</option></select>', '<option value="BOUND">已有手动关联</option>' + extra_options + '</select>')
    strip = (f'<div class="whole-summary"><span><b>{len(rows)}</b> 全部动作</span>'
             f'<span><b>{sum(bool(o["paths"]) for o in facts["operations"])}</b> 有二维几何</span>'
             f'<span><b>{len(bend_map)}</b> 折弯对应建议（含{len(local_ids)}个局部模型）</span>'
             f'<span><b>{counts.get("BASE_ANCHOR", 0)}</b> 基准孔</span>'
             f'<span><b>{counts.get("ATTACHED_LOCATION", 0)}</b> 附属位置</span>'
             f'<span><b>{counts.get("MISSING_TWO_D", 0)}</b> 缺二维</span>'
             '<a href="../../stage_review_v01/P001/review.html" target="_blank">16 个方案 · 按工序检视 ↗</a><a href="../../finite_bend_v01/P001/review.html" target="_blank">局部阶段回放 ↗</a><a href="REPORT.md" target="_blank">覆盖报告</a></div>')
    page = page.replace('<main>', strip + '<main>')
    css = (web / 'style.css').read_text(encoding='utf-8') + '\n.whole-summary{display:flex;align-items:center;flex-wrap:wrap;gap:14px;padding:9px 22px;background:white;border-bottom:1px solid #dce4ed;font-size:11px}.whole-summary b{color:#326eaf}.whole-summary a,.inspection-detail a{color:#326eaf}.inspection-detail a{display:block;margin:10px 0}.inspection-detail p{line-height:1.75;font-size:12px}.status-label{color:#836945!important}main{height:calc(100vh - 154px)}'
    for marker, value in [('STYLE', css), ('DATA', json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')),
                          ('CORE', (web / 'core.js').read_text(encoding='utf-8')), ('APP', (web / 'app.js').read_text(encoding='utf-8'))]:
        page = page.replace('/*__' + marker + '__*/', value)
    (out / 'review.html').write_text(page, encoding='utf-8')
    print(json.dumps({'output': str(out / 'review.html'), 'counts': counts, 'types': inspection['type_counts']}, ensure_ascii=False))


if __name__ == '__main__': main()
