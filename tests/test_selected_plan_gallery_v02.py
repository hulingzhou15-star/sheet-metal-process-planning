import csv
import hashlib
import json
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]


class CompletePlanGalleryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out=ROOT/'outputs/selected_plans_v02'
        cls.original=[json.loads(s) for s in (ROOT/'outputs/neighborhood_penalty_v02/selected_candidates.jsonl').read_text(encoding='utf-8').splitlines()]
        html=(cls.out/'selected_plans.html').read_text(encoding='utf-8')
        cls.payload=json.loads(re.search(r'<script id="payload" type="application/json">(.*?)</script>',html,re.S).group(1))

    def test_all_16_complete_original_assignments_and_order(self):
        p=self.payload
        self.assertEqual(len(p['plans']),16)
        self.assertEqual([r['plan_id'] for r in p['plans']],[r['plan_id'] for r in self.original])
        for visible,original in zip(p['plans'],self.original):
            self.assertEqual(visible['plan_hash'],original['plan_hash']);self.assertEqual(visible['assignments'],original['assignments'])
            self.assertEqual(len(visible['assignments']),224);self.assertEqual(sum(visible['station_loads'].values()),224)

    def test_missing_geometry_kept_in_table_and_download_data(self):
        self.assertEqual(len(self.payload['geometries']),211);self.assertEqual(len(self.payload['missing_geometry_ids']),13)
        data=json.loads((self.out/'all_16_complete_assignments.json').read_text(encoding='utf-8'))
        for p in data['plans']:
            self.assertEqual(set(p['assignments']),set(self.payload['operations']))
            self.assertTrue(set(self.payload['missing_geometry_ids'])<=set(p['assignments']))
        self.assertFalse(data['expert_used'])

    def test_complete_csv_matrix(self):
        with (self.out/'all_16_assignment_matrix.csv').open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
        self.assertEqual(len(rows),224)
        for row in rows:
            for p in self.original:self.assertEqual(row[p['plan_id']],p['assignments'][row['operation_id']])

    def test_each_svg_has_whole_geometry_outline_and_correct_station(self):
        for p in self.original:
            root=ET.parse(self.out/'svg'/(p['plan_id']+'.svg')).getroot()
            group=root.find('{http://www.w3.org/2000/svg}g')
            self.assertEqual(group.attrib['transform'],'matrix(0 1 -1 0 0 0)')
            self.assertEqual(group[0].attrib['class'],'part-outline-layer')
            ops=[g for g in group if g.attrib.get('class')=='operation']
            self.assertEqual(len(ops),211)
            for op in ops:self.assertEqual(op.attrib['data-station'],p['assignments'][op.attrib['data-operation']])
            self.assertEqual(list(map(float,root.attrib['viewBox'].split())),self.payload['full_view_box'])

    def test_protected_existing_files_unchanged(self):
        manifest=json.loads((self.out/'baseline_manifest.json').read_text(encoding='utf-8'))
        for relative,sha in manifest.items():self.assertEqual(hashlib.sha256((ROOT/relative).read_bytes()).hexdigest(),sha,relative)


if __name__=='__main__':unittest.main()
