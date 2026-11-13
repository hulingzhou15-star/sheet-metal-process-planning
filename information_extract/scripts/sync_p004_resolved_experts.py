"""Copy uniquely resolved P004 expert assignments into the editable project."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from information_extract.extract_app.drawing import load_drawing
from information_extract.extract_app.model import ProjectStore, validate_project

ROOT = Path(__file__).resolve().parents[1]


def revise(previous, draft):
    if previous['part_id'] != 'P004' or previous['revision'] != draft['annotation_revision']:
        raise ValueError('Extraction does not match the current P004 revision.')
    project = deepcopy(previous)
    rows = {r['operation_id']: r for r in draft['operations']}
    if len(rows) != len(draft['operations']) or set(rows) != {o['operation_id'] for o in project['operations']}:
        raise ValueError('Action inventory changed.')
    changed = {}
    for op in project['operations']:
        row = rows[op['operation_id']]
        if row['status'] not in {'UNIQUE_FULL_HIGHLIGHT', 'USER_CONFIRMED_EXPERT_ASSIGNMENT'}:
            continue
        station = row.get('proposed_expert_station')
        if not station:
            raise ValueError('Resolved action has no station.')
        if op.get('expert_station') and op['expert_station'] != station:
            raise ValueError('Existing assignment differs: ' + op['operation_id'])
        if not op.get('expert_station'):
            op['expert_station'] = station
            changed[op['operation_id']] = station
    project.setdefault('expert_assignment_imports', []).append({
        'source': 'outputs/p004_expert_dxf_v01/assignments_draft.json',
        'source_input_hashes': draft['input_hashes'],
        'previous_revision': previous['revision'],
        'assignments_added': changed,
        'basis': '用户反馈录入页仍未分配；同步唯一完整对应及明确指定结果。绘图差异候选不自动确认。',
    })
    return project, changed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    draft_path = ROOT / 'outputs/p004_expert_dxf_v01/assignments_draft.json'
    draft = json.loads(draft_path.read_text(encoding='utf-8'))
    for relative, expected in draft['input_hashes'].items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise ValueError('Stale extraction input: ' + relative)
    cfg = json.loads((ROOT / 'configs/P004.json').read_text(encoding='utf-8'))
    drawing = load_drawing(ROOT / cfg['source_file'], cfg)
    store = ProjectStore(ROOT, drawing, 'P004')
    previous = store.load()
    project, changed = revise(previous, draft)
    if not changed:
        print('All resolved assignments already saved.')
        return
    report = validate_project(project, drawing, annotation_only=True)
    if report['errors'] or report['process_config']['reference_issues']:
        raise ValueError(json.dumps(report, ensure_ascii=False))
    out = ROOT / 'outputs/p004_expert_sync_20261112'
    out.mkdir(parents=True, exist_ok=True)
    for name, value in [('before_project', previous), ('assignment_evidence', draft), ('validation', report)]:
        (out / (name + '.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.apply:
        project = store.save(project, previous['revision'])['project']
    print(json.dumps({'applied': args.apply, 'revision': project['revision'], 'added': len(changed),
                      'assigned': sum(bool(o.get('expert_station')) for o in project['operations']),
                      'errors': report['errors'], 'reference_issues': report['process_config']['reference_issues']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
