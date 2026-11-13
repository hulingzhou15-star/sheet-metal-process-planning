from copy import deepcopy
import unittest
from information_extract.scripts.revise_p004_pre_punch_stations import revise, PAIRS


class P004PrePunchTests(unittest.TestCase):
    def test_only_capability_and_six_pairs_change(self):
        p={'part_id':'P004','revision':9,'station_rows':[{'station_id':'OP10','allowed_operation_types':'NONE'},
            {'station_id':'OP20','allowed_operation_types':'PUNCH, FLANGING'}], 'operations':[]}
        for child,parent in PAIRS.items():
            p['operations'] += [dict(operation_id=child,operation_type='PRE_PUNCH',cad_parent_operation_id=parent,source_segment_ids=[child]),
                                dict(operation_id=parent,operation_type='FLANGING',cad_parent_operation_id='O014',source_segment_ids=[parent])]
        before=deepcopy(p);after=revise(p);self.assertEqual(p,before)
        self.assertEqual(after['station_rows'][0]['allowed_operation_types'],'PUNCH')
        self.assertEqual(after['station_rows'][1],p['station_rows'][1])
        by={o['operation_id']:o for o in after['operations']}
        for old in p['operations']:
            new=deepcopy(by[old['operation_id']]);station=new.pop('expert_station')
            self.assertEqual(new,old);self.assertEqual(station,'OP10' if old['operation_type']=='PRE_PUNCH' else 'OP20')
        with self.assertRaises(ValueError):revise(after)


if __name__=='__main__':unittest.main()
