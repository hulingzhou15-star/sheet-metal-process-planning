"""Apply the user's P002 Z-bend merge and explicit hole <= draw-form decisions."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from information_extract.extract_app.drawing import load_drawing
from information_extract.extract_app.model import ProjectStore, validate_project
from information_extract.extract_app.workflow import annotation_project

MODULE=Path(__file__).resolve().parents[1]


def revise(previous):
    project=annotation_project(previous)
    ops={o['operation_id']:o for o in project['operations']}
    if 'O136' in ops or len(ops)!=135:
        raise ValueError('Expected pre-merge 135-action project; refusing to overwrite later edits.')
    for child,parent in [('O042','O040'),('O043','O042'),('O106','O105'),('O107','O105')]:
        if ops[child].get('cad_parent_operation_id')!=parent:
            raise ValueError(f'Parent changed for {child}; inspect current user edits first.')
    merged=deepcopy(ops['O042'])
    merged.update(uid='P002_composite_O136',operation_id='O136',name='光面Z折',
                  operation_type='BEND',feature_type='BEND_Z',geometry_shape='COMPOSITE_Z',
                  surface_side='SMOOTH',cad_parent_operation_id='O040',expert_station='OP50',
                  composite_operation_ids=['O042','O043'],bend_angle_deg=None,
                  source_segment_ids=list(dict.fromkeys(ops['O042']['source_segment_ids']+ops['O043']['source_segment_ids'])),
                  shared_source_segment_ids=[],bend_source_is_centerline=False,region=None,
                  required_predecessor_ids=list(dict.fromkeys(ops['O042'].get('required_predecessor_ids',[])+ops['O043'].get('required_predecessor_ids',[]))),
                  notes='用户确认：O042/O043合并为光面Z折，O136父动作为O040；原两动作不独立分配。专家OP50沿用两原动作图示工序。')
    if set(merged['required_predecessor_ids'])&{'O042','O043'}:
        raise ValueError('Unexpected internal strict predecessors; do not discard automatically.')
    for oid in ('O042','O043'):
        ops[oid]['superseded_by_operation_id']='O136'
    project['operations'].append(merged)
    for oid in ('O106','O107'):
        ops[oid]['cad_parent_operation_id']=None
        ops[oid]['not_later_than_operation_ids']=list(dict.fromkeys(ops[oid].get('not_later_than_operation_ids',[])+['O105']))
    project.setdefault('user_decisions',[]).append({
        'id':'P002_Z_BEND_AND_HOLE_NOT_LATER_20261111',
        'merge':{'operation_ids':['O042','O043'],'replacement':'O136','parent':'O040','name':'光面Z折'},
        'removed_cad_parent_links':[{'child':oid,'parent':'O105'} for oid in ('O106','O107')],
        'not_later_than':[{'operation_id':oid,'not_later_than':'O105'} for oid in ('O106','O107')],
        'source':'用户明确确认：H7保留；两孔去除拉包父子关系，但必须不晚于拉包。',
        'expert_note':'原图孔OP40、拉包OP10仍冲突，不改图示分配以满足约束。'})
    project['expected_operation_count']=136
    project['inventory_confirmed']=False
    project.pop('geometry_batch',None)
    return project


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    config=json.loads((MODULE/'configs/P002.json').read_text(encoding='utf-8'))
    drawing=load_drawing(MODULE/config['source_file'],config)
    store=ProjectStore(MODULE,drawing,'P002');previous=store.load()
    project=revise(previous)
    report=validate_project(project,drawing,annotation_only=True)
    if report['errors']:raise ValueError(json.dumps(report['errors'],ensure_ascii=False))
    out=MODULE/'outputs/p002_z_bend_revision_20261111';out.mkdir(parents=True,exist_ok=True)
    for name,value in [('before_project.json',previous),('project_preview.json',project),('validation.json',report)]:
        (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.apply:project=store.save(project,previous['revision'])['project']
    print(json.dumps({'applied':args.apply,'revision':project['revision'],'records':len(project['operations']),
                      'allocation_count':report['process_config']['total'],'errors':report['errors']},ensure_ascii=False))


if __name__=='__main__':main()
