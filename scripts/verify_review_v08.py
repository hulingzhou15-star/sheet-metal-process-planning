"""Regression, archive/CSV checks and scoped preservation for review batch R02."""
import csv
import hashlib
from io import StringIO
import json
from pathlib import Path
import sys
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    folder = ROOT/"outputs/m3_review_v08"
    save = lambda name, data: (folder/"internal"/name).write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    design = json.loads((folder/"internal/comparison_design.json").read_text(encoding="utf-8"))
    with (folder/"expert_facing/comparison_pairs.csv").open(encoding="utf-8-sig", newline="") as stream:
        blank = list(csv.DictReader(stream))
    with (folder/"internal/browser_export_check.csv").open(encoding="utf-8-sig", newline="") as stream:
        exported = list(csv.DictReader(stream))
    expected = [(r["comparison_id"], r["blind_plan_A"], r["blind_plan_B"]) for r in design["comparisons"]]
    identity = lambda rows: [(r["comparison_id"], r["plan_A"], r["plan_B"]) for r in rows]
    browser = json.loads((folder/"internal/browser_qa.json").read_text(encoding="utf-8"))
    filled = [r for r in exported if r["answer"]]
    checks = {"blank_csv_identity": identity(blank) == expected,
              "blank_csv_answers": all(not r[k] for r in blank for k in ("answer", "confidence", "reason_note")),
              "browser_export_identity": identity(exported) == expected,
              "browser_export_test_answer": len(filled) == 1 and filled[0]["answer"] == "D" and filled[0]["confidence"] == "5",
              "browser_export_unicode_note": len(filled) == 1 and filled[0]["reason_note"] == '检查O203：包含逗号, 引号"和换行\n需复核。',
              "browser_qa_passed": browser["passed"]}
    archive = folder/"P001_R02_review_pack.zip"
    with ZipFile(archive) as zipfile:
        checks["zip_crc_ok"] = zipfile.testzip() is None
        checks["zip_has_no_internal_files"] = not any("internal" in name or "completed" in name for name in zipfile.namelist())
        checks["zip_csv_blank"] = zipfile.read("comparison_pairs.csv") == (folder/"expert_facing/comparison_pairs.csv").read_bytes()
    save("delivery_verification.json", {"passed": all(checks.values()), "checks": checks,
                                         "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()})
    stream = StringIO()
    suite = unittest.defaultTestLoader.discover(str(ROOT/"tests"))
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    (folder/"internal/test_results.txt").write_text(stream.getvalue(), encoding="utf-8")
    historical_failures = {
        "test_m3_candidate_generator.CandidateGeneratorTests.test_p001_small_batch_post_selection_report_and_frozen_integrity",
        "test_m3_review_blinding.BlindingTests.test_frozen_files_unchanged",
        "test_m3_visual_readability.RotationReadabilityTests.test_plan_and_comparison_models_equal_frozen_v04",
        "test_m3_visual_readability.RotationReadabilityTests.test_v04_output_preserved_and_only_visual_paths_allowed",
        "test_m3_visual_review_pack.ReviewPackTests.test_frozen_integrity_old_and_v03_inputs"}
    unexpected = sorted({test.id() for test, _ in result.failures}-historical_failures)
    tests = {"run": result.testsRun, "passed": result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),
             "failures": [{"test": test.id(), "traceback": trace} for test, trace in result.failures],
             "errors": [{"test": test.id(), "traceback": trace} for test, trace in result.errors],
             "skipped": len(result.skipped), "fully_green": result.wasSuccessful(), "unexpected_failure_ids": unexpected}
    save("test_results.json", tests)
    baseline = json.loads((folder/"baseline_manifest.json").read_text(encoding="utf-8"))
    allowed = {"src/visualization/geometry_adapter.py"}
    changes, unexpected_changes = [], []
    for name, digest in baseline.items():
        path = ROOT/name
        after = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        if after != digest:
            row = {"path": name, "before": digest, "after": after}
            changes.append(row)
            if name not in allowed:
                unexpected_changes.append(row)
    save("frozen_integrity.json", {"checked_existing_files": len(baseline), "unchanged": len(baseline)-len(changes),
                                   "authorized_visual_adapter_changes": [r for r in changes if r["path"] in allowed],
                                   "unexpected_changes": unexpected_changes, "protected_files_unchanged": not unexpected_changes})
    print(json.dumps({"delivery_checks_passed": all(checks.values()), "tests": tests["run"], "passed": tests["passed"],
                      "failures": len(tests["failures"]), "errors": len(tests["errors"]), "unexpected_failures": unexpected,
                      "protected_files_unchanged": not unexpected_changes, "changed_existing_paths": [r["path"] for r in changes]}), flush=True)
    return 0 if all(checks.values()) and not unexpected and not result.errors and not unexpected_changes else 1


if __name__ == "__main__":
    raise SystemExit(main())
