import json
import unittest
from pathlib import Path
import numpy as np

MODULE = Path(__file__).resolve().parents[1]


class StageGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=json.loads((MODULE/'outputs/stage_review_v01/P001/stage_data.json').read_text(encoding='utf-8'))
        cls.asset=json.loads((MODULE/'outputs/three_d_state_v01/P001/asset.json').read_text(encoding='utf-8'))

    def test_support_removes_internal_voids(self):
        regions={r['region_id']:r for r in self.data['skeleton']['regions']}
        increased=0
        for panel in self.data['panels']:
            tris=np.array(panel['triangles'])
            area=float((np.linalg.norm(np.cross(tris[:,1]-tris[:,0],tris[:,2]-tris[:,0]),axis=1)/2).sum())
            source=self.asset['faces'][regions[panel['region_id']]['front_face_id']-1]['area_mm2']
            # Tessellation is approximate; material support must not lose a hole-sized region.
            self.assertGreaterEqual(area+0.15,source)
            increased+=area-source>1
        self.assertGreater(increased,20)

    def test_missing_and_ambiguous_not_forced(self):
        for op in self.data['operations']:
            if op['mapping_status'] in ('MISSING_2D','UNRESOLVED'):
                self.assertIsNone(op['region_id']);self.assertEqual(op['formed_paths'],[])
            if op['mapping_status']=='CONTAINMENT_PROPOSAL':
                self.assertEqual(len(op['region_candidates']),1)
                self.assertLessEqual(op['max_outside_distance_mm'],self.data['config']['panel_membership_search_tolerance_mm'])

    def test_all_bend_axes_and_contours_finite(self):
        joints={j['joint_id'] for j in self.data['skeleton']['joints']}
        self.assertEqual(len(joints),59)
        for op in self.data['operations']:
            if op['operation_type']=='BEND':
                self.assertIn(op['joint_id'],joints)
            for path in op['formed_paths']:
                self.assertTrue(np.isfinite(path).all())

    def test_provenance_and_scope(self):
        import hashlib
        plans=(MODULE/self.data['config']['plans']).read_bytes()
        self.assertEqual(hashlib.sha256(plans).hexdigest(),self.data['plans_sha256'])
        self.assertEqual(self.data['asset_id'],self.asset['asset_id'])
        self.assertFalse(self.data['sheet5_used'])
        self.assertFalse(self.data['physical_simulation'])
        self.assertFalse(self.data['is_blank'])
        self.assertFalse(self.data['collision_checked'])

    def test_display_outline_coordinate_mapping(self):
        import sys
        sys.path.insert(0,str(MODULE.parent))
        from src.visualization.outline_adapter import load_display_outline
        cfg=json.loads((MODULE.parent/'configs/m3_visualization.yaml').read_text(encoding='utf-8'))
        original,audit=load_display_outline(cfg,MODULE.parent,'P001')
        out=self.data['display_outline']
        self.assertTrue(out['display_only']);self.assertEqual(out['audit']['source_sha256'],audit['source_sha256'])
        self.assertEqual(len(out['paths']),len(original['loops']))
        for loop,path in zip(original['loops'],out['paths']):
            p=loop['shapes'][0]['start']
            expected=np.array(out['registration_matrix'])@np.array([p[0],-p[1]])+out['registration_translation_mm']
            np.testing.assert_allclose(path[0],expected,atol=1e-9)
            self.assertEqual(path[0],path[-1])


if __name__=='__main__':unittest.main()
