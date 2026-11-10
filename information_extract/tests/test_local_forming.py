"""Physical geometry witnesses and frozen schedule checks for v0.3 endpoints."""
import unittest,json,math
from pathlib import Path
import numpy as np
from information_extract.scripts.build_local_forming_states import OUT,BASE,MODULE,read,write,sha
from information_extract.three_d.local_forming import load_shape,intersect,cylinder,box,fuse
from information_extract.three_d.local_collision import volume,transformed,indexed
from information_extract.three_d.band_material import station_fractions,state_transforms,map_points,band_volume_ratio
from information_extract.three_d.band_material_solids import component_at_state,common_volume,subtract
from OCP.TopAbs import TopAbs_SOLID
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.TopAbs import TopAbs_IN
from OCP.gp import gp_Pnt


class LocalFormingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v=read(OUT/'viewer_data.json');cls.model=cls.v['model'];cls.variants={x['material_id']:x for x in cls.v['variants']}
        cls.regions={r['region_id']:r for r in cls.model['regions']};cls.inventory=read(OUT/'flanging_step_inventory.json')['operations']
        cls.orders={s['station_id']:s['order'] for s in cls.v['stations']};cls.records={(r['plan_id'],r['station_id'],r['phase']):cls.variants[r['material_id']] for r in cls.v['material_states']}
        cls.initial=next(v for v in cls.v['variants'] if not v['executed_removal_ids']);cls.full=next(v for v in cls.v['variants'] if len(v['executed_forming_ids'])==50)

    def test_01_100_exact_cuts_and_no_95_event_regression(self):
        before={r['operation_id']:r for r in read(BASE/'removal_coverage.json')};after=self.v['removal_coverage']
        self.assertEqual(len(after),100)
        for r in after:
            self.assertEqual(r['status'],'FULL_SUPPORTED_DOMAIN');self.assertAlmostEqual(r['source_volume_mm3'],r['supported_removed_volume_mm3'],places=5)
            self.assertAlmostEqual(r['source_volume_mm3'],before[r['operation_id']]['source_volume_mm3'],places=5)
        self.assertEqual(read(OUT/'domain_audit.json')['overlaps'],[])

    def test_02_restored_material_before_cut_and_empty_after(self):
        for r in read(OUT/'restoration_audit.json'):
            oid=r['operation_id'];tool=load_shape(OUT/'brep'/f'tool_{oid}.brep');total=0
            for p in r['parts']:
                c=next(c for c in self.initial['components'] if c['component_id']==p['component_id']);s=load_shape(OUT/self.v['geometries'][c['geometry_id']]['brep_file'])
                total+=common_volume(s,tool);self.assertLess(volume(subtract(s,[tool])),1e-7)
                self.assertFalse(any(x['component_id']==c['component_id'] for x in self.full['components']))
            self.assertAlmostEqual(total,volume(tool),places=5)

    def test_03_new_band_material_has_closed_moving_collision_solids(self):
        samples=[]
        for c in self.initial['components']:
            if not c.get('restored_by_operation_id') or c['kind']!='band':continue
            g=self.v['geometries'][c['geometry_id']];b=next(b for b in self.model['bends'] if b['joint_id']==c['joint_id'])
            for tau in [0.,.37,1.]:
                fr={b['joint_id']:tau for b in self.model['bends']};body=component_at_state(self.model,c,None,fr,g['triangles'])
                self.assertAlmostEqual(volume(body)/(g['flat_volume_mm3']*band_volume_ratio(b,tau,.8)),1.,delta=.002)
            samples.append(c['component_id'])
        self.assertEqual(len(samples),5)

    def test_04_all_local_endpoints_match_cad_and_connect(self):
        ts=state_transforms(self.model,{b['joint_id']:1. for b in self.model['bends']})
        for row in self.v['local_forming']:
            oid=row['operation_id'];formed=load_shape(OUT/'brep'/f'{oid}_formed.brep');cad=load_shape(OUT/'brep'/f'{oid}_cad.brep')
            moved=transformed(formed,ts[row['region_id']]);self.assertLess(volume(subtract(moved,[cad]))+volume(subtract(cad,[moved])),1e-5)
            host=load_shape(OUT/'brep'/('domain_'+row['region_id']+'.brep'))
            self.assertLess(common_volume(host,formed),1e-6);self.assertLessEqual(indexed(fuse(host,formed),TopAbs_SOLID).Extent(),indexed(host,TopAbs_SOLID).Extent())
        self.assertEqual(self.model['unconnected_regions'],[])

    def test_05_flange_lumen_height_and_pre_hole_remain_distinct(self):
        for row in self.inventory:
            oid=row['operation_id'];transform=np.array(self.regions[row['region_id']]['flat_from_cad']);p=(transform@np.r_[row['center_cad_mm'],1])[:3]
            s=load_shape(OUT/'brep'/f'{oid}_formed.brep');hole=cylinder(p[0],p[1],row['inner_radius_mm']-.05,-2,2)
            self.assertLess(common_volume(s,hole),1e-7,oid)
            level=row['rim_from_front_skin_mm'];outer=row['outer_radius_mm'];inner=row['inner_radius_mm'];sign=row['geometric_direction_sign']
            q=[p[0]+(inner+outer)/2,p[1],level-sign*.05]
            self.assertEqual(BRepClass3d_SolidClassifier(s,gp_Pnt(*map(float,q)),1e-7).State(),TopAbs_IN,oid)
            # Beyond the actual STEP rim there must be no wall material.
            beyond=cylinder(p[0],p[1],outer-.01,level+.01 if sign>0 else level-.2,level+.2 if sign>0 else level-.01)
            self.assertLess(common_volume(s,beyond),1e-7,oid)
            prep=load_shape(OUT/'brep'/f'tool_{row["pre_punch_operation_id"]}.brep')
            prep_radius=math.sqrt(volume(prep)/(math.pi*.8));self.assertLess(prep_radius,inner)
            self.assertAlmostEqual(row['protrusion_beyond_exit_skin_mm'],.8,places=6)

    def test_06_all_224_stage_endpoints_obey_original_assignment(self):
        cuts={r['operation_id'] for r in self.v['removal_coverage']};forms={r['operation_id'] for r in self.v['local_forming']}
        self.assertEqual(len(self.records),224)
        for plan in self.v['plans']:
            previous=None
            for station in self.v['stations']:
                sid=station['station_id'];entry=self.records[(plan['plan_id'],sid,'entry')];end=self.records[(plan['plan_id'],sid,'completed')]
                for phase,record in [('entry',entry),('completed',end)]:
                    def done(o):return self.orders[plan['assignments'][o]]<station['order'] or phase=='completed' and self.orders[plan['assignments'][o]]==station['order']
                    self.assertEqual(set(record['executed_removal_ids']),{o for o in cuts if done(o)})
                    self.assertEqual(set(record['executed_forming_ids']),{o for o in forms if done(o)})
                    for c in record['components']:
                        if c.get('forming_operation_id'):self.assertEqual(c['forming_status']=='FORMED',done(c['forming_operation_id']))
                if previous:self.assertEqual(entry['material_id'],previous['material_id'])
                previous=end
        stage=read(MODULE/'outputs/stage_review_v01/P001/stage_data.json');self.assertEqual(self.v['plans'],stage['plans'])

    def test_07_all_frozen_candidates_hard_rules_and_sources(self):
        from src.constraints.v06.context import load_rule_context
        from src.constraints.v06.hard_constraints import HardConstraintEngine
        engine=HardConstraintEngine(load_rule_context(MODULE.parent/'data/parts/P001/钣金加工数据表_P001.xlsx'))
        for plan in self.v['plans']:self.assertTrue(engine.evaluate(plan['assignments']).to_dict()['feasible'])
        for path,digest in self.model['source_sha256'].items():self.assertEqual(sha(Path(path)),digest,path)
        self.assertFalse(self.v['summary']['volume_conservation_claimed']);self.assertFalse(self.v['summary']['physical_forming_validated'])

    def test_08_export_mapping_witnesses_for_browser(self):
        plan=self.v['plans'][0];fr=station_fractions(self.model,plan['assignments'],self.orders,'OP40',.37);ts=state_transforms(self.model,fr)
        components=[c for c in self.full['components'] if c.get('local_forming')]+[c for c in self.initial['components'] if c.get('restored_by_operation_id')]
        samples=[]
        for c in components:
            tri=self.v['geometries'][c['geometry_id']]['triangles'][0]
            samples.append({'component':c,'flat_points':tri,'world_points':map_points(self.model,c,tri,fr,ts)})
        write(OUT/'mapping_samples.json',{'plan_id':plan['plan_id'],'station_id':'OP40','tau':.37,'samples':samples})
        self.assertEqual(len(samples),65)


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(LocalFormingTests)
    with (OUT/'python_tests.log').open('w',encoding='utf-8') as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    write(OUT/'python_qa.json',{'tests_run':result.testsRun,'failures':[(str(t),e) for t,e in result.failures],'errors':[(str(t),e) for t,e in result.errors],'successful':result.wasSuccessful()})
    print((OUT/'python_tests.log').read_text(encoding='utf-8'));raise SystemExit(0 if result.wasSuccessful() else 1)
