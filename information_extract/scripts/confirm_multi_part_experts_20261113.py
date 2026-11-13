"""Persist the remaining P002 expert assignments after the user's three-part confirmation."""
from copy import deepcopy
import json
from pathlib import Path

from information_extract.extract_app.drawing import load_drawing
from information_extract.extract_app.model import ProjectStore, validate_project
from information_extract.extract_app.process_config import planning_operations, inspect_process
from information_extract.scripts.extract_full_expert_drawings import transitive_parent_conflicts
from information_extract.three_d.facts import sha256

ROOT=Path(__file__).resolve().parents[1]
KNOWN_CAPABILITY_ISSUE='O069：专家工序 OP80 未允许 IRREGULAR_PUNCH。'


def main():
    project_path=ROOT/'projects/P002/project.json'
    draft_path=ROOT/'outputs/p002_expert_dxf_v01/assignments_draft.json'
    draft=json.loads(draft_path.read_text(encoding='utf-8'))
    evidence=json.loads((draft_path.parent/'evidence.json').read_text(encoding='utf-8'))
    for path,expected in [(project_path,draft['annotation_sha256']),
                          (ROOT.parent/evidence['source'],draft['source_sha256']),
                          (ROOT/'sources/P002/P002_1.dxf',draft['flat_source_sha256'])]:
        if sha256(path)!=expected:raise ValueError('Stale expert evidence: '+str(path))
    cfg=json.loads((ROOT/'configs/P002.json').read_text(encoding='utf-8'))
    drawing=load_drawing(ROOT/cfg['source_file'],cfg);store=ProjectStore(ROOT,drawing,'P002');before=store.load()
    if before['revision']!=13:raise ValueError('Expected revision 13; refusing to overwrite later edits.')
    updated=deepcopy(before);rows={r['operation_id']:r for r in draft['operations']};changed={}
    for op in planning_operations(updated):
        row=rows[op['operation_id']];station=row.get('proposed_expert_station')
        if not row['allocation_included'] or not station or row['source_segment_ids']!=op['source_segment_ids']:
            raise ValueError('Unresolved or changed action: '+op['operation_id'])
        if op.get('expert_station') and op['expert_station']!=station:raise ValueError('Existing assignment conflict')
        if not op.get('expert_station'):
            op['expert_station']=station;changed[op['operation_id']]=station
    updated.setdefault('user_decisions',[]).append({
        'id':'P002_P004_EXPERT_CORRESPONDENCE_CONFIRMED_20261113',
        'quote':'现在确认P002-P004的专家分配工序都对应上了',
        'scope':'将已有完整对应草稿回填P002活动动作；合并退出的四条记录不参与分配。',
        'assignments_added':changed,'draft_sha256':sha256(draft_path)})
    report=validate_project(updated,drawing,annotation_only=True)
    assigned={o['operation_id']:o['expert_station'] for o in planning_operations(updated)}
    issues=report['process_config']['reference_issues']
    if report['errors'] or any(i!=KNOWN_CAPABILITY_ISSUE for i in issues) or transitive_parent_conflicts(updated,assigned):
        raise ValueError(json.dumps({'errors':report['errors'],'reference_issues':issues},ensure_ascii=False))
    out=ROOT/'outputs/expert_confirmation_20261113';out.mkdir(parents=True,exist_ok=True)
    for name,value in [('P002_before',before),('P002_assignment_evidence',draft),('P002_validation',report)]:
        (out/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    saved=store.save(updated,before['revision'])['project']
    for old,new in zip(before['operations'],saved['operations']):
        a,b=deepcopy(old),deepcopy(new);a.pop('expert_station',None);b.pop('expert_station',None)
        if a!=b:raise ValueError('Unexpected action change')
    parts={}
    for part in ['P002','P003','P004']:
        path=ROOT/f'projects/{part}/project.json';p=json.loads(path.read_text(encoding='utf-8'))
        active=planning_operations(p);assignments={o['operation_id']:o.get('expert_station') for o in active}
        checks=inspect_process(p);transitive=transitive_parent_conflicts(p,assignments)
        unexpected=[i for i in checks['reference_issues'] if part!='P002' or i!=KNOWN_CAPABILITY_ISSUE]
        if not all(assignments.values()) or checks['station_errors'] or unexpected or transitive:
            raise ValueError('Incomplete or conflicting expert assignment: '+part)
        parts[part]={'revision':p['revision'],'project_sha256':sha256(path),'record_count':len(p['operations']),
                     'active_count':len(active),'assigned_count':len(assignments),'assignments':assignments,
                     'reference_issues':checks['reference_issues'],'transitive_conflicts':transitive}
    confirmation={'date':'2026-11-13','status':'USER_CONFIRMED_EXPERT_CORRESPONDENCE',
                  'parts':parts,'P004_O013':{'geometry_authority':'CURRENT_USER_ANNOTATION',
                  'policy':'约0.04176mm二维中线差已接受，不再追究或作为阻塞项，不以STEP覆盖人工二维几何。',
                  'step_measurements':'保留为成品几何和角度定义核验资料，不触发全局容差或角度变更。'},
                  'pending_capability_question':{'part_id':'P002','operation_id':'O069','station_id':'OP80',
                     'issue':KNOWN_CAPABILITY_ISSUE,'status':'AWAITING_USER','capability_changed':False},
                  'scope':'专家动作到工序对应完成；P002一条能力不一致待确认，不代表完整方案合法、逐序实体或制造验证完成。'}
    (ROOT/'configs/P002_P004_expert_confirmation_20261113.json').write_text(json.dumps(confirmation,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'P002_added':len(changed),'parts':{k:{f:v[f] for f in ['revision','record_count','active_count','assigned_count']} for k,v in parts.items()}},ensure_ascii=False))


if __name__=='__main__':main()
