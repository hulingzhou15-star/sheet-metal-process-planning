"""Build a reference-free neighborhood-first candidate experiment and explanations."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.inference.input import load_inference_context
from src.neighborhood import build_neighborhood,load_config as neighborhood_config,measure_geometry
from src.neighborhood_penalty import load_config,build_catalogue,Scorer,BoundError,establish_primary,generate_candidates,select_candidates
from src.neighborhood_penalty.review import score_historical,summarize,build_comparisons
from scripts.run_neighborhood_v01 import write_json
from scripts.run_neighborhood_n1 import baseline_inputs,write_csv
from src.visualization.config import load_visualization_config
from src.visualization.geometry_adapter import primitive_shape
from src.visualization.outline_adapter import load_display_outline
from src.visualization.svg_renderer import shape_markup,outline_markup
from src.graph.geometry_primitives import geometry_primitives


def page(context,neighborhood,catalogue,primary,pool,selected,comparisons,old,history,summary,selection,cfg,out):
    shapes={o:[primitive_shape(p) for p in geometry_primitives(g)] for o,op in context.operations.items() if (g:=context.part_data.geometry_for(op)) is not None}
    visual=load_visualization_config();part_id=next(iter(context.operations.values())).part_id
    outline,outline_audit=load_display_outline(visual,ROOT,part_id)
    before={r['plan_hash']:r for r in [*pool,*old,*history]}
    # Page needs compact scores only; facts and pair evidence carry audit detail.
    def view_plan(r):
        return {k:r[k] for k in ('plan_id','plan_hash','assignments','score')}
    payload={'operations':neighborhood.snapshot['operations'],'geometries':{o:''.join(shape_markup(s) for s in rows) for o,rows in shapes.items()},
             'hit_shapes':shapes,'outline_markup':outline_markup({'display_outline':outline},visual),'policy':catalogue,
             'primary':{k:v for k,v in primary.items() if k!='witness'},'view':cfg['view'],'comparisons':comparisons,
             'before':[view_plan(r) for r in before.values()],'after':[view_plan(r) for r in selected],'summary':summary,'selection':selection,
             'stations':[{'station_id':s.station_id,'order':s.order} for s in context.ordered_stations]}
    encoded=json.dumps(payload,ensure_ascii=False,allow_nan=False).replace('<','\\u003c').replace('&','\\u0026')
    template=(ROOT/'src/visualization/neighborhood_penalty_review.html').read_text(encoding='utf-8')
    (out/'penalty_internal_review.html').write_text(template.replace('__PAYLOAD__',encoded),encoding='utf-8')
    return outline_audit


def write_report(out,cat,primary,run,selection,summary,bounds,comparisons,known):
    lines=['# 邻域 penalty v0.2 实施报告','','P5为最高软优先级；H1–H7不变。以下是启发式候选试验，不是模型评分或专家标签。','',
           f"P5状态：{primary['state']}；最优值：{primary.get('minimum_exact','未证明')}；有效关系：{len(cat['pairs'])}。",
           f"候选：{run['actual_pool']}；入选：{selection['actual']}；生成状态：{run['state']}。",'',
           '## 范围审计','',f"优先关系（硬排除前）：{cat['priority_type_counts_before_hard_exclusion']}。",
           f"有效关系：{cat['type_counts']}；缺几何动作：{cat['missing_geometry_operations']}。",
           f"排除原因：{dict(Counter(r['reason'] for r in cat['excluded']))}。",'',
           '## 同等口径的新旧复评','','| 集合 | 数量 | P5 min/mean/max | 次级 min/mean/max | 平均Hamming |','|---|---:|---|---|---:|']
    for name,s in summary.items():
        p,g=s['p5'],s['secondary'];lines.append(f"| {name} | {s['plan_count']} | {p['min']} / {p['mean']} / {p['max']} | {g['min']} / {g['mean']} / {g['max']} | {s['raw_hamming']['mean']} |")
    lines+=['','新旧样本量和生成方法不同，以上仅为描述性比较。旧候选在新候选生成与选择固定后才读取，旧分数未覆盖。',
            '','## 合并拆散边界','','| 分组 | 动作数 | K_min | K_max | 可调 |','|---|---:|---:|---:|---|']
    for r in bounds:lines.append(f"| {r['group_id']} | {r['operation_count']} | {r['K_min']} | {r['K_max']} | {r['participating']} |")
    lines+=['','各组极值在H1–H7下分别证明，不能推断所有组能同时最小。BEND按方向先平均，再参与类型平均。',
            '','## 选择和解释','',f"选择原因：{dict(Counter(r['reason'] for r in selection['steps']))}；完整对照：{len(comparisons)}。",
            '先选次级分数较低的8个，再保留拆散/间隔代表和Hamming多样性。具体入选原因、最近落选方案、全部动作及其它邻域变化见 selection_report.json、comparisons.json 和内部HTML。',
            '','## 已知问题回归','']
    for r in known:lines.append(f"- {r['pair_id']}：P5覆盖={r['covered']}；新池同序={r['new_pool_same_count']}；入选同序={r['selected_same_count']}。")
    lines+=['','## 文件与验证','','新配置：configs/neighborhood_penalty_v02.json。新模块：src/neighborhood_penalty/。入口：scripts/run_neighborhood_penalty_v02.py。',
            '验证结果由 verification.json、测试日志和 browser_qa.json 补充。未自动生成新专家盲评包；本页包含构造角色，不应作为盲评材料。',
            '','## 解释边界','','P5=0只表示本版已覆盖关系无同序；其它邻域、缺几何动作和其它工艺问题仍需审阅。P001已参与开发，不作为未知零件泛化证明。',
            '权重0.30/0.20/0.50及邻域阈值是试验参数，不从专家方案优化得到，不自动生成偏好标签。',
            '未运行全部历史测试时不得宣称全套通过；历史快照问题独立报告，不回写冻结快照。','']
    (out/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=ROOT/'data/parts/P001/钣金加工数据表_P001.xlsx')
    parser.add_argument('--config',type=Path,default=ROOT/'configs/neighborhood_penalty_v02.json')
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/neighborhood_penalty_v02')
    parser.add_argument('--baseline',type=Path,help='Optional old N1 cohort, opened after generation')
    parser.add_argument('--history',type=Path,help='Optional original neighborhood cohort and offline cases')
    parser.add_argument('--evidence',type=Path,help='Optional matching geometry-only cache')
    args=parser.parse_args();out=args.output.resolve()
    if not out.is_relative_to(ROOT/'outputs') or out==ROOT/'outputs' or out.is_symlink():raise ValueError('Dedicated local output folder required')
    out.mkdir(parents=True,exist_ok=True)
    owner=out/'run_metadata.json'
    previous=json.loads(owner.read_text(encoding='utf-8')) if owner.exists() else None
    if previous:
        if previous['schema_version']!='neighborhood_penalty.v0.2':raise ValueError('Foreign output owner')
    elif any(p.name not in {'baseline_manifest.json','baseline_exclusions.json','build.log','build_errors.log'} for p in out.iterdir()):raise ValueError('Unrelated output contents')
    cfg=load_config(args.config);context=load_inference_context(args.input)
    config_sha=hashlib.sha256(args.config.read_bytes()).hexdigest()
    if previous and (previous.get('facts_fingerprint')!=context.facts_fingerprint or previous.get('config_sha256')!=config_sha):
        raise ValueError('Use a new output directory for changed facts or configuration')
    write_json(out,'run_metadata.json',{'schema_version':cfg['schema_version'],'state':'building','facts_fingerprint':context.facts_fingerprint,'config_sha256':config_sha})
    progress=lambda e:print(json.dumps(e,ensure_ascii=False),flush=True)
    nc=neighborhood_config(ROOT/cfg['neighborhood_config'])
    evidence=json.loads(args.evidence.read_text(encoding='utf-8')) if args.evidence else measure_geometry(context,nc,progress=progress)
    neighborhood=build_neighborhood(context,nc,evidence=evidence)
    cat=build_catalogue(context,neighborhood,cfg);write_json(out,'p5_catalogue.json',cat)
    primary=establish_primary(context,cat,cfg);write_json(out,'primary_optimization.json',primary)
    progress({'stage':'primary','state':primary['state'],'value':primary.get('minimum_exact'),'pair_count':len(cat['pairs'])})
    if not primary['generation_allowed']:
        write_json(out,'generation_report.json',{'run':{'state':'skipped_primary_not_proven','actual_pool':0},'primary':primary,'config':cfg})
        (out/'REPORT.md').write_text('# 邻域 penalty v0.2 状态报告\n\n'+primary['state']+'：未建立可用于正式候选的P5最优证明。请查看 primary_optimization.json；未读取历史候选。\n',encoding='utf-8')
        write_json(out,'run_metadata.json',{'schema_version':cfg['schema_version'],'state':primary['state'],'facts_fingerprint':context.facts_fingerprint,'config_sha256':config_sha})
        return
    try:
        scorer=Scorer(context,cat,cfg);bounds=scorer.bounds()
    except BoundError as exc:
        write_json(out,'scoring_status.json',{'state':'bounds_not_established','message':str(exc),'generation_allowed':False})
        write_json(out,'run_metadata.json',{'schema_version':cfg['schema_version'],'state':'bounds_not_established','facts_fingerprint':context.facts_fingerprint,'config_sha256':config_sha})
        raise
    write_json(out,'fragmentation_bounds.json',bounds)
    progress({'stage':'bounds','groups':[{k:r[k] for k in ('group_id','K_min','K_max')} for r in bounds]})
    pool,run=generate_candidates(context,cat,cfg,primary,scorer,progress=progress)
    selected,selection=select_candidates(context,pool,cfg)
    for name,rows in [('candidate_pool.jsonl',pool),('selected_candidates.jsonl',selected)]:
        path=out/name
        if path.exists() and (path.is_symlink() or path.stat().st_nlink>1):raise ValueError('Aliased plan output')
        path.write_text(''.join(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n' for r in rows),encoding='utf-8')
    write_json(out,'generation_report.json',{'run':run,'config':cfg});write_json(out,'selection_report.json',selection)
    # First access to old candidate or human-highlighted case files occurs here.
    old,old_selected,_,base_hashes=baseline_inputs(args.baseline,context.facts_fingerprint)
    history,history_selected,known_cases,history_hashes=baseline_inputs(args.history,context.facts_fingerprint)
    old,history=score_historical(scorer,old),score_historical(scorer,history)
    oldmap={r['plan_hash']:r for r in old};histmap={r['plan_hash']:r for r in history}
    cohorts={'old_original_pool':history,'old_original_selected':[histmap[r['plan_hash']] for r in history_selected],
             'old_n1_pool':old,'old_n1_selected':[oldmap[r['plan_hash']] for r in old_selected],'new_pool':pool,'new_selected':selected}
    summary={k:summarize(context,v) for k,v in cohorts.items()};write_json(out,'cohort_summary.json',summary)
    write_json(out,'historical_rescoring.json',{'old_n1':old,'old_original':history})
    comparisons=build_comparisons(context,neighborhood,pool,selected,selection,old,history,known_cases);write_json(out,'comparisons.json',comparisons)
    ids={r['pair_id'] for r in cat['pairs']};known=[]
    for case in known_cases:
        a,b=case['pair_id'].split('__')
        known.append({'pair_id':case['pair_id'],'covered':case['pair_id'] in ids,'new_pool_same_count':sum(r['assignments'][a]==r['assignments'][b] for r in pool),
                      'selected_same_count':sum(r['assignments'][a]==r['assignments'][b] for r in selected),'used_for_generation':False})
    write_json(out,'known_case_coverage.json',known)
    write_csv(out/'plan_scores.csv',[{'cohort':name,'plan_id':r['plan_id'],'plan_hash':r['plan_hash'],'p5':r['score']['p5']['value'],'secondary':r['score']['secondary'],
                                   **{k:c['value'] for k,c in r['score']['components'].items()}} for name,rows in cohorts.items() for r in rows],
              ['cohort','plan_id','plan_hash','p5','secondary','area','extra_gap','group_fragmentation'])
    write_csv(out/'all_comparison_changes.csv',[{'comparison_id':c['comparison_id'],**r} for c in comparisons for r in c['all_changes']],
              ['comparison_id','operation_id','operation_type','before_station','after_station'])
    outline_audit=page(context,neighborhood,cat,primary,pool,selected,comparisons,old,history,summary,selection,cfg,out)
    write_report(out,cat,primary,run,selection,summary,bounds,comparisons,known)
    write_json(out,'run_metadata.json',{'schema_version':cfg['schema_version'],'state':'complete','facts_fingerprint':context.facts_fingerprint,
               'source_workbook':str(args.input.resolve()),'source_sha256_provenance_only':context.part_data.source_sha256,
               'catalogue_fingerprint':cat['fingerprint'],'baseline_files_first_read_after_generation':{**base_hashes,**history_hashes},
               'config_sha256':hashlib.sha256(args.config.read_bytes()).hexdigest(),'outline_audit':outline_audit,
               'expert_used':False,'training_labels_generated':False,'new_blind_pack':False})
    progress({'stage':'complete','pool':len(pool),'selected':len(selected),'summary':summary})


if __name__=='__main__':main()
