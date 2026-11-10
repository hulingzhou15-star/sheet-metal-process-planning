from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from extract_app.model import coverage
from extract_app.source_cleanup import apply_source_cleanup, load_cleanup_manifest, apply_annotation_cleanup


def line(sid, a, b):
    return {'id': sid, 'segment_type': 'LINE', 'start_x': a[0], 'start_y': a[1],
            'end_x': b[0], 'end_y': b[1]}


class SourceCleanupTests(unittest.TestCase):
    def setUp(self):
        self.segments = [line('bottom', (0, 0), (10, 0)), line('right', (10, 0), (10, 10)),
                         line('top', (10, 10), (0, 10)), line('left', (0, 10), (0, 0)),
                         line('action', (2, 2), (3, 3)), line('unused', (4, 4), (5, 5))]
        self.manifest = {'operation_source_ids': ['action'],
                         'outline_source_ids': ['bottom', 'right', 'top', 'left'],
                         'removed_source_ids': ['unused']}

    def test_only_explicit_unused_sources_removed_and_inputs_unchanged(self):
        before = deepcopy(self.segments)
        active, audit, background = apply_source_cleanup(self.segments, self.manifest, 1e-6)
        self.assertEqual({s['id'] for s in active}, {'bottom', 'right', 'top', 'left', 'action'})
        self.assertEqual(audit[0]['removed_source']['id'], 'unused')
        self.assertEqual(set(background), set(self.manifest['outline_source_ids']))
        self.assertEqual(self.segments, before)
        self.assertIs(active[-1], self.segments[-2])

    def test_without_manifest_nothing_is_removed_or_classified(self):
        self.assertEqual(apply_source_cleanup(self.segments, None, 1e-6), (self.segments, [], []))

    def test_cannot_delete_a_protected_action_source(self):
        self.manifest['removed_source_ids'].append('action')
        with self.assertRaisesRegex(ValueError, '已保留'):
            apply_source_cleanup(self.segments, self.manifest, 1e-6)

    def test_manifest_must_cover_all_sources_exactly(self):
        self.manifest['removed_source_ids'] = []
        with self.assertRaisesRegex(ValueError, '完整对应'):
            apply_source_cleanup(self.segments, self.manifest, 1e-6)

    def test_incomplete_outer_outline_rejected(self):
        self.manifest['outline_source_ids'].remove('top')
        self.manifest['removed_source_ids'].append('top')
        with self.assertRaisesRegex(ValueError, '断口'):
            apply_source_cleanup(self.segments, self.manifest, 1e-6)

    def test_geometry_outside_outline_bounds_rejected(self):
        self.segments[-2] = line('action', (11, 11), (12, 12))
        with self.assertRaisesRegex(ValueError, '超出'):
            apply_source_cleanup(self.segments, self.manifest, 1e-6)

    def test_outer_outline_is_background_and_does_not_require_an_action(self):
        active, _, background = apply_source_cleanup(self.segments, self.manifest, 1e-6)
        project = {'operations': [{'uid': 'u', 'source_segment_ids': ['action']}], 'ignored_entities': {}}
        before = deepcopy(project)
        statuses, stats, _ = coverage(project, {'segments': active, 'config': {'geometry_tolerance': 1e-6},
                                               'background_source_ids': background}, {})
        self.assertEqual(stats['bound'], 1)
        self.assertEqual(stats['ignored'], 4)
        self.assertEqual(stats['unhandled'], 0)
        self.assertEqual(statuses['action'], 'bound')
        self.assertEqual(project, before)

    def test_selecting_an_outline_source_takes_priority_over_background(self):
        project = {'operations': [{'uid': 'u', 'source_segment_ids': ['left']}], 'ignored_entities': {}}
        statuses, _, _ = coverage(project, {'segments': self.segments, 'config': {'geometry_tolerance': 1e-6},
                                           'background_source_ids': ['left']}, {})
        self.assertEqual(statuses['left'], 'bound')

    def test_source_hash_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cleanup.json'
            path.write_text(json.dumps({'schema_version': 1, 'source_sha256': 'old'}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '指纹'):
                load_cleanup_manifest(Path(folder) / 'source.dxf', {'confirmed_source_cleanup': 'cleanup.json'}, 'new')

    def test_explicit_dimension_annotation_removed_but_geometry_issues_retained(self):
        dimension = {'code': 'UNSUPPORTED_ENTITY', 'entity_type': 'DIMENSION', 'source_handle': 'd', 'insert_path': ''}
        spline = {'code': 'UNSUPPORTED_ENTITY', 'entity_type': 'SPLINE', 'source_handle': 's', 'insert_path': ''}
        before = deepcopy([dimension, spline])
        manifest = {'removed_annotation_entities': [{'entity_type': 'DIMENSION', 'source_handle': 'd'}]}
        kept, audit = apply_annotation_cleanup([dimension, spline], manifest)
        self.assertEqual(kept, [spline])
        self.assertEqual(audit[0]['source_issue'], dimension)
        self.assertEqual([dimension, spline], before)

    def test_annotation_cleanup_cannot_suppress_unknown_geometric_entity(self):
        with self.assertRaisesRegex(ValueError, '尺寸标注'):
            apply_annotation_cleanup([], {'removed_annotation_entities': [{'entity_type': 'SPLINE', 'source_handle': 's'}]})

    def test_annotation_cleanup_rejects_missing_source(self):
        with self.assertRaisesRegex(ValueError, '不属于'):
            apply_annotation_cleanup([], {'removed_annotation_entities': [{'entity_type': 'DIMENSION', 'source_handle': 'missing'}]})


if __name__ == '__main__':
    unittest.main()
