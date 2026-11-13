import tempfile
from pathlib import Path
import unittest
from zipfile import ZipFile, ZIP_DEFLATED
from xml.etree import ElementTree as ET
from dataclasses import replace
import numpy as np

from information_extract.three_d.allocation_policy import (
    load_context, AllocationContext, AllocationEngine, enumerate_single_bend_moves,
)
from information_extract.three_d.allocation_metrics import exact_distance, relative_changes, MetricProbe
from information_extract.three_d.local_forming import box, save_shape
from information_extract.scripts.experiment_bend_allocation_3d import WORKBOOK, OUT, read
from src.inference.input import load_inference_context


class AllocationPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = load_context(WORKBOOK)
        cls.old = load_inference_context(WORKBOOK)
        cls.viewer = read(OUT/'viewer_data.json')
        cls.base = next(p['assignments'] for p in cls.viewer['plans'] if p['plan_id']=='NP2_001')

    def workbook_variant(self, folder, mode):
        target = Path(folder)/f'{mode}.xlsx'
        with ZipFile(WORKBOOK) as src, ZipFile(target,'w',ZIP_DEFLATED) as dst:
            for entry in src.infolist():
                if entry.filename=='xl/worksheets/sheet5.xml':
                    if mode=='missing':continue
                    dst.writestr(entry,b'INVALID XML - ORIGINAL SHEET5 MUST NEVER BE READ')
                elif entry.filename=='xl/worksheets/sheet1.xml' and mode=='expert':
                    # Poison expert cell type/content; fact columns remain intact.
                    tree=ET.fromstring(src.read(entry.filename));ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                    header=tree.find('s:sheetData/s:row',ns)
                    # Standard workbook expert column identified via the legacy header.
                    import openpyxl
                    wb=openpyxl.load_workbook(WORKBOOK,read_only=True,data_only=True)
                    headers=next(wb['Sheet1'].iter_rows(values_only=True));wb.close()
                    from openpyxl.utils import get_column_letter
                    col=get_column_letter(headers.index('expert_station')+1)
                    for row in tree.findall('s:sheetData/s:row',ns)[1:]:
                        for c in list(row):
                            if c.attrib['r'].rstrip('0123456789')==col:
                                c.clear();c.attrib.update(r=col+row.attrib['r'],t='e')
                                ET.SubElement(c,'{'+ns['s']+'}v').text='#REF!'
                    dst.writestr(entry,ET.tostring(tree))
                else:dst.writestr(entry,src.read(entry.filename))
        return target

    def test_missing_corrupt_sheet5_and_invalid_expert_do_not_affect_facts(self):
        with tempfile.TemporaryDirectory() as d:
            for mode in ('missing','corrupt','expert'):
                with self.subTest(mode=mode):
                    c=load_context(self.workbook_variant(d,mode))
                    self.assertEqual(c.facts_fingerprint,self.context.facts_fingerprint)
                    self.assertEqual(c.precedence_relations,self.context.precedence_relations)
                    self.assertTrue(AllocationEngine(c).evaluate(self.base).feasible)

    def test_original_h6_cannot_be_injected(self):
        with self.assertRaisesRegex(ValueError,'disabled'):
            AllocationContext(self.context.part_data,self.context.fixed_assignments,self.old.bend_influences)
        with self.assertRaises(TypeError):AllocationEngine(self.old)

    def test_h7_same_station_still_rejected(self):
        rel=self.context.feature_tree_relations[0]
        a={**self.base,rel.predecessor_id:self.base[rel.operation_id]}
        result=AllocationEngine(self.context).evaluate(a)
        self.assertFalse(result.feasible)
        self.assertTrue(any(v.constraint_type=='H7' and v.operation_ids==(rel.predecessor_id,rel.operation_id) for v in result.violations))
        self.assertNotIn('H6',result.to_dict()['violation_breakdown'])

    def test_explicit_precedence_capability_and_fixed_preserved(self):
        self.assertEqual(self.context.fixed_assignments,self.old.fixed_assignments)
        self.assertEqual(self.context.part_data.operations,self.old.part_data.operations)
        self.assertEqual(self.context.part_data.stations,self.old.part_data.stations)
        self.assertEqual(self.context.precedence_relations,tuple(r for r in self.old.precedence_relations if r.constraint_type!='H6'))
        rel=next(r for r in self.context.precedence_relations if r.constraint_type=='H3')
        a={**self.base,rel.predecessor_id:self.base[rel.operation_id]}
        self.assertIn('H3',{v.constraint_type for v in AllocationEngine(self.context).evaluate(a).violations})
        fixed=self.context.fixed_assignments[0]
        a={**self.base,fixed.operation_id:'OP70'}
        self.assertTrue({'H2','H5'}<={v.constraint_type for v in AllocationEngine(self.context).evaluate(a).violations})

    def test_complete_neighbourhood_and_no_implicit_p1(self):
        plans,rejected=enumerate_single_bend_moves(self.context,self.base,[b['operation_id'] for b in self.viewer['model']['bends']])
        self.assertEqual((len(plans),len(rejected)),(32,323))
        self.assertFalse(self.context.bend_influences)
        self.assertTrue(all('H6' not in p['hard']['active_constraints'] for p in plans))
        self.assertTrue(all('area_audit' not in p for p in plans))


