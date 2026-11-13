import json
import unittest
from pathlib import Path
from information_extract.extract_app.drawing import load_drawing
from information_extract.extract_app.source_supplement import _translated, _residual

M = Path(__file__).resolve().parents[1]
def read(p):
    return json.loads(p.read_text(encoding='utf-8'))


class P004RevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = read(M/'configs/P004.json')
        cls.manifest = read(M/cls.config['confirmed_geometry_revision'])
        cls.after = load_drawing(M/cls.config['source_file'], cls.config)
        old = dict(cls.config)
        old.pop('confirmed_geometry_revision')
        cls.before = load_drawing(M/old['source_file'], old)

    def test_all_existing_bindings_and_bend_lines_unchanged(self):
        index = {s['id']:s for s in self.after['segments']}
        old = {s['id']:s for s in self.before['segments']}
        for op in read(M/'projects/P004/project.json')['operations']:
            for sid in op['source_segment_ids']:
                self.assertEqual(index[sid],old[sid])
        for row in self.before['segments']:
            if row['segment_type']=='LINE' and row['linetype'].upper() in ('DASHED','HIDDEN'):
                self.assertEqual(index[row['id']],row)

    def test_reference_coverage_and_intentional_process_extras(self):
        config = {k:self.config[k] for k in ('part_id','coordinate_mode','material_block','geometry_tolerance','parallel_tolerance','confirmed_source_units')}
        reference = load_drawing(M/self.manifest['reference_file'],config)
        rows = [_translated(s,self.manifest['registration']['translation_xy']) for s in reference['segments']]
        for row in rows:
            self.assertTrue(any(_residual(row,s)<1e-6 for s in self.after['segments']))
        extras = [s['id'] for s in self.after['segments'] if not any(_residual(s,r)<1e-6 for r in rows)]
        self.assertEqual(set(extras),set(self.manifest['preserved_process_sources']))
        self.assertEqual(len(self.after['segments']),644)

    def test_removed_flange_groups_and_added_holes(self):
        circles = [s for s in self.after['segments'] if s['segment_type']=='CIRCLE']
        for x in (3512.644910990819,3622.644910990819):
            self.assertFalse(any(abs(s['center_x']-x)<1e-6 and abs(s['center_y']-3960.425380134588)<.001 for s in circles))
        for x in (3501.834910990819,3674.834910990819):
            for y in (3957.525380134588,3989.425380134588):
                self.assertTrue(any(abs(s['center_x']-x)<.001 and abs(s['center_y']-y)<.001 and abs(s['radius']-1.485)<1e-6 for s in circles))

    def test_four_added_bend_lines_have_source_provenance(self):
        lines=[s for s in self.after['source_revision']['added_segments'] if s['linetype'].upper()=='DASHED']
        self.assertEqual(len(lines),4)
        self.assertTrue(all(s['segment_type']=='LINE' and s['provenance']['source_file']=='sources/P004/P004_1.dxf' for s in lines))
        self.assertEqual(sorted(round(s['bbox'][2]-s['bbox'][0],4) for s in lines),[10,10,19,19])


if __name__=='__main__':
    unittest.main()
