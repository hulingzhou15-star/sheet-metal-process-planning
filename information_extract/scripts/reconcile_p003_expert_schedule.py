"""Audit window 3 against window 2, then apply the user's P003 schedule revision."""
import argparse
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import numpy as np
from information_extract.scripts.extract_p002_expert_dxf import key, transformed
from information_extract.extract_app.drawing import load_drawing
from information_extract.extract_app.model import ProjectStore, validate_project

ROOT = Path(__file__).resolve().parents[1]
WINDOW_STATIONS = {'1': 'OP10', '2': 'OP20', '3': None, '4': 'OP30', '5': 'OP40', '6': 'OP50', '7': 'OP60', '8': None}


def revise_window_mapping(previous):
    if previous['part_id'] != 'P003' or previous['revision'] != 7:
        raise ValueError('Expected P003 revision 7; refusing to overwrite subsequent edits.')
    p = deepcopy(previous); by = {o['operation_id']: o for o in p['operations']}
    old = {oid: by[oid].get('expert_station') for oid in ('O069', 'O120')}
    by['O069']['expert_station'] = WINDOW_STATIONS['5']
    by['O120']['expert_station'] = WINDOW_STATIONS['6']
    if any(o.get('expert_station') == 'OP70' for o in p['operations']):
        raise ValueError('Unexpected OP70 assignment: inspect its source window before migrating.')
    for d in p.get('user_decisions', []):
        if d['id'] == 'P003_O069_O120_TWO_STEP_BEND_20261112':
            d.setdefault('expert_station_history', []).append({'superseded': old, 'reason': '用户明确保留窗口1/2/4/5/6/7连续映射OP10–60'})
            for step in d['steps']: step['expert_station'] = by[step['operation_id']]['expert_station']
            d['expert_station_revision'] = '最新窗口映射：O069在原窗口5→OP40，O120在原窗口6→OP50；替代此前OP30/OP40。'
    p.setdefault('user_decisions', []).append({
        'id': 'P003_EXPLICIT_WINDOW_MAPPING_20261112',
        'quote': '去掉第3个窗口和第8个窗口，原来的1 2 4 5 6 7就分别对应新的OP10-OP60',
        'window_to_station': WINDOW_STATIONS, 'previous_expert_stations': old,
        'scope': '窗口号为唯一编号映射依据；窗口3攻牙附图和窗口8空图退出分配。OP70仅保留NONE，无动作。'})
    return p


def compare_windows(evidence, project):
    second, third = [next(f for f in evidence['frames'] if f['frame_id'] == fid) for fid in ('view_02', 'view_03')]
    def normalized(frame):
        m = np.array(frame['matrix']); t = np.array(frame['translation'])
        return [transformed(g, m.T, -m.T @ t) for g in frame['targets']]
    index = defaultdict(list)
    for g in normalized(second): index[key(g)].append(g)
    highlights = [g for g in normalized(third) if g['color'].lower() == '#ff00ff']
    rows = []
    for g in highlights:
        matches = index[key(g)]
        rows.append({'third': g, 'second': matches,
                     'geometry_present': bool(matches),
                     'same_highlight_present': any(m['color'].lower() == '#ff00ff' for m in matches)})
    actions = []
    for op in project['operations']:
        active = [sid for sid in op['source_segment_ids'] if any(g['color'].lower() == '#ff00ff' for g in third['matches'].get(sid, []))]
        if active:
            actions.append({'operation_id': op['operation_id'], 'name': op['name'],
                            'operation_type': op['operation_type'], 'highlighted_source_ids': active,
                            'source_count': len(op['source_segment_ids']),
                            'second_same_highlight_count': sum(any(g['color'].lower() == '#ff00ff' for g in second['matches'].get(sid, [])) for sid in active)})
    return {'geometry_rows': rows, 'actions': actions, 'highlighted_primitive_count': len(rows),
            'action_count': len(actions), 'all_geometry_present': bool(rows) and all(r['geometry_present'] for r in rows),
            'all_same_highlight_present': bool(rows) and all(r['same_highlight_present'] for r in rows),
            'scope': '第三窗口所有紫红色基础图元，以及当前已录动作；严格红色图框/标识不作为加工动作。攻牙说明不新增本项目七大类动作。'}


