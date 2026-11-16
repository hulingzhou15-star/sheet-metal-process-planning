"""Publish the existing P002 dual-view workspace for the latest selected candidates."""
import argparse
from collections import defaultdict
import gzip
from html import escape
import json
from pathlib import Path
import shutil

import numpy as np

from information_extract.scripts.build_p002_stage_review import feature_data
from information_extract.scripts.check_p002_curve_motion import ASSET, M, PREP, WEB
from information_extract.three_d.artifacts import ROOT, read, source_manifest, verify_sources, write
from information_extract.three_d.p002_curve_motion import bind_vertices, frame, sweep
from information_extract.three_d.p002_staged_material import apply


OUT = M/'outputs/p002_motion_regeneration_v01'


def overview(out, summary, selected):
    counts = summary['checked_path_counts']
    lines = [
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">',
        '<title>P002 · 候选重生成</title><style>body{max-width:66rem;margin:2rem auto;padding:0 1rem;'
        'font:16px/1.55 system-ui;color:#26384a}td,th{padding:.35rem .75rem;text-align:left}</style>',
        '<main><h1>P002 · 候选重生成</h1>',
        f'<p>通过筛查 {summary["pool_size"]} 个，入选 {len(selected)} 个，'
        f'检测到正体积交叠 {counts.get("collision", 0)} 个，未定 {counts.get("unknown", 0)} 个。</p>',
        '<p>诊断范围为已建模材料区域的采样自干涉。有限折弯带、工具、装夹和连续局部成形不在该范围内。</p>',
        '<p><a href="summary.json">筛查统计</a> · <a href="screening.json">方案记录与干涉位置</a>'
        ' · <a href="../p002_candidate_motion_v05/stage/index.html">历史方案回放</a></p>',
    ]
    if selected:
        lines.append('<p><a href="stage/index.html">入选方案二维与三维回放</a></p>')
    elif (out/'screening.json').is_file():
        lines.append('<table><thead><tr><th>方案</th><th>结果</th><th>工序</th><th>材料区域</th>'
                     '<th>正体积交叠（mm³）</th></tr></thead><tbody>')
        for plan in read(out/'screening.json')['attempts']:
            result = plan['refined'] or plan['coarse']
            witnesses = result.get('overlaps', []) or [result.get('first_unknown', {}) or {}]
            for witness in witnesses:
                label = {'collision':'实体交叠','unknown':'未定'}[plan['status']]
                if 'operations' in result:
                    label += ' · 去料证据范围 '+', '.join(result['operations'])
                fields = [plan['plan_id'], label,
                          witness.get('station_id',''), ' / '.join(witness.get('region_ids',[])),
                          f'{witness["intersection_volume_mm3"]:.8g}' if 'intersection_volume_mm3' in witness else '']
                lines.append('<tr>'+''.join(f'<td>{escape(str(value))}</td>' for value in fields)+'</tr>')
        lines.append('</tbody></table>')
    (out/'index.html').write_text('\n'.join(lines+['</main></html>']),encoding='utf8')


def write_flat(mesh_path, target, model, cell, pose):
    with gzip.open(mesh_path,'rt',encoding='utf8') as stream:
        points = np.asarray(json.load(stream)['triangles']).reshape(-1,3)
    if cell in model['curve_guides']:
        ref = model['curve_guides'][cell]
        mapped = sweep(points,bind_vertices(points,ref['guides']),0.,ref['z_sign'])
    else:
        mapped = apply(np.asarray(pose[cell]),points)
    xy = mapped[:,:2].reshape(-1,3,2).round(5)
    ab,ac = xy[:,1]-xy[:,0],xy[:,2]-xy[:,0]
    area = ab[:,0]*ac[:,1]-ab[:,1]*ac[:,0]
    xy[area<0] = xy[area<0,::-1]
    with gzip.open(target,'wt',encoding='utf8',compresslevel=3) as stream:
        json.dump(xy[abs(area)>1e-8].reshape(-1).tolist(),stream,separators=(',',':'))


