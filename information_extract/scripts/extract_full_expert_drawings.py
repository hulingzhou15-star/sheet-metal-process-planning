"""P003/P004 expert evidence; no writes to annotations or inference inputs."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import numpy as np
from information_extract.scripts.extract_p002_expert_dxf import extract, source_primitive, align, transformed
from information_extract.extract_app.drawing import load_drawing
from information_extract.extract_app.process_config import planning_operations

MODULE=Path(__file__).resolve().parents[1]
SOURCES={'P003':'sources/P003/originals/P003_FULL.dxf','P004':'sources/P004/originals/P004_ALL.dxf'}


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def transitive_parent_conflicts(project, assigned):
    parents = {o['operation_id']: o.get('cad_parent_operation_id') for o in project['operations']}
    orders = {s['station_id']: int(s['order']) for s in project.get('station_rows', [])}
    conflicts = []
    for child, station in assigned.items():
        if station not in orders: continue
        chain = [child]; parent = parents.get(child)
        while parent and parent not in chain:
            chain.append(parent)
            end_station = assigned.get(parent)
            if len(chain) > 2 and end_station in orders and orders[station] + len(chain) - 1 > orders[end_station]:
                conflicts.append({'chain': chain[:], 'child_station': station, 'ancestor_station': end_station,
                                  'required_strict_steps': len(chain)-1})
            parent = parents.get(parent)
    return conflicts


def apply_confirmed_steps(rows, project):
    """Keep raw matching evidence while applying the user's explicit split actions."""
    ops = {o['operation_id']: o for o in project['operations']}
    by = {r['operation_id']: r for r in rows}
    for decision in project.get('user_decisions', []):
        if decision.get('id') != 'P003_O069_O120_TWO_STEP_BEND_20261112':
            continue
        for step in decision['steps']:
            op = ops[step['operation_id']]
            for field in ('expert_station', 'surface_side', 'bend_angle_deg'):
                if op.get(field) != step[field]:
                    raise ValueError(f'Confirmed split step changed: {step["operation_id"]} {field}')
            row = by[step['operation_id']]
            row.update(raw_matching_status=row['status'], status='USER_CONFIRMED_BEND_STEP',
                       proposed_expert_station=step['expert_station'], user_decision_id=decision['id'],
                       preferred_evidence_frame_id={'O069': 'view_05', 'O120': 'view_06'}[step['operation_id']],
                       bend_step=step.copy(),
                       basis=f"两步折弯中的独立动作；{step['expert_station']}，{op['surface_side']}侧折弯{step['bend_angle_deg']}°；以光面计增量{step['signed_increment_deg']:+}°，累计{step['cumulative_angle_deg']}°。O069严格早于O120，两者共用几何")


def apply_remaining_expert_confirmation(rows, project):
    ops = {o['operation_id']: o for o in project['operations']}; by = {r['operation_id']: r for r in rows}
    for decision in project.get('user_decisions', []):
        if decision.get('id') != 'P003_REMAINING_EXPERTS_AND_PARENT_REMOVALS_20261112': continue
        for link in decision['removed_cad_parent_links']:
            if ops[link['child']].get('cad_parent_operation_id') is not None:
                raise ValueError('Removed parent relation changed: ' + link['child'])
        for oid, station in decision['expert_assignments'].items():
            if ops[oid].get('expert_station') != station:
                raise ValueError('Confirmed expert assignment changed: ' + oid)
            row = by[oid]
            row.update(raw_matching_status=row['status'], status='USER_CONFIRMED_EXPERT_ASSIGNMENT',
                       proposed_expert_station=station, user_decision_id=decision['id'],
                       preferred_evidence_frame_id={'O070':'view_05','O099':'view_02','O113':'view_01'}[oid],
                       basis=('用户截图指认：原窗口5（OP40）加工、窗口6该特征未高亮；保留折后投影与展开双线未严格匹配的原始计数，不伪记为自动几何吻合'
                              if oid == 'O070' else f'用户明确指定{station}；保留原OP10/OP20双序同色高亮证据，不按首次出现或能力猜序'))


