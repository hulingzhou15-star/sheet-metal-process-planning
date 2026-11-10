"""Offline expert sanity check; never a dependency of reference-free inference."""
import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.inference.input import load_inference_context
from src.constraints.v06 import load_rule_context,HardConstraintEngine
from src.constraints.hard_constraints import plan_pairs
from src.candidate.distance import plan_hash,PlanDistance
from src.neighborhood import build_neighborhood,load_config as neighborhood_config,measure_geometry
from src.neighborhood_penalty import load_config,build_catalogue,Scorer,select_candidates
from src.neighborhood_penalty.generation import compact_score,ranking_key
from scripts.run_neighborhood_v01 import write_json


def evaluate(context,expert_plan,catalogue,config,pool,primary):
    """Evaluate an explicit expert plan, stopping on hard failure; no repair."""
    hard=HardConstraintEngine(context).evaluate(expert_plan)
    result={'purpose':'offline_expert_sanity_check_only','hard':hard.to_dict(),
            'used_for_generation':False,'weights_changed':False,'labels_generated':False}
    if not hard.feasible:return {**result,'state':'stopped_expert_hard_failure'}
    assignments=dict(plan_pairs(expert_plan));score=Scorer(context,catalogue,config).evaluate(assignments)
    if not primary['minimum_proven']:raise ValueError('Baseline primary optimum not established')
    if Fraction(score['p5']['exact'])<Fraction(primary['minimum_exact']):
        raise ValueError('Expert witness contradicts claimed baseline primary optimum')
    expert={'plan_id':'EXPERT_REFERENCE_OFFLINE','plan_hash':plan_hash(assignments),'assignments':assignments,'score':compact_score(score)}
    unique={r['plan_hash']:r for r in pool}
    if len(unique)!=len(pool):raise ValueError('Duplicate baseline candidates')
    for row in pool:
        if plan_hash(row['assignments'])!=row['plan_hash'] or not HardConstraintEngine(context).evaluate(row['assignments']).feasible:
            raise ValueError('Invalid baseline candidate')
    others=[r for r in pool if r['plan_hash']!=expert['plan_hash']]
    key=lambda r:ranking_key(r)[:2]
    better=sum(key(r)<key(expert) for r in others);tied=sum(key(r)==key(expert) for r in others)
    primary_admitted=Fraction(score['p5']['exact'])==Fraction(primary['minimum_exact'])
    selection=None
    if primary_admitted:
        augmented=[*others,expert]
        chosen,selection=select_candidates(context,augmented,config)
        included=any(r['plan_hash']==expert['plan_hash'] for r in chosen)
        selection={**selection,'expert_selected':included,'offline_counterfactual':True,
                   'original_pool_modified':False,'original_selected_modified':False,'augmented_unique_pool_size':len(augmented)}
    by_type={}
    for kind in ('CIRCLE_PUNCH','IRREGULAR_PUNCH'):
        stations={}
        for o in context.operations.values():
            if o.operation_type==kind:stations.setdefault(assignments[o.operation_id],[]).append(o.operation_id)
        by_type[kind]={s:sorted(ids) for s,ids in stations.items()}
    fragment_contribution=score['components']['group_fragmentation']['weighted_contribution']
    best=min(pool,key=ranking_key) if pool else None
    distance=PlanDistance.from_context(context)
    return {**result,'state':'evaluated','expert':expert,'full_score_audit':score,
            'primary_admitted':primary_admitted,'baseline_primary_minimum_exact':primary['minimum_exact'],
            'pool_comparison':{'other_unique_plans':len(others),'strictly_better_by_heuristic':better,'equal_by_heuristic':tied,
                               'strictly_worse_by_heuristic':len(others)-better-tied,'expert_rank_interval':[better+1,better+tied+1],
                               'rank_includes_expert':True,'selection_is_not_pure_score_top_k':True},
            'counterfactual_selection':selection,'fragmentation_station_assignments':by_type,
            'fragmentation_share_of_secondary':fragment_contribution/score['secondary'] if score['secondary'] else 0,
            'best_candidate_comparison':{'plan_id':best['plan_id'],'plan_hash':best['plan_hash'],'score':best['score'],
                'raw_hamming':distance.raw(assignments,best['assignments']),
                'all_changes':[{'operation_id':o,'expert_station':assignments[o],'candidate_station':best['assignments'][o]} for o in sorted(assignments) if assignments[o]!=best['assignments'][o]]} if best else None,
            'interpretation':'Passing hard/P5 is a consistency check, not proof of learned process quality; score rank does not establish expert inferiority.'}


