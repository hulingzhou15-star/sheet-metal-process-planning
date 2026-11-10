from copy import deepcopy
import csv
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.build_visual_review_pack import (check_visual_integrity,preflight_answers,validate_destination,write_visual_pack)
from scripts.generate_candidate_plans import check_integrity
from src.review.blinding import leakage_findings
from src.visualization.review_visualization import render_visual_page
from visual_fixtures import ROOT,actual


class ReviewPackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c,cls.cfg,cls.cat,cls.views,cls.comparisons,cls.debug,cls.plans,_,cls.pairs,cls.mapping=actual()
        cls.temp=tempfile.TemporaryDirectory(prefix="m3-visual-test-",dir=ROOT/"outputs")
        cls.folder=Path(cls.temp.name).resolve()
        if not cls.folder.is_relative_to(ROOT/"outputs"): raise ValueError("Unsafe test temporary root")
        cls.config=deepcopy(cls.cfg)
        cls.config["previews"].update(panel_width_px=180,panel_height_px=170,raster_scale=1)
        cls.written=write_visual_pack(cls.folder,cls.cat,cls.views,cls.comparisons,cls.debug,cls.config,cls.plans)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def test_all_24_comparison_and_13_single_pages(self):
        self.assertEqual(len(self.written["blinding_audit"]["checked_files"]),15)
        self.assertEqual(len(list((self.folder/"expert_facing/plan_cards").glob("*.html"))),13)
        html=(self.folder/"expert_facing/review_pack.html").read_text(encoding="utf-8")
        for pair in self.pairs: self.assertIn('data-comparison-details="'+pair["comparison_id"]+'"',html)

    def test_blind_ids_and_orientation_kept_verbatim(self):
        with (self.folder/"expert_facing/comparison_pairs.csv").open(encoding="utf-8-sig",newline="") as handle: rows=list(csv.DictReader(handle))
        self.assertEqual([(r["comparison_id"],r["plan_A"],r["plan_B"]) for r in rows],[(p["comparison_id"],p["blind_plan_A"],p["blind_plan_B"]) for p in self.pairs])

    def test_public_source_and_hidden_payload_no_leakage(self):
        audit=self.written["blinding_audit"];self.assertTrue(audit["passed"]);self.assertFalse(audit["findings"])
        html=(self.folder/"expert_facing/review_pack.html").read_text(encoding="utf-8")
        self.assertFalse(leakage_findings(html,self.plans));self.assertNotIn('"debug"',html)

    def test_internal_page_allows_debug_metadata(self):
        html=(self.folder/"internal/review_pack_internal.html").read_text(encoding="utf-8")
        for token in ("NEAR_001","EXPERT_REFERENCE","SoftPenalty","P1/P2/P3"): self.assertIn(token,html)

    def test_forms_start_with_all_labels_blank(self):
        with (self.folder/"expert_facing/comparison_pairs.csv").open(encoding="utf-8-sig",newline="") as handle: rows=list(csv.DictReader(handle))
        self.assertEqual(len(rows),24);self.assertTrue(all(not r[k] for r in rows for k in ("answer","confidence","reason_note")))
        html=(self.folder/"expert_facing/review_pack.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('type="radio"'),120)
        self.assertNotIn('type="radio" checked',html)

    def test_static_previews_svg_and_png_exist(self):
        self.assertGreaterEqual(len(self.written["preview_files"]),6)
        self.assertFalse(self.written["png_preview_issues"])
        for preview in self.written["preview_files"]:
            self.assertTrue(Path(preview["svg"]).is_file());self.assertTrue(Path(preview["png"]).is_file())

    def test_network_free_and_shared_canvas_controls(self):
        html=(self.folder/"expert_facing/review_pack.html").read_text(encoding="utf-8")
        for token in ("fetch(","XMLHttpRequest","<script src=","<link href=","@import"): self.assertNotIn(token,html)
        self.assertIn('id="station-controls"',html);self.assertIn('id="region-filter"',html)
        self.assertIn('for(const svg of canvases) svg.setAttribute',html)
        self.assertNotIn('get("canvas-B").setAttribute',html)

    def test_zip_contains_only_blind_material(self):
        with zipfile.ZipFile(self.written["archive"]) as handle:
            self.assertEqual(len(handle.namelist()),15)
            for name in handle.namelist():
                self.assertNotIn("internal",name);self.assertFalse(leakage_findings(handle.read(name).decode("utf-8-sig"),self.plans))

    def test_human_filled_answers_are_preserved(self):
        folder=self.folder/"answer-test";path=folder/"expert_facing/comparison_pairs.csv";path.parent.mkdir(parents=True)
        original="comparison_id,plan_A,plan_B,answer,confidence,reason_note\nCMP_001,PLAN_A08,PLAN_A11,C,3,manual\n"
        path.write_text(original,encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"human-filled"): preflight_answers(folder)
        self.assertEqual(path.read_text(encoding="utf-8"),original)

    def test_output_cannot_alias_frozen_folders(self):
        for output in (ROOT/"outputs",ROOT/"outputs/m3_candidates/nested",ROOT/"outputs/m3_human_review",ROOT/"data/parts/P001"):
            with self.assertRaises(ValueError): validate_destination(output,ROOT)

    def test_frozen_integrity_old_and_v03_inputs(self):
        manifest=json.loads((ROOT/"outputs/m3_visual_review/internal/frozen_input_manifest.json").read_text(encoding="utf-8"))
        acknowledgments=json.loads((ROOT/"outputs/m3_visual_review/internal/acknowledged_input_changes.json").read_text(encoding="utf-8"))
        integrity=check_visual_integrity(ROOT,manifest,acknowledgments)
        self.assertTrue(integrity["all_unchanged"]);self.assertEqual(integrity["checked_file_count"],len(manifest)-len(integrity["user_acknowledged_changes"]))
        self.assertIn("outputs/m3_human_review/internal/blind_plan_mapping.json",manifest)
        self.assertIn("outputs/m3_human_review/expert_facing/comparison_pairs.csv",manifest)

    def test_unacknowledged_changes_cannot_be_hidden(self):
        manifest={"PROJECT_SPEC.md":"invalid-hash"}
        self.assertFalse(check_visual_integrity(ROOT,manifest)["all_unchanged"])
        with self.assertRaises(ValueError): check_visual_integrity(ROOT,manifest,[{"path":"PROJECT_SPEC.md","change_type":"USER_REMOVED_DUPLICATE_REVIEW_COPY"}])

    def test_visual_build_does_not_mutate_view_models(self):
        before=json.dumps((self.views,self.comparisons,self.cat),sort_keys=True)
        render_visual_page(self.cat,self.views,self.comparisons,self.cfg,debug_by_blind=self.debug)
        self.assertEqual(json.dumps((self.views,self.comparisons,self.cat),sort_keys=True),before)
