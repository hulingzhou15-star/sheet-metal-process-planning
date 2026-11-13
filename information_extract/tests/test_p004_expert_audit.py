import unittest
from information_extract.scripts.audit_p004_expert_assignments import distinct_line_matches


class P004AuditTests(unittest.TestCase):
    def line(self, y, sid): return {'kind':'LINE','points':[[0,y],[10,y]],'source_id':sid}

    def test_no_reuse_and_no_blanket_tolerance(self):
        source=[self.line(0,'a'),self.line(.06,'b')]
        targets=[self.line(.04374,'t1'),self.line(.09977,'t2')]
        matches=distinct_line_matches(source,targets,.05)
        self.assertEqual(len(matches),2)
        self.assertEqual(len({m['expert']['source_id'] for m in matches}),2)
        self.assertEqual(len(distinct_line_matches(source,targets[:1],.05)),1)
        self.assertEqual(distinct_line_matches(source,targets,.0001),[])


if __name__=='__main__': unittest.main()
