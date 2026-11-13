"""Persist the user's OP10 capability and six pre-punch moves, keeping H7 links."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from information_extract.extract_app.drawing import load_drawing
from information_extract.extract_app.model import ProjectStore, validate_project

ROOT = Path(__file__).resolve().parents[1]
PAIRS = {'O016':'O015','O020':'O019','O022':'O021','O024':'O023','O030':'O029','O034':'O033'}
DECISION_ID = 'P004_OP10_PUNCH_AND_SIX_PREHOLES_20261112'


def revise(previous):
    if previous['part_id'] != 'P004' or previous['revision'] != 9:
        raise ValueError('Expected P004 revision 9; refusing to overwrite later edits.')
    p = deepcopy(previous); by = {o['operation_id']:o for o in p['operations']}
    station = next(s for s in p['station_rows'] if s['station_id']=='OP10')
    if station['allowed_operation_types'] != 'NONE': raise ValueError('OP10 capability changed')
    station['allowed_operation_types'] = 'PUNCH'
    for child,parent in PAIRS.items():
        if by[child]['operation_type']!='PRE_PUNCH' or by[parent]['operation_type']!='FLANGING' or by[child].get('cad_parent_operation_id')!=parent:
            raise ValueError('Pre-punch pairing changed: '+child)
        by[child]['expert_station']='OP10'; by[parent]['expert_station']='OP20'
    p.setdefault('user_decisions',[]).append({
        'id':DECISION_ID,'quote':'让OP10支持冲孔，把冲突的预孔移动到OP10',
        'previous_OP10_capability':'NONE','new_OP10_capability':'PUNCH',
        'pre_punch_assignments':{child:'OP10' for child in PAIRS},
        'flanging_assignments':{parent:'OP20' for parent in PAIRS.values()},
        'retained_cad_parent_links':[{'child':child,'parent':parent} for child,parent in PAIRS.items()],
        'scope':'用户调整后的专家分配；原图预孔和翻孔同在OP20红色高亮证据不改。六对关系与H7严格先后保留，未确认的15项几何候选不回填。'})
    return p


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    cfg=json.loads((ROOT/'configs/P004.json').read_text(encoding='utf-8'))
    drawing=load_drawing(ROOT/cfg['source_file'],cfg);store=ProjectStore(ROOT,drawing,'P004');previous=store.load()
    updated=revise(previous);report=validate_project(updated,drawing,annotation_only=True)
    if report['errors'] or report['process_config']['reference_issues']:
        raise ValueError(json.dumps({'errors':report['errors'],'reference_issues':report['process_config']['reference_issues']},ensure_ascii=False))
    out=ROOT/'outputs/p004_pre_punch_revision_20261112';out.mkdir(parents=True,exist_ok=True)
    for name,obj in [('before_project.json',previous),('project_preview.json',updated),('validation.json',report)]:
        (out/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.apply: updated=store.save(updated,previous['revision'])['project']
    print(json.dumps({'applied':args.apply,'revision':updated['revision'],'assigned':sum(bool(o.get('expert_station')) for o in updated['operations']),
                      'errors':report['errors'],'reference_issues':report['process_config']['reference_issues']},ensure_ascii=False))


if __name__=='__main__':main()
