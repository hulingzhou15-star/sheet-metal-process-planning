from copy import deepcopy
import unittest
from extract_app.source_subsegments import confirmed_line_subsegment


class SourceSubsegmentTests(unittest.TestCase):
    def setUp(self):
        self.parent={'id':'PARENT','segment_type':'LINE','start_x':2,'start_y':3,'end_x':12,'end_y':23}

    def test_exact_part_of_slanted_source_and_parent_unchanged(self):
        before=deepcopy(self.parent)
        child=confirmed_line_subsegment(self.parent,.2,.6,1e-6)
        self.assertEqual((child['start_x'],child['start_y'],child['end_x'],child['end_y']),(4,7,8,15))
        self.assertEqual(child['derivation']['parent_source_segment_id'],'PARENT')
        self.assertEqual(self.parent,before)

    def test_repeated_selection_has_stable_id_and_other_interval_is_distinct(self):
        a=confirmed_line_subsegment(self.parent,.2,.6,1e-6)
        self.assertEqual(a['id'],confirmed_line_subsegment(self.parent,.2,.6,1e-6)['id'])
        self.assertNotEqual(a['id'],confirmed_line_subsegment(self.parent,.3,.6,1e-6)['id'])

    def test_no_extrapolation_or_inverted_interval(self):
        for a,b in [(-.1,.2),(.8,1.1),(.5,.5),(.6,.2)]:
            with self.subTest(interval=(a,b)),self.assertRaises(ValueError):
                confirmed_line_subsegment(self.parent,a,b,1e-6)

    def test_arc_and_unidentified_parent_rejected(self):
        for parent in [{**self.parent,'segment_type':'ARC'},{**self.parent,'id':None}]:
            with self.assertRaises(ValueError):confirmed_line_subsegment(parent,.2,.6,1e-6)

    def test_invalid_or_zero_length_values_rejected(self):
        for a,b in [(True,.6),(.2,float('nan')),(.2,.2+1e-12)]:
            with self.assertRaises(ValueError):confirmed_line_subsegment(self.parent,a,b,1e-6)


if __name__=='__main__':unittest.main()
