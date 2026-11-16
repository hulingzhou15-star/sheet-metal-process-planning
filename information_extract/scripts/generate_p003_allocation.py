"""Generate and select P003 candidates with the adopted scoring policy."""
from collections import Counter
from fractions import Fraction
import html
import json
from pathlib import Path
import time

from information_extract.scripts.build_p003_material_basis import MODULE, OUT as PREP, FIRST, SECOND
from information_extract.scripts.experiment_bend_allocation_3d import protected_hashes
from information_extract.scripts.run_geometry_allocation import verify_optimizer
from information_extract.three_d.allocation_policy import AllocationEngine
from information_extract.three_d.artifacts import ROOT, read, sha, verify_sources, write
from information_extract.three_d.project_geometry_scoring import geometry_context, measure_paths, ProjectGeometryScorer
from src.neighborhood import build_neighborhood, load_config as load_neighborhood
from src.neighborhood_penalty import build_catalogue, load_config
from src.neighborhood_penalty.generation import establish_primary, generate_candidates, select_candidates

OUT = MODULE/'outputs/geometry_allocation_v1_generated/P003'


def render(payload, facts):
    by_id = {p['plan_id']:p for p in payload['plans']}
    ordered = [by_id[pid] for pid in payload['ranking']]
    table = []
    for row in ordered:
        score = row['score']
        values = (row['plan_id'],score['p5']['exact'],score['components']['area']['exact'],
                  score['components']['extra_gap']['exact'],score['components']['group_fragmentation']['exact'],
                  score['secondary_exact'])
        table.append('<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in values)+'</tr>')
    data = json.dumps(dict(plans=ordered,operations=facts['operations'],stations=facts['stations']),
                      ensure_ascii=False).replace('</','<\\/')
    page = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>P003 · 入选方案</title>
<style>
body{font:15px/1.55 system-ui,"Microsoft YaHei",sans-serif;background:#f1f4f6;color:#294252;margin:24px auto;padding:0 18px;max-width:1160px}
table{width:100%;border-collapse:collapse;background:#fff}td,th{padding:7px 12px;border:1px solid #d2dde5;text-align:left}
a{color:#2476a5}select{margin:0 12px 10px 4px;padding:5px}aside{padding:12px;background:#e7f2fa}
.scroll{overflow:auto}
</style>
<h1>P003 · 入选16个方案</h1>
<aside>120个动作，64个候选选16个。先比较P5，次级权重为几何P1 0.30、额外间隔 0.20、动作分散 0.50。O069先于O120，两步共用同一个STEP关节。OP70能力为空。</aside>
<p><a href="plans.json">入选分配和评分</a> · <a href="pool.json">64个方案</a> · <a href="material_basis.json">材料域</a> · <a href="verification.json">核验记录</a></p>
<div class="scroll"><table><thead><tr><th>方案</th><th>P5</th><th>几何P1</th><th>额外间隔</th><th>动作分散</th><th>次级总分</th></tr></thead><tbody>"""
    page += ''.join(table)+"""</tbody></table></div>
<h2>逐动作分配</h2>
<label>方案<select id="plan"></select></label>
<label>工序<select id="station"><option value="">全部</option></select></label>
<label>类型<select id="type"><option value="">全部</option></select></label><span id="count"></span>
<div class="scroll"><table><thead><tr><th>动作</th><th>名称</th><th>类型</th><th>侧别</th><th>折弯幅度</th><th>工序</th></tr></thead><tbody id="rows"></tbody></table></div>
<script type="application/json" id="review-data">"""+data+"""</script>
<script>
const data = JSON.parse(document.getElementById('review-data').textContent);
const $ = id => document.getElementById(id);
const types = {BEND:'折弯',CIRCLE_PUNCH:'圆孔',IRREGULAR_PUNCH:'异形孔',PRE_PUNCH:'预孔',FLANGING:'翻孔',DIMPLE:'凸米'};
for (const p of data.plans) $('plan').add(new Option(p.plan_id,p.plan_id));
for (const s of data.stations) $('station').add(new Option(s.station_id,s.station_id));
for (const type of new Set(data.operations.map(o=>o.operation_type))) $('type').add(new Option(types[type],type));
function show() {
  const p = data.plans.find(x=>x.plan_id===$('plan').value);
  const ops = data.operations.filter(o => (!$('station').value || p.assignments[o.operation_id]===$('station').value)
      && (!$('type').value || o.operation_type===$('type').value));
  $('count').textContent = ops.length+' 个动作';
  $('rows').replaceChildren(...ops.map(o=>{
    const tr=document.createElement('tr');
    for (const v of [o.operation_id,o.name,types[o.operation_type],{SMOOTH:'光面',ROUGH:'毛面'}[o.surface_side]||'—',
         o.bend_angle_deg ? o.bend_angle_deg+'°' : '—',p.assignments[o.operation_id]]) {
      const td=document.createElement('td'); td.textContent=v; tr.append(td);
    }
    return tr;
  }));
}
for (const id of ['plan','station','type']) $(id).addEventListener('change',show);
show();
</script></html>"""
    (OUT/'index.html').write_text(page,encoding='utf8')


def main():
    started = time.monotonic()
    protected = protected_hashes()
    hashes = read(PREP/'inputs.json')['files']
    config = load_config()
    nconfig = load_neighborhood(ROOT/config['neighborhood_config'])
    sources = [Path(__file__),MODULE/'scripts/run_geometry_allocation.py',
               ROOT/'registry/protected_inputs.json',ROOT/'configs/neighborhood_penalty_v02.json',
               ROOT/config['neighborhood_config']]
    sources += list(PREP.glob('*.json'))
    hashes.update({p.relative_to(ROOT).as_posix():sha(p) for p in sources})
    verify_sources(hashes)
    facts = read(PREP/'facts.json')
    basis = read(PREP/'material_basis.json')
    partition = read(PREP/'partition.json')
    context,path_audit = geometry_context(facts,read(PREP/'geometry_paths.json'),{'rows':[]})
    if context.facts_fingerprint!=read(PREP/'inputs.json')['facts_fingerprint']:
        raise ValueError('Foreign facts')
    evidence = measure_paths(context,nconfig,progress=lambda n:print('GEOMETRY',n,flush=True))
    neighborhood = build_neighborhood(context,nconfig,evidence=evidence)
    catalogue = build_catalogue(context,neighborhood,config)
    if catalogue['missing_geometry_operations']:
        raise ValueError('Incomplete scoring geometry')
    scorer = ProjectGeometryScorer(context,catalogue,config,basis)
    saved = {'facts':facts,'path_audit':path_audit,'geometry_evidence':evidence,
             'neighborhood':neighborhood.to_dict(),'catalogue':catalogue,'material_basis':basis,
             'inputs':dict(files=hashes,facts_fingerprint=context.facts_fingerprint,basis_fingerprint=basis['fingerprint'])}
    for name,result in saved.items():
        write(OUT/f'{name}.json',result)
    primary = establish_primary(context,catalogue,config)
    write(OUT/'primary.json',primary)
    if not primary['generation_allowed']:
        raise ValueError('P5 optimum not proved')
    print('PRIMARY',primary['minimum_exact'],flush=True)
    pool,generation = generate_candidates(context,catalogue,config,primary,scorer,
        progress=lambda p:print('GENERATE',p['pool'],p['channel'],p['status'],flush=True))
    write(OUT/'pool.json',dict(part_id='P003',plans=pool))
    write(OUT/'generation.json',generation)
    if len(pool)!=64 or len({p['plan_hash'] for p in pool})!=64:
        raise ValueError('Requested 64 distinct candidates not obtained')
    selected,selection = select_candidates(context,pool,config)
    if len(selected)!=16:
        raise ValueError('Requested 16 selected candidates not obtained')
    engine = AllocationEngine(context)
    for p in pool:
        engine.require_feasible(p['assignments'])
        if context.stations[p['assignments'][FIRST]].order >= context.stations[p['assignments'][SECOND]].order:
            raise ValueError('Two-step bend precedence violated')
    optimizer, rows = [], []
    for plan in selected:
        a = plan['assignments']
        score = scorer.evaluate(a)
        optimizer.append(dict(plan_id=plan['plan_id'],**verify_optimizer(context,catalogue,config,scorer,a)))
        rows.append(dict(plan_id=plan['plan_id'],assignments=a,score=score,hard=engine.evaluate(a).to_dict()))
    ranking = sorted(rows,key=lambda r:(Fraction(r['score']['p5']['exact']),
                                        Fraction(r['score']['secondary_exact']),r['plan_id']))
    minimum_pool_score = min(Fraction(p['score']['secondary_exact']) for p in pool)
    if Fraction(ranking[0]['score']['secondary_exact'])!=minimum_pool_score:
        raise ValueError('Selection lost the best pool score')
    payload = dict(part_id='P003',version=basis['schema_version'],plans=rows,
        ranking=[p['plan_id'] for p in ranking],selection=selection,primary=primary,
        basis_fingerprint=basis['fingerprint'],expert_used=False,motion_check_run=False)
    verify_sources(hashes)
    if protected_hashes()!=protected:
        raise ValueError('Protected source changed')
    for row in protected['preexisting_mismatches']:
        if row['path']=='information_extract/projects/P003/project.json':
            row['reason']='User-authorized revision 9 consumed at its actual SHA; registry expectation preserved.'
    write(OUT/'plans.json',payload)
    write(OUT/'verification.json',dict(part_id='P003',pool_count=len(pool),selected_count=len(rows),
        hard_constraints=dict(Counter(r.constraint_type for r in context.precedence_relations)),
        source_revision=partition['source_revision'],source_files_unchanged=len(hashes),
        unique_and_hard_feasible=True,all_two_step_orders_checked=True,optimizer=optimizer,protection=protected,
        p1_comparable_pairs=scorer.area_template['comparable_pair_count'],
        minimum_pool_secondary=str(minimum_pool_score),
        motion_check_run=False,elapsed_seconds=round(time.monotonic()-started,2)))
    render(payload,facts)
    print('COMPLETE',len(pool),len(rows),ranking[0]['plan_id'],ranking[0]['score']['secondary_exact'],flush=True)


if __name__=='__main__':
    main()
