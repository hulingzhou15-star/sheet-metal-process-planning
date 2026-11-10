from copy import deepcopy
import csv
from io import StringIO
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from zipfile import ZipFile

from src.inference import load_inference_context
from src.review.blinding import leakage_findings
from src.review.reference_free_pack import load_pack_config, load_sampling_batch, design_comparisons, prepare_views
from src.visualization.geometry_adapter import build_geometry_catalog
from src.visualization.review_visualization import render_visual_page, public_payload
from scripts.build_reference_free_review_v08 import preflight_destination
from test_m3_visual_ui_interactions import TreeParser, NODE

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/"outputs/m3_reference_free_v07"
OUTPUT = ROOT/"outputs/m3_review_v08"


class ReviewV08Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = load_inference_context(next((ROOT/"data/parts/P001").glob("*P001.xlsx")))
        cls.config = load_pack_config()
        cls.plans, cls.selected, cls.local = load_sampling_batch(cls.context, SOURCE)
        cls.mapping, cls.comparisons, cls.design = design_comparisons(cls.context, cls.plans, cls.selected, cls.local, cls.config)
        cls.catalog, cls.views, cls.rows, cls.visual = prepare_views(cls.context, cls.plans, cls.mapping, cls.comparisons, ROOT)
        cls.html = render_visual_page(cls.catalog, cls.views, cls.rows, cls.visual)
        parser = TreeParser(); parser.feed(cls.html); cls.tree = parser.tree

    def test_counts_and_whole_degree(self):
        self.assertEqual((len(self.plans), len(self.comparisons)), (29, 29))
        self.assertEqual(self.design["group_counts"], {"WHOLE": 16, "LOCAL": 13})
        self.assertTrue(self.design["all_selected_have_two_whole_comparisons"])

    def test_no_duplicate_unordered_comparisons(self):
        pairs = [tuple(sorted((r["blind_plan_A"], r["blind_plan_B"]))) for r in self.comparisons]
        self.assertEqual(len(set(pairs)), len(pairs))
        self.assertTrue(all(a != b for a, b in pairs))

    def test_blinding_deterministic_and_mapping_reversible(self):
        mapping, comparisons, audit = design_comparisons(self.context, self.plans, self.selected, self.local, self.config)
        self.assertEqual((mapping, comparisons, audit), (self.mapping, self.comparisons, self.design))
        self.assertEqual({v: k for k, v in mapping["blind_to_internal"].items()}, mapping["internal_to_blind"])

    def test_local_orientation_not_fixed_and_all_sources_preserved(self):
        self.assertGreater(self.design["local_same_role_on_A"], 0)
        self.assertLess(self.design["local_same_role_on_A"], 13)
        self.assertEqual({r["source_contrast_id"] for r in self.comparisons if r["group"] == "LOCAL"},
                         {f"LC_{i:03d}" for i in range(1, 14)})

    def test_no_reference_access_needed_and_geometry_membership_is_plan_independent(self):
        self.assertFalse(hasattr(self.context, "expert_reference_plan"))
        second = self.plans[self.selected[1]]["assignments"]
        catalog = build_geometry_catalog(self.context, self.visual, region_assignments=second)
        self.assertEqual(catalog["membership"], self.catalog["membership"])
        self.assertEqual(catalog["regions"], self.catalog["regions"])

    def test_allow_list_payload_has_no_internal_roles_or_scores(self):
        payload = public_payload(self.catalog, self.views, self.rows, self.visual)
        text = json.dumps(payload)
        self.assertFalse(leakage_findings(text, self.plans))
        for key in ("focus_pairs", "source_contrast_id", "same_station_plan", "different_station_plan", "RF_", "LC_"):
            self.assertNotIn(key, text)
        self.assertTrue(all(set(r) == {"comparison_id", "blind_plan_A", "blind_plan_B", "diff"} for r in payload["comparisons"]))

    def test_each_public_diff_matches_randomized_sides(self):
        for source, view in zip(self.comparisons, self.rows):
            a, b = (self.plans[source["internal_plan_"+side]]["assignments"] for side in ("A", "B"))
            changed = {op for op in a if a[op] != b[op]}
            self.assertEqual(set(view["diff"]["changed_ids"]), changed)
            self.assertEqual(len(changed), 1 if source["group"] == "LOCAL" else source["assignment_distance_raw"])

    def test_actual_forms_and_export_preserve_all_29_orientations(self):
        source = {"tree": self.tree, "note": '涉及O203，需复核。\n包含,"引号"'}
        run = subprocess.run([str(NODE), str(ROOT/"tests/visual_review_answers_harness.js")], input=json.dumps(source),
                             text=True, encoding="utf-8", capture_output=True, check=True)
        result = json.loads(run.stdout)
        self.assertEqual(len(result["initial_rows"]), 29)
        self.assertTrue(all(not r[k] for r in result["initial_rows"] for k in ("answer", "confidence", "reason_note")))
        exported = list(csv.DictReader(StringIO(result["csv"])))
        self.assertEqual([(r["comparison_id"], r["plan_A"], r["plan_B"]) for r in exported],
                         [(r["comparison_id"], r["blind_plan_A"], r["blind_plan_B"]) for r in self.comparisons])
        self.assertEqual(exported[0]["reason_note"], source["note"])
        self.assertEqual(result["rows"], result["restored_rows"])
        self.assertEqual(result["progress"], "已判断 2 / 29 对")
        self.assertNotEqual(result["signature"], result["changed_signature"])

    def test_new_batch_svg_handlers(self):
        first = self.rows[0]
        op = next(op for op in first["diff"]["changed_ids"] if self.catalog["operations"][op]["renderable"])
        snapshot = {"tree": self.tree, "operation_id": op, "region_id": next(iter(self.catalog["regions"])),
                    "changed_ids": first["diff"]["changed_ids"]}
        run = subprocess.run([str(NODE), str(ROOT/"tests/visual_ui_dom_harness.js"), str(ROOT/"src/visualization/review_ui.js")],
                             input=json.dumps(snapshot), text=True, encoding="utf-8", capture_output=True, check=True)
        r = json.loads(run.stdout)
        self.assertEqual(r["initial_matrices"], ["matrix(0 1 -1 0 0 0)"]*2)
        self.assertIn(op, r["clicked_detail"])
        self.assertTrue(all("OP50" in value for value in r["stage_counts"]))
        for key in ("initial_boxes", "fit_boxes", "zoom_boxes", "pan_boxes", "reset_boxes"):
            self.assertEqual(r[key][0], r[key][1])
        self.assertEqual(set(r["unchanged_states"]), {"unchanged"})
        self.assertEqual(r["full_states"], ["full"])

    def test_source_hash_and_assignments_not_rewritten(self):
        persisted = json.loads((OUTPUT/"internal/source_plans.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted, self.plans)

    def test_zip_is_exact_public_tree_and_has_no_internal_material(self):
        with ZipFile(OUTPUT/"P001_R02_review_pack.zip") as archive:
            expected = {p.relative_to(OUTPUT/"expert_facing").as_posix() for p in (OUTPUT/"expert_facing").rglob("*") if p.is_file()}
            self.assertEqual(set(archive.namelist()), expected)
            self.assertIsNone(archive.testzip())
            for name in archive.namelist():
                self.assertNotIn("internal", name)
                if not name.endswith(".png"):
                    self.assertFalse(leakage_findings(archive.read(name).decode("utf-8-sig"), self.plans))

    def test_filled_form_or_download_not_overwritten(self):
        with tempfile.TemporaryDirectory(dir=ROOT/"outputs") as temp:
            folder = Path(temp); public = folder/"expert_facing"; public.mkdir()
            path = public/"comparison_pairs.csv"
            path.write_text("comparison_id,plan_A,plan_B,answer,confidence,reason_note\nC,A,B,D,5,\n", encoding="utf-8")
            with self.assertRaises(ValueError): preflight_destination(folder, ROOT)
            path.unlink()
            (public/"comparison_pairs_completed.csv").write_text("preserve", encoding="utf-8")
            with self.assertRaises(ValueError): preflight_destination(folder, ROOT)

    def test_frozen_output_destination_rejected(self):
        with self.assertRaises(ValueError):
            preflight_destination(SOURCE, ROOT)

    def test_old_and_new_draft_signatures_are_separate(self):
        # New comparison and plan IDs prevent importing the old 24-pair drafts.
        self.assertTrue(all(r["comparison_id"].startswith("R02_CMP_") for r in self.rows))
        self.assertTrue(all(blind.startswith("R02_PLAN_") for blind in self.views))


if __name__ == "__main__":
    unittest.main()
