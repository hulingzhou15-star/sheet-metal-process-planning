from collections import Counter
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import ezdxf

from extract_app.drawing import load_drawing
from extract_app.model import new_project, ProjectStore

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'configs/P003.json').read_text(encoding='utf-8'))
SOURCE_CONFIG = {k: v for k, v in CONFIG.items() if k != 'confirmed_source_cleanup'}


class P003DrawingTests(unittest.TestCase):
    def test_real_geometry_mtext_and_dimension_are_separate(self):
        path = ROOT / CONFIG['source_file']
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        drawing = load_drawing(path, SOURCE_CONFIG)
        self.assertEqual(Counter(s['segment_type'] for s in drawing['segments']),
                         {'LINE': 135, 'ARC': 88, 'CIRCLE': 128})
        self.assertEqual(len(drawing['annotations']), 59)
        self.assertTrue(all(a['source_entity_type'] == 'MTEXT' for a in drawing['annotations']))
        self.assertTrue(all(a['baseline'] == 'text-before-edge' for a in drawing['annotations']))
        self.assertEqual([i['entity_type'] for i in drawing['issues']], ['DIMENSION'])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        self.assertEqual(len({s['id'] for s in drawing['segments']}), 351)

    def test_independent_config_does_not_apply_p002_cleanup_or_answers(self):
        self.assertEqual(CONFIG['part_id'], 'P003')
        self.assertEqual(CONFIG['port'], 8768)
        self.assertNotIn('confirmed_duplicate_line_removals', CONFIG)
        self.assertEqual(CONFIG.get('confirmed_source_cleanup'), 'configs/P003_source_cleanup.json')
        drawing = load_drawing(ROOT / CONFIG['source_file'], SOURCE_CONFIG)
        self.assertEqual(drawing['removed_sources'], [])
        self.assertEqual(drawing['background_source_ids'], [])
        self.assertIsNone(drawing['source_cleanup'])
        project = new_project(drawing, 'P003')
        self.assertEqual(project['operations'], [])
        self.assertEqual(project['station_rows'], [])
        self.assertEqual(project['fixed_rows'], [])
        self.assertFalse(project['inventory_confirmed'])

    def test_simple_top_left_mtext_is_display_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'text.dxf'
            doc = ezdxf.new()
            space = doc.modelspace()
            space.add_circle((0, 0), 1)
            space.add_mtext('翻孔', dxfattribs={'insert': (2, 3), 'char_height': 2, 'flow_direction': 1})
            doc.saveas(path)
            drawing = load_drawing(path, SOURCE_CONFIG)
            self.assertEqual(len(drawing['segments']), 1)
            self.assertEqual(drawing['annotations'][0]['text'], '翻孔')
            self.assertEqual((drawing['annotations'][0]['x'], drawing['annotations'][0]['y']), (2, 3))
            self.assertEqual(drawing['issues'], [])

    def test_unsupported_mtext_layout_is_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'text.dxf'
            doc = ezdxf.new()
            space = doc.modelspace()
            space.add_circle((0, 0), 1)
            space.add_mtext('line1\\Pline2', dxfattribs={'flow_direction': 1})
            space.add_mtext('wrapped', dxfattribs={'width': 5, 'flow_direction': 1})
            space.add_mtext('centered', dxfattribs={'attachment_point': 5, 'flow_direction': 1})
            doc.saveas(path)
            drawing = load_drawing(path, SOURCE_CONFIG)
            self.assertEqual(drawing['annotations'], [])
            self.assertEqual([i['entity_type'] for i in drawing['issues']], ['MTEXT'] * 3)

    def test_offline_preview_preserves_geometry_and_annotation_content(self):
        page = (ROOT / 'outputs/P003/P003_drawing.html').read_text(encoding='utf-8')
        self.assertEqual(page.count('<path data-id='), 342)
        self.assertEqual(page.count('<text transform='), 0)
        self.assertNotIn('毛面翻孔M3', page)
        self.assertIn('http://127.0.0.1:8768/', page)
        self.assertNotIn('PG_O008', page)

    def test_cleanup_preserves_all_action_sources_and_closed_outline(self):
        drawing = load_drawing(ROOT / CONFIG['source_file'], CONFIG)
        manifest = json.loads((ROOT / CONFIG['confirmed_source_cleanup']).read_text(encoding='utf-8'))
        active_ids = {s['id'] for s in drawing['segments']}
        self.assertEqual(active_ids, set(manifest['operation_source_ids']) | set(manifest['outline_source_ids']))
        self.assertEqual(len(drawing['segments']), 342)
        self.assertEqual(len(drawing['background_source_ids']), 104)
        self.assertEqual(len(drawing['removed_sources']), 9)
        self.assertEqual(len(drawing['removed_annotations']), 59)
        self.assertEqual(len(drawing['removed_annotation_issues']), 1)
        self.assertEqual(drawing['annotations'], [])
        self.assertEqual(drawing['issues'], [])

    def test_two_parts_save_independently_and_reject_cross_part_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            drawings = [{'source': {'sha256': digit * 64}, 'segments': [], 'issues': [], 'config': CONFIG}
                        for digit in ('2', '3')]
            p2, p3 = [ProjectStore(folder, drawing, part) for drawing, part in zip(drawings, ('P002', 'P003'))]
            second, third = p2.load(), p3.load()
            second['expected_operation_count'] = 20
            third['expected_operation_count'] = 30
            p2.save(second, 0)
            p3.save(third, 0)
            self.assertEqual(p2.load()['expected_operation_count'], 20)
            self.assertEqual(p3.load()['expected_operation_count'], 30)
            self.assertNotEqual(p2.path, p3.path)
            before = (p2.path.read_bytes(), p3.path.read_bytes())
            with self.assertRaisesRegex(ValueError, '指纹'):
                p3.save(p2.load(), 1)
            wrong_part = {**p3.load(), 'part_id': 'P002'}
            with self.assertRaisesRegex(ValueError, '零件编号'):
                p3.save(wrong_part, 1)
            self.assertEqual((p2.path.read_bytes(), p3.path.read_bytes()), before)
