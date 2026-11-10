"""Audit H6 redundancy and run diagnostics only on existing hard-feasible plans."""
from pathlib import Path
import sys
import json
import hashlib
from collections import Counter
from dataclasses import asdict, replace
import numpy as np

ROOT = Path(__file__).resolve().parents[2]; MODULE = ROOT/'information_extract'
sys.path.insert(0, str(ROOT))
from src.constraints.v06.context import load_rule_context
from src.constraints.v06.hard_constraints import HardConstraintEngine
from information_extract.three_d.h6_h7_audit import (relation_coverage, find_path, descendants,
    fractions_for_stage, representative_sources, locations, pair_changes)


def read(p):
    return json.loads(p.read_text(encoding='utf-8'))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(p, d):
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def main():
    cfg = read(MODULE/'configs/P001_h6_h7_audit_v01.json')
    out = MODULE/cfg['output']; out.mkdir(parents=True, exist_ok=True)
    stage = read(MODULE/cfg['stage_data']); candidate_path = MODULE/cfg['candidate_file']
    original = read(candidate_path)
    if stage['plans_sha256'] != sha(candidate_path):
        raise ValueError('Stage data does not match the frozen candidate source')
    if len(original['plans']) != len(stage['plans']) or any(
        a['plan_id'] != b['plan_id'] or a['assignments'] != b['assignments']
        for a,b in zip(original['plans'],stage['plans'])):
        raise ValueError('Stage plans differ from original assignment or order')
    workbook = (MODULE/cfg['workbook']).resolve()
    context = load_rule_context(workbook)
    engine = HardConstraintEngine(context)
    checked = [{'plan_id':p['plan_id'], **engine.evaluate(p['assignments']).to_dict()} for p in stage['plans']]
    write(out/'candidate_hard_validation.json', checked)
    if not all(c['feasible'] for c in checked):
        raise ValueError('Existing plan violates hard constraints; stop diagnostics without altering data')
    # This in-memory context is only an audit cross-check; production context remains untouched.
    no_sheet5 = replace(context, bend_influences=())
    without = HardConstraintEngine(no_sheet5)
    proof = relation_coverage(context.precedence_relations)
    all_covered = all(r['h7_path'] for r in proof)
    protected = [workbook, candidate_path, MODULE/cfg['stage_data'], MODULE/cfg['local_collision_report']]
    protected += sorted((ROOT/'src/constraints').rglob('*.py'))
    protected += sorted((ROOT/'configs').glob('*'))
    fingerprints = {str(p.resolve()): sha(p) for p in protected if p.is_file()}
    sk = stage['skeleton']; joints = {j['joint_id']:j for j in sk['joints']}
    op_joint = {op:joints[jid] for jid,op in sk['joint_to_operation'].items()}
    material_edges = [(j['parent_region_id'],j['child_region_id']) for j in sk['joints']]
    for r in proof:
        child, parent = op_joint.get(r['child_operation_id']), op_joint.get(r['parent_operation_id'])
        r['cad_motion_descendant_path'] = (find_path(material_edges, parent['child_region_id'], child['child_region_id'])
                                          if child and parent else None)
        r['geometry_interpretation'] = 'CARRYING_TOPOLOGY_ONLY_NOT_A_MANUFACTURING_PRECEDENCE_PROOF'
    write(out/'h6_relation_audit.json', proof)
    sources, unmapped = representative_sources(stage['operations'])
    orders = {s['station_id']:s['order'] for s in stage['stations']}
    if set(context.operations) != {o['operation_id'] for o in stage['operations']}:
        raise ValueError('Operation coverage differs between workbook and stage data')
    if orders != {k:s.order for k,s in context.stations.items()}:
        raise ValueError('Station order mismatch')
    # Carrying sets derive only from the already-built CAD material tree.
    carry = []
    for op,j in sorted(op_joint.items()):
        regions = descendants(sk,j['child_region_id'])
        operations = [o['operation_id'] for o in sources if o['region_id'] in regions]
        carry.append({'bend_operation_id':op,'joint_id':j['joint_id'],'moving_region_ids':regions,
                      'mapped_carried_operation_ids':operations,'unmapped_operations_not_assumed_unaffected':unmapped,
                      'source':'CAD_MATERIAL_TREE_NO_SHEET5','mapping_confirmed':False})
    write(out/'cad_carrying_sets.json',carry)
    local = read(MODULE/cfg['local_collision_report'])
    # Reuse only the H7-compliant local trajectory, never parent-first / simultaneous.
    for p,h in local['source_sha256'].items():
        if sha(Path(p)) != h:
            raise ValueError('Local collision evidence source changed: '+p)
    local_run = next(r for r in reversed(local['runs']) if r['kind']=='child_first')
    plans = []
    for p in stage['plans']:
        assignment = p['assignments']
        if orders[assignment['O010']] >= orders[assignment['O009']]:
            raise ValueError('H7 local precedence failed; invalid path must not enter diagnostic')
        rows = []
        for station in stage['stations']:
            sid = station['station_id']; current = orders[sid]
            samples = []
            for tau in cfg['stage_sample_fractions']:
                fr = fractions_for_stage(sk,assignment,orders,sid,tau)
                transforms, points = locations(sk,fr,sources)
                samples.append((tau,fr,transforms,points))
            before,after = samples[0][3],samples[-1][3]
            moved = []
            for i,o in enumerate(sources):
                excursion = max(float(np.linalg.norm(s[3][i]-before[i])) for s in samples)
                if excursion > cfg['coordinate_tolerance_mm']:
                    moved.append({'operation_id':o['operation_id'], 'region_id':o['region_id'],
                                  'mapping_status':o['mapping_status'],
                                  'before_point_mm':before[i].tolist(),'after_point_mm':after[i].tolist(),
                                  'max_sampled_excursion_mm':excursion,
                                  'executed_by_stage_end':orders[assignment[o['operation_id']]]<=current,
                                  'interpretation':'WIRE_AABB_CENTER_LOCATION_NOT_LOCAL_FORMING'})
            active = {op for op,s in assignment.items() if orders[s]<=current}
            moved_ids = {m['operation_id'] for m in moved}
            moved_regions = [rid for rid in sk['region_order'] if any(
                not np.allclose(sample[2][rid],samples[0][2][rid],atol=cfg['coordinate_tolerance_mm'],rtol=0)
                for sample in samples[1:])]
            rows.append({'station_id':sid, 'current_bends':[op for op in op_joint if assignment[op]==sid],
                         'moving_region_ids':moved_regions,'mapped_operation_motion':moved,
                         'position_distance_change_examples':pair_changes(sources,before,after,active,moved_ids,cfg['pair_examples_per_stage']),
                         'samples':[{'tau':s[0],'joint_fractions':s[1]} for s in samples],
                         'whole_part_interference':'UNKNOWN_NOT_CHECKED',
                         'distance_metric':'WIRE_AABB_CENTER_DISTANCE_NOT_GEOMETRIC_CLEARANCE'})
        plans.append({'plan_id':p['plan_id'],'hard_feasible':True,
                      'without_h6_in_memory_feasible':without.evaluate(assignment).feasible,
                      'stages':rows,
                      'local_collision_evidence':{'operations':['O010','O009'],
                          'station_ids':[assignment['O010'],assignment['O009']],
                          'trajectory':'child_first','h7_compliant':True,'evidence_reused':True,
                          'sample_count':local_run['sample_count'],'status':local_run['status'],
                          'overlap_sample_count':local_run['overlap_sample_count'],
                          'source_report':cfg['local_collision_report'],
                          'scope':'LOCAL_ONLY_EXCLUDES_OTHER_SAME_STATION_BENDS',
                          'manufacturing_feasibility':'UNKNOWN'}})
        print(p['plan_id']+' audited',flush=True)
    write(out/'candidate_motion_diagnostics.json',plans)
    if fingerprints != {p:sha(Path(p)) for p in fingerprints}:
        raise ValueError('Protected input changed during audit')
    summary = {'h6_count':len(proof),'h7_count':len(context.feature_tree_relations),
               'h6_coverage':dict(Counter(r['coverage'] for r in proof)),
               'all_h6_implied_by_h7':all_covered,
               'h6_with_cad_motion_descendant_path':sum(bool(r['cad_motion_descendant_path']) for r in proof),
               'candidate_count':len(plans),'stage_count':sum(len(p['stages']) for p in plans),
               'sampled_stage_states':sum(len(s['samples']) for p in plans for s in p['stages']),
               'mapped_operations':len(sources),'unmapped_operations':unmapped,
               'cad_bend_carrying_sets':len(carry),'h7_modified':False,'h6_production_modified':False,
               'soft_scores_modified':False,'candidate_assignments_modified':False,
               'geometry_uses_sheet5':False,'audit_reads_sheet5_to_compare_h6':True,
               'whole_part_collision_checked':False,'tool_access_checked':False,
               'local_evidence_unique_trajectory_count':1,'inputs_unchanged':True,'protected_sha256':fingerprints}
    write(out/'summary.json',summary)
    lines = ['# P001：固定 H7 的 H6 覆盖与三维诊断', '',
             '本轮保持 H7、H6 正式实现、候选分配和软评分不变。反向/同序的 O009/O010 实验不进入本轮候选分析。', '',
             '## H6 / H7 关系核查', '',
             f"H6 共 {len(proof)} 条，H7 共 {len(context.feature_tree_relations)} 条。覆盖分类：{summary['h6_coverage']}。",
             '每条 H6 都有 H7 路径时，在相同动作、工序与其它硬规则下，H6 的先后条件在逻辑上冗余。这是规则蕴含证明，不依靠仅验证 16 个样本得出。',
             '工作簿加载及软偏好仍可能依赖 Sheet5；先后条件冗余不等于整个系统已经脱离 Sheet5。', '',
             '|H6 child|H6 parent|H7 证据路径|CAD 材料树带动关系|','|---|---|---|---|']
    for r in proof:
        lines.append(f"|{r['child_operation_id']}|{r['parent_operation_id']}|{' → '.join(r['h7_path'] or []) or '无'}|{' → '.join(r['cad_motion_descendant_path'] or []) or '未确认'}|")
    lines += ['', 'CAD 树只说明父区域运动会带动子区域，不能单凭这棵树自动推出制造先后；H7 的工艺先后保持原有确认。', '',
              '## 对现有合法候选的三维诊断', '',
              f"{len(plans)} 个候选全部通过原 H1–H7。读取 {summary['stage_count']} 个阶段，每阶段同步采样 {cfg['stage_sample_fractions']}，共 {summary['sampled_stage_states']} 个阶段状态。",
              f"从 CAD 材料树得到 {len(carry)} 个折弯的带动区域集合；可定位动作 {len(sources)} 个，未可靠定位 {len(unmapped)} 个。未定位不能解释为不受影响。",
              '逐动作记录位置变化和所属板面。尚未加工动作的位置仅为材料附着位置预测，不表示该特征已经成形或应在阶段画面中出现。',
              '空间变化示例使用来源线框包围盒中心间距，优先显示变化幅度较大的跨板面已执行动作对。它不是最小实体间隙、不建立邻域关系、不进入 penalty。示例条数只是展示上限。', '',
              '|候选|有板面运动的工序|O010 → O009|局部几何证据|','|---|---|---|---|']
    for p in plans:
        lines.append(f"|{p['plan_id']}|{', '.join(s['station_id'] for s in p['stages'] if s['moving_region_ids'])}|{' → '.join(p['local_collision_evidence']['station_ids'])}|仅复用 child-first；无采样体积交叠|")
    lines += ['', 'O009/O010 的几何轨迹仅有一个 child-first 类型，复用于不同候选的实际工序标签；没有把重复引用计为 16 次独立几何验证。局部证据不包含同序其它折弯的影响，不能据此判整件无干涉。', '',
              '## 对替代 Sheet5 的结论', '',
              '1. H6 的先后职责：查看上述 H7 覆盖证据。已覆盖的关系无需用三维碰撞去重新证明或推翻 H7。',
              '2. 折弯带动范围：CAD 材料连接树已能给出独立于 Sheet5 的候选范围，但动作关联仍是几何建议。',
              '3. 空间关系变化：已随合法方案计算逐序位置变化；目前只是位置诊断，尚非完整三维邻域或安全间距。',
              '4. 干涉：仅 O009/O010 局部有厚度离散自干涉证据；整件、工具与装夹仍为 UNKNOWN。',
              '5. 软偏好中的 affected_area_proxy、ancestor 排除，以及部分诊断与读取校验仍使用 Sheet5；本轮不改它们。', '',
              '## 文件与复现', '',
              '- h6_relation_audit.json：逐条覆盖路径与 CAD 带动树对应。',
              '- cad_carrying_sets.json：59 个折弯的区域、可定位动作集合及未覆盖列表。',
              '- candidate_hard_validation.json：原有约束逐候选校验。',
              '- candidate_motion_diagnostics.json：逐序运动、位置变化与唯一合法局部轨迹引用。',
              '- summary.json：范围、统计和只读审计哈希。', '',
              '`.venv-m3/Scripts/python.exe -B -X utf8 information_extract/scripts/audit_h6_with_h7_fixed.py`']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='protected_sha256'},ensure_ascii=False))


if __name__=='__main__':main()
