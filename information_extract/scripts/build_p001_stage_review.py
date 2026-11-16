"""Publish the P001 finite-band workspace against recorded motion samples."""
import argparse
import base64
from collections import Counter
import gzip
import json
import os
from pathlib import Path
import re
from shutil import copyfile

import numpy as np

from information_extract.scripts.build_finite_material_states import CFG
from information_extract.scripts.check_local_forming_collision import make_samples
from information_extract.three_d.artifacts import ROOT, read, relative, sha, source_manifest, verify_sources, write
from information_extract.three_d.band_material_solids import split_triangle_grid
from information_extract.three_d.local_forming import load_shape
from information_extract.three_d.meshing import mesh

MODULE = ROOT / 'information_extract'
WEB = MODULE / 'three_d/web'
OUTPUTS = MODULE / 'outputs'
HISTORICAL = OUTPUTS / 'local_forming_v03/P001'
REGENERATED = OUTPUTS / 'p001_motion_regeneration_v01'
STEP = MODULE / 'sources/P001/formed.step'
STEP_ASSET = OUTPUTS / 'three_d_state_v01/P001/asset.json'
TEMPLATE = WEB / 'local_forming_review.html'
APP = WEB / 'local_forming_review.js'
CONTROLS = WEB / 'p001_stage_controls.js'
EXTRAS = WEB / 'p001_stage_controls.html'
THEME = MODULE / 'configs/local_forming_display_v03.json'
RENDER_SCRIPTS = {name: WEB / filename for name, filename in {
    'CORE': 'core.js', 'MOTION': 'motion_core.js', 'BAND': 'band_material_core.js',
    'RENDERER': 'finite_material_renderer.js',
}.items()}


def replace_once(source, previous, updated):
    if source.count(previous) != 1:
        raise ValueError(f'Original finite-band viewer changed: {previous[:80]}')
    return source.replace(previous, updated)


def replace_block(source, start, end, updated):
    start_at, end_at = source.index(start), source.index(end)
    if end_at <= start_at:
        raise ValueError(f'Original finite-band viewer block moved: {start}')
    return source[:start_at] + updated + source[end_at:]


def link(path, destination):
    return Path(os.path.relpath(path, destination)).as_posix()


def historical_payload():
    html = (HISTORICAL / 'review.html').read_text(encoding='utf-8')
    pack = re.search(r'data-encoding="gzip\+base64">([^<]+)</script>', html)
    if pack is None:
        raise ValueError('Missing v0.3 finite-band payload')
    return json.loads(gzip.decompress(base64.b64decode(pack[1])))


def merged_material(data, plans, sources):
    """Mesh each displayed BRep once per batch and namespace material references."""
    crops = {oid: c for oid, c in data['step_comparison'].items() if c.get('coordinate_frame') == 'CAD_WORLD'}
    data['geometries'] = {c['geometry_id']: data['geometries'][c['geometry_id']] for c in crops.values()}
    data['step_comparison'] = crops
    data['variants'], data['material_states'] = [], []
    viewers = {}
    directories = sorted({ROOT / p['motion_verification']['material_directory'] for p in plans})
    local_forming = {}
    for index, directory in enumerate(directories, 1):
        path = directory / 'viewer_data.json'
        viewer, prefix = read(path), f'B{index:02d}:'
        viewers[directory] = (viewer, prefix)
        sources.add(path)
        if viewer['model'] != data['model'] or viewer['stations'] != data['stations']:
            raise ValueError(f'Finite-band model or stations drifted: {relative(path)}')
        ids = {p['plan_id'] for p in plans if ROOT / p['motion_verification']['material_directory'] == directory}
        states = [s for s in viewer['material_states'] if s['plan_id'] in ids]
        material_ids = {s['material_id'] for s in states}
        variants = [v for v in viewer['variants'] if v['material_id'] in material_ids]
        bends = {b['joint_id']: b for b in viewer['model']['bends']}
        for operation in viewer['local_forming']:
            local_forming[operation['operation_id']] = operation
        audit_path = directory / 'material_audit.json'
        sources.add(audit_path)
        for operation in read(audit_path)['step_details']:
            local_forming[operation['operation_id']] = operation

        def display_component(component):
            result = dict(component)
            for key in ('geometry_id', 'flat_geometry_id'):
                if key not in component:
                    continue
                original = component[key]
                name = prefix + original
                if name not in data['geometries']:
                    geometry = viewer['geometries'][original]
                    brep = directory / geometry['brep_file']
                    sources.add(brep)
                    triangles = geometry.get('triangles')
                    if triangles is None:
                        triangles = mesh(load_shape(brep), CFG)['triangles']
                        if component['kind'] == 'band':
                            bend = bends[component['joint_id']]
                            triangles = [part for tri in triangles for part in split_triangle_grid(tri, bend, 24)]
                    data['geometries'][name] = {'triangles': triangles}
                result[key] = name
            return result

        for oid, component in viewer['step_comparison'].items():
            if oid not in data['step_comparison']:
                data['step_comparison'][oid] = display_component(component)
        for variant in variants:
            components = [display_component(c) for c in variant['components']]
            data['variants'].append({**variant, 'material_id': prefix + variant['material_id'], 'components': components})
        data['material_states'].extend({**s, 'material_id': prefix + s['material_id']} for s in states)
    data['local_forming'] = list(local_forming.values())
    return viewers


