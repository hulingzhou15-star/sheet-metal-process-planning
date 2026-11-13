import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from information_extract.extract_app.drawing import load_drawing
from information_extract.extract_app import source_correction as correction

MODULE = Path(__file__).resolve().parents[1]


class SourceCorrectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((MODULE/'configs/P004.json').read_text(encoding='utf-8'))
        cls.config.pop('confirmed_geometry_revision', None)
        cls.path = MODULE/cls.config['source_file']
        base_config = dict(cls.config)
        base_config.pop('confirmed_geometry_correction')
        cls.base = load_drawing(cls.path, base_config)

    def test_two_groups_and_unaffected_bindings(self):
        drawing = load_drawing(self.path, self.config)
        self.assertEqual(len(drawing['segments']), 669)
        audit = drawing['source_correction']
        old = {s['id']: s for s in self.base['segments']}
        removed = {s['id'] for s in audit['removed_segments']}
        new = {s['id']: s for s in drawing['segments']}
        for sid in old.keys() - removed:
            self.assertEqual(old[sid], new[sid])
        project = json.loads((MODULE/'projects/P004/project.json').read_text(encoding='utf-8'))
        for op in project['operations']:
            self.assertTrue(set(op['source_segment_ids']) <= new.keys())
        for group in audit['groups']:
            x, y = group['flange_center_xy_mm']
            curves = [s for s in audit['added_segments'] if s['provenance']['original_source_id'] in group['add_reference_ids']]
            self.assertEqual(sorted(s['segment_type'] for s in curves), ['ARC','ARC','LINE','LINE'])
            self.assertTrue(all(s['bbox'][2] < x for s in curves))
            self.assertEqual(sum(s['segment_type']=='CIRCLE' and abs(s['center_x']-x)<.001 and abs(s['center_y']-y)<.001 for s in new.values()),3)

    def test_wrong_baseline_rejected(self):
        with self.assertRaisesRegex(ValueError, '指纹'):
            correction.apply_confirmed_correction(self.path, self.config, 'incorrect', self.base['segments'])

    def test_bound_removal_rejected(self):
        manifest = json.loads((MODULE/self.config['confirmed_geometry_correction']).read_text(encoding='utf-8'))
        project = json.loads((MODULE/'projects/P004/project.json').read_text(encoding='utf-8'))
        project['operations'][0]['source_segment_ids'].append(manifest['remove_source_ids'][0])
        original = correction._contained
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp)/'project.json'
            fixture.write_text(json.dumps(project), encoding='utf-8')
            with patch.object(correction, '_contained', side_effect=lambda root, rel: fixture if rel==manifest['project_file'] else original(root,rel)):
                with self.assertRaisesRegex(ValueError, '已绑定动作'):
                    correction.apply_confirmed_correction(self.path, self.config, self.base['source']['sha256'], self.base['segments'])


if __name__ == '__main__':
    unittest.main()
