"""Fail-closed coverage and existing-kernel reference-motion checks for P002."""
from copy import deepcopy
from pathlib import Path
import unittest
import numpy as np

from information_extract.three_d.artifacts import ROOT,read,sha,verify_sources
from information_extract.three_d.p002_motion_reference import compile_model,frame,make_samples,ReferenceChecker,CHECK_CONFIG
from information_extract.three_d.panel_collision import PanelChecker
from information_extract.three_d.local_forming import box

M=ROOT/'information_extract';P=M/'outputs/p002_generation_v01';C=M/'outputs/p002_material_cells_v01'
G=M/'outputs/geometry_allocation_v1_generated/P002';OUT=M/'outputs/p002_candidate_motion_v01'


class MotionReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model=compile_model(read(C/'cells.json'),read(P/'straight_branch_proposals.json'),read(P/'carried_region_proposals.json'),read(P/'facts.json'))
        cls.plans=read(G/'plans.json')['plans'];cls.plan=cls.plans[0]

    def test_all_cells_have_explicit_pose_support_or_unknown_curve(self):
        self.assertEqual(len(self.model['nodes']),19);self.assertEqual(len(self.model['joints']),14)
        self.assertEqual(len({j['operation_id'] for j in self.model['joints']}),13)
        self.assertEqual(set(self.model['curved_operation_ids']),{'O069','O088','O096','O104'})
        self.assertFalse(self.model['full_process_model'])

    def test_unknown_curve_propagates_to_previously_executed_child(self):
        sid=self.plan['assignments']['O088'];s=frame(self.model,self.plan,sid,'motion',.5)
        self.assertEqual(s['unknown_cells']['C004'],['O088'])
        self.assertEqual(s['unknown_cells']['C007'],['O088'])
        self.assertIn('O094',s['executed_operation_ids'])
        self.assertNotIn('C004',s['transforms']);self.assertNotIn('C007',s['transforms'])
        end=frame(self.model,self.plan,sid,'completed',1.)
        self.assertTrue(np.allclose(end['transforms']['C004'],np.eye(4)))
        self.assertTrue(np.allclose(end['transforms']['C007'],np.eye(4)))

    def test_z_hinges_share_station_progress(self):
        sid=self.plan['assignments']['O136'];s=frame(self.model,self.plan,sid,'motion',.25)
        self.assertEqual(s['joint_fractions']['J531'],.25)
        self.assertEqual(s['joint_fractions']['J64'],.25)
        self.assertNotIn('O042',self.plan['assignments']);self.assertNotIn('O043',self.plan['assignments'])
        self.assertFalse(np.allclose(s['transforms']['C014'],s['transforms']['C015']))

    def test_node_order_does_not_insert_intra_station_sequencing(self):
        model=deepcopy(self.model);model['nodes'].reverse()
        sid=self.plan['assignments']['O136'];a=frame(self.model,self.plan,sid,'motion',.4);b=frame(model,self.plan,sid,'motion',.4)
        self.assertEqual(a['joint_fractions'],b['joint_fractions'])
        for k in a['transforms']:np.testing.assert_allclose(a['transforms'][k],b['transforms'][k],atol=1e-12)

    def test_material_events_switch_only_at_completed_and_are_not_falsely_applied(self):
        a=frame(self.model,self.plan,'OP10','entry',0.);b=frame(self.model,self.plan,'OP10','motion',.9)
        c=frame(self.model,self.plan,'OP10','completed',1.)
        self.assertEqual(a['executed_operation_ids'],b['executed_operation_ids'])
        self.assertTrue({'O063','O105','O108'}<=set(c['executed_forming_ids']))
        self.assertEqual(a['transforms'],c['transforms'])
        for s in (a,b,c):
            self.assertFalse(s['executed_material_events_applied_to_geometry'])
            self.assertEqual(s['material_scope'],'COMMON_COMPLETED_STEP_REFERENCE')

    def test_all_final_poses_equal_step_for_all_sixteen_assignments(self):
        for p in self.plans:
            s=frame(self.model,p,self.model['stations'][-1]['station_id'],'completed',1.)
            self.assertEqual(s['unknown_cells'],{});self.assertEqual(len(s['transforms']),19)
            self.assertEqual(set(s['executed_operation_ids']),set(p['assignments']))
            self.assertEqual(s['unexecuted_material_event_ids'],[])
            for m in s['transforms'].values():np.testing.assert_allclose(m,np.eye(4),atol=1e-12)
            self.assertEqual(s['process_check_status'],'INCOMPLETE_MODEL')

    def test_unknown_pairs_remain_in_denominator(self):
        checker=ReferenceChecker(self.model,C);s=frame(self.model,self.plan,'OP10','entry',0.)
        result=checker.check(s)
        self.assertEqual(result['total_reference_pairs'],171)
        self.assertEqual(result['reference_geometry']['pair_count'],91)
        self.assertEqual(len(result['unknown_motion_pairs']),80)
        self.assertEqual(sum(result['reference_geometry']['counts'].values()),91)
        self.assertEqual(result['overall_process_status'],'UNKNOWN_INCOMPLETE_MODEL')

    def test_unknown_pose_cannot_be_passed_as_stationary_to_checker(self):
        s=frame(self.model,self.plan,'OP10','entry',0.);s['transforms']['C004']=np.eye(4).tolist()
        with self.assertRaisesRegex(ValueError,'coverage'):ReferenceChecker(self.model,C).check(s)

    def test_existing_kernel_positive_contact_and_separation_controls(self):
        solids={'A':box([0,0,0],[2,2,2]),'B':box([1,1,1],[3,3,3])}
        checker=PanelChecker(solids,[('A','B')],CHECK_CONFIG);t={'A':np.eye(4),'B':np.eye(4)}
        result=checker.check(t);self.assertEqual(result['counts'].get('OVERLAP'),1)
        self.assertAlmostEqual(result['narrow_phase_pairs'][0]['intersection_volume_mm3'],1.)
        self.assertTrue(result['narrow_phase_pairs'][0]['cad_hinge_adjacent'])
        t['B'][0,3]=1;result=checker.check(t);self.assertEqual(result['counts'].get('CONTACT'),1)
        t['B'][0,3]=2;result=checker.check(t);self.assertEqual(result['counts'].get('AABB_SEPARATED'),1)

    def test_samples_cover_curve_stations_and_bad_inputs_are_rejected(self):
        samples=make_samples(self.model,self.plan,24)
        for o in self.model['curved_operation_ids']:
            self.assertTrue(any(s==self.plan['assignments'][o] and p=='motion' for s,p,t in samples))
        with self.assertRaises(ValueError):frame(self.model,self.plan,'OP10','entry',.5)
        with self.assertRaises(ValueError):make_samples(self.model,self.plan,1)


