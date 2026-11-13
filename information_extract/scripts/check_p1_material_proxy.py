"""Candidate P1 input replacement: physical carried volume, NOT a validated rule.

Retains the old small-before-large hypothesis and 1.15 contrast for controlled
comparison. New geometry magnitudes come from material subtrees, never Sheet5.
The old scores are joined only after the new observations are computed.
"""
from itertools import combinations
from pathlib import Path
import html
import time
import math

from information_extract.three_d.allocation_policy import load_context
from information_extract.three_d.allocation_metrics import MetricProbe
from information_extract.scripts.experiment_bend_allocation_3d import ROOT,OUT,WORKBOOK,read,sha,atomic_write,digest
from information_extract.scripts.check_p1_replacement import DEST


def subtree_regions(model, start):
    children={r['region_id']:[] for r in model['regions']}
    for b in model['bends']:
        children[b['parent_region_id']].append(b['child_region_id'])
    result,todo=set(),[start]
    while todo:
        r=todo.pop()
        if r in result:raise ValueError('Cyclic or duplicated material subtree')
        result.add(r);todo.extend(children[r])
    return result


def proxy_ordering(context, assignments, magnitudes, ratio=1.15):
    expected={o.operation_id for o in context.operations.values() if o.operation_type=='BEND'}
    if set(magnitudes)!=expected or any(not math.isfinite(v) or v<=0 for v in magnitudes.values()):
        raise ValueError('Positive geometric magnitude required for every bend')
    compared,excluded,insignificant=[],[],[]
    for a,b in combinations(sorted(magnitudes),2):
        if b in context.precedence_reachability[a] or a in context.precedence_reachability[b]:
            excluded.append([a,b]);continue
        large,small=(a,b) if magnitudes[a]>=magnitudes[b] else (b,a)
        if magnitudes[large]/magnitudes[small]<=ratio:
            insignificant.append([a,b]);continue
        compared.append(dict(larger=large,smaller=small,
            inversion=context.stations[assignments[large]].order<context.stations[assignments[small]].order,
            same_station=assignments[large]==assignments[small]))
    inversions=sum(r['inversion'] for r in compared)
    return dict(comparable_count=len(compared),inversion_count=inversions,
        inversion_rate=inversions/len(compared) if compared else None,
        comparable_pairs=compared,hard_excluded_pairs=excluded,insignificant_pairs=insignificant,
        significance_ratio=ratio,validated=False)


