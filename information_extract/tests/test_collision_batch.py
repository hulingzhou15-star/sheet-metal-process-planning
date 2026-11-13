"""Guard adaptive sampling, resume identity and conservative batch reporting."""
import unittest
from information_extract.scripts.check_local_forming_collision import make_samples, checked_checkpoint
from information_extract.scripts.check_all_local_forming_collisions import summarize


class CollisionBatchTests(unittest.TestCase):
    def setUp(self):
        self.viewer={'model':{'bends':[{'operation_id':'B1'},{'operation_id':'B2'}]},
                     'stations':[{'station_id':'OP70','order':7},{'station_id':'OP10','order':1}]}
        self.plan={'assignments':{'B1':'OP10','B2':'OP10'}}

    def test_motion_follows_plan_outside_historical_stations(self):
        samples=make_samples(self.viewer,self.plan)
        self.assertEqual(len(samples),27)
        self.assertEqual({s for s,p,t in samples if p=='motion'},{'OP10'})
        self.assertEqual(samples[-2:], [('OP70','entry',0.),('OP70','completed',1.)])
        self.plan['assignments']['B2']='OP70'
        self.assertEqual(len(make_samples(self.viewer,self.plan)),50)

    def test_same_station_bends_share_one_schedule(self):
        motion=[t for s,p,t in make_samples(self.viewer,self.plan) if p=='motion']
        self.assertEqual(motion,[k/24 for k in range(1,24)])

    def test_checkpoint_identity_and_prefix(self):
        samples=make_samples(self.viewer,self.plan)
        runs=[dict(zip(('station_id','phase','tau'),s)) for s in samples[:3]]
        checkpoint={'signature':'a','runs':runs,'kernel_calls':4}
        self.assertEqual(checked_checkpoint(checkpoint,'a',samples),(runs,4))
        with self.assertRaises(ValueError):checked_checkpoint(checkpoint,'b',samples)
        with self.assertRaises(ValueError):checked_checkpoint(checkpoint,'a',samples[1:])

    def test_unknown_does_not_hide_positive_overlap(self):
        report={'plan_id':'test','sample_count':1,'kernel_calls':1,'local_forming_overlap_samples':[],
                'runs':[{'station_id':'OP10','phase':'motion','pair_count':1,
                         'counts':{'UNKNOWN_KERNEL_FAILURE':1},
                         'issues':[{'intersection_volume_mm3':2.}]}]}
        summary=summarize(report)
        self.assertEqual(summary['unknown_pair_states'],1)
        self.assertEqual(summary['positive_volume_pair_states'],1)

    def test_invalid_subdivision(self):
        with self.assertRaises(ValueError):make_samples(self.viewer,self.plan,1)


if __name__=='__main__':unittest.main()
