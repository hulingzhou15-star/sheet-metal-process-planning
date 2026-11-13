"""Batch the frozen candidates through the rigid-region sampled diagnostic."""
import argparse
from collections import Counter
from pathlib import Path
import hashlib
import html
import json
import time

from information_extract.scripts.check_local_forming_collision import (
    OUT, MODULE, read, sha, run, make_samples, atomic_write, checked_checkpoint,
)


def input_signature(viewer, steps):
    files = {OUT/'viewer_data.json', MODULE/'baselines/P001/previous_panel_issues.json'}
    files.update(OUT/g['brep_file'] for g in viewer['geometries'].values())
    files.update((MODULE/'three_d').rglob('*.py'))
    files.update(Path(__file__).parent/p for p in (
        'check_local_forming_collision.py', 'check_all_local_forming_collisions.py'))
    hashes = {p.as_posix(): sha(p) for p in sorted(files)}
    payload = {'files': hashes, 'steps': steps}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(), payload


def summarize(report):
    counts = Counter()
    for state in report['runs']:
        counts.update(state['counts'])
    return {
        'plan_id': report['plan_id'], 'sample_count': report['sample_count'],
        'pair_state_count': sum(r['pair_count'] for r in report['runs']),
        'overlap_pair_states': counts['OVERLAP'],
        'unknown_pair_states': counts['UNKNOWN_KERNEL_FAILURE'],
        'positive_volume_pair_states': sum(p['intersection_volume_mm3'] > 1e-6
            for r in report['runs'] for p in r['issues']),
        'issue_states': sum(bool(r['issues']) for r in report['runs']),
        'kernel_calls': report['kernel_calls'],
        'motion_stations': sorted({r['station_id'] for r in report['runs'] if r['phase']=='motion'}),
        'local_component_overlap_samples': len(report['local_forming_overlap_samples']),
    }


def publish_summary(destination, reports, requested, signature, elapsed, baseline):
    rows = [summarize(r) for r in reports]
    regression = None
    new = next((r for r in reports if r['plan_id']=='NP2_001'), None)
    if new and baseline:
        # Compare actual sampled outcomes; cache sharing can change kernel call counts.
        keys = ('station_id', 'phase', 'tau', 'material_id', 'pair_count', 'counts', 'issues')
        pick = lambda report: [{k:r[k] for k in keys} for r in report['runs']]
        regression = {'baseline_sample_count':baseline['sample_count'],
                      'sample_outcomes_identical':pick(new)==pick(baseline),
                      'old_62_recheck_summary':new['old_62_recheck_summary']}
    result = {'signature':signature,'requested_plans':requested,
              'completed_plans':len(rows),'complete':len(rows)==len(requested),
              'elapsed_seconds':round(elapsed,2),'rows':rows,'np2_001_regression':regression,
              'scope':'Rigid region pairs with STEP local endpoint patches; entry material during motion; finite bands, continuous local forming, tools and fixtures excluded.',
              'candidate_filter_enabled':False,'manufacturing_safety_proved':False}
    atomic_write(destination/'summary.json',result)
    table=''.join(f'<tr><td><a href="{r["plan_id"]}/collision_report.json">{r["plan_id"]}</a></td>'
                  f'<td>{", ".join(r["motion_stations"])}</td><td>{r["sample_count"]}</td>'
                  f'<td>{r["pair_state_count"]}</td><td>{r["positive_volume_pair_states"]}</td>'
                  f'<td>{r["unknown_pair_states"]}</td></tr>' for r in rows)
    details=[]
    for report in reports:
        issues=[{'station':s['station_id'],'phase':s['phase'],'tau':s['tau'],**p}
                for s in report['runs'] for p in s['issues']]
        if issues:
            details.append(f'<details><summary>{report["plan_id"]} 问题明细</summary><pre>'
                           +html.escape(json.dumps(issues,ensure_ascii=False,indent=2))+'</pre></details>')
    page='''<!doctype html><html lang="zh"><meta charset="utf-8"><title>P001 · 16 候选采样干涉汇总</title>
<style>body{font:16px system-ui;margin:32px;color:#233344;background:#f5f7fa}table{border-collapse:collapse;background:white}th,td{padding:12px;border:1px solid #ccd4dd}pre{white-space:pre-wrap}aside{padding:16px;background:#fff3d9}</style>
<h1>P001 · 候选运动采样干涉检查</h1>
<aside>检查 60 个刚性区域全对，包含已接入的局部成形实体。运动中使用进入态材料，同序统一进度，完成态统一切换。未覆盖折弯带全对、连续局部成形、工具与装夹。无交叠不等于已证明可制造；交叠不自动判候选失败。</aside>'''
    page+=f'<p>已完成 {len(rows)} / {len(requested)} 个候选。每道实际折弯工序 23 个内部采样点，另检查各工序进入/完成态。</p>'
    page+='<p><a href="../review.html">打开三维审核页（按表中候选、工序和进度定位）</a> · <a href="summary.json">汇总 JSON</a></p>'
    page+='<table><tr><th>候选 / 报告</th><th>运动采样工序</th><th>状态数</th><th>板面对×状态</th><th>正体积交叠</th><th>计算未决</th></tr>'+table+'</table>'
    if regression is not None:
        page+='<p>NP2_001 与旧报告逐采样结果一致：'+str(regression['sample_outcomes_identical'])+'</p>'
    page+=''.join(details)+'</html>'
    (destination/'index.html').write_text(page,encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',action='append',help='Repeat for selected plans; default all 16')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--output',type=Path,default=OUT/'collision_batch_v01')
    args=parser.parse_args()
    viewer=read(OUT/'viewer_data.json')
    available={p['plan_id']:p for p in viewer['plans']}
    requested=list(dict.fromkeys(args.plan or available))
    if set(requested)-available.keys():parser.error('Unknown plan id')
    signature,inputs=input_signature(viewer,24)
    destination=args.output
    destination.mkdir(parents=True,exist_ok=True)
    manifest=destination/'inputs.json'
    if manifest.exists() and read(manifest)['signature']!=signature:
        raise ValueError('Existing batch has different inputs/code; use --output for a new run')
    atomic_write(manifest,{'signature':signature,**inputs})
    baseline=read(OUT/'collision_report.json') if (OUT/'collision_report.json').exists() else None
    shared={};reports=[];started=time.monotonic()
    publish_summary(destination,reports,requested,signature,0,baseline)
    for pid in requested:
        folder=destination/pid
        report_path=folder/'collision_report.json'
        if args.resume and report_path.exists():
            report=read(report_path)
            samples=make_samples(viewer,available[pid])
            checked_checkpoint(report,signature,samples)
            if report['plan_id']!=pid or len(report['runs'])!=len(samples):
                raise ValueError('Incomplete or incorrect report')
            print('Reuse completed report',pid,flush=True)
        else:
            report=run(pid,viewer,folder,shared,signature,args.resume)
        reports.append(report)
        summary=publish_summary(destination,reports,requested,signature,time.monotonic()-started,baseline)
        print('BATCH',len(reports),'/',len(requested),summarize(report),flush=True)
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    main()
