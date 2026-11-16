"""Publish the regenerated P003 candidates in the staged-material workspace."""
from collections import Counter
import gzip
import json
from pathlib import Path
import shutil

import numpy as np

from information_extract.scripts import build_p003_stage_review as viewer
from information_extract.scripts.regenerate_p003_candidates import OUT
from information_extract.scripts.build_p003_material_basis import STEP
from information_extract.three_d.artifacts import ROOT, read, relative, sha, source_manifest, verify_sources, write
from information_extract.three_d.p003_motion import FORMS, REMOVALS, samples


def page_html(source, candidates, states):
    page = viewer.specialize_html(source)
    replace = viewer.once
    page = page.replace('P003 · 逐序材料与折弯回放', 'P003 · 重新生成方案回放')
    page = replace(page, 'O069 正折 135°，O120 反折 45°，两步共用 J297。同序运动使用进入态材料，去料与局部成形在完成端统一切换。',
        f'候选池 {candidates} 个，展示入选的 16 个方案，共复核 {states:,} 个逐序状态。'
        'O069 正折 135°，O120 反折 45°，两步共用 J297。同序动作共享进度。')
    page = replace(page, '<button id="playPlan" aria-pressed="false">播放全方案</button>',
        '<button id="playPlan" aria-pressed="false">播放全方案</button>\n'
        '    <label>播放速度 <select id="speed"><option value="1">1×</option><option value="2">2×</option>'
        '<option value="4">4×</option></select></label>')
    page = replace(page, '<p id="selectedInfo">', '<p id="motionReference" class="note"></p>\n  <p id="selectedInfo">')
    page = replace(page, '  <details id="stageFeatures">',
        '  <details open><summary>方案与工序</summary><p id="planStats" class="note"></p>'
        '<div id="stationOverview" class="table-scroll"></div></details>\n  <details id="stageFeatures">')
    page = replace(page, '<a href="../summary.json">全部方案干涉汇总</a>',
        '<a href="../summary.json">筛查与评分汇总</a> · <a href="../pool.json">全部候选</a>')
    page = replace(page, '<a href="../material_audit.json">材料动作依据</a>', '<a id="materialAudit">材料动作依据</a>')
    page = replace(page, '    #selectedInfo { min-height:22px; }',
        '    #selectedInfo:empty { display:none; }\n    #stationOverview { max-height:none; }')
    return replace(page, 'review.js?v=2', 'review.js?v=3')


def page_js(source):
    script = viewer.specialize_js(source)
    replace = viewer.once
    script = replace(script, "data=await load('data.json?v=2');", "data=await load('data.json?v=3');")
    script = replace(script, "      $('step').href=\x60../\x24{id}/completed.step\x60;",
                     "      $('step').href='completed.step';")
    script = replace(script, "      $('report').href=\x60../\x24{id}/collision_report.json\x60;", """
      const stats=data.planStats[id];
      $('report').href=stats.report;
      $('materialAudit').href=stats.materialAudit;
      $('planStats').textContent=[
        '评分第 '+stats.rank+' 名', 'P5 '+stats.p5, '次级评分 '+stats.secondary,
        stats.sampleCount+' 个采样', stats.pairStates.toLocaleString('zh-CN')+' 个区域对检查',
        stats.overlaps+' 个正体积交叠', stats.unknowns+' 个内核未决',
      ].join(' · ');
      table('stationOverview',['工序','动作','折弯','采样数','正体积交叠','内核未决'],stats.stations.map(s=>{
        const button=document.createElement('button');
        button.textContent=s.station;
        button.onclick=()=>{
          if (loadingPlan) return;
          $('comparison').value='stage';
          $('station').value=s.station;
          $('station').onchange();
        };
        return [button,String(s.actions),String(s.bends),String(s.samples),String(s.overlaps),String(s.unknowns)];
      }));""")
    script = replace(script, "    $('featureSummary').textContent = \x60本序特征（\x24{r.current_operation_ids.length}）\x60;",
        """    const full=r.removalCount===data.removalCount && r.formingCount===data.formingCount;
    $('motionReference').textContent=full ?
      '本采样参照 · 完成材料，J297 STEP 圆柱轴' : '本采样参照 · DXF 展开配准';
    const subdivision=r.phase==='motion' ? ' · '+Math.round(r.tau*data.subdivisions)+'/'+data.subdivisions+' 等分' : '';
    $('progress').textContent += subdivision;
    $('featureSummary').textContent = \x60本序特征（\x24{r.current_operation_ids.length}）\x60;""")
    return replace(script, '      i++;\n      timer=setTimeout', """      const limit=Math.min(i+Number($('speed').value),end);
      i++;
      while (i<limit && report.runs[i].phase==='motion') i++;
      timer=setTimeout""")


