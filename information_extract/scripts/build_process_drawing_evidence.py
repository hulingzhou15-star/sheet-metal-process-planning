"""Build an offline, traceable P001 drawing-assisted modeling evidence pack."""
from pathlib import Path
import sys
import json
import hashlib
import math
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / 'information_extract'
sys.path.insert(0, str(ROOT))

import ezdxf
from ezdxf import bbox
from ezdxf.disassemble import recursive_decompose
from ezdxf.addons.drawing import RenderContext, Frontend, layout, svg
from ezdxf.math import BoundingBox2d
from information_extract.three_d.association import register


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def within(point, box):
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def drawing_registration(circles, facts, config, cfg):
    # Adapter to the tested rigid registration algorithm: virtual 2D drawing plane,
    # not a STEP face. No scale adjustment; all eight axis orientations are tried.
    source = {'operations': [o for o in facts['operations'] if o['operation_id'] in cfg['fit_anchor_ids']]}
    target = {'faces': [{'face_id': 1, 'plane': {'normal': [0, 0, 1]}, 'area_mm2': 1,
                         'centroid_mm': [0, 0, 0]}],
              'circles': [{'edge_id': c['source_id'], 'face_ids': [1],
                           'center_mm': c['center'], 'radius_mm': c['radius'],
                           'parameter_span_rad': 2 * math.pi} for c in circles]}
    result = register(source, target, config)
    result.pop('base_face_id', None)
    result.pop('base_plane_z_mm', None)
    result['scope'] = 'Four-circle 2D base-frame fit; three held-out holes validate it. Not full moving-material registration.'
    for a in result.get('anchors', []):
        a['drawing_source_id'] = a.pop('edge_id')
        a.pop('face_ids', None)
    result['held_out_checks'] = []
    if result['status'] != 'BASE_FRAME_SUPPORTED':
        result['independently_validated'] = False
        return result
    matrix, translation = np.array(result['matrix']), np.array(result['translation_mm'])
    for op in facts['operations']:
        if op['operation_id'] not in cfg['held_out_anchor_ids']:
            continue
        s = op['segments'][0]
        predicted = matrix @ [s['center_x'], s['center_y']] + translation
        candidates = sorted((float(np.linalg.norm(np.array(c['center'][:2]) - predicted)), c['source_id'])
                            for c in circles if abs(c['radius'] - s['radius']) <= config['registration']['radius_tolerance_mm'])
        best = candidates[0] if candidates else (None, None)
        result['held_out_checks'].append({'operation_id': op['operation_id'], 'drawing_source_id': best[1],
                                         'residual_mm': best[0],
                                         'passed': best[0] is not None and best[0] <= config['registration']['position_tolerance_mm']})
    result['independently_validated'] = len(result['held_out_checks']) == len(cfg['held_out_anchor_ids']) and all(c['passed'] for c in result['held_out_checks'])
    return result


def render_selected(doc, entities, bounds, width):
    backend = svg.SVGBackend()
    Frontend(RenderContext(doc), backend).draw_entities(entities)
    backend.set_background('#20272d')
    return backend.get_string(layout.Page(width, width * (bounds[3] - bounds[1]) / (bounds[2] - bounds[0])),
                              settings=layout.Settings(crop_at_margins=True),
                              render_box=BoundingBox2d([(bounds[0], bounds[1]), (bounds[2], bounds[3])]))


