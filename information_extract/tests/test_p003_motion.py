"""Regression checks for the overfold and return on one physical P003 hinge."""
import unittest

import numpy as np

from information_extract.scripts.check_p003_candidate_motion import setup
from information_extract.three_d.p003_motion import frame, samples


class P003MotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model, payload, _, _, _ = setup()
        cls.plans = payload['plans']

    def test_shared_joint_and_carried_child_follow_both_increments(self):
        for plan in self.plans:
            assignments = plan['assignments']
            first, second = assignments['O069'],assignments['O120']
            states = [frame(self.model,plan,sid,phase,tau) for sid,phase,tau in (
                (first,'entry',0.),(first,'motion',.5),(first,'completed',1.),
                (second,'entry',0.),(second,'motion',.5),(second,'completed',1.))]
            self.assertEqual([r['joint_angles_deg']['J297'] for r in states],
                             [0.,67.5,135.,135.,112.5,90.])
            for r,angle in zip(states,(0.,67.5,135.,135.,112.5,90.)):
                # Local relation isolates the tested hinge from unrelated motions.
                pa,ch = (np.asarray(r['transforms'][x]) for x in ('P104_105','P298_299'))
                relative = np.linalg.inv(pa)@ch
                np.testing.assert_allclose(relative[0,0],np.cos(np.deg2rad(angle-90.)),atol=1e-10)
            last = frame(self.model,plan,'OP70','completed',1.)
            self.assertEqual(last['current_operation_ids'],[])
            for matrix in last['transforms'].values():
                np.testing.assert_allclose(matrix,np.eye(4),atol=1e-10)

    def test_illegal_same_station_shared_actions_fail(self):
        plan = self.plans[0]
        modified = {**plan,'assignments':{**plan['assignments'],'O120':plan['assignments']['O069']}}
        with self.assertRaisesRegex(ValueError,'strictly precede'):
            list(samples(self.model,modified,24))

    def test_one_progress_for_concurrent_bends_and_events_at_completion(self):
        p = self.plans[0]
        r = frame(self.model,p,'OP30','motion',.25)
        active = set(r['current_operation_ids'])
        self.assertGreater(len(active),1)
        for j in self.model['joints']:
            for step in j['steps']:
                if step['operation_id'] in active:
                    self.assertAlmostEqual(r['joint_angles_deg'][j['joint_id']],step['increment_deg']/4)
        entry = frame(self.model,p,'OP20','entry',0.)
        moving = frame(self.model,p,'OP20','motion',.5)
        end = frame(self.model,p,'OP20','completed',1.)
        self.assertEqual(entry['executed_removal_ids'],moving['executed_removal_ids'])
        self.assertEqual(entry['executed_forming_ids'],moving['executed_forming_ids'])
        self.assertEqual(len(end['executed_removal_ids']),59)
        self.assertEqual(len(end['executed_forming_ids']),51)
        self.assertNotIn(('OP20','motion',.5),list(samples(self.model,p,24)))


if __name__=='__main__':
    unittest.main()
