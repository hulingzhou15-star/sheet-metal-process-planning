import json,math,hashlib,unittest
from pathlib import Path
import numpy as np
from information_extract.three_d.hole_offset_audit import compare_translation,offset_family,leave_one_out
from information_extract.three_d.local_collision import transformed
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

OUT=Path(__file__).resolve().parents[1]/'outputs/hole_offset_90_audit_v01/P001'


class OffsetAuditTests(unittest.TestCase):
    def cylinder(self,r=2.4):return BRepPrimAPI_MakeCylinder(r,.8).Shape()

    def test_true_translation_is_detected_without_scaling(self):
        a=self.cylinder();m=np.eye(4);m[0,3]=.4
        r=compare_translation(a,transformed(a,m),[0,0,1],np.eye(4))
        self.assertTrue(r['same_shape_after_translation'])
        self.assertAlmostEqual(r['in_plane_delta_mm'],.4,places=7)
        self.assertEqual(offset_family(r['in_plane_delta_mm'],.8),'HALF_THICKNESS')

    def test_radius_difference_is_not_translation(self):
        r=compare_translation(self.cylinder(.7),self.cylinder(1.2),[0,0,1],np.eye(4))
        self.assertFalse(r['same_shape_after_translation'])
        self.assertAlmostEqual(r['in_plane_delta_mm'],0.,places=7)

    def test_skin_normal_difference_is_not_in_plane_offset(self):
        a=self.cylinder();m=np.eye(4);m[2,3]=.8
        r=compare_translation(a,transformed(a,m),[0,0,1],np.eye(4))
        self.assertAlmostEqual(r['normal_delta_mm'],.8)
        self.assertAlmostEqual(r['in_plane_delta_mm'],0)
        self.assertFalse(r['same_shape_after_translation'])

    def test_components_do_not_imply_norm_is_point4_or_point8(self):
        self.assertEqual(offset_family(math.sqrt(.4**2+.4**2),.8),'OTHER')
        rows=[{'flat_delta_xyz_mm':[0,.4,0]},{'flat_delta_xyz_mm':[0,.4,0]},{'flat_delta_xyz_mm':[0,.8,0]}]
        self.assertGreater(leave_one_out(rows)['max_prediction_error_mm'],.1)

    def test_actual_coverage_and_exceptions_are_retained(self):
        a=json.loads((OUT/'audit.json').read_text(encoding='utf-8'))
        self.assertEqual(len(a['hole_rows']),100);self.assertEqual(len(a['joint_rows']),59)
        self.assertEqual(a['summary']['ninety_degree_bend_count'],53)
        pure=[r for r in a['hole_rows'] if r['operation_type']!='PRE_PUNCH' and r.get('ninety_degree_path')]
        self.assertEqual(len(pure),26)
        good=[r for r in pure if r['status']=='UNIQUE_TRANSLATION_CONGRUENT_PROPOSAL']
        self.assertEqual(len(good),18)
        self.assertTrue(all(r['same_shape_after_translation'] for r in good))
        self.assertFalse(a['summary']['all_holes_proven_04_or_08'])
        self.assertEqual(len({r['wire_id'] for r in good}),18)

    def test_geometry_driven_strip_model_predicts_held_out_holes(self):
        p=json.loads((OUT/'strip_model_probe.json').read_text(encoding='utf-8'))
        self.assertEqual(len(p['strip_rows']),53)
        self.assertTrue(all(abs(r['developed_strip_width_mm']-.4)<1e-6 for r in p['strip_rows']))
        fragmented=next(r for r in p['strip_rows'] if r['operation_id']=='O025')
        self.assertEqual(fragmented['evidence_coverage'],'EXACT_FRAGMENTED_CAD_INTERVALS')
        self.assertTrue(fragmented['lower']['exact_physical_cylinder_intervals_match'])
        self.assertTrue(fragmented['upper']['exact_physical_cylinder_intervals_match'])
        self.assertEqual(p['summary']['holes_used_to_fit'],0)
        vals=[v for v in p['independent_hole_validation'] if v['pure_90_path']]
        self.assertEqual(len(vals),18)
        self.assertTrue(all(v['new_error_mm']<1e-6 and not v['used_to_fit_model'] for v in vals))
        self.assertFalse(p['summary']['production_geometry_changed'])
        for matrix in p['flat_transforms'].values():
            r=np.array(matrix)[:3,:3];np.testing.assert_allclose(r@r.T,np.eye(3),atol=1e-8)
            self.assertAlmostEqual(np.linalg.det(r),1.,places=8)

    def test_frozen_inputs_are_unchanged(self):
        p=json.loads((OUT/'strip_model_probe.json').read_text(encoding='utf-8'))
        for file,h in p['source_sha256'].items():self.assertEqual(hashlib.sha256(Path(file).read_bytes()).hexdigest(),h,file)

    def test_prepunch_center_diagnostic_preserves_shape_difference(self):
        p=json.loads((OUT/'strip_model_probe.json').read_text(encoding='utf-8'))
        rows=[r for r in p['noncongruent_centroid_comparisons'] if r['operation_type']=='PRE_PUNCH' and r['pure_90_path']]
        self.assertEqual(len(rows),29)
        self.assertTrue(all(r['target_is_circle'] and r['same_panel_candidate_count']==1 for r in rows))
        self.assertTrue(all(np.linalg.norm(r['new_centroid_delta_xyz_mm'])<1e-6 for r in rows))
        corner=next(r for r in rows if r['operation_id']=='O064')
        self.assertAlmostEqual(np.linalg.norm(corner['old_centroid_delta_xyz_mm']),math.sqrt(.32),places=7)
        self.assertEqual(corner['status'],'SHAPE_OR_FORMING_DIFFERENCE_NOT_PURE_TRANSLATION')


if __name__=='__main__':unittest.main()
