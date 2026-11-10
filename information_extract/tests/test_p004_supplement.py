from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from extract_app.drawing import load_drawing
from extract_app.model import new_project, validate_project
from extract_app.exporter import export_workbook

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'configs/P004.json').read_text(encoding='utf-8'))
BASE_CONFIG = {k: v for k, v in CONFIG.items() if k not in {'confirmed_geometry_supplement', 'confirmed_duplicate_source_removals'}}


class P004SupplementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = load_drawing(ROOT / 'sources/P004/P004.dxf', BASE_CONFIG)
        cls.drawing = load_drawing(ROOT / 'sources/P004/P004.dxf', CONFIG)
        cls.manifest = json.loads((ROOT / CONFIG['confirmed_geometry_supplement']).read_text(encoding='utf-8'))

    def changed(self, mutate):
        with tempfile.TemporaryDirectory(dir=ROOT / 'outputs/test_tmp') as folder:
            root = Path(folder)
            for name in ('P004.dxf', 'P004_01.dxf'):
                shutil.copyfile(ROOT / 'sources/P004' / name, root / name)
            manifest = deepcopy(self.manifest)
            manifest['reference_file'] = 'P004_01.dxf'
            mutate(manifest)
            (root / 'supplement.json').write_text(json.dumps(manifest), encoding='utf-8')
            return load_drawing(root / 'P004.dxf', {**CONFIG, 'confirmed_geometry_supplement': 'supplement.json'})

    def test_retained_mirrored_sources_and_coordinates_are_unchanged(self):
        removed = {r['remove_id'] for r in CONFIG['confirmed_duplicate_source_removals']}
        retained = [s for s in self.base['segments'] if s['id'] not in removed]
        self.assertEqual(self.drawing['segments'][:len(retained)], retained)
        self.assertEqual(self.drawing['bounds'], self.base['bounds'])
        self.assertEqual(self.drawing['source'], self.base['source'])
        self.assertEqual(len({s['id'] for s in self.drawing['segments']}), 671)
        self.assertEqual(self.drawing['source']['sha256'], '623df0e24fbb7348b96767b75f66af68c9e1943e1c881e04d0974c1deadd1a33')

    def test_whole_drawing_uses_one_translation_and_records_version_differences(self):
        audit = self.drawing['source_supplement']
        self.assertEqual(audit['registration']['verified_pair_count'], 496)
        self.assertEqual(audit['registration']['circle_pair_count'], 30)
        self.assertEqual(audit['registration']['translation_xy'], [-153.90706545276612, 4174.470053604143])
        self.assertLessEqual(audit['registration']['max_pair_residual_mm'], CONFIG['geometry_tolerance'])
        self.assertTrue(audit['registration']['global_bounds_match'])
        self.assertNotIn('local_registrations', self.manifest)
        self.assertAlmostEqual(audit['known_drawing_differences']['outline_max_sample_distance_mm'], 1.5)
        self.assertEqual(audit['known_drawing_differences']['baseline_unmatched_primitive_count'], 172)

    def test_both_requested_groups_are_exact_translations_without_text_or_duplicates(self):
        reference = load_drawing(ROOT / 'sources/P004/P004_01.dxf', BASE_CONFIG)
        originals = {s['id']: s for s in reference['segments']}
        audit = self.drawing['source_supplement']
        self.assertEqual(len(audit['added_segments']), 18)
        self.assertEqual(len(audit['omitted_duplicate_sources']), 16)
        self.assertEqual(self.drawing['annotations'], [])
        self.assertEqual(self.drawing['issues'], [])
        dx, dy = audit['registration']['translation_xy']
        for s in audit['added_segments']:
            original = originals[s['provenance']['original_source_id']]
            self.assertEqual(s['provenance']['translation_xy'], [dx, dy])
            for p in ('start', 'end'):
                self.assertEqual(s[p+'_x'], original[p+'_x'] + dx)
                self.assertEqual(s[p+'_y'], original[p+'_y'] + dy)
        bends = [s for s in audit['added_segments'] if s['source_handle'] in ('E9', 'EA')]
        self.assertEqual(len(bends), 2)
        self.assertTrue(all(s['linetype'] == 'DASHED' for s in bends))
        self.assertAlmostEqual(abs(bends[0]['start_y'] - bends[1]['start_y']), .22)
        for s in bends:
            self.assertAlmostEqual(min(s['start_x'], s['end_x']), 3556.34491099082)
            self.assertAlmostEqual(max(s['start_x'], s['end_x']), 3576.64491099082)

    def test_old_base_fingerprint_and_local_mirror_are_rejected(self):
        with self.assertRaisesRegex(ValueError, '指纹'):
            self.changed(lambda m: m.update(baseline_sha256='ca46fe2718afd6181a775e2a47961b1e6babfef49c6ff715dd4f9bae8198a239'))
        with self.assertRaisesRegex(ValueError, '局部'):
            self.changed(lambda m: m.update(local_registrations=[{'type': 'REFLECTION_X_TRANSLATION'}]))
        with self.assertRaisesRegex(ValueError, '整图统一平移'):
            self.changed(lambda m: m['registration'].update(type='REFLECTION_X_TRANSLATION'))

    def test_wrong_global_translation_or_inconsistent_pair_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '包围范围'):
            self.changed(lambda m: m['registration']['translation_xy'].__setitem__(0, -702.9847898912888))
        with self.assertRaisesRegex(ValueError, '对应'):
            self.changed(lambda m: m['registration'].update(verified_pairs=[]))
        with self.assertRaisesRegex(ValueError, '残差'):
            self.changed(lambda m: m['registration']['verified_pairs'][0].update(reference_id=m['registration']['verified_pairs'][10]['reference_id']))

    def test_duplicate_or_text_selection_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '重合'):
            self.changed(lambda m: m['selected_source_ids'].append(m['omitted_duplicate_sources'][0]['omitted_id']))
        with self.assertRaisesRegex(ValueError, '不能包含文字'):
            self.changed(lambda m: m['selected_source_ids'].append('15D'))

    def test_sources_are_selectable_and_exported_audit_is_complete(self):
        project = new_project(self.drawing, 'P004')
        self.assertEqual(project['operations'], [])
        project['operations'] = [{'uid': 'qa_only', 'operation_id': 'O_QA', 'name': 'test only',
                                  'operation_type': 'MARKING', 'surface_side': 'NOT_APPLICABLE',
                                  'geometry_mode': 'source', 'source_segment_ids': self.drawing['source_supplement']['added_source_ids']}]
        report = validate_project(project, self.drawing)
        self.assertFalse(any(e['code'] == 'SOURCE_REFERENCE' for e in report['errors']))
        self.assertEqual(report['statistics']['bound'], 18)
        with tempfile.TemporaryDirectory(dir=ROOT / 'outputs/test_tmp') as folder:
            destination = Path(folder) / 'outputs'
            export_workbook(project, report, destination, module_root=Path(folder))
            manifest = json.loads(next(destination.rglob('source_manifest.json')).read_text(encoding='utf-8'))
            self.assertEqual(manifest['drawing_source_supplement'], self.drawing['source_supplement'])

    def test_offline_bend_lines_are_dashed_and_have_stable_ids(self):
        page = (ROOT / 'outputs/P004/P004_drawing.html').read_text(encoding='utf-8')
        for s in self.drawing['source_supplement']['added_segments'][-2:]:
            self.assertIn('stroke-dasharray="6 3"', page.split('data-id="'+s['id']+'"', 1)[1].split('>', 1)[0])
        reloaded = load_drawing(ROOT / 'sources/P004/P004.dxf', CONFIG)
        self.assertEqual(self.drawing['source_supplement'], reloaded['source_supplement'])

    def test_dependency_hashes_record_both_reference_and_manifest(self):
        dependencies = self.drawing['source_dependencies']
        self.assertEqual({s['file_name'] for s in dependencies}, {'sources/P004/P004_01.dxf', 'configs/P004_geometry_supplement.json'})
        for item in dependencies:
            self.assertEqual(hashlib.sha256((ROOT / item['file_name']).read_bytes()).hexdigest(), item['sha256'])
