from copy import deepcopy
from io import BytesIO
import json
import math
from pathlib import Path
import tempfile
import unittest

import openpyxl

from extract_app.model import new_project, ProjectStore, validate_project
from extract_app.operation_fields import (FIELD_DEFAULTS, fill_missing_bend_angles,
                                          parse_bend_angle, validate_bend_angle)
from extract_app import exporter

ROOT = Path(__file__).resolve().parents[1]


def bend(uid='a', angle=90):
    return {'uid': uid, 'operation_id': uid.upper(), 'name': '折弯', 'operation_type': 'BEND',
            'feature_type': 'BEND_STRAIGHT', 'geometry_shape': 'STRAIGHT', 'surface_side': 'SMOOTH',
            'source_segment_ids': ['line'], 'required_predecessor_ids': [], 'bend_angle_deg': angle}


def drawing():
    return {'source': {'sha256': 'test'}, 'config': {'geometry_tolerance': 1e-6, 'parallel_tolerance': 1e-6},
            'segments': [{'id': 'line', 'segment_type': 'LINE', 'start_x': 0., 'start_y': 0., 'end_x': 10., 'end_y': 0.}], 'issues': []}


class BendAngleTests(unittest.TestCase):
    def test_default_is_centrally_configured_90_degrees(self):
        self.assertEqual(FIELD_DEFAULTS['bend_angle_deg'], 90)

    def test_backfill_covers_straight_and_curved_bends_without_mutating_input(self):
        a, b = bend('a', None), bend('b', None)
        b.update(feature_type='BEND_CURVED', geometry_shape='CURVED')
        other = {'uid': 'c', 'operation_id': 'C', 'operation_type': 'CIRCLE_PUNCH'}
        project = {'operations': [a, b, other]}
        before = deepcopy(project)
        updated, ids = fill_missing_bend_angles(project)
        self.assertEqual(ids, ['A', 'B'])
        self.assertEqual([op.get('bend_angle_deg') for op in updated['operations']], [90, 90, None])
        self.assertNotIn('bend_angle_deg', updated['operations'][2])
        self.assertEqual(project, before)

    def test_backfill_is_idempotent_and_preserves_explicit_angles(self):
        project = {'operations': [bend('a', 45), bend('b', 0)]}
        updated, ids = fill_missing_bend_angles(project)
        self.assertEqual(updated, project)
        self.assertEqual(ids, [])

    def test_metadata_does_not_invent_a_physical_angle_range(self):
        for value in [None, -10, 0, 45.5, 90, 180, 200]:
            validate_bend_angle(value)
        for value in [True, False, '90', float('nan'), float('inf')]:
            with self.assertRaises(ValueError):
                validate_bend_angle(value)

    def test_import_parser_handles_blank_numeric_and_invalid_values(self):
        self.assertIsNone(parse_bend_angle(''))
        self.assertIsNone(parse_bend_angle(None))
        self.assertEqual(parse_bend_angle('45.5'), 45.5)
        for value in ['bad', 'nan', True]:
            with self.assertRaises(ValueError):
                parse_bend_angle(value)

    def test_save_preserves_angle_from_older_client_and_allows_explicit_clear(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ProjectStore(folder, drawing(), 'TEST')
            p = new_project(drawing(), 'TEST')
            p.update(workflow_version=2, operations=[bend(angle=45.5)])
            first = store.save(p, 0)['project']
            first['operations'][0].pop('bend_angle_deg')
            saved = store.save(first, 1)['project']
            self.assertEqual(saved['operations'][0]['bend_angle_deg'], 45.5)
            saved['operations'][0]['bend_angle_deg'] = None
            cleared = store.save(saved, 2)['project']
            self.assertIsNone(cleared['operations'][0]['bend_angle_deg'])

    def test_new_bend_saved_by_old_client_gets_default(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ProjectStore(folder, drawing(), 'TEST')
            p = new_project(drawing(), 'TEST')
            op = bend();op.pop('bend_angle_deg')
            p.update(workflow_version=2, operations=[op])
            saved = store.save(p, 0)['project']
            self.assertEqual(saved['operations'][0]['bend_angle_deg'], 90)

    def test_angle_entry_validation_and_unknown_legacy_compatibility(self):
        p = new_project(drawing(), 'TEST');p.update(workflow_version=2, operations=[bend(angle='bad')])
        self.assertIn('BEND_ANGLE_VALUE', {e['code'] for e in validate_project(p, drawing())['errors']})
        p['operations'][0].pop('bend_angle_deg')
        self.assertNotIn('BEND_ANGLE_VALUE', {e['code'] for e in validate_project(p, drawing())['errors']})

    def test_csv_and_xlsx_import_preserve_angle(self):
        data = b'operation_id,name,operation_type,bend_angle_deg\nA,bend,BEND,45.5\nB,bend,BEND,\n'
        rows = exporter.import_operation_csv(data)['operations']
        self.assertEqual(rows[0]['bend_angle_deg'], 45.5)
        self.assertIsNone(rows[1]['bend_angle_deg'])
        book = openpyxl.Workbook();book.active.append(['operation_id', 'operation_type', 'bend_angle_deg'])
        book.active.append(['A', 'BEND', 120]);buffer = BytesIO();book.save(buffer);book.close()
        self.assertEqual(exporter.import_operation_workbook(buffer.getvalue())['operations'][0]['bend_angle_deg'], 120)

    def test_export_metadata_contains_angle_and_five_sheet_contract_is_unchanged(self):
        scratch = ROOT / 'outputs/test_tmp';scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as folder:
            p = {'part_id': 'TEST', 'source_sha256': 'test', 'operations': [bend(angle=45.5)], 'station_rows': [], 'fixed_rows': []}
            report = {'errors': [], 'warnings': [], 'ready': False, 'geometries': {}, 'regions': []}
            output = exporter.export_workbook(p, report, Path(folder)/'outputs', module_root=Path(folder))
            path = Path(output['directory'])
            metadata = json.loads((path/'operation_metadata.json').read_text(encoding='utf-8'))
            self.assertEqual(metadata['operations'], [{'operation_id': 'A', 'bend_angle_deg': 45.5}])
            book = openpyxl.load_workbook(next(path.glob('*.xlsx')), read_only=True)
            try:
                self.assertEqual(book.sheetnames, list(exporter.HEADERS))
                self.assertNotIn('bend_angle_deg', [c.value for c in next(book['Sheet1'].rows)])
            finally:
                book.close()
