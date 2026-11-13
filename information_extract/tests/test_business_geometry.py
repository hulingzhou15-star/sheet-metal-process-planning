"""P001 import fidelity and replacement/persistence boundaries."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from information_extract.scripts.import_p001_business_project import (
    MODULE, WORKBOOK, build_project, read_tables, sha)
from information_extract.extract_app.drawing import load_drawing
from information_extract.extract_app.model import new_project, validate_project, ProjectStore
from information_extract.extract_app.business_geometry import business_record
from information_extract.extract_app.exporter import _rows, HEADERS, _write_workbook
import openpyxl


class BusinessGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = json.loads((MODULE / 'configs/app.json').read_text(encoding='utf-8'))
        cls.drawing = load_drawing(MODULE / config['source_file'], config)
        cls.project, cls.audit = build_project(new_project(cls.drawing, 'P001'), cls.drawing)
        cls.tables = read_tables()

    def test_exact_geometry_and_regions_in_export(self):
        report = validate_project(self.project, self.drawing, annotation_only=True)
        rows = _rows(self.project, report)
        for sheet in ('Sheet2', 'Sheet5'):
            expected = [{k: r.get(k) for k in HEADERS[sheet]} for r in self.tables[sheet]]
            actual = [{k: r.get(k) for k in HEADERS[sheet]} for r in rows[sheet]]
            self.assertEqual(actual, expected)
        self.assertEqual(len(rows['Sheet1']), 214)
        self.assertEqual(rows['Sheet4'], [])
        self.assertTrue(all('expert_station' not in r for r in rows['Sheet1']))

    def test_only_three_missing_with_reference_dxf(self):
        report = validate_project(self.project, self.drawing, annotation_only=True)
        self.assertEqual([(r['code'], r.get('operation_id')) for r in report['errors']],
                         [('SOURCES_MISSING', k) for k in ('O001', 'O026', 'O175')])
        self.assertEqual(report['statistics']['business_geometry_operations'], 211)
        self.assertEqual(report['statistics']['missing_action_geometry'], 3)
        self.assertEqual(report['statistics']['unhandled'], 0)
        self.assertEqual(set(report['statuses'].values()), {'reference', 'absorbed'})
        self.assertEqual(report['statistics']['reference'] + report['statistics']['absorbed'], len(self.drawing['segments']))
        self.assertEqual(self.audit['circle_rows_checked'], 156)
        self.assertLess(self.audit['circle_center_alignment_mm_max'], .001)

    def test_raw_binding_supersedes_and_clear_restores(self):
        op = deepcopy(next(o for o in self.project['operations'] if o['operation_type'] == 'CIRCLE_PUNCH'))
        original = business_record(op)
        op['name'] = '改名不改几何'
        op['expert_station'] = 'OP50'
        self.assertEqual(business_record(op), original)
        circle = next(s for s in self.drawing['segments'] if s['segment_type'] == 'CIRCLE')
        op['source_segment_ids'] = [circle['id']]
        self.assertIsNone(business_record(op))
        op['source_segment_ids'] = []
        self.assertEqual(business_record(op), original)
        op['feature_type'] = 'IRREGULAR_PUNCH'
        self.assertIsNone(business_record(op))

    def test_disk_roundtrip_and_workbook_values(self):
        before = sha(WORKBOOK)
        temp_root = MODULE / 'outputs/test_tmp'
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            store = ProjectStore(directory, self.drawing, 'P001')
            saved = store.save(self.project, 0)['project']
            self.assertEqual(store.load(), saved)
            target = Path(directory) / 'facts.xlsx'
            _write_workbook(saved, validate_project(saved, self.drawing), target)
            book = openpyxl.load_workbook(target, read_only=True, data_only=True)
            try:
                rows = list(book['Sheet2'].values)
                expected = [tuple(r.get(k) for k in HEADERS['Sheet2']) for r in self.tables['Sheet2']]
                self.assertEqual(rows[1:], expected)
            finally:
                book.close()
        self.assertEqual(sha(WORKBOOK), before)

    def test_existing_operations_cannot_be_overwritten(self):
        with self.assertRaisesRegex(ValueError, '禁止'):
            build_project(self.project, self.drawing)


if __name__ == '__main__':
    unittest.main()
