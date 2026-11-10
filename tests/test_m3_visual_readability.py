"""v0.5 source-reuse, rotation and visual-only regression checks."""
from copy import deepcopy
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from scripts.build_visual_review_pack import check_v05_integrity,is_authorized_visual_path,validate_destination
from src.visualization import load_visualization_config
from src.visualization.display_transform import display_box,display_matrix,display_point
from src.visualization.outline_adapter import load_display_outline
from src.visualization.plan_view_model import build_plan_view_model
from src.visualization.review_visualization import public_payload,render_visual_page
from src.visualization.svg_renderer import operation_style,render_svg
from visual_fixtures import ROOT,actual,mini

NS={"s":"http://www.w3.org/2000/svg"}
NODE=Path("C:/Users/hulin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe")
UI=ROOT/"src/visualization/review_ui.js"


def sample_html(*,closed=True,bad_endpoint=False):
    meta={"display":{"local_to_view_linear":[1,0,0,1],"pixels_per_mm":1,"margin_px":0,"view_bounds":[0,0,20,20]},"block_rotation_degrees":0}
    shapes=[]
    loops=[[(0,0),(20,0),(20,20),(0,20)],[(5,5),(7,5),(7,7),(5,7)]]
    for ring,loop in enumerate(loops):
        for i,(a,b) in enumerate(zip(loop,loop[1:]+loop[:1])):
            if not closed and ring==0 and i==3:continue
            x=a[0]+(1 if bad_endpoint else 0)
            shapes.append(f'<path id="seg-{ring}-{i}" data-role="segment" data-segment-type="LINE" data-local-start-x="{a[0]}" data-local-start-y="{a[1]}" data-local-end-x="{b[0]}" data-local-end-y="{b[1]}" d="M {x} {20-a[1]} L {b[0]} {20-b[1]}"/>')
    shapes.append('<path id="open-inside" data-role="segment" data-segment-type="LINE" data-local-start-x="10" data-local-start-y="10" data-local-end-x="12" data-local-end-y="10" d="M 10 10 L 12 10"/>')
    return '<html><svg><metadata>'+json.dumps(meta)+'</metadata>'+''.join(shapes)+'</svg><script>EXPERT_REFERENCE; throw new Error("must never run");</script></html>'


class OutlineTests(unittest.TestCase):
    def synthetic(self,text):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"drawing.html";path.write_text(text,encoding="utf-8")
            cfg=load_visualization_config();cfg["outline"]["source_path"]="drawing.html"
            return load_display_outline(cfg,Path(folder),"P001")

    def test_closed_source_outer_ring_not_internal_hole(self):
        asset,audit=self.synthetic(sample_html())
        self.assertEqual(audit["status"],"available");self.assertEqual(len(asset["loops"]),1)
        self.assertEqual(audit["selected_source_segment_count"],4);self.assertEqual(audit["excluded_inner_contour_count"],1)
        self.assertEqual(audit["excluded_interior_open_component_count"],1)

    def test_source_scripts_and_identity_are_not_copied(self):
        asset,audit=self.synthetic(sample_html())
        self.assertNotIn("EXPERT_REFERENCE",json.dumps(asset));self.assertNotIn("script",json.dumps(asset))
        self.assertTrue(audit["display_only"])

    def test_wrong_affine_or_endpoint_fails_explicitly(self):
        asset,audit=self.synthetic(sample_html(bad_endpoint=True))
        self.assertIsNone(asset);self.assertEqual(audit["status"],"unavailable");self.assertTrue(audit["fallback_used"])

    def test_open_outer_loop_not_gap_filled(self):
        asset,audit=self.synthetic(sample_html(closed=False))
        self.assertEqual(audit["status"],"partial")
        self.assertEqual(audit["selected_source_segment_count"],4) # only the existing inner square survives
        self.assertTrue(audit["unresolved_components"])

    def test_actual_complete_exterior_and_saved_cubic_paths(self):
        _,_,cat,*_=actual();audit=cat["audit"]["outline"]
        self.assertEqual(audit["status"],"available");self.assertEqual(audit["exterior_contour_count"],32)
        self.assertEqual(audit["selected_source_segment_count"],385)
        self.assertLess(audit["max_local_svg_endpoint_error"],1e-8)
        self.assertTrue(any(sh["kind"]=="cubic" for loop in cat["display_outline"]["loops"] for sh in loop["shapes"]))

    def test_source_selection_stable_across_display_flatten_precision(self):
        cfg=load_visualization_config();audits=[]
        for tolerance in (.0001,.001,.01):
            cfg=deepcopy(cfg);cfg["outline"]["containment_flatten_tolerance"]=tolerance
            _,audit=load_display_outline(cfg,ROOT,"P001");audits.append(audit)
        self.assertTrue(all(a["selected_source_segment_ids"]==audits[0]["selected_source_segment_ids"] for a in audits))

    def test_other_part_does_not_inherit_p001_outline(self):
        asset,audit=load_display_outline(load_visualization_config(),ROOT,"P002")
        self.assertIsNone(asset);self.assertEqual(audit["status"],"not_requested")