def revise(previous, assignments, audit):
    if not audit['all_geometry_present'] or not audit['all_same_highlight_present']:
        raise ValueError('Window 3 is not a fully repeated highlighted subset of window 2.')
    p = deepcopy(previous)
    if p['part_id'] != 'P003' or len(p['operations']) != 120 or p['revision'] != 6:
        raise ValueError('Expected reviewed P003 revision 6; do not overwrite subsequent edits.')
    old_stations = deepcopy(p['station_rows'])
    for row in p['station_rows']:
        row['allowed_operation_types'] = {'OP10': 'PUNCH', 'OP20': 'PUNCH, FLANGING, DIMPLE',
                                          'OP70': 'NONE'}.get(row['station_id'], 'BEND')
    proposed = {r['operation_id']: r.get('proposed_expert_station') for r in assignments['operations']}
    proposed.update(O069='OP30', O120='OP40', O071='OP60', O072='OP60', O073='OP60', O074='OP60')
    before = {}
    for op in p['operations']:
        oid = op['operation_id']; station = proposed.get(oid)
        if station:
            before[oid] = op.get('expert_station')
            op['expert_station'] = station
    for decision in p.get('user_decisions', []):
        if decision['id'] == 'P003_O069_O120_TWO_STEP_BEND_20261112':
            decision['previous_expert_stations'] = {s['operation_id']: s['expert_station'] for s in decision['steps']}
            for s in decision['steps']: s['expert_station'] = proposed[s['operation_id']]
            decision['expert_station_revision'] = '用户最新指定O069=OP30、O120=OP40；旧OP40/OP50被替代，角度和几何不变。'
    p.setdefault('user_decisions', []).append({
        'id': 'P003_EXPERT_SCHEDULE_RECONCILIATION_20261112',
        'quote': '第3窗口高亮若均在第2窗口出现：OP10冲孔，OP20冲孔+翻孔+凸米，OP30-60折弯，OP70 None；O069 OP30，O120 OP40；专家序重新对应。',
        'audit': {'highlighted_primitives': audit['highlighted_primitive_count'], 'actions': audit['action_count'], 'all_same_highlight_present': True},
        'previous_station_rows': old_stations, 'previous_expert_stations': before,
        'expert_assignments_applied': {k: v for k, v in proposed.items() if v},
        'unresolved_operation_ids': [k for k, v in proposed.items() if not v],
        'scope': '第三窗口为第二窗口已有29个翻孔的攻牙附图；O071-O074按原6/7图的实际工序OP60，不再按第7窗口记OP70。仅O069/O120按用户指定改为OP30/OP40，其余按已有逐项证据；不机械整体平移，不按能力猜未决动作，不修改H7。'})
    return p


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--apply', action='store_true'); parser.add_argument('--window-map', action='store_true'); args = parser.parse_args()
    cfg = json.loads((ROOT / 'configs/P003.json').read_text(encoding='utf-8'))
    drawing = load_drawing(ROOT / cfg['source_file'], cfg)
    store = ProjectStore(ROOT, drawing, 'P003'); previous = store.load()
    evidence = json.loads((ROOT / 'outputs/p003_expert_dxf_v01/evidence.json').read_text(encoding='utf-8'))
    for path, expected in evidence['summary']['input_hashes'].items():
        if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != expected:
            raise ValueError('Stale extraction input: ' + path)
    audit = compare_windows(evidence, previous)
    updated = revise_window_mapping(previous) if args.window_map else revise(previous, evidence['summary'], audit)
    report = validate_project(updated, drawing, annotation_only=True)
    if report['errors']: raise ValueError(json.dumps(report['errors'], ensure_ascii=False))
    out = ROOT / ('outputs/p003_window_mapping_20261112' if args.window_map else 'outputs/p003_schedule_reconciliation_20261112'); out.mkdir(parents=True, exist_ok=True)
    for name, obj in [('window_comparison.json', audit), ('before_project.json', previous), ('project_preview.json', updated), ('validation.json', report)]:
        (out / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.apply: updated = store.save(updated, previous['revision'])['project']
    print(json.dumps({'applied': args.apply, 'revision': updated['revision'], 'highlighted_primitives': audit['highlighted_primitive_count'],
                      'actions': audit['action_count'], 'all_same_highlight_present': audit['all_same_highlight_present'],
                      'errors': report['errors'], 'reference_issues': report['process_config']['reference_issues']}, ensure_ascii=False))


if __name__ == '__main__': main()
