"""Apply the user-confirmed two-step bend, retaining shared material geometry."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from information_extract.extract_app.drawing import load_drawing
from information_extract.extract_app.model import ProjectStore, validate_project

MODULE = Path(__file__).resolve().parents[1]
DECISION_ID = 'P003_O069_O120_TWO_STEP_BEND_20261112'


def revise(previous):
    project = deepcopy(previous)
    ops = {o['operation_id']: o for o in project['operations']}
    if project['part_id'] != 'P003' or 'O120' in ops or len(ops) != 119:
        raise ValueError('Expected P003 before the split; refusing to overwrite later edits.')
    first = ops['O069']
    if (first['operation_type'], first['surface_side'], first['bend_angle_deg']) != ('BEND', 'SMOOTH', 90):
        raise ValueError('O069 changed; inspect the current annotation before applying.')
    second = deepcopy(first)
    second.update(uid='P003_two_step_O120', operation_id='O120', name='光面一般折弯4',
                  surface_side='ROUGH', bend_angle_deg=45, expert_station='OP50',
                  cad_parent_operation_id=None, required_predecessor_ids=['O069'])
    first.update(bend_angle_deg=135, expert_station='OP40')
    for op in (first, second):
        op['shared_source_segment_ids'] = list(dict.fromkeys(
            op.get('shared_source_segment_ids', []) + op['source_segment_ids']))
    first['notes'] = (first.get('notes', '') + '\n用户确认两步折弯：本动作 OP40 光面 +135°；随后 O120 毛面反折45°，最终累计光面90°。两动作共用几何。').strip()
    second['notes'] = '用户确认：名称保留“光面一般折弯4”，表面侧为毛面；OP50 在 O069 完成态基础上反折45°，以光面计增量−45°、累计90°。与 O069 共用几何，严格晚于 O069。'
    project['operations'].append(second)
    if project.get('expected_operation_count') is not None:
        project['expected_operation_count'] = len(project['operations'])
    project.pop('geometry_batch', None)
    project.setdefault('user_decisions', []).append({
        'id': DECISION_ID, 'source': '用户明确确认，2026-11-12',
        'quote': '新增一个O120，仍然叫光面一般折弯4，表面侧属毛面，折弯角45度，原O069需把折弯角改为135；二者共用几何',
        'type': 'SEQUENTIAL_BEND_ACTIONS', 'angle_reference_side': 'SMOOTH',
        'steps': [
            {'operation_id': 'O069', 'expert_station': 'OP40', 'surface_side': 'SMOOTH',
             'bend_angle_deg': 135, 'signed_increment_deg': 135, 'cumulative_angle_deg': 135},
            {'operation_id': 'O120', 'expert_station': 'OP50', 'surface_side': 'ROUGH',
             'bend_angle_deg': 45, 'signed_increment_deg': -45, 'cumulative_angle_deg': 90}],
        'shared_source_segment_ids': first['source_segment_ids'][:],
        'strict_precedence': {'before': 'O069', 'after': 'O120'},
        'scope': '两独立分配动作；共用几何不是误重复。原CAD父子关系不变，原始多序高亮证据保留。三维累计旋转适配尚未实现。'})
    return project


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--apply', action='store_true'); args = parser.parse_args()
    config = json.loads((MODULE / 'configs/P003.json').read_text(encoding='utf-8'))
    drawing = load_drawing(MODULE / config['source_file'], config)
    store = ProjectStore(MODULE, drawing, 'P003'); previous = store.load()
    project = revise(previous)
    report = validate_project(project, drawing, annotation_only=True)
    if report['errors']:
        raise ValueError(json.dumps(report['errors'], ensure_ascii=False))
    out = MODULE / 'outputs/p003_two_step_bend_20261112'; out.mkdir(parents=True, exist_ok=True)
    for name, value in [('before_project.json', previous), ('project_preview.json', project), ('validation.json', report)]:
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.apply:
        project = store.save(project, previous['revision'])['project']
    print(json.dumps({'applied': args.apply, 'revision': project['revision'], 'records': len(project['operations']),
                      'errors': report['errors'], 'reference_issues': report['process_config']['reference_issues']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
