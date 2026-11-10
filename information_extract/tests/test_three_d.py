"""Independent facts, registration, mesh provenance and state-kernel tests."""
from copy import deepcopy
from io import BytesIO
import json
import math
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED
import numpy as np

from information_extract.three_d.facts import read_facts
from information_extract.three_d.association import register, apply_transform, bend_proposals
from information_extract.three_d.state import rotation_about_axis, transform_point, station_state, readiness

MODULE = Path(__file__).resolve().parents[1]
ROOT = MODULE.parent
OUT = MODULE/'outputs/three_d_state_v01/P001'
CONFIG = json.loads((MODULE/'configs/three_d_state_v01.json').read_text(encoding='utf-8'))
NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def stripped_workbook(source, output):
    """Mutate only a test copy: delete Sheet5 and poison expert cells + Sheet4."""
    with ZipFile(source) as incoming, ZipFile(output,'w',ZIP_DEFLATED) as dest:
        for entry in incoming.infolist():
            content = incoming.read(entry.filename)
            if entry.filename == 'xl/worksheets/sheet5.xml':
                continue
            if entry.filename == 'xl/workbook.xml':
                tree = ET.fromstring(content)
                sheets = tree.find(NS+'sheets')
                for sheet in list(sheets):
                    if sheet.attrib['name']=='Sheet5':
                        sheets.remove(sheet)
                content=ET.tostring(tree)
            elif entry.filename == 'xl/worksheets/sheet1.xml':
                tree=ET.fromstring(content)
                for cell in tree.iter(NS+'c'):
                    if cell.attrib['r'].startswith('I') and cell.attrib['r']!='I1':
                        address=cell.attrib['r'];cell.clear();cell.attrib.update(r=address,t='inlineStr')
                        ET.SubElement(ET.SubElement(cell,NS+'is'),NS+'t').text='INVALID_EXPERT_MUST_NOT_BE_READ'
                content=ET.tostring(tree)
            elif entry.filename == 'xl/worksheets/sheet4.xml':
                content=b'<invalid-unread-sheet4>'
            dest.writestr(entry,content)


class FactsTests(unittest.TestCase):
    def test_actual_coverage_and_no_supervision(self):
        f=read_facts(ROOT/'data/parts/P001/钣金加工数据表_P001.xlsx','P001')
        self.assertEqual(f['coverage']['operation_count'],224)
        self.assertEqual(f['coverage']['with_2d_geometry'],211)
        self.assertNotIn('expert_station',json.dumps(f))
        self.assertEqual(f['read_scope']['sheets'],['Sheet1','Sheet2','Sheet3'])

    def test_deleted_sheet5_poisoned_expert_and_sheet4_are_irrelevant(self):
        original=ROOT/'data/parts/P001/钣金加工数据表_P001.xlsx'
        a=read_facts(original,'P001')
        with tempfile.TemporaryDirectory(dir=MODULE/'outputs/test_tmp') as temp:
            path=Path(temp)/'no_sheet5.xlsx';stripped_workbook(original,path)
            b=read_facts(path,'P001')
        self.assertEqual(a,b)
        asset=json.loads((OUT/'asset.json').read_text())
        self.assertEqual(register(a,asset,CONFIG),register(b,asset,CONFIG))

    def test_foreign_part_is_rejected(self):
        with self.assertRaises(ValueError):
            read_facts(ROOT/'data/parts/P001/钣金加工数据表_P001.xlsx','P002')


class GeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.asset=json.loads((OUT/'asset.json').read_text())
        cls.facts=json.loads((OUT/'facts.json').read_text(encoding='utf-8'))

    def test_mesh_face_provenance_and_symmetric_adjacency(self):
        a=self.asset
        self.assertEqual(len(a['mesh']['positions']),len(a['mesh']['triangle_face_ids'])*9)
        self.assertEqual(len(a['faces']),1247)
        for f in a['faces']:
            n=f['triangle_count'];start=f['triangle_start']
            self.assertTrue(all(i==f['face_id'] for i in a['mesh']['triangle_face_ids'][start:start+n]))
            for neighbor in f['neighbor_face_ids']:
                self.assertIn(f['face_id'],a['faces'][neighbor-1]['neighbor_face_ids'])

    def test_base_registration_has_seven_noncollinear_anchors(self):
        r=register(self.facts,self.asset,CONFIG)
        self.assertEqual(r['status'],'BASE_FRAME_SUPPORTED')
        self.assertEqual(r['anchor_count'],7)
        self.assertEqual(r['determinant'],1)
        self.assertLess(r['max_residual_mm'],CONFIG['registration']['position_tolerance_mm'])

    def test_registration_predicts_held_out_anchor(self):
        facts=deepcopy(self.facts)
        anchor=next(o for o in facts['operations'] if o['operation_id']=='O190')
        facts['operations'].remove(anchor)
        r=register(facts,self.asset,CONFIG)
        self.assertEqual(r['status'],'BASE_FRAME_SUPPORTED')
        s=anchor['segments'][0]
        predicted=apply_transform([s['center_x'],s['center_y']],r['matrix'],r['translation_mm'])
        np.testing.assert_allclose(predicted,[338.9,-1.1],atol=CONFIG['registration']['position_tolerance_mm'])

    def test_ambiguous_sparse_registration_not_accepted(self):
        facts=deepcopy(self.facts)
        facts['operations']=[o for o in facts['operations'] if o['operation_id']=='O186']
        self.assertEqual(register(facts,self.asset,CONFIG)['status'],'AMBIGUOUS')

    def test_bend_proposals_are_not_motion_definitions(self):
        r=register(self.facts,self.asset,CONFIG)
        rows=bend_proposals(self.facts,self.asset,r,CONFIG)
        self.assertEqual(len(rows),59)
        self.assertTrue(any(row['candidates'] for row in rows))
        for row in rows:
            for c in row['candidates']:
                self.assertEqual(c['status'],'PROPOSAL_NOT_MOTION')
                self.assertIsNone(c['moving_side'])
                self.assertIsNone(c['angle_deg'])
                for a,b in zip(c['base_adjacency_path'],c['base_adjacency_path'][1:]):
                    self.assertIn(b,self.asset['faces'][a-1]['neighbor_face_ids'])

    def test_readiness_cannot_treat_unknown_as_pass(self):
        result=readiness(self.facts,{})
        self.assertFalse(result['ready_for_candidate_3d_filter'])
        self.assertEqual(result['status'],'UNKNOWN')
        self.assertEqual(len(result['missing_bindings']),224)


class StateTests(unittest.TestCase):
    def regions(self):
        return [{'region_id':'base','parent_region_id':None},
                {'region_id':'parent','parent_region_id':'base','joint':{'axis_point_mm':[0,0,0],'axis_direction':[0,0,1]}},
                {'region_id':'child','parent_region_id':'parent','joint':{'axis_point_mm':[1,0,0],'axis_direction':[0,0,1]}}]

    def test_rotation_around_offset_axis(self):
        m=rotation_about_axis([1,0,0],[0,0,1],90)
        np.testing.assert_allclose(transform_point(m,[2,0,0]),[1,1,0],atol=1e-10)

    def test_nested_material_motion_carries_unexecuted_feature(self):
        s=station_state(self.regions(),{'parent':lambda t:90,'child':lambda t:0},1)
        np.testing.assert_allclose(transform_point(s['transforms']['child'],[2,0,0]),[0,2,0],atol=1e-10)

    def test_simultaneous_nested_joints_use_common_tau(self):
        fn={'parent':lambda t:90*t,'child':lambda t:90*t*t}
        a=station_state(self.regions(),fn,.5)
        b=station_state(list(reversed(self.regions())),dict(reversed(list(fn.items()))),.5)
        self.assertEqual(a['angles_deg'],{'parent':45,'child':22.5})
        np.testing.assert_allclose(a['transforms']['child'],b['transforms']['child'])

    def test_missing_motion_is_not_stationary(self):
        with self.assertRaises(ValueError):
            station_state(self.regions(),{'parent':lambda t:0},.5)

    def test_cycle_is_rejected(self):
        regions=self.regions();regions[0]['parent_region_id']='child'
        with self.assertRaises(ValueError):
            station_state(regions,{'parent':lambda t:0,'child':lambda t:0},.5)

    def test_global_pose_does_not_change_internal_transform(self):
        pose=np.eye(4);pose[:3,3]=[100,20,30]
        fn={'parent':lambda t:90,'child':lambda t:0}
        a=station_state(self.regions(),fn,1)
        b=station_state(self.regions(),fn,1,pose)
        np.testing.assert_allclose(np.linalg.inv(b['transforms']['base'])@b['transforms']['child'],a['transforms']['child'])

    def test_invalid_tau_axis_pose_and_unknown_parent_rejected(self):
        for tau in [-.1,1.1,float('nan')]:
            with self.assertRaises(ValueError):station_state([],{},tau)
        with self.assertRaises(ValueError):rotation_about_axis([0,0,0],[0,0,0],90)
        with self.assertRaises(ValueError):station_state([],{},0,np.zeros((4,4)))
        with self.assertRaises(ValueError):station_state([{'region_id':'x','parent_region_id':'missing'}],{},0)


if __name__=='__main__':
    unittest.main()