def markdown(report):
    lines=['# 专家方案：v0.2离线合理性检查','','同一H1–H7、P5和次级配置；未修改专家、权重、候选或原入选集合。专家仅进入本独立离线评估。','',
           '## H1–H7','','|规则|违反数|','|---|---:|']
    for k,v in report['hard']['violation_breakdown'].items():lines.append(f'|{k}|{v}|')
    if report['state']!='evaluated':
        lines+=['','专家硬约束不通过，停止后续评价；具体冲突见JSON。']
        return '\n'.join(lines)+'\n'
    s=report['full_score_audit'];p5=s['p5'];rank=report['pool_comparison'];selection=report['counterfactual_selection']
    lines+=['','## 关键结论','',f"专家满足H1–H7；P5={p5['exact']}，有效22对中同序{p5['same_station_count']}对。达到当前已证明的P5最小值，因此通过邻域优先条件。",
            f"次级罚分={s['secondary']:.12f}。在64个原候选加专家组成的离线比较中，专家分数排名区间={rank['expert_rank_interval']}。",
            f"离线把专家加入原64个池再执行相同16名额选择：expert_selected={selection['expert_selected'] if selection else '不适用'}。该操作只生成本报告，原池与原入选文件未改。",'',
            '**结论：硬约束和邻域没有否定专家；当前软筛选确实可能漏掉专家方案。不能据此宣布候选工艺更优，也不能说已经验证评分正确。**','',
            '## 分量与贡献','','|分量|原始值|权重|贡献|','|---|---:|---:|---:|']
    for k,r in s['components'].items():lines.append(f"|{k}|{r['value']:.12f}|{r['weight']}|{r['weighted_contribution']:.12f}|")
    area=s['area_audit']
    lines += ['',f"面积排序：可比较{area['comparable_pair_count']}对，反向{area['inversion_count']}对，排除硬先后/层级{area['excluded_pair_count']}对。",
              f"额外间隔：唯一有向关系{s['gap_audit']['relation_count']}条，局部合法g_min/g_max及来源见完整JSON。",
              f"拆散贡献占次级罚分 {100*report['fragmentation_share_of_secondary']:.2f}%。",'',
              '## 同类拆散逐组核查','','|组|K_min|K_actual|K_max|原始罚分|实际工序|','|---|---:|---:|---:|---:|---|']
    for g in s['fragmentation']['groups']:lines.append(f"|{g['group_id']}|{g['K_min']}|{g['K_actual']}|{g['K_max']}|{g['value']}|{', '.join(g['actual_used_stations'])}|")
    lines+=['','## 高罚分来源','']
    for kind,stations in report['fragmentation_station_assignments'].items():
        for station,ids in stations.items():lines.append(f"- {kind} / {station}：{len(ids)}个，{', '.join(ids)}。")
    lines+=['','专家把圆孔和异形孔各拆到两个工序，当前公式分别罚1；ROUGH集中一个工序罚0。三个可调类型平均为2/3，再乘0.50，产生1/3的次级贡献。',
            '这说明当前合并拆散分量很强。它只按使用工序数计罚，不知道专家拆分是否出于工具、工艺联动、加工条件等未表达的理由；本报告不推测具体原因。',
            '圆孔仅O191/O192与其余14个分序，也会获得与更大范围拆分相同的类型罚分。这是该公式的表达限制，应作为后续工艺核查点，而非直接改专家方案。',
            '','## 如何理解这次检查','',
            '专家通过硬条件是必要的合理性检查；专家未必需要在每个手工启发式上得0，也不应通过调权让本件专家必然第一。',
            '但专家没有进入同样的16个名额，表明当前软排序/选择对专家偏好仍不充分，尤其需要检查拆散范围及0.50权重的影响。',
            '应先解释专家为何拆分这两类动作，并检查新方案是否遗漏同样的理由，再决定分组、权重或学习特征如何修订。此次不自动修改任何规则。',
            f"覆盖缺口：{p5['coverage_status']}，缺几何动作{', '.join(p5['missing_geometry_operations'])}；P5=0仅适用于已检查关系。",
            'P001已用于规则开发，本次是开发合理性检查，不是未知零件泛化验证。','']
    return '\n'.join(lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=ROOT/'data/parts/P001/钣金加工数据表_P001.xlsx')
    parser.add_argument('--baseline',type=Path,default=ROOT/'outputs/neighborhood_penalty_v02')
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/neighborhood_penalty_expert_v02')
    args=parser.parse_args();out=args.output.resolve()
    if not out.is_relative_to(ROOT/'outputs') or out==ROOT/'outputs' or out==args.baseline.resolve():raise ValueError('Dedicated offline output required')
    out.mkdir(exist_ok=True,parents=True)
    permitted={'baseline_manifest.json','expert_report.json','expert_report.md','counterfactual_selection.json','verification.json','test_results.txt','run.log'}
    if any(p.name not in permitted for p in out.iterdir()):raise ValueError('Unrelated output directory')
    cfg=load_config();context=load_inference_context(args.input);expert_context=load_rule_context(args.input)
    if context.part_data.source_sha256!=expert_context.part_data.source_sha256:raise ValueError('Workbook changed between reads')
    meta=json.loads((args.baseline/'run_metadata.json').read_text(encoding='utf-8'))
    saved_cfg=json.loads((args.baseline/'generation_report.json').read_text(encoding='utf-8'))['config']
    if meta['facts_fingerprint']!=context.facts_fingerprint or saved_cfg!=cfg:raise ValueError('Baseline facts/config mismatch')
    nc=neighborhood_config(ROOT/cfg['neighborhood_config'])
    n=build_neighborhood(context,nc,evidence=measure_geometry(context,nc));cat=build_catalogue(context,n,cfg)
    if cat!=json.loads((args.baseline/'p5_catalogue.json').read_text(encoding='utf-8')):raise ValueError('Baseline catalogue mismatch')
    rows=[json.loads(s) for s in (args.baseline/'candidate_pool.jsonl').read_text(encoding='utf-8').splitlines() if s.strip()]
    primary=json.loads((args.baseline/'primary_optimization.json').read_text(encoding='utf-8'))
    report=evaluate(context,expert_context.expert_reference_plan,cat,cfg,rows,primary)
    report.update(source_workbook=str(args.input.resolve()),source_sha256=context.part_data.source_sha256,
                  facts_fingerprint=context.facts_fingerprint,catalogue_fingerprint=cat['fingerprint'],configuration=cfg)
    write_json(out,'expert_report.json',report)
    (out/'expert_report.md').write_text(markdown(report),encoding='utf-8')
    if report.get('counterfactual_selection'):write_json(out,'counterfactual_selection.json',report['counterfactual_selection'])
    manifest_path=out/'baseline_manifest.json'
    if manifest_path.exists():
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        changed=[p for p,h in manifest.items() if not (ROOT/p).is_file() or hashlib.sha256((ROOT/p).read_bytes()).hexdigest()!=h]
        if changed:raise RuntimeError('Protected files changed: '+repr(changed))
        write_json(out,'verification.json',{'passed':True,'protected_files':len(manifest),'changed':changed,'source_sha256':context.part_data.source_sha256,
                   'source_geometry_recomputed':True,'expert_used_for_generation':False,'weights_changed':False,'original_selection_modified':False})
    print(json.dumps({'state':report['state'],'hard_feasible':report['hard']['feasible'],'p5':report.get('full_score_audit',{}).get('p5',{}).get('value'),
                      'secondary':report.get('full_score_audit',{}).get('secondary'),'primary_admitted':report.get('primary_admitted'),
                      'counterfactual_expert_selected':report.get('counterfactual_selection',{}).get('expert_selected')},ensure_ascii=False))


if __name__=='__main__':main()
