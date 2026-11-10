"""NP2_001 whole supported-panel diagnostic, with H7 fixed and no plan changes."""
from pathlib import Path
import sys,json,hashlib
from collections import Counter,defaultdict
import numpy as np
ROOT=Path(__file__).resolve().parents[2];MODULE=ROOT/'information_extract'
sys.path.insert(0,str(ROOT))
from src.constraints.v06.context import load_rule_context
from src.constraints.v06.hard_constraints import HardConstraintEngine
from information_extract.three_d.local_collision import panel_solids,indexed
from information_extract.three_d.panel_collision import PanelChecker
from information_extract.three_d.local_motion import poses
from information_extract.three_d.h6_h7_audit import fractions_for_stage,descendants
from information_extract.three_d.meshing import mesh
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_FACE


def read(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,d):p.write_text(json.dumps(d,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def main():
    config_path=MODULE/'configs/P001_whole_panel_collision_v01.json';cfg=read(config_path)
    out=MODULE/cfg['output'];out.mkdir(parents=True,exist_ok=True)
    stage=read(MODULE/cfg['stage_data']);asset=read(MODULE/cfg['asset'])
    cad=read(MODULE/cfg['cad_config']);material=read(MODULE/cfg['material_config']);step=MODULE/cad['step']
    candidates=read(MODULE/cfg['candidates'])
    if sha(step)!=stage['asset_id'] or sha(step)!=asset['asset_id']:
        raise ValueError('STEP / stage / topology provenance mismatch')
    if sha(MODULE/cfg['candidates'])!=stage['plans_sha256']:
        raise ValueError('Candidate source mismatch')
    plan=next(p for p in stage['plans'] if p['plan_id']==cfg['plan_id'])
    original=next(p for p in candidates['plans'] if p['plan_id']==cfg['plan_id'])
    if plan['assignments']!=original['assignments']:raise ValueError('Assignment mismatch')
    context=load_rule_context(MODULE/cfg['workbook'])
    hard=HardConstraintEngine(context).evaluate(plan['assignments']).to_dict()
    write(out/'hard_validation.json',hard)
    if not hard['feasible']:raise ValueError('H1-H7 failed: do not run or alter candidate')
    files=[config_path,step,*[MODULE/cfg[k] for k in ['stage_data','asset','cad_config','material_config','candidates','workbook']]]
    files+=list((ROOT/'src/constraints').rglob('*.py'))
    protected={str(p.resolve()):sha(p) for p in files}
    sk=stage['skeleton'];orders={s['station_id']:s['order'] for s in stage['stations']}
    if orders!={k:v.order for k,v in context.stations.items()}:raise ValueError('Station order mismatch')
    reader=STEPControl_Reader()
    if reader.ReadFile(str(step))!=IFSelect_RetDone or not reader.TransferRoots():raise ValueError('STEP read failed')
    faces=indexed(reader.OneShape(),TopAbs_FACE)
    if faces.Extent()!=asset['face_count']:raise ValueError('Face index mismatch')
    solids={};evidence=[];excluded=[];panel_meshes=[]
    for region in sk['regions']:
        rid=region['region_id']
        try:
            body,ev=panel_solids(faces,[region],asset,material['stock_thickness_mm'],cfg['contact_tolerance_mm'])
            solids[rid]=body[0];evidence+=ev
            panel_meshes.append({'region_id':rid,**mesh(body[0],cfg)})
        except Exception as exc:
            excluded.append({'region_id':rid,'reason':str(exc),'status':'UNKNOWN_NOT_CHECKED'})
    if not solids:raise ValueError('No valid panel envelopes')
    checker=PanelChecker(solids,[(j['parent_region_id'],j['child_region_id']) for j in sk['joints']],cfg)
    panel_ops=defaultdict(list)
    for op in stage['operations']:
        if op['region_id']:panel_ops[op['region_id']].append(op['operation_id'])
    carrying={j['joint_id']:set(descendants(sk,j['child_region_id'])) for j in sk['joints']}
    cache={};baselines={}
    def evaluate(fr):
        key=tuple(fr[j['joint_id']] for j in sk['joints'])
        if key not in cache:
            ts=poses(sk,fr)
            cache[key]=checker.check({r:ts[r] for r in solids})
        return cache[key]
    for name,value in [('flat',0.0),('formed_reference',1.0)]:
        print('baseline '+name,flush=True)
        baselines[name]=evaluate({j['joint_id']:value for j in sk['joints']})
        write(out/'baseline_checkpoint.json',baselines)
    runs=[]
    for n in cfg['time_intervals']:
        for sid in cfg['stations']:
            samples=[]
            for k in range(n+1):
                tau=k/n;fr=fractions_for_stage(sk,plan['assignments'],orders,sid,tau)
                samples.append({'index':k,'tau':tau,'joint_fractions':fr,**evaluate(fr)})
                if k%6==0:print(f'{sid} intervals={n} sample={k}/{n} kernel_calls={checker.kernel_evaluations}',flush=True)
            runs.append({'station_id':sid,'intervals':n,'samples':samples})
            write(out/'checkpoint.json',{'runs':runs,'baselines':baselines})
    fine=[r for r in runs if r['intervals']==cfg['time_intervals'][-1]]
    baseline_pairs={name:{tuple(p['region_ids']) for p in v['narrow_phase_pairs'] if p['status']=='OVERLAP'} for name,v in baselines.items()}
    groups={}
    for run in fine:
        sid=run['station_id']
        for sample in run['samples']:
            for pair in sample['narrow_phase_pairs']:
                if pair['status'] not in ('OVERLAP','UNKNOWN_KERNEL_FAILURE'):continue
                a,b=pair['region_ids'];key=(sid,a,b)
                if key not in groups:
                    drivers=[sk['joint_to_operation'][j['joint_id']] for j in sk['joints']
                             if plan['assignments'][sk['joint_to_operation'][j['joint_id']]]==sid
                             and (a in carrying[j['joint_id']] or b in carrying[j['joint_id']])]
                    groups[key]={'station_id':sid,'region_ids':[a,b],'cad_hinge_adjacent':pair['cad_hinge_adjacent'],
                        'associated_operation_ids':sorted(set(panel_ops[a]+panel_ops[b])),
                        'current_driving_bend_ids':drivers,'samples':[],
                        'also_in_flat_baseline':(a,b) in baseline_pairs['flat'],
                        'also_in_formed_reference':(a,b) in baseline_pairs['formed_reference'],
                        'classification':'REQUIRES_GEOMETRY_REVIEW_NOT_MANUFACTURING_REJECTION'}
                groups[key]['samples'].append({'index':sample['index'],'tau':sample['tau'],**pair})
    issues=list(groups.values())
    for i,issue in enumerate(issues,1):
        issue['issue_id']=f'WP{i:03d}'
        issue['max_intersection_volume_mm3']=max((s.get('intersection_volume_mm3',0) for s in issue['samples']),default=0)
    refinement=[]
    for sid in cfg['stations']:
        rows=[r for r in runs if r['station_id']==sid]
        pair_sets=[{tuple(p['region_ids']) for s in r['samples'] for p in s['narrow_phase_pairs'] if p['status']=='OVERLAP'} for r in rows]
        refinement.append({'station_id':sid,'coarse_pairs':len(pair_sets[0]),'fine_pairs':len(pair_sets[1]),
                           'new_pairs_on_refinement':[list(p) for p in sorted(pair_sets[1]-pair_sets[0])],
                           'continuous_collision_certified':False})
    if protected!={p:sha(Path(p)) for p in protected}:raise ValueError('Input changed during run')
    summary={'plan_id':cfg['plan_id'],'hard_feasible':True,'h7_preserved':True,
        'supported_panel_count':len(solids),'total_pair_count_per_sample':len(solids)*(len(solids)-1)//2,
        'fine_sample_count':sum(len(r['samples']) for r in fine),'unique_geometry_state_count':len(cache),
        'kernel_pair_evaluations':checker.kernel_evaluations,'issue_group_count':len(issues),
        'adjacent_issue_groups':sum(i['cad_hinge_adjacent'] for i in issues),
        'nonadjacent_issue_groups':sum(not i['cad_hinge_adjacent'] for i in issues),
        'kernel_failure_sample_pairs':sum(s['counts'].get('UNKNOWN_KERNEL_FAILURE',0) for r in fine for s in r['samples']),
        'excluded_panels':excluded,'unconnected_material_regions':sk['unconnected_region_proposal_ids'],
        'unmapped_operations':[o['operation_id'] for o in stage['operations'] if not o['region_id']],
        'bend_transition_bands_included':False,'holes_filled':True,'initial_blank_recovered':False,
        'local_feature_protrusions_included':False,'tools_checked':False,'fixtures_checked':False,
        'physical_forming_validated':False,'continuous_collision_certified':False,
        'candidate_filter_enabled':False,'manufacturing_feasibility':'UNKNOWN','inputs_unchanged':True}
    report={'summary':summary,'config':cfg,'source_sha256':protected,'panel_evidence':evidence,
            'baselines':baselines,'runs':runs,'refinement':refinement,'issues':issues}
    write(out/'report.json',report)
    viewer={'summary':summary,'config':cfg,'skeleton':sk,'plan':plan,'stations':stage['stations'],
            'operations':stage['operations'],'panels':panel_meshes,'runs':fine,'issues':issues,
            'baselines':baselines,'refinement':refinement}
    write(out/'viewer_data.json',viewer)
    lines=['# NP2_001 整件支持板面运动干涉诊断', '',
           '仅诊断既有合法候选，H1–H7 校验通过。H7、方案分配、软评分均未修改。', '',
           '## 覆盖', '',f"构造 {len(solids)} 个 0.8 mm 厚板体包络，每状态检查 {summary['total_pair_count_per_sample']} 对。",
           f"OP40/OP50/OP60 每段先 12 等分、后 24 等分；加密档 {summary['fine_sample_count']} 个状态。",
           '同序折弯同时使用相同进度。先由保守 AABB 排除不相交对，剩余对用 OCCT 距离与 Common 交叠体积检查。相邻板体不豁免。',
           '相对刚体变换相同的实体对复用计算，缓存精度见配置；包围盒交叠本身不判为干涉。', '',
           '## 结果', '', '以下是模型交叠清单，不能视为真实碰撞数量。请结合 [建模敏感性核对](ARTIFACT_REVIEW.md) 查看填孔对检测结果的影响。', '',f"问题组 {len(issues)} 个（工序×板面对）：相邻 {summary['adjacent_issue_groups']}，非相邻 {summary['nonadjacent_issue_groups']}。",
           f"内核未决记录 {summary['kernel_failure_sample_pairs']} 条。任何内核失败均 UNKNOWN，不按无干涉处理。", '',
           '|问题|工序|板面对|当前驱动折弯|交叠状态数|最大体积 mm³|连接相邻|展开/成品基线也交叠|',
           '|---|---|---|---|---:|---:|---|---|']
    for i in issues:
        lines.append(f"|{i['issue_id']}|{i['station_id']}|{' / '.join(i['region_ids'])}|{', '.join(i['current_driving_bend_ids'])}|{len(i['samples'])}|{i['max_intersection_volume_mm3']:.6g}|{i['cad_hinge_adjacent']}|{i['also_in_flat_baseline']} / {i['also_in_formed_reference']}|")
    lines+=['', '## 误报核对与不能覆盖的部分', '',
            '- 这是全件中可支持的板体包络检查，不是完整零件实体成形检查。',
            '- 真实外边界沿用成品，内孔填平；尚未恢复原始毛坯与逐序去料。',
            '- 全件仍采用零宽刚性转轴，没有实体折弯过渡带。与铰链邻接相关的交叠尤其需要检查建模误差。',
            '- 同时列出完全展开及成品位置基线；基线也交叠是模型核查线索，不自动据此删除问题。',
            '- 非相邻板面交叠也只能说明当前几何假设存在交叠，不能直接认定真实工艺失败。',
            '- 翻孔、凸米、折桥等局部高度、工具及装夹未纳入。未连接材料区域和未定位动作见 report.json。',
            '- 离散采样无法排除采样间碰撞；加密一致也不是连续安全证明。制造可行性保持 UNKNOWN。', '',
            '## 查看与复现', '', '[检视页](review.html) · [全部逐状态记录](report.json)', '',
            '`.venv-m3/Scripts/python.exe -B -X utf8 information_extract/scripts/check_whole_panel_collision.py`',
            '`.venv-m3/Scripts/python.exe -B -X utf8 information_extract/scripts/build_whole_panel_collision_review.py`']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
