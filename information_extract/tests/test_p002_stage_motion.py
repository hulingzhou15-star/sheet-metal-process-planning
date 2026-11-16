"""Stage correspondence and fail-closed local-carrier coverage."""
from copy import deepcopy
import unittest
import numpy as np
from information_extract.three_d.artifacts import ROOT,read
from information_extract.three_d.p002_motion_reference import compile_model,frame as old_frame,ReferenceChecker
from information_extract.three_d.p002_stage_motion import extend_model,frame
from information_extract.three_d.p002_stage_evidence import distances

M=ROOT/'information_extract';P=M/'outputs/p002_generation_v01';C=M/'outputs/p002_material_cells_v01'


class StageMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.evidence=read(M/'outputs/p002_stage_evidence_v01/stage_geometry.json')
        cls.original=compile_model(read(C/'cells.json'),read(P/'straight_branch_proposals.json'),read(P/'carried_region_proposals.json'),read(P/'facts.json'))
        cls.model=extend_model(cls.original,cls.evidence)
        cls.plans=read(M/'outputs/geometry_allocation_v1_generated/P002/plans.json')['plans']

    def test_arc_distance_uses_midpoint_to_choose_long_or_short_arc(self):
        arc=dict(kind='ARC',points=[[1,0],[0,1],[-1,0]])
        np.testing.assert_allclose(distances([[0,-1],[2,0]], [arc]),[0,1],atol=1e-12)
        self.assertGreater(distances([[.707,.707]],[arc])[0],.7)

    def test_line_distance_is_finite_not_infinite_supporting_line(self):
        np.testing.assert_allclose(distances([[-1,0],[.5,2]], [dict(kind='LINE',points=[[0,0],[1,0]])]),[1,2])

    def test_dxf_free_rim_and_independent_side_dimension_agree(self):
        c=self.evidence['o094_carrier']
        self.assertEqual([r['station'] for r in c['free_rim_validation']],['OP50','OP70','OP80'])
        for r in c['free_rim_validation']:
            self.assertEqual(r['sample_count'],170);self.assertEqual(r['within_0_001_mm'],170)
            self.assertLess(r['max_residual_mm'],1e-6)
        self.assertAlmostEqual(c['independent_dimension']['height_after_local_fold_mm'],5.2)
        self.assertLess(c['independent_dimension']['residual_mm'],1e-6)
        self.assertAlmostEqual(c['measured_x_compensation_mm'],.4)
        self.assertAlmostEqual(c['initial_world_translation_correction_mm'][1],.4)

    def test_bad_endpoint_evidence_fails_closed(self):
        e=deepcopy(self.evidence);e['o094_carrier']['free_rim_validation'][0]['max_residual_mm']=.1
        with self.assertRaisesRegex(ValueError,'endpoint'):extend_model(self.original,e)

    def test_o094_uses_each_candidates_station_and_allows_local_motion(self):
        for p in self.plans:
            sid=p['assignments']['O094'];s=frame(self.model,p,sid,'motion',.5)
            self.assertIn('C007',old_frame(self.original,p,sid,'motion',.5)['unknown_cells'])
            self.assertNotIn('C007',s['unknown_cells']);self.assertEqual(s['unknown_cells']['C004'],['O088'])
            self.assertEqual(s['additional_reference_support']['C007']['child_fraction'],.5)
            self.assertFalse(s['additional_reference_support']['C007']['continuous_deformation_verified'])
            pose=np.array(s['transforms']['C007']);np.testing.assert_allclose(pose[:3,:3].T@pose[:3,:3],np.eye(3),atol=1e-12)

    def test_parent_curve_motion_stays_unknown_and_completion_returns_exact_step(self):
        for p in self.plans:
            sid=p['assignments']['O088'];a=frame(self.model,p,sid,'entry',0)
            self.assertIn('C007',a['transforms'])
            s=frame(self.model,p,sid,'motion',.5)
            self.assertEqual(s['unknown_cells']['C007'],['O088']);self.assertNotIn('C007',s['transforms'])
            final=frame(self.model,p,'OP100','completed',1)
            self.assertFalse(final['unknown_cells'])
            for matrix in final['transforms'].values():np.testing.assert_allclose(matrix,np.eye(4),atol=1e-12)

    def test_shared_progress_and_material_events_keep_original_semantics(self):
        for p in self.plans:
            sid=p['assignments']['O094'];s=frame(self.model,p,sid,'motion',.7);old=old_frame(self.original,p,sid,'motion',.7)
            self.assertEqual(s['joint_fractions'],old['joint_fractions']);self.assertEqual(s['executed_operation_ids'],old['executed_operation_ids'])
            self.assertFalse(s['executed_material_events_applied_to_geometry'])
            for cid in old['transforms']:np.testing.assert_allclose(s['transforms'][cid],old['transforms'][cid],atol=1e-12)

    def test_added_reference_pairs_preserve_unknown_pair_denominator(self):
        p=self.plans[0];s=frame(self.model,p,'OP10','entry',0)
        r=ReferenceChecker(self.model,C).check(s)
        self.assertEqual(r['reference_geometry']['pair_count'],105)
        self.assertEqual(len(r['unknown_motion_pairs']),66)
        self.assertEqual(r['overall_process_status'],'UNKNOWN_INCOMPLETE_MODEL')
        self.assertFalse(r['manufacturing_safety_proved'])


if __name__=='__main__':unittest.main()
