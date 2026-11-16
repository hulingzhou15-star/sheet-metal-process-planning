"""Generate and rank P002 candidates from project facts and exact STEP cells."""
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path
import html
import json
import time

from information_extract.three_d.artifacts import ROOT, read, write, sha
from information_extract.three_d.project_geometry_scoring import (geometry_context, measure_paths,
    compile_cell_basis, ProjectGeometryScorer)
from information_extract.three_d.allocation_policy import AllocationEngine
from information_extract.scripts.run_geometry_allocation import verify_optimizer
from information_extract.scripts.experiment_bend_allocation_3d import protected_hashes
from src.neighborhood import build_neighborhood, load_config as load_neighborhood
from src.neighborhood_penalty import load_config, build_catalogue
from src.neighborhood_penalty.generation import establish_primary, generate_candidates, select_candidates

MODULE=ROOT/'information_extract';PREP=MODULE/'outputs/p002_generation_v01'
CELLS=MODULE/'outputs/p002_material_cells_v01';OUT=MODULE/'outputs/geometry_allocation_v1_generated/P002'


def render(out,payload,facts,basis):
    by={p['plan_id']:p for p in payload['plans']};rank=[by[k] for k in payload['ranking']]
    table=''.join(f'<tr><td>{r["plan_id"]}</td><td>{r["score"]["p5"]["exact"]}</td><td>{r["score"]["components"]["area"]["value"]:.6f}</td><td>{r["score"]["secondary"]:.6f}</td></tr>' for r in rank)
    data=json.dumps(dict(plans=rank,operations=facts['operations'],domains=basis['domains']),ensure_ascii=False).replace('</','<\\/')
    page='''<!doctype html><html lang="zh"><meta charset="utf-8"><title>P002 · 几何P1候选方案</title>
<style>body{font:16px system-ui;margin:28px auto;max-width:1100px;color:#243247;background:#f7f9fc}aside{padding:18px;background:#e7f2fa}table{border-collapse:collapse;background:white;width:100%;margin:16px 0}th,td{padding:8px;border:1px solid #ccd;text-align:left}select{padding:8px;margin:8px}small{color:#526170}a{color:#1763a6}</style>
<h1>P002 · 新生成候选方案</h1><aside>132个动作，10道工序；64个候选选出16个。P5优先，次级评分采用几何P1 0.30、额外间隔0.20、分散度0.50。切边排除；H6停用，H7严格子先父后。同序同时加工。</aside>
<p>17个折弯的材料域来自STEP实体：12个一般折弯、4个曲线折弯、1个合并Z折。拉包、凸米、翻孔保留STEP完成态。尚未执行这批方案的三维运动干涉检查；评分不等于制造安全证明。</p>
<p><a href="plans.json">完整入选方案与评分</a> · <a href="pool.json">64候选池</a> · <a href="material_basis.json">材料树和体积</a> · <a href="verification.json">验证记录</a> · <a href="catalogue.json">P5几何依据</a></p>
<table><thead><tr><th>方案（按评分排序）</th><th>P5</th><th>几何P1</th><th>次级总分</th></tr></thead><tbody>'''+table+'''</tbody></table>
<h2>查看逐动作分配</h2><label>方案<select id="plan"></select></label><label>工序<select id="station"><option value="">全部工序</option></select></label><label>类型<select id="kind"><option value="">全部类型</option></select></label>
<table><thead><tr><th>动作</th><th>名称</th><th>类型</th><th>侧别</th><th>工序</th></tr></thead><tbody id="rows"></tbody></table>
<small>本页方案身份属于P002本轮独立输出；不是冻结基线或专家方案。</small><script>const data='''+data+''';
const labels={BEND:'折弯',FLANGING:'翻孔',PRE_PUNCH:'预孔',CIRCLE_PUNCH:'圆孔',IRREGULAR_PUNCH:'异形孔',DIMPLE:'凸米',DRAW_FORM:'拉包'};
const sides={SMOOTH:'光面',ROUGH:'毛面',NOT_APPLICABLE:'—'};
const plan=document.querySelector('#plan'),station=document.querySelector('#station'),kind=document.querySelector('#kind');
for(const p of data.plans)plan.add(new Option(p.plan_id,p.plan_id));
for(const s of [...new Set(Object.values(data.plans[0].assignments))].sort())station.add(new Option(s,s));
for(const k of [...new Set(data.operations.map(o=>o.operation_type))].sort())kind.add(new Option(labels[k]||k,k));
function show(){const p=data.plans.find(x=>x.plan_id===plan.value),rows=document.querySelector('#rows');rows.replaceChildren();
for(const o of data.operations){if(station.value&&p.assignments[o.operation_id]!==station.value||kind.value&&o.operation_type!==kind.value)continue;
const tr=document.createElement('tr');for(const v of [o.operation_id,o.name,labels[o.operation_type]||o.operation_type,sides[o.surface_side]||o.surface_side,p.assignments[o.operation_id]]){const td=document.createElement('td');td.textContent=v||'—';tr.append(td)}rows.append(tr)}}
for(const el of [plan,station,kind])el.addEventListener('change',show);show();</script></html>'''
    (out/'index.html').write_text(page,encoding='utf-8')