class MetricTests(unittest.TestCase):
    def test_probe_measures_real_gap_and_labels_adjacent_omission(self):
        # Two siblings, only B moves; A is a distinct stationary obstacle.
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            save_shape(root/'a.brep',box([2.4,0,-.5],[3.4,1,0]))
            save_shape(root/'b.brep',box([1,0,-.5],[2,1,0]))
            def bend(joint,child,angle):
                return dict(joint_id=joint,operation_id=joint,parent_region_id='R',child_region_id=child,
                    start_flat_mm=[0,0,0],forward_flat=[1,0,0],axis_flat=[0,1,0],width_mm=1,
                    target_angle_deg=angle,geometric_offset_from_front_mm=0)
            model=dict(root_region_id='R',thickness_mm=.8,bends=[bend('J0','A',0),bend('J1','B',90)])
            components=[dict(component_id=k,geometry_id=k,region_id=k.upper(),kind='panel') for k in ('a','b')]
            viewer=dict(model=model,stations=[dict(station_id='S',order=1)],
                variants=[dict(material_id='M',components=components)],
                geometries={k:dict(brep_file=k+'.brep') for k in ('a','b')},
                material_states=[dict(plan_id='P',station_id='S',phase='entry',material_id='M')])
            plan=dict(plan_id='P',assignments={'J0':'S','J1':'S'})
            probe=MetricProbe(viewer,root);result=probe.evaluate(plan,steps=4)
            self.assertAlmostEqual(result['samples'][0]['observed_min_within_cap_mm'],.4,places=6)
            # The finite-thickness corner initially approaches the obstacle as
            # it rotates; the flat-state gap is NOT the path minimum.
            self.assertGreater(result['minimum_observed_clearance_mm'],0)
            self.assertLess(result['minimum_observed_clearance_mm'],.4)
            self.assertEqual(result['max_close_pairs'],[1,1,1,1])
            self.assertAlmostEqual(result['peak_moving_volume_fraction'],.5)
            self.assertGreater(result['volume_weighted_centroid_travel_mm'],0)
            self.assertEqual(result['error_count'],0)
            probe.adjacent.add(frozenset(('A','B')))
            omitted=probe.evaluate(plan,steps=4)
            self.assertIsNone(omitted['minimum_observed_clearance_mm'])
            self.assertEqual(omitted['max_close_pairs'],[0,0,0,0])

    def test_distance_separates_close_from_overlap(self):
        a=box([0,0,0],[1,1,1])
        self.assertAlmostEqual(exact_distance(a,box([1.4,0,0],[2,1,1])),.4)
        self.assertAlmostEqual(exact_distance(a,box([.5,0,0],[2,1,1])),0)

    def test_relative_rigid_pairs_ignored_for_soft_observable(self):
        i=np.eye(4);move=i.copy();move[0,3]=4
        self.assertFalse(relative_changes('a','b',[{'a':i,'b':i},{'a':move,'b':move}]))
        self.assertTrue(relative_changes('a','b',[{'a':i,'b':i},{'a':i,'b':move}]))


if __name__=='__main__':unittest.main()