def apply_p004_pre_punch_confirmation(rows, project):
    by = {r['operation_id']:r for r in rows}; ops = {o['operation_id']:o for o in project['operations']}
    for decision in project.get('user_decisions', []):
        if decision.get('id') != 'P004_OP10_PUNCH_AND_SIX_PREHOLES_20261112': continue
        for link in decision['retained_cad_parent_links']:
            if ops[link['child']].get('cad_parent_operation_id') != link['parent']:
                raise ValueError('P004 confirmed parent relation changed')
        for oid,station in {**decision['pre_punch_assignments'],**decision['flanging_assignments']}.items():
            if ops[oid].get('expert_station') != station: raise ValueError('P004 confirmed station changed: '+oid)
        for oid,station in decision['pre_punch_assignments'].items():
            row=by[oid]
            row.update(raw_matching_status=row['status'], raw_proposed_expert_station=row['proposed_expert_station'],
                       status='USER_CONFIRMED_EXPERT_ASSIGNMENT',proposed_expert_station=station,
                       user_decision_id=decision['id'],preferred_evidence_frame_id='view_02',
                       basis='用户将预孔移至OP10，配对翻孔保留OP20；原图预孔在OP20红色高亮的证据保留，这是用户调整后的分配，不宣称原图已在OP10加工')


def apply_p004_variant_confirmation(rows, project):
    by = {r['operation_id']: r for r in rows}
    ops = {o['operation_id']: o for o in project['operations']}
    for decision in project.get('user_decisions', []):
        if decision.get('id') != 'P004_DRAWING_VARIANTS_RESOLVED_20261112': continue
        for relative, expected in decision['evidence_input_hashes'].items():
            if relative == 'projects/P004/project.json': continue
            if digest(MODULE / relative) != expected: raise ValueError('Variant source changed: ' + relative)
        for oid, station in decision['expert_assignments'].items():
            if ops[oid].get('expert_station') != station or ops[oid]['source_segment_ids'] != decision['preserved_source_ids'][oid]:
                raise ValueError('P004 resolved variant changed: ' + oid)
            row = by[oid]
            row.update(raw_matching_status=row['status'], raw_proposed_expert_station=row['proposed_expert_station'],
                       status='RESOLVED_DRAWING_VARIANT', proposed_expert_station=station,
                       user_decision_id=decision['id'], preferred_evidence_frame_id='view_03' if oid == 'O077' else 'view_06',
                       basis=decision['explanations'][oid])


def apply_p003_local_confirmation(rows, project, source, frames, decision):
    """Independent local panel alignment; station identity is explicitly user supplied."""
    base = next(f for f in frames if f['frame_id'] == decision['matched_raw_frame_id'])
    if base['text'] != decision['raw_nearest_title']:
        raise ValueError('Raw frame title changed; recheck the screenshot correspondence.')
    def inside(s, b):
        return all(b[0] < p[0] < b[2] and b[1] < p[1] < b[3] for p in s['points'])
    local = [s for s in source if inside(s, decision['local_flat_bounds'])]
    m = np.array(base['matrix']); t = np.array(base['translation'])
    target_pairs = [(s, transformed(s, m.T, -m.T @ t)) for s in base['targets']]
    target_pairs = [(raw, norm) for raw, norm in target_pairs if inside(norm, decision['local_expert_bounds_in_base_frame'])]
    ranks = align(local, [norm for _, norm in target_pairs])
    if len(ranks) < 2 or ranks[0]['matched_count'] < 40 or ranks[0]['matched_count'] - ranks[1]['matched_count'] < 20:
        raise ValueError('Insufficient independent local-panel registration support.')
    best = ranks[0]; lm = np.array(best['matrix']); lt = np.array(best['translation'])
    matches = {s['source_id']: [target_pairs[j][0] for j in js] for s, js in zip(local, best['matches'])}
    station = decision['station_id']
    frame = dict(base, frame_id='user_left_panel', station_id=station, station_order=int(station[2:]) // 10,
                 text=f'已核对 {station} · 左侧板面（第7窗口，原标题6/7）',
                 display_title=f'原窗口7 → {station} · 左侧板面局部对应',
                 matrix=(m @ lm).tolist(), translation=(m @ lt + t).tolist(),
                 matches=matches, targets=[raw for raw, _ in target_pairs],
                 matched_count=best['matched_count'], runner_up_count=ranks[1]['matched_count'],
                 local_shift_in_base_frame_mm=lt.tolist(), source_segment_count=len(local),
                 raw_nearest_title=base['text'], station_identification='USER_SCREENSHOT',
                 alignment_note='Local circle-anchored rigid panel registration; screenshot identifies positions, latest user schedule defines station.')
    by = {r['operation_id']: r for r in rows}
    ops = {o['operation_id']: o for o in project['operations']}
    for oid in decision['operation_ids']:
        local_row = classify(ops[oid], [frame])
        if local_row['status'] != 'UNIQUE_FULL_HIGHLIGHT':
            raise ValueError(f'Incomplete local highlighted geometry for {oid}')
        row = by[oid]
        row.update(raw_matching_status=row['status'], status='USER_CONFIRMED_LOCAL_PANEL',
                   proposed_expert_station=station, user_decision_id=decision['decision_id'],
                   preferred_evidence_frame_id=frame['frame_id'],
                   basis=f"用户截图确认四个折弯位置，最新口径为{station}；左板面局部配准{best['matched_count']}/{len(local)}片段，领先第二名{best['matched_count']-ranks[1]['matched_count']}；相对主板横移{lt[0]:.3f} mm。第7窗口不等于第7序，原图标题6/7保留")
        row['evidence'].extend(local_row['evidence'])
    frames.append(frame)