def sample_records(report, viewer, plan, prefix):
    expected = make_samples(viewer, plan, report['motion_subdivisions'])
    runs = report['runs']
    observed = [(r['station_id'], r['phase'], r['tau']) for r in runs]
    if expected != observed or len(runs) != report['sample_count'] or report['plan_id'] != plan['plan_id']:
        raise ValueError(f'Report sample sequence differs: {plan["plan_id"]}')
    endpoints = {(s['station_id'], s['phase']): s['material_id'] for s in viewer['material_states']
                 if s['plan_id'] == plan['plan_id']}
    variants = {v['material_id']: v for v in viewer['variants']}
    counts = Counter()
    for row in runs:
        phase = 'completed' if row['phase'] == 'completed' else 'entry'
        if (row['material_id'] != endpoints[row['station_id'], phase]
                or row['executed_forming_ids'] != variants[row['material_id']]['executed_forming_ids']
                or sum(row['counts'].values()) != row['pair_count']):
            raise ValueError(f'Report material or pair count differs: {plan["plan_id"]}')
        counts.update(row['counts'])
    return [{**r, 'material_id': prefix + r['material_id']} for r in runs], counts


def assemble(regenerated, destination):
    sources = {HISTORICAL / 'review.html', STEP, STEP_ASSET, TEMPLATE, APP, CONTROLS, EXTRAS, THEME,
               *RENDER_SCRIPTS.values()}
    data = historical_payload()
    if regenerated:
        plan_path = REGENERATED / 'plans.json'
        summary_path = REGENERATED / 'summary.json'
        main = read(plan_path)
        indexed = {plan['plan_id']: plan for plan in main['plans']}
        plans = [indexed[pid] for pid in main['ranking']]
        summary = read(summary_path)
        if len(plans) != len(indexed) or len(plans) != summary['selected_count']:
            raise ValueError('P001 published selection and ranking disagree')
        verify_sources(summary['sources'])
        sources.update((plan_path, summary_path))
        viewers = merged_material(data, plans, sources)
        data['plans'] = plans
        data['summary_href'] = link(summary_path, destination)
    data['replays'] = {}
    checked_sources = {}
    for plan in data['plans']:
        plan_id = plan['plan_id']
        if regenerated:
            verification = plan['motion_verification']
            report_path = ROOT / verification['report']
            viewer, prefix = viewers[ROOT / verification['material_directory']]
        else:
            report_path = HISTORICAL / 'collision_batch_v01' / plan_id / 'collision_report.json'
            viewer, prefix = data, ''
        report = read(report_path)
        unchecked = {p: h for p, h in report.get('source_sha256', {}).items() if checked_sources.get(p) != h}
        verify_sources(unchecked)
        checked_sources.update(unchecked)
        sources.add(report_path)
        runs, counts = sample_records(report, viewer, plan, prefix)
        if regenerated and (report['assignments'] != plan['assignments'] or report['counts'] != dict(counts)
                            or report['sample_count'] != verification['sample_count']
                            or sum(counts.values()) != report['pair_states']
                            or report['pair_states'] != verification['pair_states']
                            or report['status'] != 'passed'
                            or report['motion_subdivisions'] != summary['refined_subdivisions']):
            raise ValueError(f'Regenerated P001 candidate is missing a passing dense report: {plan_id}')
        data['replays'][plan_id] = {
            'runs': runs, 'counts': dict(counts), 'step_count': report['motion_subdivisions'],
            'href': link(report_path, destination),
            'status': 'collision' if counts['OVERLAP'] else 'unknown' if counts['UNKNOWN_KERNEL_FAILURE'] else 'passed',
        }
        if regenerated:
            directory = ROOT / verification['material_directory']
            data['replays'][plan_id]['material_href'] = link(directory / 'material_audit.json', destination)
    if regenerated:
        total_samples = sum(len(replay['runs']) for replay in data['replays'].values())
        total_pairs = sum(sum(replay['counts'].values()) for replay in data['replays'].values())
        if (total_samples, total_pairs) != (summary['selected_sample_count'], summary['selected_pair_states']):
            raise ValueError('The regenerated P001 replay totals disagree with the run summary')
    active = set().union(*(p['assignments'] for p in data['plans']))
    action_types = {o['operation_id']: o['operation_type'] for o in data['feature_highlights']['operations']}
    data['operations'] = [{'operation_id': oid, 'operation_type': action_types[oid]} for oid in sorted(active)]
    asset = read(STEP_ASSET)
    if asset['asset_id'] != sha(STEP):
        raise ValueError('Finished STEP mesh and source STEP have different hashes')
    data['geometries']['FULL_STEP'] = {
        'triangles': np.asarray(asset['mesh']['positions']).reshape((-1, 3, 3)).tolist(),
    }
    sources.update(ROOT / p for p in checked_sources)
    data['whole_step'] = {
        'component_id': 'FULL_STEP', 'kind': 'panel', 'region_id': data['model']['root_region_id'],
        'geometry_id': 'FULL_STEP', 'coordinate_frame': 'CAD_WORLD',
    }
    data['status_caption'] = '重新生成并复筛的候选' if regenerated else '历史 v0.3 候选复核'
    data['historic'] = not regenerated
    return data, sources