class DeliveredMotionArtifactTests(unittest.TestCase):
    def test_sixteen_models_are_preserved_candidates_and_report_incomplete(self):
        plans=read(G/'plans.json')['plans'];summary=read(OUT/'summary.json');verification=read(OUT/'verification.json')
        self.assertEqual(summary['completed'],16);self.assertFalse(summary['full_process_collision_checked'])
        verify_sources(read(OUT/'inputs.json')['files']);verify_sources(read(OUT/'output_manifest.json')['files'])
        for p in plans:
            m=read(OUT/p['plan_id']/'model.json');r=read(OUT/p['plan_id']/'collision_report.json')
            self.assertEqual(m['assignments'],p['assignments']);self.assertEqual(r['assignments'],p['assignments'])
            self.assertEqual(sha(OUT/p['plan_id']/'completed.step'),m['completed_step_sha256'])
            self.assertFalse(m['full_process_model']);self.assertFalse(r['full_process_collision_checked'])
            for s in r['runs']:
                self.assertEqual(s['reference_geometry']['pair_count']+len(s['unknown_motion_pairs']),171)
                self.assertEqual(s['overall_process_status'],'UNKNOWN_INCOMPLETE_MODEL')
        self.assertLessEqual(verification['refinement']['maximum_numeric_difference'],1e-6)


if __name__=='__main__':unittest.main()
