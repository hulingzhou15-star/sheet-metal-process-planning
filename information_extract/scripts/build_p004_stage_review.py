"""Publish verified P004 staged samples with the shared dual-view replay."""
from collections import Counter
import json
from pathlib import Path
import shutil

import numpy as np

from information_extract.scripts import build_p003_regenerated_review as shared
from information_extract.scripts import build_p003_stage_review as viewer
from information_extract.three_d.artifacts import ROOT, read, relative, sha, source_manifest, verify_sources, write
from information_extract.three_d.p003_motion import FORMS, REMOVALS
from information_extract.three_d.p004_motion import Schedule

MODULE = ROOT/'information_extract'
OUT = MODULE/'outputs/p004_motion_regeneration_v01'
PREP = MODULE/'outputs/p004_generation_v01'
ASSET = MODULE/'outputs/multi_part_step_v01/P004/asset.json'
STEP = MODULE/'sources/P004/originals/P004.stp'


def page_html(source, candidates, selected, states):
    page = shared.page_html(source, candidates, states).replace('P003 ·', 'P004 ·')
    replace = viewer.once
    page = replace(page, f'候选池 {candidates} 个，展示入选的 16 个方案，共复核 {states:,} 个逐序状态。'
        'O069 正折 135°，O120 反折 45°，两步共用 J297。同序动作共享进度。',
        f'候选池 {candidates} 个，展示入选的 {selected} 个方案，共复核 {states:,} 个逐序状态。'
        '同序折弯共享进度，去料和局部成形在工序完成时更新。')
    page = replace(page, '<button id="curveExample">第一步 · O069</button>', '')
    page = replace(page, '<button id="drawExample">第二步 · O120</button>', '')
    return replace(page, '#selectedInfo:empty { display:none; }',
                   '#selectedInfo:empty, #jointAngle:empty, #motionReference:empty { display:none; }')


def page_js(source):
    script = shared.page_js(source)
    script = script.replace("doc.title='P003 · '+title;", "doc.title='P004 · '+title;")
    script = script.replace('p003-view-', 'p004-view-').replace('window.p003StageReview', 'window.p004StageReview')
    replace = viewer.once
    script = replace(script, "FLANGING:'翻孔',DIMPLE:'凸米',DRAW_FORM:'拉包'", "FLANGING:'翻孔',DIMPLE:'凸米',DRAW_FORM:'拉包',MARKING:'刻印'")
    script = replace(script, "    const full=r.removalCount===data.removalCount && r.formingCount===data.formingCount;\n"
        "    $('motionReference').textContent=full ?\n"
        "      '本采样参照 · 完成材料，J297 STEP 圆柱轴' : '本采样参照 · DXF 展开配准';",
        """    const beforeFolding=data.preFoldStations.includes(r.station_id);
    const reference=beforeFolding ? 'DXF 展开配准' : data.foldReference;
    const forming=r.phase==='motion' && r.current_operation_ids.some(id=>data.formingTypes.includes(operations.get(id).operation_type));
    $('motionReference').textContent='本采样参照 · '+reference+
      (forming ? ' · 共同加工采样使用进入态材料，局部成形在完成态切换' : '');""")
    script = viewer.replace_block(script, 'function name(op)', 'function sizeCanvases()', """  function name(op) {
    return op.operation_type==='BEND' && Number.isFinite(op.bend_angle_deg) ?
      op.name+'（夹角 '+op.bend_angle_deg+'°）' : op.name;
  }""")
    script = replace(script, """    const angle = r.joint_angles_deg?.J297;
    $('jointAngle').textContent = Number.isFinite(angle) ?
      \x60J297 累计折弯角 \x24{Math.abs(angle)<.00001 ? '0' : angle.toFixed(2)}°\x60 : '';""",
        """    const angles=(data.bendJoints[picked] || []).map(j=>r.joint_angles_deg?.[j]).filter(Number.isFinite);
    $('jointAngle').textContent=angles.length
      ? '当前采样折角 '+angles.map(angle=>Math.abs(angle).toFixed(2)+'°').join(' / ') : '';""")
    script = replace(script, "    $('curveExample').onclick=run(()=>jumpToFeature('O069','completed'));\n", '')
    return replace(script, "    $('drawExample').onclick=run(()=>jumpToFeature('O120','completed'));\n", '')


