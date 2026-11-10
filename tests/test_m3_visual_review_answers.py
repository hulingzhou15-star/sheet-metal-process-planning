"""Human-entered answers round-trip through the actual offline HTML handlers."""
import csv
import io
import json
import subprocess
import unittest

from src.review.blinding import leakage_findings
from src.visualization.review_visualization import render_visual_page
from test_m3_visual_ui_interactions import NODE, TreeParser
from visual_fixtures import ROOT, actual


class ReviewAnswerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _,cls.cfg,cls.catalog,cls.views,cls.comparisons,*_=actual()
        cls.html=render_visual_page(cls.catalog,cls.views,cls.comparisons,cls.cfg)
        parser=TreeParser();parser.feed(cls.html);cls.tree=parser.tree
        cls.note='O017：建议后移，避免干涉。\n理由含逗号, 和 "引号" <script>文字</script>'
        cls.normal=cls.run_ui()

    @classmethod
    def run_ui(cls, **options):
        source={"tree":cls.tree,"note":cls.note,**options}
        result=subprocess.run([str(NODE),str(ROOT/'tests/visual_review_answers_harness.js')],
                              input=json.dumps(source),text=True,encoding='utf-8',capture_output=True,check=True)
        return json.loads(result.stdout)

    def test_html_has_direct_entry_progress_and_export(self):
        for target in ('review-progress','review-focus-form','review-export-csv','review-save-status'):
            self.assertIn(f'id="{target}"',self.html)
        self.assertTrue(self.normal['focused']);self.assertTrue(self.normal['scrolled'])

    def test_clean_open_starts_blank_and_no_automatic_judgments(self):
        self.assertEqual(len(self.normal['initial_rows']),24)
        self.assertTrue(all(not row[k] for row in self.normal['initial_rows'] for k in ('answer','confidence','reason_note')))

    def test_export_preserves_all_frozen_pairs_and_orientation(self):
        rows=list(csv.DictReader(io.StringIO(self.normal['csv'])))
        self.assertEqual([(r['comparison_id'],r['plan_A'],r['plan_B']) for r in rows],
                         [(r['comparison_id'],r['blind_plan_A'],r['blind_plan_B']) for r in self.comparisons])
        self.assertEqual(rows[0]['answer'],'B');self.assertEqual(rows[0]['confidence'],'4')
        self.assertEqual(rows[1]['answer'],'E');self.assertTrue(all(not r['answer'] for r in rows[2:]))

    def test_unicode_quotes_commas_newlines_round_trip(self):
        rows=list(csv.DictReader(io.StringIO(self.normal['csv'])))
        self.assertEqual(rows[0]['reason_note'],self.note)
        self.assertEqual(list(rows[0]),['comparison_id','plan_A','plan_B','answer','confidence','reason_note'])
        self.assertEqual(self.normal['csv_bytes'],[239,187,191])

    def test_real_download_uses_completed_filename_and_revokes_url(self):
        self.assertEqual(self.normal['filename'],'comparison_pairs_completed.csv')
        self.assertEqual(self.normal['revoked'],'blob:test-result')
        self.assertIn('已发起',self.normal['export_status'])

    def test_drafts_restore_exact_answers_and_notes(self):
        self.assertEqual(self.normal['rows'],self.normal['restored_rows'])
        self.assertEqual(self.normal['stored']['rows'],self.normal['rows'])
        self.assertIn('已在当前浏览器本地暂存',self.normal['save_status'])

    def test_progress_and_navigation_record_only_human_answers(self):
        self.assertEqual(self.normal['progress'],'已判断 2 / 24 对')
        self.assertEqual(self.normal['answered_nav'],['0','1'])

    def test_blocked_storage_keeps_form_and_csv_usable(self):
        result=self.run_ui(read_blocked=True,write_blocked=True)
        self.assertIn('未能本地暂存',result['save_status'])
        self.assertEqual(list(csv.DictReader(io.StringIO(result['csv'])))[0]['reason_note'],self.note)

    def test_storage_quota_failure_is_reported(self):
        result=self.run_ui(write_blocked=True)
        self.assertIn('务必导出',result['save_status']);self.assertIsNotNone(result['csv'])

    def test_corrupt_storage_does_not_break_review(self):
        result=self.run_ui(corrupt=True,fill=False)
        self.assertIn('未能读取',result['initial_status']);self.assertIsNotNone(result['csv'])

    def test_mismatched_draft_is_not_restored(self):
        saved={**self.normal['stored'],'signature':'different-pack'}
        result=self.run_ui(saved=saved,fill=False)
        self.assertTrue(all(not r['answer'] for r in result['initial_rows']))
        self.assertIn('不一致',result['initial_status'])

    def test_same_pack_restores_and_rejects_wrong_orientation_row(self):
        saved=json.loads(json.dumps(self.normal['stored']))
        saved['rows'][0]['plan_A']='PLAN_A99'
        result=self.run_ui(saved=saved,fill=False)
        self.assertEqual(result['initial_rows'][0]['answer'],'')
        self.assertEqual(result['initial_rows'][1]['answer'],'E')

    def test_batch_identity_and_debug_page_are_isolated(self):
        self.assertNotEqual(self.normal['signature'],self.normal['changed_signature'])
        self.assertNotEqual(self.normal['signature'],self.normal['private_signature'])

    def test_invalid_saved_choices_are_rejected(self):
        self.assertEqual([self.normal['sanitized'][k] for k in ('answer','confidence','reason_note')],['','',''])

    def test_download_failure_is_visible_and_preserves_form(self):
        result=self.run_ui(download_blocked=True)
        self.assertIn('下载失败',result['export_status']);self.assertEqual(result['rows'][0]['answer'],'B')

    def test_public_source_and_blank_csv_remain_blind(self):
        self.assertFalse(leakage_findings(self.html))
        self.assertFalse(leakage_findings(self.normal['blank_csv']))
        self.assertFalse(leakage_findings(self.normal['csv']))

    def test_single_plan_has_no_answer_or_download_controls(self):
        blind=next(iter(self.views))
        html=render_visual_page(self.catalog,{blind:self.views[blind]},[],self.cfg,single_plan=blind)
        self.assertNotIn('id="review-export-csv"',html);self.assertNotIn('const ReviewAnswers',html)


if __name__=='__main__':unittest.main()
