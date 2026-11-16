"""P002 authoritative facts, expert isolation and composite action semantics."""
from copy import deepcopy
import unittest
from information_extract.three_d.artifacts import ROOT,read
from information_extract.three_d.project_allocation_facts import extract_facts,hard_context,solve_hard
from information_extract.extract_app.drawing import load_drawing
from information_extract.scripts.prepare_p002_generation import topology


class P002FactsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        m=ROOT/'information_extract';cls.project=read(m/'projects/P002/project.json');cfg=read(m/'configs/P002.json')
        cls.drawing=load_drawing(m/cfg['source_file'],cfg);cls.facts=extract_facts(cls.project,cls.drawing)

    def test_activity_and_draw_holes_are_not_split(self):
        ops={o['operation_id']:o for o in self.facts['operations']}
        self.assertEqual(len(ops),132)
        self.assertFalse({'O042','O043','O106','O107'}&ops.keys())
        self.assertEqual(ops['O105']['operation_type'],'DRAW_FORM')
        self.assertEqual(set(ops['O105']['composite_operation_ids']),{'O106','O107'})
        self.assertEqual(ops['O136']['feature_type'],'BEND_Z')

    def test_expert_and_old_influence_changes_cannot_change_facts_or_witness(self):
        p=deepcopy(self.project)
        for o in p['operations']:
            o['expert_station']='CORRUPT';o['region']={'affected_area_proxy':-1};o['notes']='ignored'
        other=extract_facts(p,self.drawing)
        self.assertEqual(other,self.facts)
        self.assertEqual(solve_hard(other)[1]['assignments'],solve_hard(self.facts)[1]['assignments'])

    def test_h7_is_strict_and_draw_capability_is_real(self):
        c,w=solve_hard(self.facts)
        self.assertTrue(w['feasible']);self.assertEqual(w['status'],'OPTIMAL')
        self.assertFalse(any(r.constraint_type=='H6' for r in c.precedence_relations))
        orders={s.station_id:s.order for s in c.ordered_stations}
        for r in c.precedence_relations:self.assertLess(orders[w['assignments'][r.predecessor_id]],orders[w['assignments'][r.operation_id]])
        for o in ('O063','O105','O108'):
            self.assertEqual([s.station_id for s in c.allowed_stations(o)],['OP10'])

    def test_excluded_parent_is_rejected_not_deleted(self):
        p=deepcopy(self.project);next(o for o in p['operations'] if o['operation_id']=='O015')['cad_parent_operation_id']='O042'
        with self.assertRaisesRegex(ValueError,'excluded'):extract_facts(p,self.drawing)

    def test_broken_composite_geometry_rejected(self):
        p=deepcopy(self.project);next(o for o in p['operations'] if o['operation_id']=='O105')['source_segment_ids'].pop()
        with self.assertRaisesRegex(ValueError,'coverage'):extract_facts(p,self.drawing)

    def test_trim_is_excluded_without_requiring_geometry(self):
        p=deepcopy(self.project);p['operations'].append(dict(operation_id='T',operation_type='TRIM',source_segment_ids=[]))
        f=extract_facts(p,self.drawing)
        self.assertNotIn('T',hard_context(f).operations)

    def test_not_later_constraint_is_consumed(self):
        p=deepcopy(self.project);o=next(o for o in p['operations'] if o['operation_id']=='O015');o['not_later_than_operation_ids']=['O020']
        c,w=solve_hard(extract_facts(p,self.drawing));self.assertTrue(w['feasible'])
        self.assertLessEqual(c.stations[w['assignments']['O015']].order,c.stations[w['assignments']['O020']].order)

    def test_fact_tampering_rejected(self):
        f=deepcopy(self.facts);f['operations'][0]['operation_type']='BEND'
        with self.assertRaisesRegex(ValueError,'fingerprint'):hard_context(f)

    def test_cycle_not_coerced_to_arbitrary_tree(self):
        m=dict(regions=[dict(region_id=x,skin_face_ids=[i]) for i,x in enumerate('ABC')],
            joints=[dict(joint_id=str(i),region_ids=list(pair)) for i,pair in enumerate(('AB','BC','CA'))])
        r=topology(m,0)
        self.assertEqual(r['base_component_cycle_rank'],1)
        self.assertFalse(r['arbitrary_spanning_tree_selected'])
        self.assertFalse(r['branches'][0]['single_hinge_subtree_supported'])

    def test_derived_curved_sides_follow_o069_calibration(self):
        from information_extract.scripts.probe_p002_curved_sides import probe
        m=ROOT/'information_extract/outputs/multi_part_step_v01/P002'
        result=probe(self.project,self.drawing,read(m/'asset.json'),read(m/'base_registration.json'))
        self.assertEqual({r['operation_id']:r['surface_side'] for r in result['rows']},
                         {'O069':'ROUGH','O088':'SMOOTH','O096':'SMOOTH','O104':'ROUGH'})
        self.assertTrue(all(r['matched_source_count']>=3 for r in result['rows']))

    def test_complete_topology_proposals_do_not_claim_solid_volume(self):
        from information_extract.three_d.material import infer_material
        from information_extract.scripts.prepare_p002_generation import straight_branch_proposals,carried_region_proposals
        from information_extract.scripts.probe_p002_curved_sides import probe
        m=ROOT/'information_extract';base=m/'outputs/multi_part_step_v01/P002'
        asset,reg=read(base/'asset.json'),read(base/'base_registration.json')
        material=infer_material(asset,read(m/'configs/material_state_v01.json'));structure=topology(material,468)
        straight=straight_branch_proposals(material,structure,asset,reg,self.project,self.drawing,read(m/'configs/P002.json'))
        sides=probe(self.project,self.drawing,asset,reg)
        result=carried_region_proposals(material,structure,straight,sides)
        self.assertEqual(result['operation_count'],17)
        self.assertFalse(result['exact_solid_volumes_available'])
        self.assertFalse(result['p1_basis_compiled'])
        rows={r['operation_id']:r for r in result['rows']}
        self.assertEqual(set(rows['O136']['joint_ids']),{'J531','J64'})
        self.assertEqual(len(rows['O136']['region_ids']),2)
        self.assertTrue(set(rows['O094']['region_ids'])<set(rows['O088']['region_ids']))


if __name__=='__main__':unittest.main()