class RotationReadabilityTests(unittest.TestCase):
    def setUp(self):
        self.c,self.a,self.b,self.cfg,self.cat=mini()
        self.va=build_plan_view_model(self.c,self.a,self.cat,"PLAN_A01")

    def test_default_270_matches_saved_html_familiar_orientation(self):
        self.assertEqual(self.cfg["display"]["default_rotation_degrees"],270)
        # Engineering (3,5) => native SVG (3,-5) => familiar screen (5,3).
        self.assertEqual(display_point((3,-5),270),(5,3));self.assertEqual(display_point((3,-5),0),(3,-5))
        self.assertEqual(display_box((1,-9,4,8),270),(1,1,8,4))

    def test_svg_same_parent_transform_covers_region_visible_and_hit(self):
        root=ET.fromstring(render_svg(self.cat,self.va,"OP10",self.cfg,show_influence=True))
        scene=root.find('s:g[@class="display-scene"]',NS)
        self.assertEqual(scene.attrib["transform"],display_matrix(270))
        self.assertTrue(scene.findall('s:rect[@class="influence-region"]',NS))
        ops=scene.findall('s:g[@class="operation"]',NS);self.assertTrue(ops)
        self.assertTrue(all(op.find('s:g[@class="hit-geometry"]',NS) is not None for op in ops))
        self.assertFalse(any("transform" in node.attrib for node in scene.iter() if node is not scene))

    def test_target_ids_and_details_identical_across_orientations(self):
        roots=[ET.fromstring(render_svg(self.cat,self.va,"OP10",self.cfg,rotation_degrees=r)) for r in (0,270)]
        facts=lambda root:{g.attrib["id"]:(g.attrib["data-operation"],g.find("s:title",NS).text) for g in root.findall('.//s:g[@class="operation"]',NS)}
        self.assertEqual(facts(roots[0]),facts(roots[1]))

    def test_station_difference_and_region_states_rotation_invariant(self):
        roots=[ET.fromstring(render_svg(self.cat,self.va,"OP10",self.cfg,rotation_degrees=r,changed_ids={"C","B2"},difference_only=True,region_filter="R_SMALL")) for r in (0,270)]
        state=lambda root:{g.attrib["data-operation"]:(g.attrib["data-status"],g.attrib["data-focus"]) for g in root.findall('.//s:g[@class="operation"]',NS)}
        self.assertEqual(state(roots[0]),state(roots[1]));self.assertEqual(state(roots[1])["C"],("current","primary"))

    def test_outline_below_operations_and_has_no_click_metadata(self):
        _,cfg,cat,views,*_=actual();view=next(iter(views.values()))
        root=ET.fromstring(render_svg(cat,view,"OP20",cfg));scene=root.find('s:g[@class="display-scene"]',NS)
        self.assertEqual(scene[0].attrib["class"],"part-outline-layer")
        self.assertEqual(scene[0].attrib["pointer-events"],"none");self.assertEqual(len(scene[0]),32)
        self.assertFalse(any("data-operation" in n.attrib or "data-station" in n.attrib for n in scene[0].iter()))

    def test_non_current_visible_and_current_professional_blue(self):
        row=self.va["operations"]["C"]
        inactive=operation_style(row,0,self.cfg);current=operation_style(row,1,self.cfg);selected=operation_style(row,1,self.cfg,selected=True)
        self.assertGreaterEqual(inactive["opacity"],.4);self.assertGreater(current["opacity"],inactive["opacity"])
        self.assertEqual(current["stroke"],"#2E6DAE");self.assertEqual(selected["stroke"],"#C56A1F")
        self.assertGreater(selected["stroke_width"],current["stroke_width"])

    def test_js_fit_region_rotated_corners_and_zoom(self):
        script="""const l=require(process.argv[1]);let s='';process.stdin.on('data',c=>s+=c);process.stdin.on('end',()=>{const d=JSON.parse(s),base=l.displayBox(d.base,270),fit=l.fitRegion(d.region,270,base,d.config);console.log(JSON.stringify({base,fit,points:d.corners.map(p=>l.displayPoint(p,270))}));});"""
        box=(0,5,10,10);corners=((0,-5),(0,-10),(10,-5),(10,-10))
        output=subprocess.run([str(NODE),"-e",script,str(UI)],input=json.dumps({"base":self.cat["view_box"],"region":box,"corners":corners,"config":self.cfg}),text=True,capture_output=True,check=True)
        d=json.loads(output.stdout);self.assertEqual(d["base"],list(display_box(self.cat["view_box"],270)))
        x,y,w,h=d["fit"]
        self.assertTrue(all(x<=a<=x+w and y<=b<=y+h for a,b in d["points"]))

    def test_plan_and_comparison_models_equal_frozen_v04(self):
        _,_,_,views,comparisons,*_=actual()
        old=json.loads((ROOT/"outputs/m3_visual_review/internal/visual_view_models.json").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(json.dumps(views)),old["plans"]);self.assertEqual(json.loads(json.dumps(comparisons)),old["comparisons"])

    def test_outline_not_put_into_plan_or_blind_payload_metadata(self):
        _,cfg,cat,views,comparisons,*_=actual();data=public_payload(cat,views,comparisons,cfg)
        self.assertNotIn("display_outline",data["geometry"]);self.assertNotIn("outline",data["config"])
        self.assertNotIn("reconstructed_local.html",json.dumps(data))

    def test_v04_output_preserved_and_only_visual_paths_allowed(self):
        baseline=json.loads((ROOT/"outputs/m3_visual_review_v05/internal/input_baseline_manifest.json").read_text(encoding="utf-8"))
        check=check_v05_integrity(ROOT,baseline);self.assertTrue(check["all_unchanged"])
        self.assertTrue(any(r["path"].startswith("outputs/m3_visual_review/") for r in check["files"]))
        self.assertFalse(is_authorized_visual_path("src/constraints/context.py"))
        self.assertFalse(is_authorized_visual_path("outputs/m3_human_review/internal/blind_plan_mapping.json"))
        self.assertFalse(is_authorized_visual_path("information_extract/sources/P001/legacy_outline.html"))
        with self.assertRaises(ValueError):validate_destination(ROOT/"outputs/m3_visual_review",ROOT)