def publish(out):
    summary,payload = read(out/'summary.json'),read(out/'plans.json')
    verify_sources(summary['sources'])
    selected = payload['plans']
    if summary['selected_count'] != len(selected) or {p['plan_id'] for p in selected} != set(payload['ranking']):
        raise ValueError('P002 selection and ranking disagree')
    if not selected:
        overview(out,summary,selected)
        print('Published P002 screening overview')
        return
    stage = out/'stage'
    flat = stage/'flat'
    flat.mkdir(parents=True,exist_ok=True)
    model = read(out/'model.json')
    bends = {o['operation_id'] for o in model['operations'] if o['operation_type']=='BEND'}
    material_by_batch, variants, projected = {}, {}, set()
    synchronous_groups, material_paths = defaultdict(list), set()
    publications = []
    for plan in selected:
        evidence = plan['motion_verification']
        report_path = ROOT/evidence['report']
        report = read(report_path)
        if report['plan_id'] != plan['plan_id'] or report['assignments'] != plan['assignments'] or (
            report['subdivisions'] != summary['refined_subdivisions'] or
            report['sample_count'] != len(report['runs']) or
            any(report['summary']['counts'].get(status,0) for status in ('OVERLAP','UNKNOWN_KERNEL_FAILURE'))
        ):
            raise ValueError(f'P002 report does not match selected plan {plan["plan_id"]}')
        material_path = tuple(tuple(r['current_operation_ids']) for r in report['runs']
                              if r['phase']=='completed' and r['current_operation_ids'])
        bend_groups = tuple(group for actions in material_path
                            if (group:=tuple(op for op in actions if op in bends)))
        synchronous_groups[bend_groups].append(plan['plan_id'])
        material_paths.add(material_path)
        batch = ROOT/evidence['material_directory']
        if batch not in material_by_batch:
            material_by_batch[batch] = {
                f'{batch.name}_{v["material_id"]}': {cid:f'{batch.name}_{gid}' for cid,gid in v['cells'].items()}
                for v in read(batch/'material_states.json')['variants']
            }
            publications.extend([batch/'material_states.json', batch/'material_audit.json'])
        publications.append(report_path)
        pose = frame(model,plan,model['stations'][0]['station_id'],'entry',0.)['transforms']
        for mid in sorted({row['material_id'] for row in report['runs']}):
            cells = material_by_batch[batch][mid]
            variants[mid] = dict(material_id=mid,cells=cells)
            for cid,gid in cells.items():
                if gid not in projected:
                    write_flat(out/f'meshes/{gid}.json.gz',flat/f'{gid}.json.gz',model,cid,pose)
                    projected.add(gid)
    source_audit = next(iter(material_by_batch))/'material_audit.json'
    features = feature_data(model,selected[0],read(ASSET/'asset.json'),read(ASSET/'base_registration.json'),
                            read(source_audit)['operations'],read(PREP/'geometry_paths.json'))
    style = WEB/'local_forming_review.html'
    theme = M/'configs/local_forming_display_v03.json'
    (stage/'review.css').write_text(style.read_text(encoding='utf8').split('<style>',1)[1].split('</style>',1)[0],
                                  encoding='utf8')
    (stage/'theme.js').write_text('globalThis.FiniteMaterialTheme='+json.dumps(read(theme))+';\n',encoding='utf8')
    js = WEB/'p002_stage_workspace.js'
    source = js.read_text(encoding='utf8').replace('网格穿越提示的实体复核见下方链接。','正体积交叠、扫掠预筛和实体未决计入逐采样报告。')
    advance = '      i++;\n      timer=setTimeout'
    if source.count(advance) != 1:
        raise ValueError('P002 playback template changed')
    source = source.replace(advance, '''      const limit=Math.min(end,i+Number($('speed').value));
      do { i++; } while (i<limit && report.runs[i].phase==='motion');
      timer=setTimeout''')
    (stage/'review.js').write_text(source,encoding='utf8')
    html = WEB/'p002_stage_workspace.html'
    page = html.read_text(encoding='utf8').replace('href="../replay_verification.json">16方案核验',
        'href="verification.json">入选方案核验').replace('href="../representative_contacts.json">网格穿越的实体复核',
        'href="../screening.json">逐方案筛查').replace('href="../material_audit.json">材料动作依据',
        f'href="../staged/{source_audit.parent.name}/material_audit.json">材料动作依据').replace(
        'href="../replay.html">单视图回放','href="../index.html">筛查结果')
    speed = '<label>速度 <select id="speed"><option value="1">1×</option>' \
            '<option value="2">2×</option><option value="4">4×</option></select></label>'
    page = page.replace('<span id="progress"></span>','<span id="progress"></span>\n    '+speed)
    (stage/'index.html').write_text(page,encoding='utf8')
    for name in ('core.js','finite_material_renderer.js'):
        shutil.copyfile(WEB/name,out/name)
    write(stage/'data.json',dict(model=model,features=features,variants=list(variants.values()),
        ranking=payload['ranking'],assignments={p['plan_id']:p['assignments'] for p in selected}))
    sources = source_manifest([Path(__file__),style,theme,js,html,out/'plans.json',out/'summary.json',
        ASSET/'asset.json',ASSET/'base_registration.json',PREP/'geometry_paths.json',*publications])
    write(stage/'verification.json',dict(sources=sources,feature_count=len(features),plan_count=len(selected),
        material_projection_count=len(projected),
        distinct_synchronous_bend_groups=len(synchronous_groups),
        distinct_material_and_fold_schedules=len(material_paths),
        same_synchronous_bend_groups=[ids for ids in synchronous_groups.values() if len(ids)>1]))
    overview(out,summary,selected)
    print('Published P002 screening overview and',len(selected),'workspace replays')


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=OUT)
    args = parser.parse_args()
    directory = args.input.resolve()
    if not directory.is_relative_to(M/'outputs') or not directory.name.startswith(OUT.name):
        parser.error('Expected a P002 regeneration output directory')
    publish(directory)
