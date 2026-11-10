"""Read only Sheet1 fact columns, Sheet2, Sheet3. No legacy context loader.

The reader never requests Sheet4/5 or expert_station. The input fingerprint
depends on the permitted facts only; the file hash is provenance, not a feature.
"""
from collections import Counter
from io import BytesIO
from pathlib import Path
import hashlib
import json
import math
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED
import openpyxl

ALLOWED = {
    'Sheet1': ('part_id', 'operation_id', 'source_feature_ref', 'operation_type',
               'surface_side', 'required_predecessor_ids', 'cad_parent_operation_id',
               'process_group_id', 'geometry_id'),
    'Sheet2': ('part_id', 'geometry_id', 'seq', 'segment_type', 'start_x', 'start_y',
               'end_x', 'end_y', 'center_x', 'center_y', 'radius', 'sweep_angle'),
    'Sheet3': ('part_id', 'station_id', 'order', 'allowed_operation_types'),
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def read_facts(path, part_id):
    content = Path(path).read_bytes()
    # openpyxl normally inspects every sheet's dimensions on load. Construct
    # a memory-only package containing just the allowed sheets first, so even
    # malformed/absent constraint sheets cannot affect this reader.
    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    relns = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
    isolated = BytesIO()
    with ZipFile(BytesIO(content)) as incoming, ZipFile(isolated,'w',ZIP_DEFLATED) as outgoing:
        tree = ET.fromstring(incoming.read('xl/workbook.xml'))
        rels = ET.fromstring(incoming.read('xl/_rels/workbook.xml.rels'))
        targets = {r.attrib['Id']: r.attrib['Target'] for r in rels}
        permitted = set()
        for sheet in list(tree.find(ns+'sheets')):
            if sheet.attrib['name'] not in ALLOWED:
                tree.find(ns+'sheets').remove(sheet)
            else:
                target = targets[sheet.attrib[relns+'id']]
                permitted.add(target.lstrip('/') if target.startswith('/') else 'xl/'+target)
        for entry in incoming.infolist():
            if entry.filename.startswith('xl/worksheets/') and entry.filename not in permitted:
                continue
            outgoing.writestr(entry,ET.tostring(tree) if entry.filename=='xl/workbook.xml' else incoming.read(entry.filename))
    workbook = openpyxl.load_workbook(BytesIO(isolated.getvalue()), read_only=True, data_only=True)
    formulas = openpyxl.load_workbook(BytesIO(isolated.getvalue()), read_only=True, data_only=False)
    tables = {}
    try:
        for name, fields in ALLOWED.items():
            sheet = workbook[name]
            header = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))
            indices = {field: header.index(field) for field in fields}
            rows = []
            for cells, raw in zip(sheet.iter_rows(min_row=2), formulas[name].iter_rows(min_row=2)):
                values = {key: cells[index].value for key, index in indices.items()}
                if not any(v is not None for v in values.values()):
                    continue
                if values['part_id'] != part_id:
                    raise ValueError(f'Foreign part in {name}: {values["part_id"]}')
                if any(raw[index].data_type == 'e' or (raw[index].data_type == 'f' and cells[index].value is None)
                       for index in indices.values()):
                    raise ValueError(f'Error or missing formula cache: {name}, row {raw[0].row}')
                rows.append(values)
            tables[name] = rows
    finally:
        workbook.close()
        formulas.close()
    operations = sorted(tables['Sheet1'], key=lambda row: row['operation_id'])
    if len({o['operation_id'] for o in operations}) != len(operations):
        raise ValueError('Duplicate operation_id')
    geometry = {}
    for row in tables['Sheet2']:
        geometry.setdefault(row['geometry_id'], []).append(row)
    issues = []
    for op in operations:
        op['segments'] = sorted(geometry.get(op['geometry_id'], []), key=lambda row: row['seq'])
        try:
            op['paths'] = paths(op['segments'])
        except (ValueError, TypeError) as exc:
            op['paths'] = []
            issues.append({'operation_id': op['operation_id'], 'reason': str(exc)})
        op['geometry_status'] = 'AVAILABLE_2D' if op['paths'] else 'MISSING_2D'
    return {'part_id': part_id, 'facts_fingerprint': fingerprint(tables),
            'operations': operations, 'stations': tables['Sheet3'],
            'coverage': {'operation_count': len(operations),
                         'with_2d_geometry': sum(bool(o['paths']) for o in operations),
                         'missing_2d': [o['operation_id'] for o in operations if not o['paths']],
                         'types': dict(Counter(o['operation_type'] for o in operations)),
                         'geometry_issues': issues},
            'read_scope': {'sheets': list(ALLOWED), 'columns': ALLOWED,
                           'expert_assignments_read': False, 'sheet5_read': False}}


def paths(segments):
    """Engineering y-up display polylines; never change stored curve parameters."""
    result = []
    for s in segments:
        kind = s['segment_type']
        if kind in ('LINE', 'BEND_LINE'):
            points = [[s['start_x'], s['start_y']], [s['end_x'], s['end_y']]]
        elif kind in ('ARC', 'CIRCLE'):
            cx, cy, r = s['center_x'], s['center_y'], s['radius']
            if r is None or r <= 0:
                raise ValueError('Invalid circle radius')
            angle = math.atan2(s['start_y']-cy, s['start_x']-cx) if kind == 'ARC' else 0
            sweep = math.radians(s['sweep_angle']) if kind == 'ARC' else 2*math.pi
            steps = max(8, math.ceil(abs(sweep)/(math.pi/24)))
            points = [[cx+r*math.cos(angle+sweep*i/steps), cy+r*math.sin(angle+sweep*i/steps)]
                      for i in range(steps+1)]
        elif kind == 'BRIDGE':
            # Same recorded axis-aligned footprint convention as frozen M1.
            x0, y0, x1, y1 = s['start_x'], s['start_y'], s['end_x'], s['end_y']
            points = [[x0,y0],[x1,y0],[x1,y1],[x0,y1],[x0,y0]]
        else:
            raise ValueError(f'Unsupported segment {kind}')
        if not all(isinstance(v, (float, int)) and math.isfinite(v) for p in points for v in p):
            raise ValueError('Nonfinite coordinates')
        result.append(points)
    return result


def frozen_snapshot(root):
    """Hash project artifacts outside this experiment; no timestamp-based claims."""
    roots = ['src', 'configs', 'docs', 'tests', 'data', 'outputs']
    files = [root/'PROJECT_SPEC.md', root/'README.md']
    for name in roots:
        files.extend(p for p in (root/name).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and p.suffix != '.pyc')
    return {p.relative_to(root).as_posix(): sha256(p) for p in sorted(files) if p.exists()}
