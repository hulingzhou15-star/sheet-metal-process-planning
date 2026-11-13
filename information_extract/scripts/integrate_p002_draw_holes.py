"""User-authorized O106/O107 integration into O105; keep source geometry."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from information_extract.extract_app.drawing import load_drawing
from information_extract.extract_app.model import ProjectStore, validate_project
from information_extract.extract_app.workflow import annotation_project

MODULE=Path(__file__).resolve().parents[1]


def integrate(previous):
    project=annotation_project(previous)
    ops={o['operation_id']:o for o in project['operations']}
    target=ops['O105']
    if len(ops)!=136 or target.get('composite_operation_ids') or target['operation_type']!='DRAW_FORM':
        raise ValueError('Expected pre-integration P002; refusing to overwrite later edits.')
    for oid in ('O106','O107'):
        hole=ops[oid]
        if hole.get('superseded_by_operation_id') or hole['operation_type']!='CIRCLE_PUNCH' or hole.get('not_later_than_operation_ids')!=['O105'] or hole.get('cad_parent_operation_id'):
            raise ValueError(f'{oid} changed since the previous decision; inspect before applying.')
    target['primary_source_segment_ids']=list(target['source_segment_ids'])
    target['composite_operation_ids']=['O106','O107']
    target['composite_policy']='INTEGRATED_DRAW_FORM'
    target['source_segment_ids']=list(dict.fromkeys(target['source_segment_ids']+ops['O106']['source_segment_ids']+ops['O107']['source_segment_ids']))
    target['expert_station']='OP10'
    target['notes']=(target.get('notes','')+'\n用户确认：O106/O107视作本拉包的组成孔，随O105作为一个动作分配；保留孔几何，不推定序内先冲后拉。').strip()
    for oid in ('O106','O107'):
        ops[oid]['superseded_by_operation_id']='O105'
        ops[oid]['not_later_than_operation_ids']=[]
    project.setdefault('user_decisions',[]).append({
        'id':'P002_DRAW_INTEGRATED_HOLES_20261111','quote':'那把这两个孔视作拉包的一部分吧',
        'operation_id':'O105','component_operation_ids':['O106','O107'],
        'single_allocation_action':True,'expert_station':'OP10',
        'supersedes_not_later_relations':[{'operation_id':oid,'not_later_than':'O105'} for oid in ('O106','O107')],
        'scope':'用户建模抽象；保留原图OP40两孔高亮证据，不将其冒充为OP10孔轮廓高亮，不假设序内先后。'})
    project['inventory_confirmed']=False
    project.pop('geometry_batch',None)
    return project


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    config=json.loads((MODULE/'configs/P002.json').read_text(encoding='utf-8'))
    drawing=load_drawing(MODULE/config['source_file'],config)
    store=ProjectStore(MODULE,drawing,'P002');previous=store.load();project=integrate(previous)
    report=validate_project(project,drawing,annotation_only=True)
    if report['errors']:raise ValueError(json.dumps(report['errors'],ensure_ascii=False))
    out=MODULE/'outputs/p002_integrated_draw_holes_20261111';out.mkdir(parents=True,exist_ok=True)
    for name,value in [('before_project.json',previous),('project_preview.json',project),('validation.json',report)]:
        (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.apply:project=store.save(project,previous['revision'])['project']
    print(json.dumps({'applied':args.apply,'revision':project['revision'],'records':len(project['operations']),
                      'allocation_count':report['process_config']['total'],'errors':report['errors'],
                      'capability_conflicts':report['process_config']['relation_capacity_issues']},ensure_ascii=False))


if __name__=='__main__':main()
