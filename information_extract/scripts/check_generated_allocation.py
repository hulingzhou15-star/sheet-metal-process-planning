"""Rebuild and diagnose the selected geometry-P1 allocations, without reranking."""
import argparse
from collections import Counter
from pathlib import Path
import time
import numpy as np

from information_extract.three_d.artifacts import ROOT, read, write, sha
from information_extract.three_d.allocation_policy import load_context, AllocationEngine
from information_extract.three_d.allocation_material_states import rebuild, compare_materials
from information_extract.three_d.step_detail_endpoints import extract_details
from information_extract.three_d.band_material import state_transforms, station_fractions
from information_extract.scripts.experiment_bend_allocation_3d import protected_hashes, kernel_controls, digest
from information_extract.scripts.check_local_forming_collision import run, make_samples, checked_checkpoint
from information_extract.scripts.check_all_local_forming_collisions import summarize

MODULE = ROOT/'information_extract'
SOURCE = MODULE/'outputs/local_forming_v03/P001'
SELECTED = MODULE/'outputs/geometry_allocation_v1_generated/P001'
DEST = MODULE/'outputs/generated_allocation_collision_v01/P001'


def publish(out, plans, reports, elapsed):
    rows=[]
    for report in reports:
        row=summarize(report)
        row['status']=('UNRESOLVED_KERNEL' if row['unknown_pair_states'] else
                       'MODEL_OVERLAP_DETECTED' if row['positive_volume_pair_states'] else 'NO_OVERLAP_AT_SAMPLES')
        rows.append(row)
    summary=dict(complete=len(rows)==len(plans),requested_count=len(plans),completed_count=len(rows),rows=rows,
        totals={k:sum(r[k] for r in rows) for k in ('sample_count','pair_state_count','positive_volume_pair_states','unknown_pair_states','kernel_calls')},
        elapsed_seconds=round(elapsed,2),candidate_filter_enabled=False,manufacturing_safety_proved=False,
        scope='All rigid region pairs; entry material during shared station motion, completed endpoints switch together. TRIM excluded from research scope. No finite bend-band pairs, continuous local forming or tooling/fixtures.')
    write(out/'summary.json',summary)
    table=''.join(f'<tr><td><a href="{r["plan_id"]}/collision_report.json">{r["plan_id"]}</a></td><td>{r["sample_count"]}</td><td>{r["positive_volume_pair_states"]}</td><td>{r["unknown_pair_states"]}</td><td>{r["status"]}</td></tr>' for r in rows)
    page='''<!doctype html><html lang="zh"><meta charset="utf-8"><title>P001 新生成入选方案检查</title>
<style>body{font:16px system-ui;margin:32px;color:#234;background:#f5f7fa}table{border-collapse:collapse;background:white}td,th{border:1px solid #ccd;padding:10px}aside{padding:16px;background:#fff1d7}</style>
<h1>P001 · 几何P1入选16方案的逐序材料与运动检查</h1>
<aside>逐方案重建去料和局部成形状态，凸米与刻印采用STEP成品端态。同序动作同时加工，运动使用进入态材料，完成端统一切换。切边不属于研究范围。未覆盖折弯带全对、连续局部成形、工具/装夹。不检出交叠不证明制造安全；交叠不自动判候选失败或改变评分。</aside>
<p>本页方案编号属于新生成集合；即使与冻结编号同名，也不代表同一分配。</p>
<p><a href="summary.json">汇总</a> · <a href="verification.json">材料和动作核验</a> · <a href="plans.json">本次分配</a> · <a href="inputs.json">来源指纹</a></p>'''
    page+=f'<p>完成 {len(rows)}/{len(plans)}</p><table><tr><th>方案</th><th>状态数</th><th>正体积交叠对×状态</th><th>计算未决</th><th>诊断</th></tr>{table}</table></html>'
    (out/'index.html').write_text(page,encoding='utf-8')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=DEST)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--steps',type=int,default=24)
    args=parser.parse_args();out=args.output.resolve()
    if not out.is_relative_to(MODULE/'outputs') or out==MODULE/'outputs' or out==SOURCE or out==SELECTED:
        raise ValueError('Independent geometry output directory required')
    if args.steps<2:raise ValueError('At least two subdivisions')
    started=time.monotonic();protected=protected_hashes()
    viewer=read(SOURCE/'viewer_data.json');payload=read(SELECTED/'plans.json');plans=payload['plans']
    if payload['mode']!='generate' or len(plans)!=16:raise ValueError('Expected selected 16 generated plans')
    for p,h in read(SELECTED/'inputs.json')['files'].items():
        if sha(ROOT/p)!=h:raise ValueError('Stale generation source: '+p)
    for p,h in viewer['model']['source_sha256'].items():
        if sha(ROOT/p)!=h:raise ValueError('Stale geometry source: '+p)
    workbook=ROOT/'data/parts/P001/钣金加工数据表_P001.xlsx'
    context=load_context(workbook);engine=AllocationEngine(context)
    for p in plans:engine.require_feasible(p['assignments'])
    excluded={o for o,x in context.operations.items() if x.operation_type=='TRIM'}
    for rel in context.precedence_relations:
        if rel.predecessor_id in excluded and rel.operation_id not in excluded:
            raise ValueError('Active action depends on excluded trim')
    plans=[{**p,'assignments':{o:s for o,s in p['assignments'].items() if o not in excluded}} for p in plans]
    if {s['station_id']:s['order'] for s in viewer['stations']}!={sid:s.order for sid,s in context.stations.items()}:
        raise ValueError('Geometry stations differ from planning facts')
    files={workbook,Path(__file__),SOURCE/'viewer_data.json',SOURCE/'flanging_step_inventory.json',ROOT/'registry/protected_inputs.json'}
    files.update(SOURCE.glob('brep/*.brep'))
    files.update(SELECTED.glob('*.json'))
    files.update((MODULE/'three_d').glob('*.py'))
    files.update((MODULE/'extract_app').rglob('*.py'))
    files.update(MODULE/p for p in ('outputs/stage_review_v01/P001/stage_data.json','projects/P001/project.json','configs/app.json','configs/three_d_state_v01.json','sources/P001/Plotview 1.dxf','sources/P001/formed.step'))
    files.update((ROOT/'src').rglob('*.py'))
    files.update(MODULE/'scripts'/p for p in ('check_local_forming_collision.py','check_all_local_forming_collisions.py','experiment_bend_allocation_3d.py','build_local_forming_states.py','build_finite_material_states.py'))
    hashes={p.relative_to(ROOT).as_posix():sha(p) for p in sorted(files)}
    signature=digest(dict(files=hashes,steps=args.steps))
    if (out/'inputs.json').exists() and read(out/'inputs.json')['signature']!=signature:
        raise ValueError('Inputs changed; choose a fresh output directory')
    out.mkdir(parents=True,exist_ok=True)
    write(out/'inputs.json',dict(signature=signature,files=hashes,steps=args.steps))
    write(out/'plans.json',{**payload,'plans':plans,'excluded_from_execution':sorted(excluded),
        'source_plans':'information_extract/outputs/geometry_allocation_v1_generated/P001/plans.json',
        'score_scope':'Scores preserved from original 224-record generation; no rescoring of the 214-action execution projection'})
    if args.resume and (out/'material_manifest.json').exists():
        for p,h in read(out/'material_manifest.json')['files'].items():
            if sha(ROOT/p)!=h:raise ValueError('Rebuilt material changed: '+p)
        v=read(out/'viewer_data.json');verification=read(out/'verification.json')
    else:
        details,detail_audit=extract_details(viewer,SOURCE,out)
        write(out/'step_detail_audit.json',detail_audit)
        v=rebuild(viewer,plans,SOURCE,out,details)
        comparisons=compare_materials(viewer,SOURCE,v,out,details)
        bends={b['operation_id'] for b in v['model']['bends']}
        if bends!={o for o,x in context.operations.items() if x.operation_type=='BEND'}:
            raise ValueError('Incomplete bend coverage')
        events=set(v['step_comparison'])|{r['operation_id'] for r in v['removal_coverage']}
        unsupported=[dict(operation_id=o,operation_type=x.operation_type) for o,x in context.operations.items() if o not in events|bends|excluded]
        if unsupported:raise ValueError('Uncovered active actions: '+str(unsupported))
        # Final rigid transforms must be independent of allocation.
        orders={s['station_id']:s['order'] for s in v['stations']};last=max(orders,key=orders.get)
        expected=state_transforms(v['model'],{b['joint_id']:1. for b in v['model']['bends']})
        for p in plans:
            actual=state_transforms(v['model'],station_fractions(v['model'],p['assignments'],orders,last,1.))
            if any(not np.allclose(actual[r],expected[r],atol=1e-10,rtol=0) for r in expected):
                raise ValueError('Final rigid pose differs')
        verification=dict(protection=protected,all_hard_feasible=True,H6_enabled=False,H7_preserved=True,
            material_state_count=len(v['material_states']),material_variant_count=len(v['variants']),
            shared_material_comparisons=comparisons,final_rigid_transforms_equal=True,
            covered_bend_count=len(bends),covered_removal_count=len(v['removal_coverage']),covered_local_forming_count=len(v['step_comparison']),
            unsupported_geometry_actions=unsupported,kernel_controls=kernel_controls(),
            same_station_semantics='ENTRY during shared motion; all current material events switch only at COMPLETED',
            selected_assignments_unchanged=True,manufacturing_safety_proved=False)
        verification.update(excluded_from_execution=sorted(excluded),active_operation_count=len(context.operations)-len(excluded),step_detail_audit=detail_audit,
            source_assignments_preserved=True,execution_projection='Original assignments minus excluded TRIM; all other assignments unchanged')
        write(out/'viewer_data.json',v);write(out/'verification.json',verification)
        material_files=[out/'viewer_data.json',*out.glob('brep/*.brep'),*out.glob('detail_brep/*.brep')]
        write(out/'material_manifest.json',dict(files={p.relative_to(ROOT).as_posix():sha(p) for p in material_files}))
    reports=[];shared={};publish(out,plans,reports,0)
    for p in plans:
        folder=out/p['plan_id'];saved=folder/'collision_report.json'
        if args.resume and saved.exists():
            report=read(saved);samples=make_samples(v,p,args.steps);checked_checkpoint(report,signature,samples)
            if report['plan_id']!=p['plan_id'] or len(report['runs'])!=len(samples):raise ValueError('Incomplete saved report')
        else:
            report=run(p['plan_id'],v,folder,shared,signature,args.resume,args.steps,geometry_root=out,historical_recheck=False)
        reports.append(report);summary=publish(out,plans,reports,time.monotonic()-started)
        print('COMPLETE',len(reports),'/',len(plans),summarize(report),flush=True)
    if any(sha(ROOT/p)!=h for p,h in hashes.items()) or protected_hashes()!=protected:
        raise ValueError('Sources changed during run')
    verification.update(input_files_unchanged=len(hashes),complete=True,elapsed_seconds=summary['elapsed_seconds'])
    write(out/'verification.json',verification)
    print('TOTALS',summary['totals'],flush=True)


if __name__=='__main__':main()
