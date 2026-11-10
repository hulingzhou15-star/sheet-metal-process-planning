from copy import deepcopy
import json
from pathlib import Path
import unittest

from extract_app.drawing import load_drawing
from extract_app.model import validate_project
from extract_app.source_deduplication import wholly_covered, remove_confirmed_redundant_sources

ROOT = Path(__file__).resolve().parents[1]


def line(sid, start, end, y=0):
    return {'id': sid, 'segment_type': 'LINE', 'start_x': start, 'start_y': y, 'end_x': end, 'end_y': y}


def arc(sid, start, end, sweep):
    return {'id': sid, 'segment_type': 'ARC', 'start_x': start[0], 'start_y': start[1],
            'end_x': end[0], 'end_y': end[1], 'center_x': 0, 'center_y': 0, 'radius': 1, 'sweep_angle': sweep}


class SourceDeduplicationTests(unittest.TestCase):
    def test_reversed_duplicate_line_keeps_original_id_and_coordinates(self):
        original = line('keep', 0, 10)
        rows, audit = remove_confirmed_redundant_sources([original, line('duplicate', 10, 0)],
                    [{'keep_id': 'keep', 'remove_id': 'duplicate', 'relation': 'COINCIDENT'}], 1e-6)
        self.assertEqual(rows, [original])
        self.assertEqual(audit[0]['retained_source_id'], 'keep')

    def test_close_parallel_bend_boundaries_are_not_duplicates(self):
        for gap in (.15, .22, .3):
            with self.subTest(gap=gap):
                self.assertFalse(wholly_covered(line('b', 0, 10, gap), line('a', 0, 10), 1e-6))
                with self.assertRaisesRegex(ValueError, '整段覆盖'):
                    remove_confirmed_redundant_sources([line('a', 0, 10), line('b', 0, 10, gap)],
                                [{'keep_id': 'a', 'remove_id': 'b', 'relation': 'COINCIDENT'}], 1e-6)

    def test_contained_line_can_be_removed_but_partial_overlap_is_rejected(self):
        self.assertTrue(wholly_covered(line('short', 2, 3), line('long', 0, 10), 1e-6))
        self.assertFalse(wholly_covered(line('partial', 9, 12), line('long', 0, 10), 1e-6))
        with self.assertRaisesRegex(ValueError, '范围不一致'):
            remove_confirmed_redundant_sources([line('long', 0, 10), line('short', 2, 3)],
                         [{'keep_id': 'long', 'remove_id': 'short', 'relation': 'COINCIDENT'}], 1e-6)

    def test_arc_coverage_checks_swept_part_and_reversed_direction(self):
        top = arc('top', (1, 0), (-1, 0), 180)
        quarter = arc('quarter', (1, 0), (0, 1), 90)
        reversed_quarter = arc('reversed', (0, 1), (1, 0), -90)
        bottom = arc('bottom', (1, 0), (0, -1), -90)
        self.assertTrue(wholly_covered(quarter, top, 1e-6))
        self.assertTrue(wholly_covered(reversed_quarter, top, 1e-6))
        self.assertFalse(wholly_covered(bottom, top, 1e-6))

    def test_deletion_cannot_remove_a_source_retained_by_another_rule(self):
        rows = [line(sid, 0, 10) for sid in ('a', 'b', 'c')]
        rules = [{'keep_id': 'a', 'remove_id': 'b', 'relation': 'COINCIDENT'},
                 {'keep_id': 'c', 'remove_id': 'a', 'relation': 'COINCIDENT'}]
        with self.assertRaisesRegex(ValueError, '要求保留'):
            remove_confirmed_redundant_sources(rows, rules, 1e-6)

    def test_real_p004_has_no_remaining_completely_coincident_geometry(self):
        config = json.loads((ROOT / 'configs/P004.json').read_text(encoding='utf-8'))
        drawing = load_drawing(ROOT / 'sources/P004/P004.dxf', config)
        self.assertEqual(len(drawing['segments']), 671)
        self.assertEqual(len(drawing['removed_sources']), 15)
        for i, a in enumerate(drawing['segments']):
            for b in drawing['segments'][i+1:]:
                if a['segment_type'] == b['segment_type']:
                    self.assertFalse(wholly_covered(a, b, config['geometry_tolerance']) and wholly_covered(b, a, config['geometry_tolerance']), (a['id'], b['id']))

    def test_contour_connection_arcs_are_preserved_and_unsafe_removal_rejected(self):
        from extract_app.workflow import _paths
        config = json.loads((ROOT / 'configs/P004.json').read_text(encoding='utf-8'))
        drawing = load_drawing(ROOT / 'sources/P004/P004.dxf', config)
        for group in ('9E', 'B6', 'A1', '198'):
            self.assertEqual(len(_paths([s for s in drawing['segments'] if s['source_group'] == group], config['geometry_tolerance'])), 1)
        original_config = {k: v for k, v in config.items() if k != 'confirmed_duplicate_source_removals'}
        original = load_drawing(ROOT / 'sources/P004/P004.dxf', original_config)
        candidate_ids = ('S_3e0c18472acd8b842d6df342', 'S_3bd58777bbd3e7f9f3d46728', 'S_34927413353a2f74ae7428a0')
        keeper_ids = ('S_7765cfbf66ad529d207ae590', 'S_de821c207e50081063cb73d6', 'S_5d955e590085ed705eb46cf4')
        rules = [{'keep_id': keep, 'remove_id': remove, 'relation': 'FULLY_COVERED'} for keep, remove in zip(keeper_ids, candidate_ids)]
        self.assertTrue(set(candidate_ids) <= {s['id'] for s in drawing['segments']})
        with self.assertRaisesRegex(ValueError, '闭合轮廓'):
            remove_confirmed_redundant_sources(original['segments'], rules, config['geometry_tolerance'])

    def test_real_p004_bindings_and_both_added_features_survive(self):
        config = json.loads((ROOT / 'configs/P004.json').read_text(encoding='utf-8'))
        drawing = load_drawing(ROOT / 'sources/P004/P004.dxf', config)
        project = json.loads((ROOT / 'projects/P004/project.json').read_text(encoding='utf-8'))
        report = validate_project(project, drawing)
        self.assertFalse(any(e['code'] == 'SOURCE_REFERENCE' for e in report['errors']))
        ids = {s['id'] for s in drawing['segments']}
        self.assertTrue(set(drawing['source_supplement']['added_source_ids']) <= ids)
        self.assertEqual(len(drawing['source_supplement']['added_source_ids']), 18)