def main():
    context=load_context(WORKBOOK)
    inputs=read(DEST/'inputs.json')
    if any(sha(ROOT/p)!=h for p,h in inputs['files'].items()):raise ValueError('Audit sources changed')
    inventory=read(DEST/'inventory.json');viewer=read(OUT/'viewer_data.json')
    plans=inventory['plans'];last=max(viewer['stations'],key=lambda s:s['order'])['station_id']
    mids={r['material_id'] for r in inventory['material_states'] if r['station_id']==last and r['phase']=='completed'}
    if len(mids)!=1:raise ValueError('End material differs: cannot use common carried-material reference')
    material=next(v for v in viewer['variants'] if v['material_id'] in mids)
    probe=MetricProbe(viewer,OUT);volumes={r['region_id']:0. for r in viewer['model']['regions']}
    for c in material['components']:
        if c['kind']!='panel':continue
        probe.geometry(c['geometry_id'])
        volumes[c['region_id']]+=probe.mass[c['geometry_id']][0]
    domains=[]
    for b in viewer['model']['bends']:
        regions=subtree_regions(viewer['model'],b['child_region_id'])
        domains.append(dict(operation_id=b['operation_id'],joint_id=b['joint_id'],region_ids=sorted(regions),
            carried_volume_mm3=sum(volumes[r] for r in regions)))
    magnitudes={d['operation_id']:d['carried_volume_mm3'] for d in domains}
    rows=[dict(plan_id=p['plan_id'],changes=p['changes'],audit=proxy_ordering(context,p['assignments'],magnitudes)) for p in plans]
    # Old P1 is an offline comparison only; it did not define the new magnitudes.
    old=read(DEST/'policy_audit.json')['old_p1']
    for r in rows:r['old_p1_inversion_count']=old[r['plan_id']]
    from src.inference.input import load_inference_context
    from src.constraints.v06.soft_priors import area_ordering
    old_context=load_inference_context(WORKBOOK)
    old_pairs=area_ordering(old_context,plans[0]['assignments'],1.15)['comparable_pairs']
    old_directions={(r['larger_operation_id'],r['smaller_operation_id']) for r in old_pairs}
    new_directions={(r['larger'],r['smaller']) for r in rows[0]['audit']['comparable_pairs']}
    comparison=dict(all_32_inversion_counts_identical=all(r['old_p1_inversion_count']==r['audit']['inversion_count'] for r in rows),
        comparable_pair_directions_identical=old_directions==new_directions,
        old_only_directions=sorted(old_directions-new_directions),new_only_directions=sorted(new_directions-old_directions))
    result=dict(candidate='carried_material_volume_ordering',status='UNVALIDATED_GEOMETRIC_PROXY',
        base_experiment_signature=inputs['signature'],script_sha256=sha(Path(__file__)),
        material_id=material['material_id'],domains=domains,rows=rows,offline_comparison=comparison,
        hypothesis_retained='Small-before-large, same-station not penalized; 1.15 significance retained only for controlled comparison',
        limitations=['Not a physical force/time/tool-access model','Finite bend bands omitted',
            'Uses common completed material; not an initial blank or state-dependent mass reconstruction',
            'Local STEP patches and constant thickness model do not prove volume conservation',
            'P002 curved-bend support/kinematics not implemented here'],
        production_score_modified=False)
    atomic_write(DEST/'material_proxy.json',result)
    table=''
    for r in rows:
        change='NP2_001' if not r['changes'] else '; '.join(f'{c["operation_id"]} {c["before"]}→{c["after"]}' for c in r['changes'])
        table+=f'<tr><td>{r["plan_id"]}</td><td>{change}</td><td>{r["old_p1_inversion_count"]}/1451</td><td>{r["audit"]["inversion_count"]}/{r["audit"]["comparable_count"]}</td><td>{r["audit"]["inversion_rate"]:.6f}</td></tr>'
    page='''<!doctype html><html lang="zh"><meta charset="utf-8"><title>P1 · 材料带动量替代试算</title>
<style>body{font:16px system-ui;margin:32px;color:#234;background:#f5f7fa}td,th{border:1px solid #ccd;padding:9px}table{border-collapse:collapse;background:white}aside{background:#fff1da;padding:16px}</style>
<h1>P1 · 用几何带动材料量替代人工影响面积</h1><aside>仅为过渡候选指标，尚未验证工艺优劣，也未接入正式评分。材料量来自几何连接树与实体体积，不读取Sheet5面积。仍保留“小先大后”的原假设、同序不罚及1.15显著比；因此这是替换代理量来源，不是已经发现新的物理优劣规则。</aside>
<p>使用全部动作完成后的共同材料实体；不含折弯带，不能视为每序完整真实质量。实际力、工具可达性和曲线折弯需另验。两个指标的可比较对数不同，应比较各自比例，不能直接比较反向对数大小。</p>
<p><a href="index.html">间隙与运动量检查</a> · <a href="material_proxy.json">每个折弯的带动区域、体积与逐对审计</a></p>
'''
    page+=f'<p>本轮32方案反向对数全部一致：{comparison["all_32_inversion_counts_identical"]}；可比较对及大小方向全部一致：{comparison["comparable_pair_directions_identical"]}。这证明本件可以用该几何代理复现旧偏好，尚未证明旧偏好是制造优劣真值或曲线件泛化。</p>'
    page+='<table><tr><th>方案</th><th>改序</th><th>旧面积P1</th><th>新体积代理</th><th>新代理反向比例</th></tr>'+table+'</table></html>'
    (DEST/'material_proxy.html').write_text(page,encoding='utf-8')
    if any(sha(ROOT/p)!=h for p,h in inputs['files'].items()):raise ValueError('Sources changed during proxy audit')
    print('PROXY',len(domains),'domains',len(rows),'plans',
          'inversions',sorted({r['audit']['inversion_count'] for r in rows}),
          'comparable',sorted({r['audit']['comparable_count'] for r in rows}),flush=True)


if __name__=='__main__':main()
