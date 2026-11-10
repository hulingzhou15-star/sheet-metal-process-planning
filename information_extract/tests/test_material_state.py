from copy import deepcopy
import json
from pathlib import Path
import unittest
import numpy as np

from information_extract.three_d.material import infer_material
from information_extract.three_d.local_motion import poses,local_schedule,associate_bends
from information_extract.three_d.state import transform_point
from information_extract.scripts.build_material_state_review import clip_triangle

MODULE=Path(__file__).resolve().parents[1]
SOURCE=MODULE/'outputs/three_d_state_v01/P001'
OUT=MODULE/'outputs/material_state_v01/P001'


class MaterialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.asset=json.loads((SOURCE/'asset.json').read_text(encoding='utf-8'))
        cls.facts=json.loads((SOURCE/'facts.json').read_text(encoding='utf-8'))
        cls.registration=json.loads((SOURCE/'registration.json').read_text(encoding='utf-8'))
        cls.config=json.loads((MODULE/'configs/material_state_v01.json').read_text(encoding='utf-8'))
        cls.material=json.loads((OUT/'material_proposals.json').read_text(encoding='utf-8'))
        cls.skeleton=json.loads((OUT/'kinematic_proposals.json').read_text(encoding='utf-8'))
        cls.example=json.loads((OUT/'local_example.json').read_text(encoding='utf-8'))
        cls.local=cls.example['skeleton']

    def test_geometric_thickness_uses_four_large_pairs(self):
        result=infer_material(self.asset,self.config)
        self.assertAlmostEqual(result['thickness_estimate']['value_mm'],.8)
        self.assertEqual(len(result['thickness_estimate']['large_plane_support']),4)
        self.assertEqual(len(result['regions']),64)
        self.assertEqual(len(result['joints']),59)
        self.assertEqual(result['thickness_estimate']['status'],'GEOMETRIC_ESTIMATE_NOT_STOCK_SPEC')

    def test_skin_pairs_unique_and_joint_evidence_real(self):
        used=[face for r in self.material['regions'] for face in r['skin_face_ids']]
        self.assertEqual(len(used),len(set(used)))
        for joint in self.material['joints']:
            for evidence in joint['surface_evidence']:
                face=self.asset['faces'][evidence['cylinder_face_id']-1]
                self.assertTrue(all(f in face['neighbor_face_ids'] for f in evidence['contact_skin_faces'].values()))

    def test_whole_tree_flattens_midplanes_and_front_normals(self):
        sk=self.skeleton
        matrices=poses(sk,{j['joint_id']:0 for j in sk['joints']})
        centers=[transform_point(matrices[r['region_id']],r['midplane']['point_mm']) for r in sk['regions']]
        self.assertLess(max(p[2] for p in centers)-min(p[2] for p in centers),1e-7)
        for j in sk['joints']:
            n=matrices[j['child_region_id']][:3,:3]@j['reference_surface_normals']['child']
            np.testing.assert_allclose(n,[0,0,1],atol=1e-7)

    def test_final_pose_is_identity_closure_only(self):
        matrices=poses(self.skeleton,{j['joint_id']:1 for j in self.skeleton['joints']})
        for m in matrices.values():np.testing.assert_allclose(m,np.eye(4),atol=1e-10)

    def test_unknown_joint_fraction_is_rejected(self):
        values={j['joint_id']:0 for j in self.local['joints']};values.pop(next(iter(values)))
        with self.assertRaises(ValueError):poses(self.local,values)

    def test_out_of_range_or_nonfinite_fraction_rejected(self):
        for value in [-.1,1.1,float('nan')]:
            with self.assertRaises(ValueError):poses(self.local,{j['joint_id']:value for j in self.local['joints']})

    def test_child_then_parent_schedule_tracks_actual_feature(self):
        sk=self.local;assignment={'O010':'OP40','O009':'OP50'};orders={'OP40':4,'OP50':5}
        feature=self.example['feature'];region=feature['region_id'];point=feature['formed_surface_center_mm']
        start=local_schedule(sk,sk['joint_to_operation'],assignment,orders,'OP40',0)
        middle=local_schedule(sk,sk['joint_to_operation'],assignment,orders,'OP40',1)
        final=local_schedule(sk,sk['joint_to_operation'],assignment,orders,'OP50',1)
        np.testing.assert_allclose(transform_point(start['transforms'][region],point),[354.8,1.1,0],atol=1e-7)
        np.testing.assert_allclose(transform_point(middle['transforms'][region],point),[354.8,-3.7,-5.6],atol=1e-7)
        np.testing.assert_allclose(transform_point(final['transforms'][region],point),point,atol=1e-7)
        self.assertFalse(final['full_plan_checked'])

    def test_same_station_fraction_evaluates_both_joints_together(self):
        # A clock/kernel test, not a permitted P001 candidate (UI rejects H7).
        a=local_schedule(self.local,self.local['joint_to_operation'],{'O009':'S','O010':'S'},{'S':1},'S',.5)
        self.assertEqual(set(a['fractions'].values()),{.5})
        b=poses(self.local,dict(reversed(list(a['fractions'].items()))))
        for region in b:np.testing.assert_allclose(b[region],a['transforms'][region])

    def test_independent_joint_fractions_change_different_motion(self):
        first,second=[j['joint_id'] for j in self.local['joints']]
        a=poses(self.local,{first:.5,second:0});b=poses(self.local,{first:0,second:.5})
        self.assertFalse(np.allclose(a[self.local['region_order'][-1]],b[self.local['region_order'][-1]]))

    def test_associations_unique_residuals_not_hidden(self):
        result=associate_bends(self.skeleton,self.facts,self.registration,self.config)
        self.assertEqual(len(result['matches']),59)
        self.assertEqual(len({r['joint_id'] for r in result['matches']}),59)
        self.assertEqual(len({r['operation_id'] for r in result['matches']}),59)
        example={r['operation_id']:r for r in result['matches']}
        self.assertAlmostEqual(example['O009']['endpoint_residual_mm'],.2)
        self.assertAlmostEqual(example['O010']['endpoint_residual_mm'],.6)
        self.assertFalse(example['O009']['quantitative_match'])
        self.assertAlmostEqual(self.example['feature']['center_residual_mm'],.8)

    def test_perturbing_semantic_tree_cannot_change_geometric_matches(self):
        facts=deepcopy(self.facts)
        for op in facts['operations']:op['cad_parent_operation_id']='FAKE';op['required_predecessor_ids']='FAKE'
        self.assertEqual(associate_bends(self.skeleton,self.facts,self.registration,self.config),associate_bends(self.skeleton,facts,self.registration,self.config))

    def test_display_crop_does_not_move_whole_shared_side_face(self):
        data=json.loads((OUT/'viewer_data.json').read_text(encoding='utf-8'))
        moving_faces={i for s in data['surfaces'][1:] for i in s['face_ids']}
        self.assertNotIn(850,moving_faces)
        triangles=clip_triangle([[-2,-2,0],[2,-2,0],[0,2,0]],[-1,-1,-1],[1,1,1])
        self.assertTrue(triangles)
        self.assertTrue(all(-1-1e-10<=v<=1+1e-10 for t in triangles for p in t for v in p))

    def test_multiple_bend_segments_are_all_preserved(self):
        multiple=[j for j in self.skeleton['joints'] if len(j['surface_evidence'])>1]
        self.assertTrue(multiple)
        for j in multiple:
            self.assertEqual(len(j['surface_evidence']),len(j['developed_axis_segment_evidence_mm']))
            whole=np.array(j['developed_axis_endpoints_mm']);direction=whole[1]-whole[0];direction/=np.linalg.norm(direction)
            for segment in j['developed_axis_segment_evidence_mm']:
                for point in segment['endpoints']:
                    distance=np.dot(np.array(point)-whole[0],direction)
                    self.assertGreaterEqual(distance,-1e-7)
                    self.assertLessEqual(distance,np.linalg.norm(whole[1]-whole[0])+1e-7)

    def test_unconnected_proposals_are_unknown_not_in_motion_tree(self):
        self.assertEqual(len(self.skeleton['regions']),60)
        unknown=set(self.skeleton['unconnected_region_proposal_ids'])
        self.assertEqual(len(unknown),4)
        self.assertFalse(unknown & set(self.skeleton['region_order']))

    def test_small_panel_recovery_stable_under_screening_variation(self):
        config=deepcopy(self.config);config['plane_pairing']['minimum_panel_area_mm2']=8
        m=infer_material(self.asset,config)
        self.assertEqual({r['region_id'] for r in m['regions']},{r['region_id'] for r in self.material['regions']})
        self.assertEqual({j['joint_id'] for j in m['joints']},{j['joint_id'] for j in self.material['joints']})

    def test_independent_semantic_crosscheck_records_all_41_relations(self):
        report=json.loads((OUT/'semantic_crosscheck.json').read_text(encoding='utf-8'))
        self.assertEqual(report['comparable_relation_count'],41)
        self.assertEqual(report['consistent_count'],41)
        self.assertEqual(report['stage'],'POST_ASSOCIATION_ONLY_NO_SELECTION_FEEDBACK')


if __name__=='__main__':unittest.main()