def main():
    cfg = read(MODULE / 'configs/P001_process_drawing_assistance.json')
    source = Path(cfg['source']); digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != cfg['expected_sha256']:
        raise ValueError('DXF changed; review view identities before rebuilding')
    out = MODULE / cfg['output']; out.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.readfile(source)
    facts = read(MODULE / 'outputs/three_d_state_v01/P001/facts.json')
    config = read(MODULE / 'configs/three_d_state_v01.json')
    base = read(MODULE / 'outputs/three_d_state_v01/P001/registration.json')
    base_m, base_t = np.array(base['matrix']), np.array(base['translation_mm'])
    cache = bbox.Cache(); entities = list(doc.modelspace())
    boxes = {e.dxf.handle: bbox.extents([e], cache=cache) for e in entities}
    rows = []
    for view in cfg['views']:
        bounds = view.get('bounds')
        if bounds is None:
            b = boxes[view['frame_handle']]
            bounds = [b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y]
        title = view.get('title') or doc.entitydb[view['title_handle']].plain_text()
        if view.get('insert_handles'):
            selected = [doc.entitydb[h] for h in view['insert_handles']]
        else:
            selected = [e for e in entities if boxes[e.dxf.handle].has_data and
                        boxes[e.dxf.handle].extmax.x >= bounds[0] and boxes[e.dxf.handle].extmin.x <= bounds[2] and
                        boxes[e.dxf.handle].extmax.y >= bounds[1] and boxes[e.dxf.handle].extmin.y <= bounds[3]]
        (out / (view['id'] + '.svg')).write_text(render_selected(doc, selected, bounds, cfg['render_page_width_mm']), encoding='utf-8')
        # Exclude title-block geometry from numerical matching; preserve provenance
        # through top-level handle plus stable virtual-entity index.
        geom = []
        for e in selected:
            if e.dxftype() == 'INSERT' and e.dxf.name == 'A3':
                continue
            for i, child in enumerate(recursive_decompose([e])):
                if child.dxftype() not in ('CIRCLE', 'ARC', 'LINE', 'LWPOLYLINE', 'SPLINE', 'ELLIPSE'):
                    continue
                geom.append((f'{e.dxf.handle}:{i}', child))
        circles = [{'source_id': sid, 'center': list(e.dxf.center), 'radius': e.dxf.radius}
                   for sid, e in geom if e.dxftype() == 'CIRCLE' and within(e.dxf.center, bounds)]
        reg = drawing_registration(circles, facts, config, cfg)
        row = {'id': view['id'], 'title': title, 'title_handle': view.get('title_handle'),
               'frame_handle': view.get('frame_handle'), 'drawing_bounds': bounds,
               'top_level_source_handles': [e.dxf.handle for e in selected], 'registration': reg,
               'circle_count': len(circles), 'local_curves': []}
        if reg['independently_validated']:
            rm, rt = np.array(reg['matrix']), np.array(reg['translation_mm'])
            row['drawing_to_cad_base_xy'] = {'matrix': (base_m @ rm.T).tolist(), 'translation_mm': (base_t - base_m @ rm.T @ rt).tolist()}
            def to_cad(p):
                return base_m @ rm.T @ (np.array(list(p)[:2]) - rt) + base_t
            for sid, e in geom:
                if e.dxftype() in ('CIRCLE', 'ARC'):
                    p = to_cad(e.dxf.center)
                    if within(p, cfg['local_crop_cad_xy_mm']):
                        row['local_curves'].append({'source_id': sid, 'type': e.dxftype(), 'center_cad_xy_mm': p.tolist(),
                                                   'radius_mm': e.dxf.radius})
                elif e.dxftype() == 'LINE':
                    a, b = to_cad(e.dxf.start), to_cad(e.dxf.end)
                    if within(a, cfg['local_crop_cad_xy_mm']) and within(b, cfg['local_crop_cad_xy_mm']):
                        row['local_curves'].append({'source_id': sid, 'type': 'LINE', 'start_cad_xy_mm': a.tolist(), 'end_cad_xy_mm': b.tolist()})
            corners = [rm @ (base_m.T @ (np.array([x, y]) - base_t)) + rt
                       for x in cfg['local_crop_cad_xy_mm'][::2] for y in cfg['local_crop_cad_xy_mm'][1::2]]
            corners = np.array(corners)
            local_box = [*corners.min(axis=0), *corners.max(axis=0)]
            (out / (view['id'] + '_local.svg')).write_text(render_selected(doc, selected, local_box, 120), encoding='utf-8')
        rows.append(row)
        print(view['id'], reg['status'], reg.get('anchor_count'), 'held-out', reg['independently_validated'], flush=True)
    local = read(MODULE / 'outputs/material_state_v01/P001/local_example.json')
    feature = local['feature']
    comparisons = []
    for view_id, expected, meaning in [
        ('unfold', feature['source_center_xy_mm'], 'DXF unfolded location versus existing Sheet2'),
        ('stage06', feature['formed_surface_center_mm'][:2], 'Drawing 6/7 center versus formed STEP base-plane projection'),
        ('stage07', feature['formed_surface_center_mm'][:2], 'Drawing 7/7 center versus formed STEP base-plane projection'),
    ]:
        row = next(v for v in rows if v['id'] == view_id)
        circles = [c for c in row['local_curves'] if c['type'] == 'CIRCLE' and
                   np.linalg.norm(np.array(c['center_cad_xy_mm']) - expected) <= cfg['local_center_search_mm']]
        comparisons.append({'view_id': view_id, 'meaning': meaning, 'expected_center_mm': expected,
                            'matches': [{**c, 'center_residual_mm': float(np.linalg.norm(np.array(c['center_cad_xy_mm']) - expected))}
                                        for c in circles],
                            'status': 'LOCATION_SUPPORTED_DIMENSION_MEANING_UNRESOLVED' if circles else 'UNRESOLVED'})
    comparison = {
        'operation_location': 'O011 / O012 shared location; shape semantics remain separate',
        'observations': comparisons,
        'zero_width_hinge_flat_center_mm': feature['developed_surface_center_mm'],
        'recorded_flat_center_mm': feature['source_center_xy_mm'],
        'flat_center_residual_mm': feature['center_residual_mm'],
        'inference': 'The raw drawing supports the recorded flat center and the final projected center. The 0.8 mm discrepancy is not resolved by copying the raw DXF center or changing global registration. The zero-width unfolding approximation and bend conventions still require investigation.',
        'not_proven': ['Exact bend compensation', 'Inner bend radius', 'Neutral axis', 'Intermediate Z height',
                       'Tool motion', 'Collision-free forming', 'Automatic stage-to-operation assignment'],
    }
    write(out / 'local_model_comparison.json', comparison)
    evidence = {'schema_version': 1, 'source_sha256': digest, 'source': str(source), 'usage': cfg['usage'],
                'thickness_confirmation': read(MODULE / 'configs/P001_confirmed_material_parameters.json'),
                'views': rows, 'local_model_source': 'outputs/material_state_v01/P001/local_example.json',
                'local_model_sha256': hashlib.sha256((MODULE / 'outputs/material_state_v01/P001/local_example.json').read_bytes()).hexdigest(),
                'local_model_comparison': comparison,
                'scope': 'Drawing-assisted development evidence. No new assignments, hard constraints, calibration or simulated states created.'}
    write(out / 'evidence.json', evidence)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    # Offline gallery keeps full and local drawing views next to explicit fit evidence.
    data = json.dumps(rows, ensure_ascii=False).replace('<', '\\u003c')
    page = '''<!doctype html><html lang="zh"><meta charset="utf-8"><title>P001 工序图辅助建模</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f3f6fa;color:#23344a;font:15px system-ui,"Microsoft YaHei"}header{padding:20px 28px;background:white;border-bottom:1px solid #dce4ed}h1{font-size:22px;margin:0 0 8px}p{line-height:1.65;margin:7px 0}main{padding:18px 28px}select,button,a{font:inherit}select{padding:9px;border:1px solid #bac9da;border-radius:6px;min-width:360px}.bar{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.grid{display:grid;grid-template-columns: minmax(0,3fr) minmax(250px,1fr);gap:16px;margin-top:16px}.card{background:white;padding:16px;border:1px solid #dce4ed;border-radius:8px}.card img{width:100%;display:block;background:#20272d}#local{max-height:620px;object-fit:contain}.muted{color:#61738a}.warn{color:#875a16;background:#fff6e5;padding:12px;border-radius:6px}#stats{margin:12px 0}.actions a{margin-right:18px;color:#245e99}iframe{width:100%;height:820px;border:1px solid #dce4ed}summary{cursor:pointer;padding:15px;font-weight:600}@media(max-width:850px){.grid{grid-template-columns:1fr}select{min-width:0;width:100%}}</style>
<header><h1>P001 · 工序图辅助建模证据</h1><p>原 DXF 的阶段形状、俯视和侧视信息，与已有三维模型核对。板厚：0.8 mm（已确认）。</p><p class="muted">专家工序图辅助开发；不作为新零件推理的必需输入。原图缺线不等于缺材料，同序动作不按编号串行执行。</p></header>
<main><div class="bar"><select id="view"></select><button id="prev">上一张</button><button id="next">下一张</button><span id="count"></span></div><div id="stats"></div>
<div class="grid"><section class="card"><h3 id="title"></h3><img id="drawing"><p class="muted">保留原图颜色与各投影；颜色尚未自动解释为动作或完成状态。</p><a id="open" target="_blank">单独打开 SVG 放大查看</a></section><section class="card"><h3>O009／O010／O011 局部</h3><img id="local"><p id="local-note"></p><p class="muted">由固定孔校准位置后裁取，不按视觉猜位置。这里只对应基准坐标，侧视高度尚未自动配准。</p></section></div>
<section class="card" style="margin-top:16px"><p><b>当前用途：</b>核对阶段形状、翻起方向和展开位置；还没有把图纸自动变成三维中间实体。</p><p>第 6/7 图中 O011 对应圆心与成品 STEP 的基准平面投影吻合；第 7/7 图的同位置圆半径又有变化。位置对应和尺寸语义必须分别核查。</p><p class="warn">首序基准孔不足，配准未通过，不强行显示局部。图纸另有“3/7 后攻牙46-M3”及“7/7 校平”，当前局部折弯模型未模拟这些工艺。</p><div class="actions"><a href="REPORT.md">检查报告</a><a href="evidence.json">来源与数值证据</a><a href="../../material_state_v01/P001/material_review.html" target="_blank">已有局部三维回放</a></div></section>
<p class="muted">三维回放请通过上方链接单独打开，仍为原测试分配，不自动跟随图纸序号。</p></main>
<script>const rows=__DATA__;const sel=document.querySelector('#view');rows.forEach((r,i)=>sel.add(new Option(r.title,i)));function show(){const r=rows[Number(sel.value)],g=r.registration;document.querySelector('#title').textContent=r.title;document.querySelector('#drawing').src=r.id+'.svg';document.querySelector('#open').href=r.id+'.svg';document.querySelector('#count').textContent=(Number(sel.value)+1)+' / '+rows.length;document.querySelector('#stats').textContent=g.independently_validated?'基准配准通过：4 孔拟合 + 3 孔独立核验；不缩放；最大核验误差 '+Math.max(...g.held_out_checks.map(a=>a.residual_mm)).toExponential(2)+' mm':'基准配准未通过：可读原图，不能据此自动定位动作。';const img=document.querySelector('#local');img.hidden=!g.independently_validated;if(g.independently_validated)img.src=r.id+'_local.svg';document.querySelector('#local-note').textContent=g.independently_validated?'局部范围：基准 X 348–362，Y −18–5 mm。':'未生成猜测的局部对应。';}sel.onchange=show;document.querySelector('#prev').onclick=()=>{sel.value=Math.max(0,Number(sel.value)-1);show()};document.querySelector('#next').onclick=()=>{sel.value=Math.min(rows.length-1,Number(sel.value)+1);show()};sel.value=5;show();</script></html>'''
    (out / 'review.html').write_text(page.replace('__DATA__', data), encoding='utf-8')
    print('Written', out / 'review.html')


if __name__ == '__main__':
    main()
