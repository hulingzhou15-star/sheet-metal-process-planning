"""Explicit, reversible P001 workbook -> editor import; no workbook writes."""
import argparse
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

import openpyxl
from information_extract.extract_app.drawing import load_drawing
from information_extract.extract_app.model import ProjectStore, validate_project
from information_extract.extract_app.workflow import annotation_project
from information_extract.extract_app.geometry import FIELDS

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / 'information_extract'
WORKBOOK = ROOT / 'data/parts/P001/钣金加工数据表_P001.xlsx'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_tables():
    book = openpyxl.load_workbook(WORKBOOK, read_only=True, data_only=True)
    try:
        tables = {}
        for name in ('Sheet1', 'Sheet2', 'Sheet3', 'Sheet4', 'Sheet5'):
            rows = iter(book[name].values)
            header = next(rows)
            tables[name] = [dict(zip(header, row)) for row in rows if any(v is not None for v in row)]
        return tables
    finally:
        book.close()


def build_project(previous, drawing):
    if previous.get('operations'):
        raise ValueError('P001 已有编辑器动作；禁止用本接入覆盖。请使用已有项目继续编辑。')
    fingerprint = sha(WORKBOOK)
    tables = read_tables()
    source = {'path': WORKBOOK.relative_to(ROOT).as_posix(), 'sha256': fingerprint, 'sheet': 'Sheet2'}
    groups = defaultdict(list)
    for row in tables['Sheet2']:
        groups[row['geometry_id']].append({k: row[k] for k in ('seq', 'segment_type', *FIELDS)})
    regions = {row['bend_operation_id']: row for row in tables['Sheet5']}
    project = deepcopy(previous)
    for row in tables['Sheet1']:
        oid, kind = row['operation_id'], row['operation_type']
        op = {'uid': f'P001_business_{oid}', 'operation_id': oid, 'name': row['source_feature_ref'],
              'operation_type': kind, 'surface_side': row['surface_side'] or 'NOT_APPLICABLE',
              'required_predecessor_ids': [x.strip() for x in (row['required_predecessor_ids'] or '').split(',') if x.strip()],
              'cad_parent_operation_id': row['cad_parent_operation_id'], 'process_group_id': row['process_group_id'],
              'expert_station': row['expert_station'], 'source_segment_ids': [], 'shared_source_segment_ids': [],
              'geometry_options': {}, 'no_geometry': False, 'no_geometry_reason': '',
              'region': None, 'notes': '', 'source_business_row': deepcopy(row)}
        if oid in regions:
            op['region'] = {**regions[oid], 'confirmed': True, 'parent_confirmed': True}
        project['operations'].append(op)
    project = annotation_project(project)
    for op in project['operations']:
        gid = op['source_business_row']['geometry_id']
        if gid in groups:
            op['business_geometry'] = {'geometry_id': gid, 'segments': deepcopy(groups[gid]),
                                       'operation_type': op['operation_type'], 'feature_type': op['feature_type'],
                                       'source': source}
    project['station_rows'] = deepcopy(tables['Sheet3'])
    project['fixed_rows'] = deepcopy(tables['Sheet4'])  # Preserved history; TRIM excluded from new exports.
    project['sheet4_confirmed'] = True
    project['inventory_confirmed'] = False
    project['expected_operation_count'] = len(tables['Sheet1'])
    project['business_import'] = {'source': source, 'geometry_mode': 'approved_workbook_not_dxf_binding',
                                  'previous_editor_revision': previous['revision']}
    project = annotation_project(project)
    # Audit coordinates without inventing a raw source binding. Radius differences
    # include approved pre-hole/flange semantics, and must not overwrite Sheet2.
    circles = [s for s in drawing['segments'] if s['segment_type'] == 'CIRCLE']
    distances = [min(math.hypot(r['center_x']-s['center_x'], r['center_y']-s['center_y']) for s in circles)
                 for rows in groups.values() for r in rows if r['segment_type'] == 'CIRCLE']
    audit = {'source': source, 'actions': len(project['operations']), 'with_business_geometry': len(groups),
             'geometry_rows': sum(map(len, groups.values())), 'excluded_trim': [o['operation_id'] for o in project['operations'] if o['operation_type'] == 'TRIM'],
             'missing_active_geometry': [o['operation_id'] for o in project['operations'] if 'business_geometry' not in o and o['operation_type'] != 'TRIM'],
             'circle_center_alignment_mm_max': max(distances), 'circle_rows_checked': len(distances),
             'raw_dxf_bindings_inferred': 0, 'workbook_unchanged': sha(WORKBOOK) == fingerprint}
    if not audit['workbook_unchanged'] or audit['circle_center_alignment_mm_max'] > .001:
        raise ValueError('源表或材料坐标核对失败，未接入。')
    return project, audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    config = json.loads((MODULE / 'configs/app.json').read_text(encoding='utf-8'))
    drawing = load_drawing(MODULE / config['source_file'], config)
    store = ProjectStore(MODULE, drawing, 'P001')
    previous = store.load()
    project, audit = build_project(previous, drawing)
    report = validate_project(project, drawing, annotation_only=True)
    if [(e['code'], e.get('operation_id')) for e in report['errors']] != [('SOURCES_MISSING', oid) for oid in audit['missing_active_geometry']]:
        raise ValueError('存在额外标注错误：' + json.dumps(report['errors'], ensure_ascii=False))
    out = MODULE / 'outputs/p001_editor_import_v01'
    out.mkdir(parents=True, exist_ok=True)
    for name, value in [('before_project.json', previous), ('project_preview.json', project), ('audit.json', audit)]:
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.apply:
        saved = store.save(project, previous['revision'])
        audit['saved_revision'] = saved['project']['revision']
        (out / 'audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