def main():
    started=time.monotonic();protected=protected_hashes()
    prep_inputs=read(PREP/'inputs.json')['files']
    for p,h in prep_inputs.items():
        if sha(ROOT/p)!=h:raise ValueError('Stale preparation input: '+p)
    facts=read(PREP/'facts.json');paths=read(PREP/'geometry_paths.json');sides=read(PREP/'curved_side_evidence.json')
    cells=read(CELLS/'cells.json');cfg=load_config();ncfg=load_neighborhood(ROOT/cfg['neighborhood_config'])
    files={ROOT/p for p in prep_inputs}|{ROOT/p for p in cells['inputs']}
    files.update(PREP.glob('*.json'));files.add(CELLS/'cells.json')
    files.update(MODULE/'scripts'/p for p in ['generate_p002_allocation.py','run_geometry_allocation.py','build_p002_material_cells.py'])
    files.update([ROOT/'configs/neighborhood_penalty_v02.json',ROOT/cfg['neighborhood_config']])
    hashes={p.relative_to(ROOT).as_posix():sha(p) for p in sorted(files)}
    context,path_audit=geometry_context(facts,paths,sides);basis=compile_cell_basis(context,cells)
    print('MATERIAL',len(basis['domains']),'domains',flush=True)
    evidence=measure_paths(context,ncfg,progress=lambda n:print('GEOMETRY',n,flush=True))
    neighborhood=build_neighborhood(context,ncfg,evidence=evidence);catalogue=build_catalogue(context,neighborhood,cfg)
    if catalogue['missing_geometry_operations']:raise ValueError('Incomplete scoring geometry')
    scorer=ProjectGeometryScorer(context,catalogue,cfg,basis)
    write(OUT/'material_basis.json',basis);write(OUT/'catalogue.json',catalogue)
    write(OUT/'facts.json',facts);write(OUT/'path_audit.json',path_audit)
    write(OUT/'geometry_evidence.json',evidence);write(OUT/'neighborhood.json',neighborhood.to_dict())
    write(OUT/'config.json',dict(inherited_settings=cfg,adapter='seven_classes_finite_paths.v1',
        fragmentation_types=scorer.config['fragmentation_types'],H6_enabled=False,trim_included=False))
    primary=establish_primary(context,catalogue,cfg);write(OUT/'primary.json',primary)
    if not primary['generation_allowed']:raise ValueError('Primary optimum not established')
    print('PRIMARY',primary['minimum_exact'],flush=True)
    pool,generation=generate_candidates(context,catalogue,cfg,primary,scorer,progress=lambda *v:print('GENERATE',*v,flush=True))
    write(OUT/'pool.json',dict(plans=pool));write(OUT/'generation.json',generation)
    if len(pool)!=cfg['generation']['pool_target']:raise ValueError('Incomplete requested candidate pool')
    chosen,selection=select_candidates(context,pool,cfg);rows=[];verification=[]
    for p in pool:
        if not AllocationEngine(context).evaluate(p['assignments']).feasible:raise ValueError('Illegal candidate')
    for p in chosen:
        a=p['assignments'];score=scorer.evaluate(a)
        verification.append(dict(plan_id=p['plan_id'],**verify_optimizer(context,catalogue,cfg,scorer,a)))
        rows.append(dict(plan_id=p['plan_id'],assignments=a,hard=AllocationEngine(context).evaluate(a).to_dict(),score=score))
    rank=sorted(rows,key=lambda r:(Fraction(r['score']['p5']['exact']),Fraction(r['score']['secondary_exact']),r['plan_id']))
    payload=dict(part_id='P002',version='geometry_p1.v1',adapter='seven_classes_finite_paths.v1',plans=rows,
        ranking=[r['plan_id'] for r in rank],selection=selection,primary=primary,basis_fingerprint=basis['fingerprint'],
        expert_used=False,motion_check_run=False,manufacturing_safety_proved=False)
    write(OUT/'plans.json',payload);render(OUT,payload,facts,basis)
    if protected_hashes()!=protected or any(sha(ROOT/p)!=h for p,h in hashes.items()):raise ValueError('Inputs changed during generation')
    for row in protected['preexisting_mismatches']:
        if row['path']=='information_extract/projects/P002/project.json':
            row['reason']='Existing user-authorized revision 16 is consumed at actual_sha256; registry expectation is unchanged.'
    write(OUT/'inputs.json',dict(files=hashes,facts_fingerprint=context.facts_fingerprint,basis_fingerprint=basis['fingerprint']))
    write(OUT/'verification.json',dict(source_files_unchanged=len(hashes),protection=protected,optimizer=verification,
        pool_count=len(pool),selected_count=len(rows),all_pool_hard_feasible=True,H6_enabled=False,
        material_domain_count=len(basis['domains']),comparable_pairs=scorer.area_template['comparable_pair_count'],
        finite_curve_witness_residual=evidence['numeric_max_witness_residual'],elapsed_seconds=round(time.monotonic()-started,2),
        motion_check_run=False,manufacturing_safety_proved=False))
    print('COMPLETE',len(pool),len(rows),rank[0]['plan_id'],rank[0]['score']['secondary_exact'],flush=True)


if __name__=='__main__':main()