def application():
    js = APP.read_text(encoding='utf-8')
    js = replace_block(js, ' function stop()', ' function bind(', '''
 $('operation').onchange=()=>{selectedFeature=true;if($('comparison').value==='step'&&!data.step_comparison[$('operation').value])$('comparison').value='stage';buildGeometry();focus();};
 $('focus').onclick=focus;
 $('showBands').onchange=()=>{buildGeometry();draw3();draw2();detail();};
 $('highlightCurrent').onchange=()=>{updateHighlights();draw3();draw2();detail();};
 $('comparison').onchange=()=>{buildGeometry();if($('comparison').value==='whole-step'){focused=false;fit3(geometry.points);detail();}else focus();};
 $('clearSelection').onclick=()=>{focused=false;selectedFeature=false;draw2();draw3();detail();};
 $('renderQuality').onchange=()=>{sizeScene();draw3();};
 $('whole').onclick=()=>{focused=false;selectedFeature=false;$('comparison').value='stage';buildGeometry();detail();fit3(geometry.points);const b=flatGeometry();fit2([b.lo,b.hi]);};
''' + js[js.index(' function setView('):js.index(' function cExample(')])
    js = replace_block(js, ' function calculate()', ' function buildGeometry()', CONTROLS.read_text(encoding='utf-8'))
    substitutions = {
        "$('plan').value='NP2_001'": "$('plan').value=data.plans[0].plan_id",
        "$('station').value='OP20'": "$('station').value=data.stations[0].station_id",
        "$('operation').value='O196'": "$('operation').value=data.operations.some(o=>o.operation_id==='O196')?'O196':data.operations[0].operation_id",
        "const step=$('comparison').value==='step',selected=$('operation').value,source=step&&data.step_comparison[selected]?[data.step_comparison[selected]]:current.variant.components;": "const comparison=$('comparison').value,selected=$('operation').value,source=comparison==='whole-step'?[data.whole_step]:comparison==='step'&&data.step_comparison[selected]?[data.step_comparison[selected]]:current.variant.components;",
        "const outlines=$('comparison').value==='step'?[]:": "const outlines=$('comparison').value!=='stage'?[]:",
        "if(selectedFeature&&$('comparison').value!=='step')": "if(selectedFeature&&$('comparison').value==='stage')",
        "points=op.flat_paths.flat();fit2(points,5);": "points=op?.flat_paths.flat()||[];if(points.length)fit2(points,5);if($('comparison').value==='whole-step'){fit3(geometry.points);detail();return;}",
        "if(local||step&&data.step_comparison[oid])": "if((local||step)&&data.step_comparison[oid])",
        "phase:current.phase,tau:current.tau,fractions": "phase:current.phase,tau:current.tau,sample_index:current.sampleIndex,counts:current.run.counts,fractions",
        "window.addEventListener('resize',resize);resize();render();$('whole').click();": "window.addEventListener('resize',resize);resize();render();$('whole').click();document.documentElement.dataset.replayReady='true';",
    }
    for previous, updated in substitutions.items():
        js = replace_once(js, previous, updated)
    return replace_block(js, ' function detail()', ' function render()', ' function detail(){describeStage();}\n')


