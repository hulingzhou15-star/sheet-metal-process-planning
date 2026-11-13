"""Current geometry-based allocation/scoring entry; frozen planning CLIs are historical."""
import argparse
from fractions import Fraction
from pathlib import Path
import time

from information_extract.three_d.artifacts import ROOT, read, sha, write
from information_extract.three_d.allocation_policy import load_context, AllocationEngine
from information_extract.three_d.geometric_p1 import compile_basis, check_basis, GeometryScorer, VERSION
from src.neighborhood import build_neighborhood, load_config as neighborhood_config, measure_geometry
from src.neighborhood_penalty import load_config, build_catalogue
from src.neighborhood_penalty.generation import establish_primary, generate_candidates, select_candidates
from src.neighborhood_penalty.model import build_objective_model
from ortools.sat.python import cp_model

MODULE = ROOT/'information_extract'
DEFAULT = MODULE/'outputs/geometry_allocation_v1/P001'


def prepare(context, basis, config):
    nc = neighborhood_config(ROOT/config['neighborhood_config'])
    n = build_neighborhood(context,nc,evidence=measure_geometry(context,nc))
    catalogue = build_catalogue(context,n,config)
    return GeometryScorer(context,catalogue,config,basis), catalogue


def verify_optimizer(context, catalogue, config, scorer, assignments):
    model,ranks,p5,secondary = build_objective_model(context,catalogue,config,scorer,'secondary')
    for oid,sid in assignments.items():
        model.add(ranks[oid]==context.station_ranks[sid])
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers=1
    solver.parameters.max_time_in_seconds=30
    status=solver.solve(model)
    if status != cp_model.OPTIMAL:
        raise ValueError('Fixed-plan objective verification incomplete: '+solver.status_name(status))
    score=scorer.evaluate(assignments)
    if Fraction(secondary.integer_value(solver),secondary.scale)!=Fraction(score['secondary_exact']):
        raise ValueError('Geometry optimizer/scorer disagree')
    if Fraction(p5.integer_value(solver),p5.scale)!=Fraction(score['p5']['exact']):
        raise ValueError('P5 optimizer/scorer disagree')
    return dict(status=solver.status_name(status), secondary_exact=score['secondary_exact'],
                p5_exact=score['p5']['exact'], geometric_pair_count=scorer.area_template['comparable_pair_count'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['score','generate'],default='score')
    parser.add_argument('--input',type=Path,default=ROOT/'data/parts/P001/钣金加工数据表_P001.xlsx')
    parser.add_argument('--viewer',type=Path,default=MODULE/'outputs/local_forming_v03/P001/viewer_data.json')
    parser.add_argument('--basis',type=Path,help='Reuse a compiled geometry basis; no Sheet5/model-source workbook needed at scoring time')
    parser.add_argument('--plans',type=Path,default=ROOT/'baselines/P001/plans/all_16_complete_assignments.json')
    parser.add_argument('--output',type=Path,default=DEFAULT)
    args=parser.parse_args()
    out=args.output.resolve()
    if not out.is_relative_to(MODULE/'outputs') or out==MODULE/'outputs':
        raise ValueError('Dedicated output under information_extract/outputs required')
    from information_extract.scripts.experiment_bend_allocation_3d import protected_hashes
    protected=protected_hashes()
    started=time.monotonic();context=load_context(args.input)
    cfg=load_config()
    files={args.input.resolve(),Path(__file__),ROOT/'configs/neighborhood_penalty_v02.json',ROOT/cfg['neighborhood_config']}
    if args.basis:
        basis=read(args.basis);check_basis(context,basis);files.add(args.basis.resolve())
    else:
        viewer=read(args.viewer)
        # Compilation proves provenance against the original model sources.
        # Subsequent scoring can use --basis with Sheet5/expert-free workbooks;
        # semantic facts fingerprint, not whole workbook hash, governs reuse.
        for path,h in viewer['model']['source_sha256'].items():
            if sha(ROOT/path)!=h:raise ValueError('Stale geometry source: '+path)
            files.add(ROOT/path)
        basis=compile_basis(context,viewer,args.viewer.resolve().parent)
        files.add(args.viewer.resolve())
        files.update(args.viewer.resolve().parent/g['brep_file'] for g in viewer['geometries'].values())
    files.update((ROOT/'src').rglob('*.py'))
    files.update((MODULE/'three_d').glob('*.py'))
    if args.mode=='score':files.add(args.plans.resolve())
    hashes={p.resolve().relative_to(ROOT).as_posix():sha(p) for p in sorted(files)}
    scorer,catalogue=prepare(context,basis,cfg)
    write(out/'material_basis.json',basis)
    write(out/'catalogue.json',catalogue)
    write(out/'config.json',dict(scoring_version=VERSION,inherited_settings=cfg,
        P1='geometric carried material volume; no Sheet5 fallback',H6='DISABLED'))
    if args.mode=='score':
        files.add(args.plans.resolve())
        plans=read(args.plans)['plans']
        rows=[]
        for p in plans:
            hard=AllocationEngine(context).evaluate(p['assignments']).to_dict()
            if not hard['feasible']:raise ValueError('Illegal input plan: '+p['plan_id'])
            score=scorer.evaluate(p['assignments'])
            rows.append(dict(plan_id=p['plan_id'],assignments=p['assignments'],hard=hard,score=score))
            print('SCORED',p['plan_id'],score['components']['area']['exact'],score['secondary_exact'],flush=True)
        selection=None;primary=None
    else:
        primary=establish_primary(context,catalogue,cfg)
        write(out/'primary.json',primary)
        if not primary['generation_allowed']:
            raise ValueError('Primary optimum not established; no candidates generated')
        def progress(*values):print('GENERATE',*values,flush=True)
        pool,generation=generate_candidates(context,catalogue,cfg,primary,scorer,progress=progress)
        write(out/'generation.json',generation)
        write(out/'pool.json',dict(plans=pool))
        chosen,selection=select_candidates(context,pool,cfg)
        rows=[dict(plan_id=p['plan_id'],assignments=p['assignments'],
                   hard=AllocationEngine(context).evaluate(p['assignments']).to_dict(),score=scorer.evaluate(p['assignments'])) for p in chosen]
        if not rows:raise ValueError('No selected candidates')
    verification=verify_optimizer(context,catalogue,cfg,scorer,rows[0]['assignments'])
    # Independent output rank; never overwrites frozen candidate selection.
    rank=sorted(rows,key=lambda r:(Fraction(r['score']['p5']['exact']),Fraction(r['score']['secondary_exact']),r['plan_id']))
    payload=dict(version=VERSION,mode=args.mode,plans=rows,ranking=[r['plan_id'] for r in rank],
        basis_fingerprint=basis['fingerprint'],selection=selection,primary=primary,
        policy_status='ACTIVE_USER_ADOPTED',expert_used=False,manufacturing_safety_claimed=False)
    write(out/'plans.json',payload)
    if any(sha(ROOT/p)!=h for p,h in hashes.items()):
        raise ValueError('Input or implementation changed during allocation')
    write(out/'inputs.json',dict(files=hashes,facts_fingerprint=context.facts_fingerprint,
        basis_fingerprint=basis['fingerprint'],policy_version=VERSION))
    if protected_hashes()!=protected:raise ValueError('Protected files changed during run')
    write(out/'verification.json',dict(protection=protected,optimizer=verification,
        elapsed_seconds=round(time.monotonic()-started,2),all_hard_feasible=True,
        mode=args.mode,plan_count=len(rows),P1_weight=cfg['secondary_weights']['area'],H6_enabled=False))
    table=''.join(f'<tr><td>{r["plan_id"]}</td><td>{r["score"]["p5"]["exact"]}</td><td>{r["score"]["components"]["area"]["value"]:.8f}</td><td>{r["score"]["secondary"]:.8f}</td></tr>' for r in rank)
    page='''<!doctype html><html lang="zh"><meta charset="utf-8"><title>几何连接树 P1 · 正式评分</title>
<style>body{font:16px system-ui;margin:32px;color:#234;background:#f5f7fa}table{border-collapse:collapse;background:white}td,th{border:1px solid #ccd;padding:10px}aside{padding:16px;background:#e8f2fa}</style>
<h1>P001 · 几何连接树 P1 已启用</h1><aside>P1已按用户确认采用几何材料带动量，H6停用、H7保留。沿用1.15显著比、小先大后、同序不罚；P1权重0.30，其余评分及P5优先顺序不变。规则采纳不等于制造安全证明。</aside>
<p><a href="plans.json">完整分配与评分</a> · <a href="material_basis.json">材料域与体积</a> · <a href="verification.json">求解器/评分一致性</a></p>'''
    page+=f'<p>模式：{args.mode}；方案数：{len(rows)}。此处排序为新流程独立输出，冻结候选未改。</p>'
    page+='<table><tr><th>方案</th><th>P5（优先）</th><th>几何P1</th><th>次级总分</th></tr>'+table+'</table></html>'
    (out/'index.html').write_text(page,encoding='utf-8')
    print('COMPLETE',args.mode,len(rows),out,flush=True)


if __name__=='__main__':main()