def classify(op,frames):
    evidence=[]
    for f in frames:
        if f['ancillary'] or not f['alignment_supported']:continue
        matched={sid:gs for sid in op['source_segment_ids'] if (gs:=f['matches'].get(sid))}
        active={sid:[g for g in gs if g['color'].lower() not in {'#808080','#ffffff','#000000'}] for sid,gs in matched.items()}
        active={sid:gs for sid,gs in active.items() if gs}
        evidence.append({'frame_id':f['frame_id'],'station_id':f['station_id'],
                         'matched_source_count':len(matched),'highlighted_source_count':len(active),
                         'highlighted_source_ids':list(active),'colors':dict(Counter(g['color'] for gs in active.values() for g in gs))})
    active=[e for e in evidence if e['highlighted_source_count']]
    status='NO_HIGHLIGHT_MATCH' if not active else 'MULTIPLE_STATION_HIGHLIGHTS' if len(active)>1 else 'PARTIAL_HIGHLIGHT' if active[0]['highlighted_source_count']!=len(op['source_segment_ids']) else 'UNIQUE_FULL_HIGHLIGHT'
    return {'operation_id':op['operation_id'],'name':op['name'],'feature_type':op['feature_type'],
            'source_count':len(op['source_segment_ids']),'source_segment_ids':op['source_segment_ids'],
            'status':status,'proposed_expert_station':active[0]['station_id'] if status=='UNIQUE_FULL_HIGHLIGHT' else None,
            'evidence':evidence,'superseded_by_operation_id':op.get('superseded_by_operation_id')}


