"""Station editor persistence, reference isolation, and diagnostics."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from information_extract.tests import test_api as api_fixture
from information_extract.tests.test_exporter import project, report
from extract_app.process_config import inspect_process, existing_station_defaults
from extract_app.workflow import batch_fingerprint, annotation_project, compile_project
from extract_app.exporter import _rows, export_workbook


class ProcessConfigTests(unittest.TestCase):
    def test_punch_class_covers_all_three_subtypes(self):
        p = annotation_project(project())
        p['station_rows'][0]['allowed_operation_types'] = 'PUNCH'
        p['operations'] = [dict(p['operations'][0], operation_id=f'O{i}',
                               operation_type=kind, expert_station='OP10')
                           for i, kind in enumerate(('CIRCLE_PUNCH', 'IRREGULAR_PUNCH', 'PRE_PUNCH'))]
        self.assertEqual(inspect_process(p)['reference_issues'], [])
        self.assertEqual(set(_rows(p, report())['Sheet3'][0]['allowed_operation_types'].replace(' ', '').split(',')),
                         {'CIRCLE_PUNCH', 'IRREGULAR_PUNCH', 'PRE_PUNCH'})

    def test_trim_excluded_but_references_not_silently_removed(self):
        p = annotation_project(project())
        trim = dict(p['operations'][0], uid='trim', operation_id='OT', operation_type='TRIM', expert_station='MISSING')
        p['operations'].append(trim)
        p['fixed_rows'] = [{'operation_id':'OT','fixed_station':'OP10'}]
        self.assertEqual(len(_rows(p, report())['Sheet1']), 1)
        self.assertEqual(_rows(p, report())['Sheet4'], [])
        self.assertEqual(inspect_process(p)['reference_issues'], [])
        p['operations'][0]['required_predecessor_ids'] = ['OT']
        self.assertIn('不会自动删除依赖', inspect_process(p)['station_errors'][0])
        self.assertEqual(len(p['operations']), 2)

    def test_reference_does_not_change_facts_or_geometry(self):
        p = project()
        original = deepcopy(p)
        p['operations'][0]['expert_station'] = 'NONEXISTENT'
        self.assertEqual(_rows(p, report()), _rows(original, report()))
        self.assertEqual(batch_fingerprint(p, {}), batch_fingerprint(original, {}))
        checked = inspect_process(p)
        self.assertEqual(checked['station_errors'], [])
        self.assertEqual(len(checked['reference_issues']), 1)

    def test_order_capability_and_h7(self):
        p = project()
        p['operations'][0]['expert_station'] = 'OP10'
        child = deepcopy(p['operations'][0])
        child.update(operation_id='O002', cad_parent_operation_id='O001')
        p['operations'].append(child)
        self.assertIn('严格先后', inspect_process(p)['reference_issues'][0])
        p['station_rows'][0]['allowed_operation_types'] = 'PUNCH'
        self.assertFalse(inspect_process(p)['station_errors'])
        p['station_rows'][0]['allowed_operation_types'] = 'NONE, MARKING'
        self.assertTrue(inspect_process(p)['station_errors'])
        p['station_rows'][0]['allowed_operation_types'] = 'NONE'
        self.assertFalse(inspect_process(p)['station_errors'])
        p['station_rows'].append(deepcopy(p['station_rows'][0]))
        self.assertTrue(inspect_process(p)['station_errors'])

    def test_separate_reference_export(self):
        p = project()
        p['operations'][0]['expert_station'] = 'OP10'
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'outputs' / 'test_tmp') as d:
            root = Path(d)
            result = export_workbook(p, report(), root / 'outputs', module_root=root, mode='draft')
            expert = json.loads((Path(result['directory']) / 'expert_assignments.json').read_text(encoding='utf-8'))
            self.assertEqual(expert['assignments'][0]['expert_station'], 'OP10')
            self.assertEqual(_rows(p, report())['Sheet4'], [])

    def test_existing_sheet3_read_only(self):
        root = Path(__file__).resolve().parents[1]
        data = existing_station_defaults(root, 'P002')
        self.assertEqual(len(data['station_rows']), 10)
        self.assertEqual(data['source']['sheets'], ['Sheet3'])
        self.assertEqual(existing_station_defaults(root, '../P002')['station_rows'], [])


class ProcessApiTests(unittest.TestCase):
    setUp = api_fixture.ApiTests.setUp
    tearDown = api_fixture.ApiTests.tearDown
    request = api_fixture.ApiTests.request
    operation = api_fixture.ApiTests.operation
    project = api_fixture.ApiTests.project

    def test_trim_needs_no_geometry_or_fixed_station_in_new_export(self):
        p = annotation_project(self.project(ready=True))
        p['sheet4_confirmed'] = False
        p['operations'].append(dict(p['operations'][0], uid='trim', operation_id='OT',
                                    name='切边', operation_type='TRIM', feature_type='TRIM',
                                    geometry_shape='UNSPECIFIED', source_segment_ids=[]))
        p['fixed_rows'] = [{'operation_id':'OT', 'fixed_station':'OP10'}]
        status, result = self.request('POST', '/api/compile_geometry', {'project': p})
        self.assertEqual(status, 200, result)
        status, exported = self.request('POST', '/api/export', {'project':result['project'], 'mode':'validated'})
        self.assertEqual(status, 200, exported)
        self.assertTrue(exported['ready'], exported)
        import openpyxl
        folder = Path(exported['directory'])
        book = openpyxl.load_workbook(folder / exported['files'][0]['name'], read_only=True)
        self.assertEqual(book['Sheet1'].max_row, 2)
        self.assertEqual(book['Sheet4'].max_row, 1)
        book.close()


def test_saved_expert_and_stations_roundtrip(self):
    p = self.project(ready=True)
    p['operations'][0]['expert_station'] = 'OP10'
    status, saved = self.request('POST', '/api/save', {'project': p, 'expected_revision': 0})
    self.assertEqual(status, 200, saved)
    status, loaded = self.request('GET', '/api/project')
    self.assertEqual(loaded['project']['operations'][0]['expert_station'], 'OP10')
    self.assertEqual(loaded['project']['station_rows'][0]['allowed_operation_types'], 'PUNCH')
    self.assertEqual(loaded['project']['fixed_rows'], [])


ProcessApiTests.test_saved_expert_and_stations_roundtrip = test_saved_expert_and_stations_roundtrip

if __name__ == '__main__':
    unittest.main()
