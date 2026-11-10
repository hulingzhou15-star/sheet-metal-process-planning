from pathlib import Path
import json,math,unittest,hashlib
import numpy as np
from information_extract.scripts.build_finite_material_states import prepare,CFG
from information_extract.scripts.build_local_collision_review import mesh
from information_extract.three_d.band_material import state_transforms,station_fractions,strip_point,map_points,band_volume_ratio
from information_extract.three_d.band_material_solids import subtract,common_volume,component_at_state,split_triangle_grid
from information_extract.three_d.local_collision import volume,transformed
from information_extract.three_d.state import transform_point
from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.TopAbs import TopAbs_IN
from OCP.gp import gp_Pnt

OUT=Path(__file__).resolve().parents[1]/'outputs/finite_material_v02/P001'


def inside(shape,point):return BRepClass3d_SolidClassifier(shape,gp_Pnt(*map(float,point)),1e-7).State()==TopAbs_IN


class BandMaterialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model,cls.stage,cls.domains,cls.components,cls.tools,cls.affected,cls.coverage=prepare()
        cls.orders={s['station_id']:s['order'] for s in cls.stage['stations']}
        cls.plan=next(p for p in cls.stage['plans'] if p['plan_id']=='NP2_001')
        cls.viewer=json.loads((OUT/'viewer_data.json').read_text(encoding='utf-8'))

    def test_flat_identity_and_final_cad_closure(self):
        m=self.model;flat=state_transforms(m,{b['joint_id']:0 for b in m['bends']});end=state_transforms(m,{b['joint_id']:1 for b in m['bends']})
        self.assertEqual(len(flat),60);self.assertEqual(len(m['bends']),59)
        for r in m['regions']:
            np.testing.assert_allclose(flat[r['region_id']],np.eye(4),atol=1e-8)
            np.testing.assert_allclose(end[r['region_id']]@r['flat_from_cad'],np.eye(4),atol=1e-7)

    def test_tangent_connections_throughout_nested_motion(self):
        for fraction in [0.,.37,1.]:
            fr={b['joint_id']:fraction for b in self.model['bends']};ts=state_transforms(self.model,fr)
            for b in self.model['bends']:
                a=np.array(b['start_flat_mm']);end=a+np.array(b['forward_flat'])*b['width_mm']
                for depth in [0.,.4,.8]:
                    p=end-[0,0,depth];on_band=transform_point(ts[b['parent_region_id']],strip_point(b,p,fraction))
                    on_child=transform_point(ts[b['child_region_id']],p)
                    np.testing.assert_allclose(on_band,on_child,atol=1e-7)
                    np.testing.assert_allclose(strip_point(b,a-[0,0,depth],fraction),a-[0,0,depth],atol=1e-8)

    def test_same_station_common_progress_and_missing_fraction_rejected(self):
        for sid in self.orders:
            fr=station_fractions(self.model,self.plan['assignments'],self.orders,sid,.37)
            for b in self.model['bends']:
                if self.plan['assignments'][b['operation_id']]==sid:self.assertEqual(fr[b['joint_id']],.37)
        with self.assertRaises(ValueError):state_transforms(self.model,{})

    def test_zero_domain_overlap_and_explicit_unknown_holes(self):
        d=json.loads((OUT/'domain_audit.json').read_text(encoding='utf-8'));self.assertEqual(d['overlaps'],[])
        self.assertEqual(sum(r['status']=='FULL_SUPPORTED_DOMAIN' for r in self.coverage),95)
        outside=[r['operation_id'] for r in self.coverage if r['status']=='OUTSIDE_SUPPORTED_REFERENCE']
        self.assertEqual(outside,['O196','O197','O198','O199','O200'])

    def test_c_shape_is_removed_and_inner_tongue_retained(self):
        tool=self.tools['O193'];affected=self.affected['O193'];after={k:subtract(self.domains[k],[tool]) for k in affected}
        row=next(r for r in self.coverage if r['operation_id']=='O193')
        self.assertAlmostEqual(row['source_volume_mm3'],row['supported_removed_volume_mm3'],places=6)
        # Identify removal and retained-notch witnesses from the actual planar domains.
        source=next(o for o in self.stage['operations'] if o['operation_id']=='O193')
        points=np.array(source['flat_paths'][0])
        allpoints=np.array([p for path in source['flat_paths'] for p in path]);lo,hi=allpoints.min(axis=0),allpoints.max(axis=0)
        removed=None;retained=None
        for x in np.linspace(lo[0]+.2,hi[0]-.2,19):
            for y in np.linspace(lo[1]+.2,hi[1]-.2,19):
                p=[x,y,-.4];owner=next((k for k in affected if inside(self.domains[k],p)),None)
                if not owner:continue
                if inside(tool,p) and removed is None:removed=(owner,p)
                if not inside(tool,p) and inside(after[owner],p) and retained is None:retained=(owner,p)
        self.assertIsNotNone(removed);self.assertIsNotNone(retained)
        self.assertFalse(inside(after[removed[0]],removed[1]))
        self.assertTrue(inside(after[retained[0]],retained[1]))
        for tau in [0,.37,1.]:
            fr={b['joint_id']:tau for b in self.model['bends']};ts=state_transforms(self.model,fr)
            for owner,p in [removed,retained]:
                moved=transformed(after[owner],ts[owner]);w=transform_point(ts[owner],p)
                self.assertEqual(inside(moved,w),owner==retained[0] and p==retained[1])
        self.witnesses={'removed':removed,'retained':retained}

    def test_actual_circle_remains_empty_during_bending(self):
        rid='P466_470';tool=self.tools['O170'];after=subtract(self.domains[rid],[tool])
        p=[-206.2,-162.1,-.4];pin=BRepPrimAPI_MakeSphere(gp_Pnt(*p),.15).Shape()
        self.assertGreater(common_volume(self.domains[rid],pin),.001)
        for tau in [0,.37,1.]:
            fr={b['joint_id']:tau for b in self.model['bends']};ts=state_transforms(self.model,fr)
            self.assertLess(common_volume(transformed(after,ts[rid]),transformed(pin,ts[rid])),1e-7)

    def test_pre_punch_radius_does_not_become_final_flange(self):
        self.assertAlmostEqual(volume(self.tools['O012']),math.pi*.7**2*.8,places=7)
        self.assertNotIn('O011',self.tools)

    def test_faceted_band_collision_solid_matches_rendered_boundary(self):
        variant=next(v for v in self.viewer['variants'] if not v['executed_removal_ids'])
        for oid in ['O144','O057']:
            b=next(b for b in self.model['bends'] if b['operation_id']==oid)
            c=next(c for c in variant['components'] if c.get('joint_id')==b['joint_id'])
            triangles=self.viewer['geometries'][c['geometry_id']]['triangles']
            for tau in [0.,.37,1.]:
                fr={j['joint_id']:tau for j in self.model['bends']}
                shape=component_at_state(self.model,c,None,fr,triangles)
                expected=c['flat_volume_mm3']*band_volume_ratio(b,tau,.8)
                self.assertAlmostEqual(volume(shape)/expected,1.,delta=.002)

    def test_material_stage_boundaries_do_not_add_future_holes(self):
        v=self.viewer;variants={x['material_id']:x for x in v['variants']}
        for plan in v['plans']:
            for sid in self.orders:
                entry=next(r for r in v['material_states'] if r['plan_id']==plan['plan_id'] and r['station_id']==sid and r['phase']=='entry')
                actual=variants[entry['material_id']]['executed_removal_ids']
                self.assertTrue(all(self.orders[plan['assignments'][op]]<self.orders[sid] for op in actual))

    def test_all_frozen_candidates_still_pass_hard_rules(self):
        from src.constraints.v06.context import load_rule_context
        from src.constraints.v06.hard_constraints import HardConstraintEngine
        root=Path(__file__).resolve().parents[2]
        engine=HardConstraintEngine(load_rule_context(root/'data/parts/P001/钣金加工数据表_P001.xlsx'))
        for plan in self.stage['plans']:self.assertTrue(engine.evaluate(plan['assignments']).to_dict()['feasible'],plan['plan_id'])
        for path,h in self.model['source_sha256'].items():self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(),h,path)

    def test_volume_limitation_is_reported(self):
        b=next(b for b in self.model['bends'] if b['operation_id']=='O144')
        self.assertAlmostEqual(band_volume_ratio(b,1.,.8),math.pi/2,places=7)
        self.assertFalse(self.model['validation']['volume_conservation_claimed'])
        self.assertFalse(self.model['physical_forming_validated'])


if __name__=='__main__':unittest.main()
