"""Regression tests for schedule-driven material reconstruction."""
import tempfile
import unittest
from pathlib import Path

from information_extract.three_d.allocation_material_states import event_schedule, rebuild
from information_extract.three_d.local_forming import box, save_shape, load_shape
from information_extract.three_d.local_collision import volume
from information_extract.three_d.artifacts import write
from information_extract.scripts.check_local_forming_collision import make_samples


class ScheduleTests(unittest.TestCase):
    stations=[dict(station_id='A',order=1),dict(station_id='B',order=2)]

    def test_simultaneous_events_only_switch_at_completion(self):
        rows=event_schedule([dict(plan_id='x',assignments={'P':'B','F':'B'})],self.stations,['P'],['F'],{})
        self.assertEqual([(r['executed_removal_ids'],r['executed_forming_ids']) for r in rows],
                         [([],[]),([],[]),([],[]),(['P'],['F'])])

    def test_pre_hole_same_station_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'strictly'):
            event_schedule([dict(plan_id='x',assignments={'P':'B','F':'B'})],self.stations,['P'],['F'],{'F':'P'})

    def test_missing_event_and_unknown_station_rejected(self):
        for a in ({'F':'A'},{'P':'Z','F':'B'}):
            with self.assertRaises(ValueError):
                event_schedule([dict(plan_id='x',assignments=a)],self.stations,['P'],['F'],{})

    def test_duplicate_identity_rejected(self):
        p=dict(plan_id='x',assignments={'P':'A'})
        with self.assertRaises(ValueError):event_schedule([p,p],self.stations,['P'],[],{})

    def test_motion_samples_use_shared_progress(self):
        v=dict(stations=self.stations,model={'bends':[{'operation_id':'X'},{'operation_id':'Y'}]})
        samples=make_samples(v,{'assignments':{'X':'B','Y':'B'}},4)
        self.assertEqual(samples,[('A','entry',0.),('A','completed',1.),('B','entry',0.),
            ('B','motion',.25),('B','motion',.5),('B','motion',.75),('B','completed',1.)])

    def test_nonbend_move_changes_actual_solid_and_keeps_final(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source';out=Path(folder)/'out'
            save_shape(source/'brep/domain_R.brep',box([0,0,0],[2,2,1]))
            save_shape(source/'brep/tool_P.brep',box([0,0,-1],[1,2,2]))
            write(source/'flanging_step_inventory.json',{'operations':[]})
            c=dict(component_id='R',kind='panel',region_id='R')
            v=dict(variants=[dict(executed_removal_ids=[],executed_forming_ids=[],components=[c])],
                removal_coverage=[dict(operation_id='P',components=[{'component_id':'R'}])],
                step_comparison={},stations=self.stations,geometries={},summary={})
            plans=[dict(plan_id='early',assignments={'P':'A'}),dict(plan_id='late',assignments={'P':'B'})]
            new=rebuild(v,plans,source,out)
            variants={m['material_id']:m for m in new['variants']}
            def solid_volume(pid,sid,phase):
                rec=next(r for r in new['material_states'] if (r['plan_id'],r['station_id'],r['phase'])==(pid,sid,phase))
                c=variants[rec['material_id']]['components'][0]
                return volume(load_shape(out/new['geometries'][c['geometry_id']]['brep_file']))
            self.assertAlmostEqual(solid_volume('early','A','entry'),4.)
            self.assertAlmostEqual(solid_volume('early','A','completed'),2.)
            self.assertAlmostEqual(solid_volume('late','A','completed'),4.)
            self.assertAlmostEqual(solid_volume('late','B','entry'),4.)
            self.assertAlmostEqual(solid_volume('late','B','completed'),2.)
            self.assertEqual(len(new['variants']),2)


if __name__=='__main__':unittest.main()