def html_page(data):
    html = TEMPLATE.read_text(encoding='utf-8')
    extras = (EXTRAS.read_text(encoding='utf-8')).split('<!-- panels -->')
    html = replace_once(html, '逐序去料与 STEP 局部成形 v0.3', '候选逐序回放与运动自干涉')
    html = replace_block(html, '<main>', '<div class="toolbar"><label>聚焦', extras[0])
    html = replace_once(html, '<option value="step">STEP 局部成品</option>', '<option value="step">STEP 局部成品</option><option value="whole-step">完整 STEP 成品</option>')
    html = replace_once(html, '<button id="cflat">C 形孔 · OP20 完成</button><button id="cformed">C 形孔 · OP60 完成</button>', '<button id="cflat">C 形孔 · 加工前</button><button id="cformed">C 形孔 · 加工后</button>')
    html = replace_once(html, '白色细线用于定位；四个中桥、46 个翻孔已显示真实局部端态实体。', '白色细线标记本序特征，折弯带随同序进度连续弯曲。')
    html = replace_block(html, '<p id="trajectoryNotice"', '<script id="data"', extras[1])
    scripts = {name: path.read_text(encoding='utf-8') for name, path in RENDER_SCRIPTS.items()}
    renderer = replace_once(scripts['RENDERER'], 'active=band&&this.activeOperations.has(b.operation_id)', 'active=band?this.activeOperations.has(b.operation_id):!!c.forming_operation_id&&this.activeOperations.has(c.forming_operation_id)')
    scripts['RENDERER'] = 'globalThis.FiniteMaterialTheme=' + json.dumps(read(THEME)) + ';\n' + renderer
    serialized = json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    for name, content in {**scripts, 'DATA': base64.b64encode(gzip.compress(serialized, compresslevel=5, mtime=0)).decode(),
                          'APP': application()}.items():
        html = replace_once(html, f'/*__{name}__*/', content)
    return html


def build(regenerated=False):
    destination = (REGENERATED if regenerated else OUTPUTS / 'p001_stage_workspace_v01') / 'stage'
    data, sources = assemble(regenerated, destination)
    html = html_page(data)
    destination.mkdir(parents=True, exist_ok=True)
    copyfile(STEP, destination / 'completed.step')
    temporary = destination / 'index.html.tmp'
    temporary.write_text(html, encoding='utf-8')
    temporary.replace(destination / 'index.html')
    write(destination / 'manifest.json', {
        'source_sha256': source_manifest(sorted(sources)), 'page_sha256': sha(destination / 'index.html'),
        'candidate_count': len(data['plans']), 'geometry_count': len(data['geometries']),
        'local_endpoint_count': len(data['local_forming']),
        'plans': {pid: {'sample_count': len(replay['runs']), 'counts': replay['counts'], 'status': replay['status']}
                  for pid, replay in data['replays'].items()},
    })
    print(relative(destination / 'index.html'), len(data['plans']), 'plans', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--regenerated', action='store_true', help='Publish the newly selected P001 candidates')
    build(parser.parse_args().regenerated)