def plan_metadata(report, model):
    stats = {}
    bend_ids = {row['operation_id'] for row in model['operations'] if row['operation_type']=='BEND'}
    for run in report['runs']:
        ids = run['current_operation_ids']
        station = run['station_id']
        row = stats.setdefault(station, dict(station=station, actions=len(ids), bends=len(set(ids) & bend_ids),
                                             samples=0, overlaps=0, unknowns=0))
        counts = run['reference_geometry']['counts']
        row['samples'] += 1
        row['overlaps'] += counts.get('OVERLAP', 0)
        row['unknowns'] += counts.get('UNKNOWN_KERNEL_FAILURE', 0)
    return list(stats.values())


def display_assets(output, geom_sources, poses, color):
    for folder in ('flat_raster','packed'):
        (output/folder).mkdir(parents=True, exist_ok=True)
    rasters = {}
    for geometry, (source, cell) in sorted(geom_sources.items()):
        with gzip.open(source, 'rt', encoding='utf8') as stream:
            triangles = np.asarray(json.load(stream)['triangles'])
        if triangles.ndim != 3 or triangles.shape[1:] != (3, 3) or not len(triangles) or not np.isfinite(triangles).all():
            raise ValueError(f'Invalid display mesh {source}')
        viewer.pack_mesh(output/'packed'/f'{geometry}.f32.gz', triangles)
        if cell is not None:
            rasters[geometry] = viewer.raster_projection(
                output/'flat_raster'/f'{geometry}.png', triangles, poses[cell], color, geometry,
            )
    return rasters


