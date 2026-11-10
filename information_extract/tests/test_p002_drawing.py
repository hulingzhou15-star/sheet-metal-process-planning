from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import ezdxf

from extract_app.drawing import load_drawing, remove_confirmed_duplicate_lines
from extract_app.model import new_project

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'configs/P002.json').read_text('utf-8'))
SOURCE_CONFIG = deepcopy(CONFIG)
SOURCE_CONFIG.pop('confirmed_duplicate_line_removals', None)
SOURCE_CONFIG.pop('confirmed_source_cleanup', None)


class P002DrawingTests(unittest.TestCase):
    def test_real_p002_cleanup_preserves_sources_and_original_drawing(self):
        source = ROOT / 'sources/P002/P002_1.dxf'
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        drawing = load_drawing(source, CONFIG)
        self.assertEqual(len(drawing['segments']), 603)
        self.assertEqual(len(drawing['annotations']), 0)
        self.assertEqual(drawing['issues'], [])
        self.assertEqual(len({s['id'] for s in drawing['segments']}), 603)
        ids = {s['id'] for s in drawing['segments']}
        self.assertIn('S_a051af663dd2e4edfd536e34', ids)
        self.assertNotIn('S_d5b5b6472c2e3271d0142cea', ids)
        self.assertIn('S_7f9f8f0dd4dc213fbb253194', ids)
        self.assertEqual(len(drawing['removed_sources']), 75)
        self.assertEqual(len(drawing['background_source_ids']), 142)
        self.assertEqual(len(drawing['removed_annotations']), 58)
        manifest = json.loads((ROOT / CONFIG['confirmed_source_cleanup']).read_text('utf-8'))
        self.assertEqual(ids, set(manifest['operation_source_ids']) | set(manifest['outline_source_ids']))
        original = load_drawing(source, SOURCE_CONFIG)
        self.assertEqual(len(original['segments']), 678)
        self.assertEqual(len(original['annotations']), 58)
        self.assertEqual(len({s['instance_path'] for s in original['segments'] if s['instance_path']}), 3)
        originals = {s['id']: s for s in original['segments']}
        self.assertTrue(all(s == originals[s['id']] for s in drawing['segments']))
        self.assertEqual(new_project(drawing, 'P002')['coordinate_system'], 'drawing WCS XY')
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

    def test_world_coordinates_include_unblocked_and_transformed_block(self):
        with tempfile.TemporaryDirectory() as directory:
            doc = ezdxf.new()
            doc.modelspace().add_circle((10, 20), 2)
            block = doc.blocks.new('local')
            block.add_circle((1, 0), 1)
            doc.modelspace().add_blockref('local', (100, 200), dxfattribs={'rotation': 90})
            source = Path(directory) / 'world.dxf'
            doc.saveas(source)
            drawing = load_drawing(source, SOURCE_CONFIG)
            centers = {(round(s['center_x'], 8), round(s['center_y'], 8)) for s in drawing['segments']}
            self.assertEqual(centers, {(10, 20), (100, 201)})

    def test_missing_material_block_does_not_silently_use_world_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            doc = ezdxf.new()
            doc.modelspace().add_circle((10, 20), 2)
            source = Path(directory) / 'world.dxf'
            doc.saveas(source)
            config = {**SOURCE_CONFIG, 'coordinate_mode': 'block_local'}
            with self.assertRaisesRegex(ValueError, '材料坐标实例'):
                load_drawing(source, config)

    def test_nonplanar_world_drawing_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            doc = ezdxf.new()
            doc.modelspace().add_circle((10, 20, 3), 2)
            source = Path(directory) / 'elevated.dxf'
            doc.saveas(source)
            with self.assertRaisesRegex(ValueError, '非零 Z'):
                load_drawing(source, SOURCE_CONFIG)

    def test_text_is_display_only_and_unsupported_placement_keeps_issue(self):
        with tempfile.TemporaryDirectory() as directory:
            doc = ezdxf.new()
            space = doc.modelspace()
            space.add_circle((0, 0), 1)
            space.add_text('shown', dxfattribs={'insert': (2, 3), 'rotation': 25})
            space.add_text('mirrored', dxfattribs={'insert': (2, 4), 'text_generation_flag': 2})
            source = Path(directory) / 'text.dxf'
            doc.saveas(source)
            drawing = load_drawing(source, SOURCE_CONFIG)
            self.assertEqual(len(drawing['segments']), 1)
            self.assertEqual([a['text'] for a in drawing['annotations']], ['shown'])
            self.assertEqual(drawing['annotations'][0]['rotation'], 25)
            self.assertEqual(len(drawing['issues']), 1)
            self.assertEqual(drawing['issues'][0]['entity_type'], 'TEXT')

    def test_offline_html_contains_exact_curve_ids_and_labels(self):
        page = (ROOT / 'outputs/P002/P002_drawing.html').read_text('utf-8')
        self.assertEqual(page.count('<path data-id='), 603)
        self.assertNotIn('data-id="S_d5b5b6472c2e3271d0142cea"', page)
        self.assertEqual(page.count('<text transform='), 0)
        self.assertNotIn('光面拉包', page)
        self.assertIn('http://127.0.0.1:8767/', page)
        self.assertNotIn('__BOXJSON__', page)
        self.assertNotIn('<script src=', page)


class ConfirmedDuplicateRemovalTests(unittest.TestCase):
    def setUp(self):
        self.segments = [
            {'id': 'keep', 'segment_type': 'LINE', 'start_x': 0., 'start_y': 0., 'end_x': 0., 'end_y': 10.},
            {'id': 'remove', 'segment_type': 'LINE', 'start_x': 0., 'start_y': 10., 'end_x': 0., 'end_y': 0.},
            {'id': 'parallel', 'segment_type': 'LINE', 'start_x': .4, 'start_y': 0., 'end_x': .4, 'end_y': 10.}
        ]
        self.rules = [{'keep_id': 'keep', 'remove_id': 'remove'}]

    def test_reversed_coincident_line_removed_and_other_ids_retained(self):
        before = deepcopy(self.segments)
        active, audit = remove_confirmed_duplicate_lines(self.segments, self.rules, 1e-6)
        self.assertEqual([s['id'] for s in active], ['keep', 'parallel'])
        self.assertEqual(audit[0]['removed_source']['id'], 'remove')
        self.assertEqual(self.segments, before)
        self.assertIs(active[0], self.segments[0])

    def test_no_rule_does_not_automatically_remove_coincident_lines(self):
        active, audit = remove_confirmed_duplicate_lines(self.segments, [], 1e-6)
        self.assertEqual(active, self.segments)
        self.assertEqual(audit, [])

    def test_noncoincident_parallel_line_cannot_be_removed(self):
        with self.assertRaisesRegex(ValueError, '不重合'):
            remove_confirmed_duplicate_lines(self.segments, [{'keep_id': 'keep', 'remove_id': 'parallel'}], 1e-6)

    def test_missing_source_id_rejected(self):
        with self.assertRaisesRegex(ValueError, 'ID 无效'):
            remove_confirmed_duplicate_lines(self.segments, [{'keep_id': 'keep', 'remove_id': 'missing'}], 1e-6)

    def test_retained_source_cannot_be_deleted_by_another_rule(self):
        third = {**self.segments[0], 'id': 'third'}
        with self.assertRaisesRegex(ValueError, '保留的来源'):
            remove_confirmed_duplicate_lines(self.segments + [third], self.rules + [{'keep_id': 'third', 'remove_id': 'keep'}], 1e-6)


if __name__ == '__main__':
    unittest.main()