def stage_features(model, asset, skeleton, registration, audits, paths, axis_offsets):
    features = viewer.features_for(
        {**model, 'joints': []}, skeleton, asset, registration, audits, paths,
    )
    inverse = {c:np.linalg.inv(pose) for c,pose in model['flat_transforms'].items()}
    bend_joints = {}
    for joint in model['joints']:
        cell = joint['parent_region_id']
        offset = np.asarray(axis_offsets.get(joint['joint_id'],[0.,0.,0.]))
        lines = [viewer.transform(line['endpoints'],inverse[cell])+offset
                 for line in joint['developed_axis_segment_evidence_mm']]
        if not lines:
            raise ValueError(f"Missing display support for {joint['joint_id']}")
        outline = viewer.group(cell,lines,joint['reference_surface_normals']['parent'])
        for step in joint['steps']:
            oid = step['operation_id']
            features[oid]['groups'].append(outline)
            features[oid]['spatial_location'] = 'STEP_AXIAL_SEGMENT_EVIDENCE'
            bend_joints.setdefault(oid,[]).append(joint['joint_id'])
    return features,bend_joints


def build():
    output = OUT/'stage'
    model_path, pool_path, plans_path, summary_path = (OUT/f'{k}.json' for k in ('model','pool','plans','summary'))
    model, plans, summary = map(read, (model_path,plans_path,summary_path))
    verify_sources(summary['sources'])
    ranked = plans['ranking']
    by_id = {p['plan_id']:p for p in plans['plans']}
    if model['part_id']!='P004' or not ranked or len(set(ranked))!=len(ranked) or set(ranked)!=by_id.keys():
        raise ValueError('Expected a nonempty verified P004 selection')
    pool = {p['plan_id']:p for p in read(pool_path)['plans']}
    if len(pool)!=summary['pool_size'] or any(
        pid not in pool or p['assignments']!=pool[pid]['assignments'] for pid,p in by_id.items()
    ):
        raise ValueError('The selected plans differ from the screened pool')
    subdivisions = summary['refined_subdivisions']
    axis = summary['axis']
    fold_reference = {'midplane':'STEP 中面交线轴','step-cylinder':'STEP 圆柱轴'}[axis]
    axis_offsets = summary['axis_offsets_mm'] if axis=='step-cylinder' else {}
    first_fold_order = next(s['order'] for s in model['stations'] if s['station_id']=='OP40')
    pre_fold = [s['station_id'] for s in model['stations'] if s['order']<first_fold_order]
    asset = read(ASSET)
    if asset['asset_id']!=sha(STEP):
        raise ValueError('The finished STEP differs from the motion reference')
    material_directories = {(ROOT/p['motion_verification']['material_directory']).resolve() for p in by_id.values()}
    if any(directory.parent!=OUT/'staged' for directory in material_directories):
        raise ValueError('Expected staged-material batches from the P004 run')
    batches = {b:read(b/'material_states.json') for b in sorted(material_directories)}
    first = next(iter(batches))
    audits = {b:read(b/'material_audit.json') for b in batches}
    if any(a['operations']!=audits[first]['operations'] for a in audits.values()):
        raise ValueError('Material action references differ between batches')
    prep = [PREP/f'{name}.json' for name in ('skeleton','registration','geometry_paths')]
    features,bend_joints = stage_features(model,asset,read(prep[0]),read(prep[1]),
                                         audits[first]['operations'],read(prep[2]),axis_offsets)
    variants = {b:{v['material_id']:v for v in data['variants']} for b,data in batches.items()}
    material_records = {
        b:{(r['plan_id'],r['station_id'],r['phase']):r for r in data['records']} for b,data in batches.items()
    }
    geometry = {'COMPLETED_STEP':(first/'meshes/COMPLETED_STEP.json.gz',None)}
    used_variants, plan_stats = {}, {}
    references = [model_path, ASSET, STEP, *prep, plans_path, pool_path, summary_path,
                  Path(__file__), Path(shared.__file__), Path(viewer.__file__)]
    expected_pairs = len(model['cells'])*(len(model['cells'])-1)//2
    replay_dir = output/'replay_plans'
    replay_dir.mkdir(parents=True, exist_ok=True)
    for rank,pid in enumerate(ranked, 1):
        plan = by_id[pid]
        check = plan['motion_verification']
        report_path = (OUT/'selected'/pid/'collision_report.json').resolve()
        if report_path.parent.parent!=OUT/'selected' or (ROOT/check['report']).resolve()!=report_path:
            raise ValueError(f'Unexpected report path for {pid}')
        report = read(report_path)
        motion_reference = dict(before_op40='P004_DXF_REGISTERED_FLAT',op40_and_later=axis)
        if (report['plan_id']!=pid or report['assignments']!=plan['assignments']
                or report['motion_reference']!=motion_reference):
            raise ValueError(f'Invalid motion report for {pid}')
        schedule = Schedule(model,plan)
        actual = [(s['station_id'],s['phase'],s['tau']) for s in report['runs']]
        if (actual!=list(schedule.samples(subdivisions))
                or len(actual)!=check['sample_count'] or len(actual)!=report['sample_count']):
            raise ValueError(f'Wrong sampling grid for {pid}')
        batch = (ROOT/check['material_directory']).resolve()
        prefix = f'{batch.name}_' if len(batches)>1 else ''
        totals = Counter()
        for run in report['runs']:
            mid = run['material_id']
            sid, phase = run['station_id'], run['phase']
            endpoint = 'completed' if phase=='completed' else 'entry'
            record = material_records[batch][pid,sid,endpoint]
            event_ids = tuple(tuple(sorted(run[k])) for k in ('executed_removal_ids','executed_forming_ids'))
            if mid!=record['material_id'] or event_ids!=schedule.material_key(sid,phase):
                raise ValueError(f'Material phase differs from the staged plan for {pid} {sid} {phase}')
            cells = variants[batch][mid]['cells']
            if run['geometry_ids']!=cells or run['transforms'].keys()!=cells.keys() or any(
                np.asarray(pose).shape!=(4,4) or not np.isfinite(pose).all() for pose in run['transforms'].values()
            ):
                raise ValueError(f'Invalid material or transforms for {pid} {mid}')
            if sorted(run['current_operation_ids'])!=schedule.current[sid]:
                raise ValueError(f'Actions differ from the plan at {pid} {sid}')
            counts = run['reference_geometry']['counts']
            if run['reference_geometry']['pair_count']!=expected_pairs or sum(counts.values())!=expected_pairs:
                raise ValueError(f'Incomplete material pair counts for {pid}')
            if run['unapplied_material_event_ids']:
                raise ValueError(f'Unapplied material actions for {pid}')
            totals.update(counts)
            variant_id = prefix+mid
            used_variants[variant_id] = dict(material_id=variant_id,cells={c:prefix+g for c,g in cells.items()})
            for cell,geo in cells.items():
                pair = batch/'meshes'/f'{geo}.json.gz',cell
                if geometry.setdefault(prefix+geo,pair)!=pair:
                    raise ValueError(f'Conflicting material mesh {geo}')
            run['material_id'] = variant_id
        if (dict(totals)!=report['summary']['counts'] or sum(totals.values())!=check['pair_states']
                or totals['OVERLAP'] or totals['UNKNOWN_KERNEL_FAILURE']):
            raise ValueError(f'Motion checks do not match the verified selection for {pid}')
        plan_stats[pid] = dict(rank=rank,p5=plan['score']['p5']['exact'], secondary=plan['score']['secondary_exact'],
            sampleCount=len(actual), pairStates=check['pair_states'], overlaps=totals['OVERLAP'],
            unknowns=totals['UNKNOWN_KERNEL_FAILURE'], stations=shared.plan_metadata(report,model),
            report='../'+report_path.relative_to(OUT).as_posix(),
            materialAudit='../'+(batch/'material_audit.json').relative_to(OUT).as_posix())
        viewer.write_replay_plan(replay_dir/f'{pid}.json.gz',report)
        references.append(report_path)
    states = sum(p['sampleCount'] for p in plan_stats.values())
    pairs = sum(p['pairStates'] for p in plan_stats.values())
    if states!=summary['selected_sample_count'] or pairs!=summary['selected_pair_states']:
        raise ValueError('Replay totals differ from the regeneration summary')
    references.extend(path for b in batches for path in (b/'material_states.json',b/'material_audit.json'))
    references.extend(path for path,_ in geometry.values())
    web = viewer.WEB
    template, app, css = web/'p002_stage_workspace.html',web/'p002_stage_workspace.js',web/'local_forming_review.html'
    theme = MODULE/'configs/local_forming_display_v03.json'
    renderers = [web/name for name in ('core.js','finite_material_renderer.js','p003_replay_assets.js')]
    sources = source_manifest([*references,template,app,css,theme,*renderers])
    style = read(theme)
    rasters = shared.display_assets(output,geometry,model['flat_transforms'],style['baseCss'])
    counts = Counter(o['operation_type'] for o in model['operations'])
    write(output/'data.json',dict(model={k:model[k] for k in ('operations','stations')},
        features=features, variants=list(used_variants.values()), ranking=ranked, flatRasters=rasters,
        removalCount=sum(counts[t] for t in REMOVALS), formingCount=sum(counts[t] for t in FORMS),
        subdivisions=subdivisions, planStats=plan_stats, formingTypes=sorted(FORMS), bendJoints=bend_joints,
        foldReference=fold_reference, preFoldStations=pre_fold))
    (output/'index.html').write_text(page_html(template.read_text('utf8'),summary['pool_size'],len(ranked),states),encoding='utf8')
    (output/'review.js').write_text(page_js(app.read_text('utf8')),encoding='utf8')
    (output/'review.css').write_text(css.read_text('utf8').split('<style>',1)[1].split('</style>',1)[0],encoding='utf8')
    (output/'theme.js').write_text('globalThis.FiniteMaterialTheme='+json.dumps(style)+';\n',encoding='utf8')
    for path in renderers:
        shutil.copyfile(path,output/path.name)
    shutil.copyfile(STEP,output/'completed.step')
    if sha(output/'completed.step')!=asset['asset_id']:
        raise ValueError('The displayed STEP differs from the motion reference')
    verify_sources(sources)
    published = [output/name for name in ('index.html','review.js','review.css','data.json','theme.js','completed.step')]
    published.extend(output/p.name for p in renderers)
    published.extend(replay_dir/f'{pid}.json.gz' for pid in ranked)
    published.extend(output/'packed'/f'{gid}.f32.gz' for gid in geometry)
    published.extend(output/'flat_raster'/f'{gid}.png' for gid in rasters)
    write(output/'verification.json',dict(plan_count=len(ranked),feature_count=len(features),sample_count=states,
        pair_states=pairs, subdivisions=subdivisions, axis=axis, flat_rasters=len(rasters), meshes=len(geometry),
        source_files=sources,output_files=source_manifest(published)))
    print('P004 replay', len(ranked), 'plans', states, 'samples', relative(output))


if __name__=='__main__':
    build()
