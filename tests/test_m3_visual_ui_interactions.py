"""Actual UI handlers against a test DOM, plus SVG transform assertions elsewhere."""
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import unittest

from src.visualization.display_transform import display_point
from src.visualization.review_visualization import render_visual_page
from visual_fixtures import ROOT,actual

NODE=Path("C:/Users/hulin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe")


class TreeParser(HTMLParser):
    def __init__(self):super().__init__();self.tree=[];self.stack=[self.tree]
    def handle_starttag(self,tag,attrs):
        node={"tag":tag,"attrs":dict(attrs),"children":[]};self.stack[-1].append(node)
        if tag not in {"meta","input","br","link","img","hr","wbr"}:self.stack.append(node["children"])
    def handle_endtag(self,tag):
        if tag not in {"meta","input","br","link","img","hr","wbr"}:self.stack.pop()
    def handle_data(self,text):self.stack[-1].append(text)


class UIInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _,cls.cfg,cls.cat,cls.views,cls.comparisons,*_=actual()
        html=render_visual_page(cls.cat,cls.views,cls.comparisons,cls.cfg);p=TreeParser();p.feed(html)
        cls.row=cls.comparisons[0];cls.op=next(r["operation_id"] for r in cls.row["diff"]["bend_changes"])
        cls.region=next(iter(cls.cat["regions"]))
        source={"tree":p.tree,"operation_id":cls.op,"region_id":cls.region,"changed_ids":cls.row["diff"]["changed_ids"]}
        result=subprocess.run([str(NODE),str(ROOT/"tests/visual_ui_dom_harness.js"),str(ROOT/"src/visualization/review_ui.js")],input=json.dumps(source),text=True,encoding="utf-8",capture_output=True,check=True)
        cls.result=json.loads(result.stdout)

    def test_default_rotation_and_station_sync_handlers(self):
        r=self.result;self.assertEqual(r["initial_matrices"],["matrix(0 1 -1 0 0 0)"]*2)
        self.assertEqual(r["initial_boxes"][0],r["initial_boxes"][1]);self.assertEqual(r["station_boxes"][0],r["station_boxes"][1])
        self.assertTrue(all("OP50" in s for s in r["stage_counts"]))

    def test_transformed_hit_click_and_keyboard_show_correct_metadata(self):
        r=self.result;self.assertIn(self.op,r["clicked_detail"]);self.assertIn("affected_depth",r["clicked_detail"])
        self.assertIn("A Station",r["clicked_detail"]);self.assertIn("B Station",r["clicked_detail"])
        self.assertIn("operation_id",r["keyboard_detail"])

    def test_orientation_toggle_keeps_selected_operation_details(self):
        r=self.result;self.assertEqual(r["original_matrices"],["matrix(1 0 0 1 0 0)"]*2)
        self.assertEqual(r["rotated_matrices"],r["initial_matrices"])
        self.assertIn(self.op,r["detail_after_rotation"]);self.assertIn("affected_area_proxy",r["detail_after_rotation"])

    def test_region_and_difference_handlers_keep_correct_membership(self):
        self.assertEqual(self.result["visible_regions"],[self.region])
        self.assertTrue(self.result["unchanged_states"]);self.assertEqual(set(self.result["unchanged_states"]),{"unchanged"})

    def test_fit_region_zoom_pan_reset_are_synchronized_after_rotation(self):
        r=self.result
        for key in ("fit_boxes","zoom_boxes","pan_boxes","reset_boxes"):self.assertEqual(r[key][0],r[key][1])
        self.assertNotEqual(r["fit_boxes"],r["zoom_boxes"]);self.assertNotEqual(r["zoom_boxes"],r["pan_boxes"])
        self.assertEqual(r["reset_boxes"],r["initial_boxes"])
        x,y,w,h=map(float,r["fit_boxes"][0].split());a,b,c,d=self.cat["regions"][self.region]["bbox"]
        self.assertTrue(all(x<=u<=x+w and y<=v<=y+h for u,v in [display_point(p,270) for p in ((a,-b),(a,-d),(c,-b),(c,-d))]))

    def test_cumulative_and_full_modes_still_execute(self):
        view=self.views[self.row["blind_plan_A"]]
        expected={id for id,row in view["operations"].items() if row["assigned_station"]=="OP50" and row["renderable"]}
        self.assertEqual(set(self.result["cumulative_current"]),expected);self.assertEqual(self.result["full_states"],["full"])