def build():
    output = OUT/'stage'
    model_path = viewer.DEST/'model.json'
    model_manifest = viewer.DEST/'output_manifest.json'
    model_key = relative(model_path)
    verify_sources({model_key:read(model_manifest)['files'][model_key]})
    model = read(model_path)
    plans = read(OUT/'plans.json')
    summary = read(OUT/'summary.json')
    verify_sources(summary['sources'])
    ranked = plans['ranking']
    by_id = {p['plan_id']:p for p in plans['plans']}
    if model['part_id']!='P003' or len(ranked)!=16 or len(set(ranked))!=16 or set(ranked)!=by_id.keys():
        raise ValueError('Expected all sixteen regenerated P003 candidates')
    pool_path = OUT/'pool.json'
    pool = {p['plan_id']:p for p in read(pool_path)['plans']}
    if len(pool)!=summary['pool_size'] or any(
        pid not in pool or plan['assignments']!=pool[pid]['assignments'] for pid,plan in by_id.items()
    ):
        raise ValueError('The selected plans differ from the regenerated candidate pool')
    subdivisions = summary['refined_subdivisions']
    asset = read(viewer.ASSET)
    if asset['asset_id']!=sha(STEP):
        raise ValueError('The shared finished STEP differs from the motion reference')
    batches = {}
    for row in by_id.values():
        directory = (ROOT/row['motion_verification']['material_directory']).resolve()
        if directory.parent != OUT/'staged':
            raise ValueError('Expected a staged-material batch from the regenerated run')
        if directory not in batches:
            batches[directory] = read(directory/'material_states.json')
    first = next(iter(batches))
    audits = {b:read(b/'material_audit.json') for b in batches}
    if any(a!=audits[first] for a in audits.values()):
        raise ValueError('Material action references differ between batches')
    prep = [viewer.PREP/f'{name}.json' for name in ('skeleton','registration','geometry_paths')]
    features = viewer.features_for(model, asset=asset, audits=audits[first]['operations'],
        skeleton=read(prep[0]), reg=read(prep[1]), paths=read(prep[2]))
    variants = {b:{v['material_id']:v for v in data['variants']} for b,data in batches.items()}
    used_variants = {}
    geom_sources = {'COMPLETED_STEP':(first/'meshes/COMPLETED_STEP.json.gz', None)}
    references = [model_path, model_manifest, viewer.ASSET, STEP, *prep, OUT/'plans.json', pool_path, OUT/'summary.json',
                  Path(__file__), Path(viewer.__file__)]
    plan_stats = {}
    expected_pairs = len(model['cells'])*(len(model['cells'])-1)//2
    motion_reference = dict(partial_material='REGISTERED_DXF_FLAT_PLACEMENT',
                            completed_material='J297_STEP_CYLINDER_AXIS')
    replay_dir = output/'replay_plans'
    replay_dir.mkdir(parents=True, exist_ok=True)
    for rank,pid in enumerate(ranked, 1):
        plan = by_id[pid]
        check = plan['motion_verification']
        report_path = OUT/'selected'/pid/'collision_report.json'
        if (ROOT/check['report']).resolve()!=report_path:
            raise ValueError(f'Unexpected report path for {pid}')
        report = read(report_path)
        if (report['plan_id']!=pid or report['assignments']!=plan['assignments']
                or report['motion_reference']!=motion_reference):
            raise ValueError(f'Invalid motion report for {pid}')
        actual = [(s['station_id'],s['phase'],s['tau']) for s in report['runs']]
        if actual!=list(samples(model,plan,subdivisions)) or len(actual)!=check['sample_count']:
            raise ValueError(f'Wrong sampling grid for {pid}')
        batch = (ROOT/check['material_directory']).resolve()
        prefix = f'{batch.name}_' if len(batches)>1 else ''
        totals = Counter()
        for run in report['runs']:
            mid = run['material_id']
            cells = variants[batch][mid]['cells']
            if run['geometry_ids']!=cells or run['transforms'].keys()!=cells.keys():
                raise ValueError(f'Invalid material or transforms for {pid} {mid}')
            counts = run['reference_geometry']['counts']
            if run['reference_geometry']['pair_count']!=expected_pairs or sum(counts.values())!=expected_pairs:
                raise ValueError(f'Incomplete material pair counts for {pid}')
            if run['unapplied_material_event_ids']:
                raise ValueError(f'Unapplied material actions for {pid}')
            totals.update(counts)
            display_cells = {cell:prefix+geo for cell,geo in cells.items()}
            variant_id = prefix+mid
            used_variants[variant_id] = dict(material_id=variant_id, cells=display_cells)
            for cell,geo in cells.items():
                geom_sources[prefix+geo] = (batch/'meshes'/f'{geo}.json.gz',cell)
            run['material_id'] = variant_id
        if (dict(totals)!=report['summary']['counts'] or sum(totals.values())!=check['pair_states']
                or totals['OVERLAP'] or totals['UNKNOWN_KERNEL_FAILURE']):
            raise ValueError(f'Motion checks do not match the selected result for {pid}')
        plan_stats[pid] = dict(rank=rank, p5=plan['score']['p5']['exact'], secondary=plan['score']['secondary_exact'],
            sampleCount=len(actual), pairStates=check['pair_states'], overlaps=totals['OVERLAP'],
            unknowns=totals['UNKNOWN_KERNEL_FAILURE'], stations=plan_metadata(report,model),
            report='../'+report_path.relative_to(OUT).as_posix(),
            materialAudit='../'+(batch/'material_audit.json').relative_to(OUT).as_posix())
        viewer.write_replay_plan(replay_dir/f'{pid}.json.gz', report)
        references.append(report_path)
    total_states = sum(stats['sampleCount'] for stats in plan_stats.values())
    total_pairs = sum(stats['pairStates'] for stats in plan_stats.values())
    if total_states!=summary['selected_sample_count'] or total_pairs!=summary['selected_pair_states']:
        raise ValueError('The sixteen replay totals differ from the regeneration summary')
    references.extend(path for batch in batches for path in (batch/'material_states.json', batch/'material_audit.json'))
    references.extend(path for path,_ in geom_sources.values())
    web = viewer.WEB
    template, app, css = web/'p002_stage_workspace.html', web/'p002_stage_workspace.js', web/'local_forming_review.html'
    theme = viewer.MODULE/'configs/local_forming_display_v03.json'
    renderers = [web/name for name in ('core.js','finite_material_renderer.js','p003_replay_assets.js')]
    sources = source_manifest([*references, template, app, css, theme, *renderers])
    style = read(theme)
    rasters = display_assets(output, geom_sources, model['flat_transforms'], style['baseCss'])
    counts = Counter(o['operation_type'] for o in model['operations'])
    write(output/'data.json', dict(model={k:model[k] for k in ('operations','stations')},
        features=features, variants=list(used_variants.values()), ranking=ranked, flatRasters=rasters,
        removalCount=sum(counts[t] for t in REMOVALS), formingCount=sum(counts[t] for t in FORMS),
        planStats=plan_stats, subdivisions=subdivisions))
    (output/'index.html').write_text(page_html(template.read_text('utf8'), summary['pool_size'], total_states), encoding='utf8')
    (output/'review.js').write_text(page_js(app.read_text('utf8')), encoding='utf8')
    (output/'review.css').write_text(css.read_text('utf8').split('<style>',1)[1].split('</style>',1)[0], encoding='utf8')
    (output/'theme.js').write_text('globalThis.FiniteMaterialTheme='+json.dumps(style)+';\n', encoding='utf8')
    for path in renderers:
        shutil.copyfile(path, output/path.name)
    shutil.copyfile(STEP, output/'completed.step')
    if sha(output/'completed.step')!=asset['asset_id']:
        raise ValueError('The displayed STEP differs from the motion reference')
    verify_sources(sources)
    published = [output/name for name in ('index.html','review.js','review.css','data.json','theme.js','completed.step')]
    published.extend(output/path.name for path in renderers)
    published.extend(replay_dir/f'{pid}.json.gz' for pid in ranked)
    published.extend(output/'packed'/f'{gid}.f32.gz' for gid in geom_sources)
    published.extend(output/'flat_raster'/f'{gid}.png' for gid in rasters)
    write(output/'verification.json', dict(plan_count=len(ranked), feature_count=len(features),
        sample_count=total_states, pair_states=total_pairs, subdivisions=subdivisions, flat_rasters=len(rasters),
        meshes=len(geom_sources), source_files=sources, output_files=source_manifest(published)))
    print('P003 regenerated replay', len(ranked), 'plans', total_states, 'samples', len(rasters), 'projections', relative(output))


if __name__=='__main__':
    build()
