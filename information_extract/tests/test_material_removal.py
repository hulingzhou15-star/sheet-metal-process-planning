import json,math,unittest
from pathlib import Path
import numpy as np
from information_extract.three_d.material_removal import source_prism,active_cuts,cut_panels
from information_extract.three_d.local_collision import volume,transformed
from information_extract.three_d.panel_collision import narrow_pair
from information_extract.three_d.state import rotation_about_axis
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox,BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Pnt,gp_Ax2,gp_Dir

CFG={'contact_tolerance_mm':1e-6,'volume_tolerance_mm3':1e-6}
OUT=Path(__file__).resolve().parents[1]/'outputs/staged_removal_v01/P001'


class RemovalTests(unittest.TestCase):
    def fixture(self):
        op={'segments':[{'seq':1,'segment_type':'CIRCLE','center_x':5,'center_y':5,'radius':2}]}
        cutter=source_prism(op,{'registration_matrix':[[1,0],[0,1]],'registration_translation_mm':[0,0]},0,np.eye(4),[0,0,.8])
        base={'p':BRepPrimAPI_MakeBox(10,10,.8).Shape()}
        event={'operation_id':'hole','wire_id':'w','region_id':'p'}
        return base,{'w':cutter},event

    def test_hole_volume_and_detection(self):
        base,cutters,event=self.fixture();after,ev=cut_panels(base,cutters,[event])
        self.assertAlmostEqual(ev[0]['removed_mm3'],math.pi*4*.8,places=6)
        pin=BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(5,5,0),gp_Dir(0,0,1)),1,.8).Shape()
        self.assertEqual(narrow_pair(base['p'],pin,CFG)['status'],'OVERLAP')
        self.assertEqual(narrow_pair(after['p'],pin,CFG)['status'],'SEPARATED')
        self.assertAlmostEqual(volume(base['p']),80) # original state immutable

    def test_cut_follows_rigid_motion(self):
        base,cutters,event=self.fixture();after,_=cut_panels(base,cutters,[event])
        pin=BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(5,5,0),gp_Dir(0,0,1)),1,.8).Shape()
        mat=rotation_about_axis([1,2,3],[0,1,0],73)
        self.assertEqual(narrow_pair(transformed(after['p'],mat),transformed(pin,mat),CFG)['status'],'SEPARATED')
        self.assertAlmostEqual(volume(transformed(after['p'],mat)),volume(after['p']),places=6)

    def test_station_order_and_simultaneous_boundary(self):
        events=[{'operation_id':o} for o in ['a','b','c']];assign={'a':'last','b':'first','c':'middle'}
        orders={'last':30,'first':10,'middle':20}
        for phase in ['entry','during']:self.assertEqual(active_cuts(events,assign,orders,'middle',phase),[events[1]])
        self.assertEqual(active_cuts(events,assign,orders,'middle','completed'),events[1:])
        with self.assertRaises(ValueError):active_cuts(events,assign,orders,'middle','pre_punch_first')

    def test_duplicate_cut_rejected(self):
        base,cutters,event=self.fixture()
        with self.assertRaises(ValueError):cut_panels(base,cutters,[event,event])

    def test_actual_audit_and_state_continuity(self):
        audit=json.loads((OUT/'mapping_audit.json').read_text(encoding='utf-8'))
        accepted=audit['accepted'];self.assertGreaterEqual(len(accepted),7)
        self.assertEqual(len({e['wire_id'] for e in accepted}),len(accepted))
        self.assertEqual(len(audit['operations']),224)
        states=json.loads((OUT/'material_states.json').read_text(encoding='utf-8'))
        for previous,current in zip(states[2::3],states[3::3]):self.assertEqual(previous['material_id'],current['material_id'])
        for entry,during in zip(states[::3],states[1::3]):self.assertEqual(entry['material_id'],during['material_id'])
        report=json.loads((OUT/'report.json').read_text(encoding='utf-8'))
        self.assertEqual(len(report['groups']),62);self.assertFalse(report['summary']['candidate_filter_enabled'])

    def test_remaining_mapping_diagnostic_is_explicit_and_non_destructive(self):
        d=json.loads((OUT/'remaining_mapping_diagnostic.json').read_text(encoding='utf-8'))
        self.assertEqual(d['classification_counts'],{
            'PARTIAL_GEOMETRIC_MATCH':26,
            'PANEL_ASSOCIATION_UNRESOLVED':5,
            'CONTAINED_IN_LARGER_CAD_REGION':11})
        self.assertEqual(len(d['rows']),42)
        self.assertIn('Only the prior 12 exact unique matches',d['interpretation'])


if __name__=='__main__':unittest.main()
