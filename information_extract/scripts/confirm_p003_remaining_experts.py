"""Apply the user's last three expert assignments and five explicit parent removals."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from information_extract.extract_app.drawing import load_drawing
from information_extract.extract_app.model import ProjectStore, validate_project

ROOT = Path(__file__).resolve().parents[1]
DECISION = 'P003_REMAINING_EXPERTS_AND_PARENT_REMOVALS_20261112'


def revise(previous, screenshot_file=None, screenshot_sha256=None):
    if previous['part_id'] != 'P003' or previous['revision'] != 8:
        raise ValueError('Expected P003 revision 8; refusing to overwrite later edits.')
    p = deepcopy(previous); by = {o['operation_id']: o for o in p['operations']}
    removals = [('O070', 'O069')] + [(oid, 'O070') for oid in ('O071', 'O072', 'O073', 'O074')]
    for child, parent in removals:
        if by[child].get('cad_parent_operation_id') != parent:
            raise ValueError('Parent changed for ' + child)
        by[child]['cad_parent_operation_id'] = None
    assignments = {'O113': 'OP10', 'O099': 'OP20', 'O070': 'OP40'}
    for oid, station in assignments.items(): by[oid]['expert_station'] = station
    p.setdefault('user_decisions', []).append({
        'id': DECISION,
        'quote': 'O113指定到OP10，O099到OP20；O070在窗口5 OP40高亮、窗口6未高亮；取消O071–O074与O070的父子关系，并同步取消O070与O069的关系。',
        'expert_assignments': assignments,
        'removed_cad_parent_links': [{'child': c, 'parent': par} for c, par in removals],
        'screenshot_file': screenshot_file, 'screenshot_sha256': screenshot_sha256,
        'scope': '只取消用户明确的五条CAD父子边；H7规则不变。O120的明确前置O069保留；不改变几何、材料侧、角度或能力。原双序高亮/投影不匹配证据保留。'})
    return p


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--apply', action='store_true'); args = parser.parse_args()
    cfg = json.loads((ROOT / 'configs/P003.json').read_text(encoding='utf-8'))
    drawing = load_drawing(ROOT / cfg['source_file'], cfg); store = ProjectStore(ROOT, drawing, 'P003')
    previous = store.load()
    screenshot = 'sources/P003/confirmations/O070_windows5_6_20261112.png'
    project = revise(previous, screenshot, hashlib.sha256((ROOT / screenshot).read_bytes()).hexdigest())
    report = validate_project(project, drawing, annotation_only=True)
    if report['errors'] or report['process_config']['reference_issues']:
        raise ValueError(json.dumps({'errors': report['errors'], 'reference_issues': report['process_config']['reference_issues']}, ensure_ascii=False))
    out = ROOT / 'outputs/p003_remaining_experts_20261112'; out.mkdir(parents=True, exist_ok=True)
    for name, value in [('before_project.json', previous), ('project_preview.json', project), ('validation.json', report)]:
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.apply: project = store.save(project, previous['revision'])['project']
    print(json.dumps({'applied': args.apply, 'revision': project['revision'],
                      'assigned': sum(bool(o.get('expert_station')) for o in project['operations']),
                      'errors': report['errors'], 'reference_issues': report['process_config']['reference_issues']}, ensure_ascii=False))


if __name__ == '__main__': main()