def run(part):
    cfg_path=MODULE/f'configs/{part}.json';cfg=json.loads(cfg_path.read_text(encoding='utf-8'))
    project_path=MODULE/f'projects/{part}/project.json';project=json.loads(project_path.read_text(encoding='utf-8'))
    path=MODULE/SOURCES[part]
    tracked=[path,project_path,cfg_path,MODULE/cfg['source_file']]
    for item in project.get('user_decisions', []):
        if item.get('screenshot_file'):
            screenshot = MODULE / item['screenshot_file']
            if digest(screenshot) != item['screenshot_sha256']: raise ValueError('User evidence screenshot changed')
            tracked.append(screenshot)
    decision = None
    if part == 'P003':
        decision_path = MODULE / 'configs/P003_expert_decisions_20261112.json'
        decision = json.loads(decision_path.read_text(encoding='utf-8'))
        if digest(path) != decision['source_sha256'] or digest(MODULE / decision['screenshot']) != decision['screenshot_sha256']:
            raise ValueError('User-confirmed source changed; recheck local-panel correspondence.')
        tracked.extend([decision_path, MODULE / decision['screenshot']])
    # Include all configuration manifests and source dependencies used by the flat loader.
    for value in cfg.values():
        if isinstance(value,str) and (MODULE/value).is_file():tracked.append(MODULE/value)
    drawing=load_drawing(MODULE/cfg['source_file'],cfg)
    for dep in drawing.get('source_dependencies',[]):
        candidate=MODULE/dep['file_name']
        if candidate.is_file():tracked.append(candidate)
    hashes={p.relative_to(MODULE).as_posix():digest(p) for p in tracked}
    if drawing['source']['sha256']!=project['source_sha256']:raise ValueError('Annotation source mismatch')
    source=[dict(source_primitive(s),source_id=s['id']) for s in drawing['segments']]
    geometry,labels,unsupported=extract(path)
    frames=[]
    for i,label in enumerate(sorted(labels,key=lambda l:l['xy'][0]),1):
        match=re.search(r'(\d+)\s*/\s*(\d+)',label['text'])
        if not match:continue
        x,y=label['xy'];width=1500 if part=='P003' else 850
        targets=[g for g in geometry if x-width<g['points'][0][0]<x+80 and y-650<g['points'][0][1]<y+650]
        ranks=align(source,targets)
        best=ranks[0] if ranks else {'matrix':[[1,0],[0,1]],'translation':[0,0],'matched_count':0,'matches':[[] for _ in source]}
        runner=ranks[1]['matched_count'] if len(ranks)>1 else 0
        supported=best['matched_count']>=20 and best['matched_count']-runner>=10
        frame=dict(label,frame_id=f'view_{i:02d}',station_id=f'OP{int(match[1])*10}',station_order=int(match[1]),
                   ancillary=bool(re.search(r'/\s*\d+\s*后',label['text'])),alignment_supported=supported,
                   matrix=best['matrix'],translation=best['translation'],matched_count=best['matched_count'],runner_up_count=runner,
                   matches={s['source_id']:[targets[j] for j in js] for s,js in zip(source,best['matches'])},targets=targets)
        frame['alignment_note']='Circle-anchored rigid 2D registration; accepted by exact primitive support and margin.' if supported else 'No reliable circle-anchored registration; this frame is not used to assign operations.'
        if decision and 'window_to_station' in decision:
            station = decision['window_to_station'][str(i)]
            frame.update(original_window_number=i, raw_station_id=frame['station_id'],
                         station_id=station, station_order=int(station[2:]) // 10 if station else None,
                         ancillary=station is None,
                         display_title=f'原窗口{i} → {station or "不分配"} · {frame["text"]}')
        frames.append(frame)
        print(part,label['text'],best['matched_count'],runner,supported,flush=True)
    active_ids={o['operation_id'] for o in planning_operations(project)}
    rows=[dict(classify(op,frames),allocation_included=op['operation_id'] in active_ids) for op in project['operations']]
    apply_confirmed_steps(rows, project)
    if decision:
        apply_p003_local_confirmation(rows, project, source, frames, decision)
    apply_remaining_expert_confirmation(rows, project)
    apply_p004_pre_punch_confirmation(rows, project)
    apply_p004_variant_confirmation(rows, project)
    assigned={r['operation_id']:r['proposed_expert_station'] for r in rows if r['allocation_included'] and r['proposed_expert_station']}
    if decision and 'window_to_station' in decision and any(s not in set(decision['window_to_station'].values())-{None} for s in assigned.values()):
        raise ValueError('Assignment outside explicit retained-window mapping.')
    conflicts=[]
    for op in project['operations']:
        a,b=op['operation_id'],op.get('cad_parent_operation_id')
        if a in assigned and b in assigned and int(assigned[a][2:])>=int(assigned[b][2:]):
            conflicts.append({'child':a,'parent':b,'child_station':assigned[a],'parent_station':assigned[b]})
    result={'part_id':part,'status':'EXPERT_DRAWING_EXTRACTION_DRAFT','source':SOURCES[part],
            'input_hashes':hashes,'annotation_revision':project['revision'],'record_count':len(rows),'allocation_count':len(active_ids),
            'counts':dict(Counter(r['status'] for r in rows if r['allocation_included'])),
            'resolved_count':len(assigned),'unresolved_count':len(active_ids)-len(assigned),
            'station_counts':dict(Counter(assigned.values())), 'operations':rows,
            'cad_parent_order_diagnostics':conflicts,'not_later_order_diagnostics':[],'relation_capacity_issues':[],
            'cad_parent_transitive_diagnostics':transitive_parent_conflicts(project, assigned),
            'unregistered_frames':[f['text'] for f in frames if not f['alignment_supported'] and not f['ancillary']],
            'window_to_station':decision.get('window_to_station') if decision else None,
            'complete_formal_frame_registration':all(f['alignment_supported'] for f in frames if not f['ancillary']),
            'excluded_ancillary_views':[f['text'] for f in frames if f['ancillary']],
            'unsupported_entity_counts':unsupported,'workbook_modified':False,'annotations_modified':False,
            'notes':['Strict per-segment geometry/color evidence only; no assignment guessed from first appearance or capability.',
                     'No automatic tolerance relaxation for corner-radius differences; partial and absent matches remain review items.',
                     'Expert extraction coverage is not full H1-H7 validation or 3D pairing.']}
    for relative,expected in hashes.items():
        if digest(MODULE/relative)!=expected:raise ValueError('Input changed during extraction: '+relative)
    out=MODULE/f'outputs/{part.lower()}_expert_dxf_v01';out.mkdir(parents=True,exist_ok=True)
    (out/'evidence.json').write_text(json.dumps({'summary':result,'frames':frames,'flat_primitives':source},ensure_ascii=False),encoding='utf-8')
    (out/'assignments_draft.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    display=[]
    for f in frames:
        if not f['alignment_supported']:continue
        if part == 'P003' and f['ancillary']:continue
        m=np.array(f['matrix']);t=np.array(f['translation'])
        display.append({k:v for k,v in f.items() if k not in ('matches','targets')}|{'targets':[transformed(g,m.T,-m.T@t) for g in f['targets']]})
    template=(MODULE/'web/p002_expert_review.html').read_text(encoding='utf-8')
    template=template.replace('P002',part).replace('P002_1',part+'_1')
    template=template.replace('esc(f.text)', 'esc(f.display_title||f.text)').replace("$('rightTitle').textContent=f.text", "$('rightTitle').textContent=f.display_title||f.text")
    template=template.replace('拉包按用户确认仅在 OP10 加工，原色证据保留。','原始颜色保留，未决项不猜序。')
    template=template.replace("$('frame').innerHTML=", "if(DATA.summary.unregistered_frames.length)$('summary').textContent+=' 尚未配准：'+DATA.summary.unregistered_frames.join('；')+'。唯一匹配仅限已配准工序，仍为提取建议。';\n$('frame').innerHTML=")
    template=template.replace('const labels={', "const labels={RESOLVED_DRAWING_VARIANT:'已核实绘图差异对应',USER_CONFIRMED_EXPERT_ASSIGNMENT:'用户确认专家工序',USER_CONFIRMED_LOCAL_PANEL:'已核对局部板面对应',USER_CONFIRMED_BEND_STEP:'用户确认的分步折弯',")
    template=template.replace('用户确认：${esc(o.basis)}', '确认依据：${esc(o.basis)}')
    template=template.replace('function shape(s){', "$('summary').textContent+=' 另有 '+DATA.summary.cad_parent_transitive_diagnostics.length+' 条跨未定序动作的H7传递冲突。';\n$('diagnostics').textContent+=' H7传递链：'+DATA.summary.cad_parent_transitive_diagnostics.map(d=>d.chain.join(' → ')+'：'+d.child_station+'必须严格早于'+d.ancestor_station).join('；');\nfunction shape(s){")
    if part == 'P003':
        template=template.replace('尚未配准：', '原图标题视图尚未配准（当前OP70=NONE，不分配动作）：')
        template=template.replace('function shape(s){', "$('summary').textContent+=' 窗口映射：原1/2/4/5/6/7 → OP10/20/30/40/50/60；原3/8不参与分配，OP70仅NONE（0动作）。';\nfunction shape(s){")
    template=template.replace("const e=o.evidence.find(e=>e.station_id===o.proposed_expert_station)", "const e=o.evidence.find(e=>e.frame_id===o.preferred_evidence_frame_id)||o.evidence.find(e=>e.station_id===o.proposed_expert_station)")
    template=template.replace('已明确工序 ${DATA.summary.resolved_count}', '已取得单序建议或用户确认 ${DATA.summary.resolved_count}')
    template=template.replace("+' 项 CAD 父子先后冲突、'+DATA.summary.not_later_order_diagnostics.length+' 项不晚于约束冲突。原专家图示分配保留。'", "+' 项 CAD 父子先后冲突。未检查不晚于约束及工序能力。原专家图示分配保留。'")
    template=template.replace("+' 不晚于约束：'+DATA.summary.not_later_order_diagnostics.map(d=>`${d.operation_id}（${d.operation_station}）≤ ${d.not_later_than}（${d.target_station}），允许同序，当前图示分配不满足。`).join(' ')+' 没有用这些关系反推或修改工序。 工序能力核查：'+DATA.summary.relation_capacity_issues.join(' ')", "+' 本轮未检查不晚于约束及工序能力；没有用关系反推或修改工序。'")
    payload={'summary':result,'frames':display,'flat_primitives':source}
    (out/'review.html').write_text(template.replace('__DATA__',json.dumps(payload,ensure_ascii=False).replace('<','\\u003c')),encoding='utf-8')
    lines=[f'# {part} 专家工序提取草稿', '',f'来源：{SOURCES[part]}；标注修订 {project["revision"]}。',
           f'单序建议或用户确认 {len(assigned)}/{len(active_ids)}；分类 {result["counts"]}；待核 {result["unresolved_count"]}。',
           '未配准工序：'+('；'.join(result['unregistered_frames']) or '无'),
           '数量不代表专家方案已审定或三维/完整H1–H7验证通过。', '', '|动作|名称|状态|彩色匹配/绑定数|','|---|---|---|---|']
    for r in rows:
        if not r['proposed_expert_station']:
            detail='；'.join(f"{e['station_id']} {e['highlighted_source_count']}/{r['source_count']}" for e in r['evidence'] if e['highlighted_source_count']) or '未找到高亮'
            lines.append(f"|{r['operation_id']}|{r['name']}|{r['status']}|{detail}|")
    lines+=['','CAD父子严格先后冲突（仅检查已提取建议）：']+[f"- {c['child']}({c['child_station']}) → {c['parent']}({c['parent_station']})" for c in conflicts]
    lines+=['','H7传递冲突（中间动作尚未定序也不能忽略）：']+[f"- {' → '.join(c['chain'])}：{c['child_station']} 必须严格早于 {c['ancestor_station']}。" for c in result['cad_parent_transitive_diagnostics']]
    lines+=['','用户确认的分步动作（原始逐段高亮证据保留）：']+[f"- {r['operation_id']}：{r['basis']}。" for r in rows if r['status']=='USER_CONFIRMED_BEND_STEP']
    lines+=['','用户截图确认的局部板面对应：']+[f"- {r['operation_id']}：{r['basis']}。" for r in rows if r['status']=='USER_CONFIRMED_LOCAL_PANEL']
    lines+=['','用户确认剩余专家工序：']+[f"- {r['operation_id']} → {r['proposed_expert_station']}：{r['basis']}。" for r in rows if r['status']=='USER_CONFIRMED_EXPERT_ASSIGNMENT']
    lines+=['','原图、人工标注、工作簿及reference未修改。未匹配实体不自动新增为动作；未决项不按首次出现、能力或父关系猜序。']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(part,result['counts'],'H7 conflicts',conflicts,flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--part',choices=list(SOURCES),required=True)
    run(parser.parse_args().part)
