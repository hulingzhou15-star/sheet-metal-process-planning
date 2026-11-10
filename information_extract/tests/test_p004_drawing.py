from collections import Counter
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import ezdxf

from extract_app.drawing import load_drawing
from extract_app.model import new_project

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'configs/P004.json').read_text(encoding='utf-8'))
SOURCE_CONFIG = {k: v for k, v in CONFIG.items() if k not in {'confirmed_geometry_supplement', 'confirmed_duplicate_source_removals'}}


class P004DrawingTests(unittest.TestCase):
    def test_real_p004_geometry_is_complete_and_source_unmodified(self):
        source = ROOT / CONFIG['source_file']
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        drawing = load_drawing(source, SOURCE_CONFIG)
        self.assertEqual(Counter(s['segment_type'] for s in drawing['segments']),
                         {'LINE': 319, 'ARC': 297, 'CIRCLE': 52})
        self.assertEqual(len({s['id'] for s in drawing['segments']}), 668)
        self.assertEqual(drawing['annotations'], [])
        self.assertEqual(drawing['issues'], [])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

    def test_confirmed_mm_is_provenance_without_coordinate_scaling(self):
        drawing = load_drawing(ROOT / CONFIG['source_file'], CONFIG)
        raw_config = {k: v for k, v in CONFIG.items() if k != 'confirmed_source_units'}
        raw = load_drawing(ROOT / CONFIG['source_file'], raw_config)
        self.assertEqual(drawing['source']['insunits'], 0)
        self.assertEqual(drawing['source']['units'], 'mm')
        self.assertEqual(drawing['source']['unit_basis'], 'USER_CONFIRMED')
        self.assertEqual(drawing['source']['declared_units'], '源单位（未指定）')
        self.assertEqual(raw['source']['unit_basis'], 'UNDECLARED')
        self.assertEqual(raw['source']['units'], '源单位（未指定）')
        self.assertEqual(drawing['segments'], raw['segments'])
        self.assertEqual(drawing['bounds'], raw['bounds'])

    def test_confirmation_cannot_override_a_conflicting_declared_unit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'inch.dxf'
            doc = ezdxf.new()
            doc.units = 1
            doc.modelspace().add_circle((2, 3), 1)
            doc.saveas(path)
            with self.assertRaisesRegex(ValueError, '单位冲突'):
                load_drawing(path, SOURCE_CONFIG)

    def test_independent_empty_project_has_no_previous_part_data(self):
        self.assertEqual(CONFIG['part_id'], 'P004')
        self.assertEqual(CONFIG['port'], 8769)
        self.assertNotIn('confirmed_source_cleanup', CONFIG)
        self.assertNotIn('confirmed_duplicate_line_removals', CONFIG)
        drawing = load_drawing(ROOT / CONFIG['source_file'], CONFIG)
        self.assertEqual(len(drawing['removed_sources']), 15)
        self.assertTrue(all(s['reason'] == 'USER_CONFIRMED_REDUNDANT_GEOMETRY' for s in drawing['removed_sources']))
        self.assertEqual(drawing['background_source_ids'], [])
        project = new_project(drawing, 'P004')
        self.assertEqual(project['part_id'], 'P004')
        self.assertEqual(project['operations'], [])
        self.assertEqual(project['station_rows'], [])
        self.assertEqual(project['fixed_rows'], [])
        self.assertFalse(project['inventory_confirmed'])

    def test_offline_preview_contains_complete_geometry_and_p004_entry(self):
        page = (ROOT / 'outputs/P004/P004_drawing.html').read_text(encoding='utf-8')
        self.assertEqual(page.count('<path data-id='), 671)
        self.assertEqual(page.count('<text transform='), 0)
        self.assertIn('http://127.0.0.1:8769/', page)
        self.assertIn('start_P004.ps1', page)
        self.assertNotIn('PG_O008', page)
