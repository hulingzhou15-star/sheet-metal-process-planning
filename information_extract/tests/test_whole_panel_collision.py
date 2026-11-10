from pathlib import Path
import json,hashlib,unittest
from unittest.mock import patch
import numpy as np
from information_extract.three_d.panel_collision import PanelChecker,aabb_gap,transformed_bounds,narrow_pair
from information_extract.three_d.h6_h7_audit import fractions_for_stage
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox,BRepPrimAPI_MakeSphere
from OCP.gp import gp_Pnt

OUT=Path(__file__).resolve().parents[1]/'outputs/whole_panel_collision_v01/P001'
CFG={'contact_tolerance_mm':1e-6,'volume_tolerance_mm3':1e-6,'relative_transform_cache_decimals':12}


class PanelCollisionTests(unittest.TestCase):
    def boxes(self,x):
        return {'a':BRepPrimAPI_MakeBox(1,1,1).Shape(),'b':BRepPrimAPI_MakeBox(gp_Pnt(x,0,0),1,1,1).Shape()}

    def test_aabb_separation_rejects_without_kernel(self):
        c=PanelChecker(self.boxes(2),[],CFG);r=c.check({'a':np.eye(4),'b':np.eye(4)})
        self.assertEqual(r['counts'],{'AABB_SEPARATED':1});self.assertEqual(c.kernel_evaluations,0)

    def test_aabb_overlap_is_not_a_collision(self):
        bodies={'a':BRepPrimAPI_MakeSphere(1).Shape(),'b':BRepPrimAPI_MakeSphere(gp_Pnt(1.8,1.8,0),1).Shape()}
        r=PanelChecker(bodies,[],CFG).check({'a':np.eye(4),'b':np.eye(4)})
        self.assertEqual(r['counts'],{'SEPARATED':1})

    def test_adjacent_intersection_is_not_exempt(self):
        r=PanelChecker(self.boxes(.25),[('a','b')],CFG).check({'a':np.eye(4),'b':np.eye(4)})
        self.assertEqual(r['counts'],{'OVERLAP':1});p=r['narrow_phase_pairs'][0]
        self.assertTrue(p['cad_hinge_adjacent']);self.assertAlmostEqual(p['intersection_volume_mm3'],.75)

    def test_contact_not_volume_overlap(self):
        r=PanelChecker(self.boxes(1),[],CFG).check({'a':np.eye(4),'b':np.eye(4)})
        self.assertEqual(r['counts'],{'CONTACT':1})

    def test_relative_cache_and_world_witness_transform(self):
        c=PanelChecker(self.boxes(.25),[],CFG)
        a=c.check({'a':np.eye(4),'b':np.eye(4)});t=np.eye(4);t[:3,3]=[10,-3,7]
        b=c.check({'a':t,'b':t})
        self.assertEqual(c.kernel_evaluations,1)
        np.testing.assert_allclose(np.array(b['narrow_phase_pairs'][0]['intersection_world_aabb_mm'])-a['narrow_phase_pairs'][0]['intersection_world_aabb_mm'],[10,-3,7,10,-3,7])

    def test_rigid_rotation_bounding_box_conservative(self):
        t=np.array([[0,-1,0,10],[1,0,0,0],[0,0,1,0],[0,0,0,1]],float)
        np.testing.assert_allclose(transformed_bounds([0,0,0,1,2,3],t),[8,0,0,10,1,3])

    def test_kernel_failure_is_unknown(self):
        with patch('information_extract.three_d.panel_collision.narrow_pair',side_effect=ValueError('synthetic failure')):
            r=PanelChecker(self.boxes(.25),[],CFG).check({'a':np.eye(4),'b':np.eye(4)})
        self.assertEqual(r['status'],'UNKNOWN_KERNEL_FAILURE')
        self.assertEqual(r['counts']['UNKNOWN_KERNEL_FAILURE'],1)

    def test_missing_transform_fails(self):
        with self.assertRaises(ValueError):PanelChecker(self.boxes(0),[],CFG).check({'a':np.eye(4)})

    def test_actual_all_pairs_accounted_and_h7_kept(self):
        r=json.loads((OUT/'report.json').read_text(encoding='utf-8'))
        self.assertEqual(r['summary']['supported_panel_count'],60)
        self.assertTrue(r['summary']['hard_feasible']);self.assertTrue(r['summary']['h7_preserved'])
        self.assertFalse(r['summary']['candidate_filter_enabled'])
        self.assertEqual(r['summary']['manufacturing_feasibility'],'UNKNOWN')
        for run in r['runs']:
            for sample in run['samples']:
                self.assertEqual(sum(sample['counts'].values()),1770)
                self.assertEqual(len(sample['narrow_phase_pairs'])+sample['counts'].get('AABB_SEPARATED',0),1770)

    def test_actual_synchronous_schedule_and_continuous_stage_boundaries(self):
        d=json.loads((OUT/'viewer_data.json').read_text(encoding='utf-8'))
        orders={s['station_id']:s['order'] for s in d['stations']}
        for run in d['runs']:
            for sample in run['samples']:
                self.assertEqual(sample['joint_fractions'],fractions_for_stage(d['skeleton'],d['plan']['assignments'],orders,run['station_id'],sample['tau']))
        for a,b in zip(d['runs'],d['runs'][1:]):self.assertEqual(a['samples'][-1]['joint_fractions'],b['samples'][0]['joint_fractions'])

    def test_artifact_variant_keeps_raw_issues_and_does_not_certify(self):
        d=json.loads((OUT/'artifact_sensitivity.json').read_text(encoding='utf-8'))
        r=json.loads((OUT/'report.json').read_text(encoding='utf-8'))
        self.assertEqual(d['source_report_sha256'],hashlib.sha256((OUT/'report.json').read_bytes()).hexdigest())
        self.assertEqual({x['issue_id'] for x in d['rows']},{x['issue_id'] for x in r['issues']})
        self.assertFalse(d['inner_wires_variant_is_real_stage_geometry'])
        self.assertTrue(all(x['manufacturing_verdict']=='UNKNOWN' for x in d['rows']))

    def test_protected_sources_unchanged(self):
        r=json.loads((OUT/'report.json').read_text(encoding='utf-8'))
        for p,h in r['source_sha256'].items():self.assertEqual(hashlib.sha256(Path(p).read_bytes()).hexdigest(),h,p)


if __name__=='__main__':unittest.main()
