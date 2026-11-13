import unittest
from types import SimpleNamespace as S
from information_extract.scripts.check_p1_material_proxy import proxy_ordering, subtree_regions


class MaterialProxyTests(unittest.TestCase):
    def setUp(self):
        self.context=S(operations={k:S(operation_id=k,operation_type='BEND') for k in ('A','B')},
            precedence_reachability={'A':set(),'B':set()},stations={'S1':S(order=1),'S2':S(order=2)})

    def test_larger_earlier_counts_and_same_station_does_not(self):
        a=proxy_ordering(self.context,{'A':'S1','B':'S2'},{'A':100,'B':10})
        self.assertEqual(a['inversion_count'],1)
        b=proxy_ordering(self.context,{'A':'S1','B':'S1'},{'A':100,'B':10})
        self.assertEqual(b['inversion_count'],0)
        self.assertFalse(b['validated'])

    def test_hard_precedence_not_penalized(self):
        self.context.precedence_reachability['A'].add('B')
        a=proxy_ordering(self.context,{'A':'S1','B':'S2'},{'A':100,'B':10})
        self.assertEqual(a['hard_excluded_pairs'],[['A','B']])
        self.assertIsNone(a['inversion_rate'])

    def test_missing_and_nonfinite_magnitudes_rejected(self):
        for m in ({'A':100},{'A':100,'B':0},{'A':100,'B':float('nan')}):
            with self.assertRaises(ValueError):proxy_ordering(self.context,{'A':'S1','B':'S2'},m)

    def test_geometry_subtree_not_semantic_operation_tree(self):
        m={'regions':[{'region_id':k} for k in ('root','child','tip','sibling')],
           'bends':[{'parent_region_id':'root','child_region_id':'child'},
                    {'parent_region_id':'child','child_region_id':'tip'},
                    {'parent_region_id':'root','child_region_id':'sibling'}]}
        self.assertEqual(subtree_regions(m,'child'),{'child','tip'})


if __name__=='__main__':unittest.main()
