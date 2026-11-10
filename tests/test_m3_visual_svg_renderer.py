import json
from pathlib import Path
import subprocess
import unittest
import xml.etree.ElementTree as ET

from src.visualization.plan_view_model import build_plan_view_model
from src.visualization.preview import preview_svg
from src.visualization.svg_renderer import operation_style, render_svg, target_id
from visual_fixtures import mini

NODE=Path("C:/Users/hulin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe")
UI=Path(__file__).resolve().parents[1]/"src/visualization/review_ui.js"
NS={"s":"http://www.w3.org/2000/svg"}


class SvgRendererTests(unittest.TestCase):
    def setUp(self):
        self.c,self.a,self.b,self.cfg,self.cat=mini()
        self.va=build_plan_view_model(self.c,self.a,self.cat,"PLAN_A01")
        self.vb=build_plan_view_model(self.c,self.b,self.cat,"PLAN_A02")

    def test_shared_viewbox_nonempty_for_a_and_b(self):
        a=ET.fromstring(render_svg(self.cat,self.va,"OP10",self.cfg,prefix="A"))
        b=ET.fromstring(render_svg(self.cat,self.vb,"OP10",self.cfg,prefix="B"))
        self.assertEqual(a.attrib["viewBox"],b.attrib["viewBox"])
        self.assertTrue(all(v>0 for v in map(float,a.attrib["viewBox"].split()[2:])))

    def test_click_targets_unique_in_same_document(self):
        roots=[ET.fromstring(render_svg(self.cat,v,"OP10",self.cfg,prefix=p)) for p,v in (("A",self.va),("B",self.vb))]
        ids=[n.attrib["id"] for root in roots for n in root.iter() if "id" in n.attrib]
        self.assertEqual(len(ids),len(set(ids)));self.assertNotEqual(target_id("a-b","c"),target_id("a","b-c"))

    def test_current_bend_uses_configured_thicker_width(self):
        row=self.va["operations"]["B1"];style=operation_style(row,3,self.cfg)
        self.assertEqual(style["stroke_width"],self.cfg["svg"]["bend_current_stroke_width"])

    def test_influence_rectangle_svg_y_and_defaults(self):
        root=ET.fromstring(render_svg(self.cat,self.va,"OP10",self.cfg))
        rects={n.attrib["data-region"]:n.attrib for n in root.findall(".//s:rect",NS)}
        r=rects["R_SMALL"];self.assertEqual((r["x"],r["y"],r["width"],r["height"]),("0","-10","10","5"))
        self.assertEqual(r["visibility"],"hidden")

    def test_region_switch_draws_only_selected_rectangle(self):
        root=ET.fromstring(render_svg(self.cat,self.va,"OP10",self.cfg,region_filter="R_SMALL",show_influence=True))
        visible=[r.attrib["data-region"] for r in root.findall(".//s:rect",NS) if r.attrib["visibility"]=="visible"]
        self.assertEqual(visible,["R_SMALL"])

    def test_current_only_moved_action_inactive_in_other_panel(self):
        a=ET.fromstring(render_svg(self.cat,self.va,"OP10",self.cfg));b=ET.fromstring(render_svg(self.cat,self.vb,"OP10",self.cfg))
        find=lambda root:next(g for g in root.findall(".//s:g",NS) if g.attrib.get("data-operation")=="C")
        self.assertEqual(find(a).attrib["data-status"],"current");self.assertEqual(find(b).attrib["data-status"],"inactive")
        self.assertIn("此序未加工",find(b).find("s:title",NS).text)

    def test_difference_only_keeps_unchanged_low_opacity(self):
        root=ET.fromstring(render_svg(self.cat,self.va,"OP10",self.cfg,changed_ids={"C"},difference_only=True,selected_operation="F"))
        g=next(g for g in root.findall(".//s:g",NS) if g.attrib.get("data-operation")=="F")
        self.assertEqual(g.attrib["data-focus"],"unchanged")
        visible=g.find('s:g[@class="visible-geometry"]',NS)
        self.assertEqual(float(visible.attrib["opacity"]),self.cfg["svg"]["unchanged_opacity"])

    def test_bend_hover_full_metadata(self):
        root=ET.fromstring(render_svg(self.cat,self.va,"OP10",self.cfg))
        g=next(g for g in root.findall(".//s:g",NS) if g.attrib.get("data-operation")=="B2")
        text=g.find("s:title",NS).text
        for field in ("affected_depth","bend_length","affected_area_proxy","influence_region_id","parent_region_id","region_level"): self.assertIn(field,text)

    def test_preview_svg_valid_with_two_equal_nested_viewports(self):
        root=ET.fromstring(preview_svg(self.cat,[self.va,self.vb],"OP10",self.cfg,title="CMP_001"))
        nested=root.findall("s:svg",NS);self.assertEqual(len(nested),2)
        self.assertEqual(nested[0].attrib["viewBox"],nested[1].attrib["viewBox"])
        self.assertEqual(nested[0].attrib["width"],nested[1].attrib["width"])

    def test_javascript_display_styles_match_python_and_zoom_bounds(self):
        script="""const logic=require(process.argv[1]);let input='';process.stdin.on('data',c=>input+=c);process.stdin.on('end',()=>{const d=JSON.parse(input);let rows=[];for(const q of d.cases)rows.push(logic.style(q.row,q.rank,d.config,q.mode,q.color,q.focus,q.selected));process.stdout.write(JSON.stringify({rows,zoom:logic.zoom([0,0,100,100],[0,0,100,100],100,{minimum_zoom:.5,maximum_zoom:10})}));});"""
        cases=[]
        for mode in ("CURRENT_ONLY","CUMULATIVE","FULL_PLAN_BY_STATION"):
            for focus in ("primary","related","filtered","unchanged"):
                for selected in (False,True):
                    cases.append({"row":self.va["operations"]["B1"],"rank":1,"mode":mode,"color":"BY_OPERATION_TYPE","focus":focus,"selected":selected})
        result=subprocess.run([str(NODE),"-e",script,str(UI)],input=json.dumps({"cases":cases,"config":self.cfg}),text=True,capture_output=True,check=True)
        result=json.loads(result.stdout)
        expected=[operation_style(q["row"],q["rank"],self.cfg,mode=q["mode"],color_mode=q["color"],focus=q["focus"],selected=q["selected"]) for q in cases]
        self.assertEqual(result["rows"],expected);self.assertEqual(result["zoom"],[45,45,10,10])

    def test_javascript_smallest_region_filter_matches_python(self):
        from src.visualization.plan_view_model import operation_focus
        script="""const l=require(process.argv[1]);let s='';process.stdin.on('data',c=>s+=c);process.stdin.on('end',()=>{const d=JSON.parse(s);console.log(JSON.stringify(d.ids.map(id=>l.focus(id,d.cat,new Set(['C','B2']),true,'R_SMALL'))));});"""
        ids=list(self.cat["operations"])
        result=subprocess.run([str(NODE),"-e",script,str(UI)],input=json.dumps({"ids":ids,"cat":self.cat}),text=True,capture_output=True,check=True)
        self.assertEqual(json.loads(result.stdout),[operation_focus(op,self.cat,changed_ids={"C","B2"},difference_only=True,region_filter="R_SMALL") for op in ids])
