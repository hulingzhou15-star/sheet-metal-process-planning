import csv
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import tempfile
import unittest

from scripts.generate_candidate_plans import check_integrity
from src.review.blinding import assign_blind_ids, audit_expert_facing, blind_comparisons, blank_comparison_form, leakage_findings
from src.review.report import write_review_pack
from review_fixtures import ROOT, batch, review_data


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class BlindingTests(unittest.TestCase):
    def test_blind_ids_deterministic_generic_and_order_independent(self):
        ids = list(review_data()[2])
        first = assign_blind_ids(ids, 42)
        self.assertEqual(first, assign_blind_ids(list(reversed(ids)), 42))
        self.assertTrue(all(re.fullmatch(r"PLAN_A\d{2}", blind) for blind in first["blind_to_internal"]))
        self.assertFalse(any("NEAR" in blind or "MEDIUM" in blind or "FAR" in blind for blind in first["blind_to_internal"]))

    def test_internal_mapping_complete_and_reversible_including_reference(self):
        plans, mapping = review_data()[2], review_data()[3]
        self.assertEqual(len(mapping["blind_to_internal"]), 13)
        self.assertEqual(set(mapping["internal_to_blind"]), set(plans))
        for blind, internal in mapping["blind_to_internal"].items():
            self.assertEqual(mapping["internal_to_blind"][internal], blind)
        self.assertNotIn("REF", mapping["internal_to_blind"]["EXPERT_REFERENCE"])

    def test_comparison_a_b_and_order_randomization_reproducible(self):
        pairs, mapping = review_data()[4], review_data()[3]
        first = blind_comparisons(pairs, mapping, 123)
        self.assertEqual(first, blind_comparisons(pairs, mapping, 123))
        self.assertNotEqual(first, blind_comparisons(pairs, mapping, 124))
        for p in first:
            self.assertEqual({p["internal_plan_A"], p["internal_plan_B"]}, {p["internal_plan_1"], p["internal_plan_2"]})

    def test_comparison_form_answers_confidence_notes_empty(self):
        forms = blank_comparison_form(review_data()[4])
        self.assertEqual(len(forms), 24)
        self.assertTrue(all(not row[key] for row in forms for key in ("answer", "confidence", "reason_note")))
        self.assertEqual(set(forms[0]), {"comparison_id", "plan_A", "plan_B", "answer", "confidence", "reason_note"})

    def test_leakage_checker_rejects_internal_id_bucket_soft_reference_and_hidden_text(self):
        for text in ("NEAR_001", "MEDIUM_017", "FAR_002", "SoftPenalty", "P1", "P2", "P3", "expert", "reference", "EXPERT_REFERENCE", "<!-- NEAR_020 -->"):
            with self.subTest(text=text):
                self.assertTrue(leakage_findings(text))
        self.assertTrue(leakage_findings("CUSTOM_PLAN_123", ["CUSTOM_PLAN_123"]))

    def test_station_names_not_false_positive_prior_tokens(self):
        self.assertEqual(leakage_findings("OP10 OP20 OP30 PLAN_A01 O001 R001"), [])

    def test_real_public_pack_has_no_disclosure_and_contains_thirteen_plan_cards(self):
        c, config = batch()[0], batch()[4]
        reps, selection, plans, mapping, pairs, pair_audit = review_data()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "outputs/review"
            result = write_review_pack(folder, c, plans, reps, selection, pairs, pair_audit, mapping, config, project_root=tmp)
            self.assertTrue(result["blinding_audit"]["passed"])
            self.assertEqual(result["blinding_audit"]["file_count"], 42)
            self.assertEqual(len(list((folder / "expert_facing/plan_cards").glob("*.md"))), 13)
            self.assertFalse((folder / "expert_facing/blind_plan_mapping.json").exists())
            self.assertTrue(audit_expert_facing(folder / "expert_facing", plans)["passed"])

    def test_html_offline_collapsible_and_forms_unfilled(self):
        c, config = batch()[0], batch()[4]
        reps, selection, plans, mapping, pairs, pair_audit = review_data()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "outputs/review"
            write_review_pack(folder, c, plans, reps, selection, pairs, pair_audit, mapping, config, project_root=tmp)
            text = (folder / "expert_facing/review_pack.html").read_text(encoding="utf-8")
            parser = Tags()
            parser.feed(text)
            self.assertEqual(sum(tag == "details" and attrs.get("class") == "pair" for tag, attrs in parser.tags), 24)
            radios = [attrs for tag, attrs in parser.tags if tag == "input" and attrs.get("type") == "radio"]
            self.assertEqual(len(radios), 120)
            self.assertTrue(all("checked" not in attrs for attrs in radios))
            self.assertFalse(any(tag in ("script", "iframe") for tag, attrs in parser.tags))
            self.assertFalse(re.search(r"https?://|@import|url\(", text))
            for tag, attrs in parser.tags:
                if tag == "a" and attrs.get("href", "").startswith("plan_cards/"):
                    self.assertTrue((folder / "expert_facing" / attrs["href"]).exists())
            with (folder / "expert_facing/comparison_pairs.csv").open(encoding="utf-8-sig", newline="") as handle:
                forms = list(csv.DictReader(handle))
            self.assertTrue(all(not row[key] for row in forms for key in ("answer", "confidence", "reason_note")))

    def test_csv_blank_labels_are_not_automatically_replaced_on_rebuild(self):
        c, config = batch()[0], batch()[4]
        reps, selection, plans, mapping, pairs, pair_audit = review_data()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "outputs/review"
            write_review_pack(folder, c, plans, reps, selection, pairs, pair_audit, mapping, config, project_root=tmp)
            form = folder / "expert_facing/comparison_pairs.csv"
            with form.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            rows[0]["reason_note"] = "人工保留的备注"
            with form.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            before = form.read_bytes()
            with self.assertRaisesRegex(ValueError, "human entries"):
                write_review_pack(folder, c, plans, reps, selection, pairs, pair_audit, mapping, config, project_root=tmp)
            self.assertEqual(form.read_bytes(), before)

    def test_csv_exports_have_one_physical_row_per_record_on_windows(self):
        c, config = batch()[0], batch()[4]
        reps, selection, plans, mapping, pairs, pair_audit = review_data()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "outputs/review"
            write_review_pack(folder, c, plans, reps, selection, pairs, pair_audit, mapping, config, project_root=tmp)
            for path in folder.rglob("*.csv"):
                content = path.read_bytes()
                self.assertNotIn(b"\r\r\n", content)
                lines = path.read_text(encoding="utf-8-sig").splitlines()
                self.assertTrue(all(line for line in lines))
            form = folder / "expert_facing/comparison_pairs.csv"
            self.assertEqual(len(form.read_text(encoding="utf-8-sig").splitlines()), 25)
            for path in (folder / "expert_facing/plan_cards").glob("*.csv"):
                self.assertEqual(len(path.read_text(encoding="utf-8-sig").splitlines()), 225)

    def test_frozen_files_unchanged(self):
        manifest = json.loads((ROOT / "outputs/m3_human_review/internal/frozen_input_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest), 87)
        self.assertTrue(check_integrity(ROOT, manifest)["all_unchanged"])
