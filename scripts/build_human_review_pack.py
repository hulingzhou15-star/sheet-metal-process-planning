"""Build one review pack from frozen candidates; no solving or labeling."""
from collections import Counter
from copy import deepcopy
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.generate_candidate_plans import check_integrity
from src.candidate.distance import PlanDistance, plan_hash
from src.candidate.serialization import read_jsonl
from src.constraints import HardConstraintEngine, SoftPriorEngine, StationOverloadGuardrail, load_constraint_context
from src.data import validate
from src.review import assign_blind_ids, blind_comparisons, load_review_config, select_pairs, select_representatives
from src.review.config import BUCKET_ORDER, DEFAULT_CONFIG_PATH
from src.review.report import aggregate_report_markdown, distribution, review_statistics, safe_write, write_review_pack


def capture_review_manifest(root):
    paths = [root / name for name in ("PROJECT_SPEC.md", "README.md", "requirements.txt", "requirements-m3.txt")]
    for folder in ("src", "tests", "scripts", "docs", "configs", "data/parts/P001", "outputs/m3_diagnostics", "outputs/m3_candidates"):
        for path in (root / folder).rglob("*"):
            if (path.is_file() and path.suffix in (".py", ".md", ".yaml", ".xlsx", ".json", ".jsonl", ".csv", ".txt")
                    and "__pycache__" not in path.parts and not path.name.startswith("~")
                    and not path.is_relative_to(root / "src/review") and not path.name.startswith("test_m3_review")
                    and path.name not in ("review_fixtures.py", "build_human_review_pack.py", "m3_human_review.yaml", "MILESTONE_3_HUMAN_REVIEW_PACK.md")):
                paths.append(path)
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(set(paths))}


def read_source_batch(context, candidate_dir, config):
    folder = Path(candidate_dir)
    candidates = read_jsonl(folder / "selected_candidates.jsonl")
    expected = config["inputs"]
    if len(candidates) != expected["expected_selected_count"] or Counter(c["expert_distance"]["bucket"] for c in candidates) != {
            bucket: expected["expected_per_bucket"] for bucket in BUCKET_ORDER}:
        raise ValueError("Frozen selected batch must contain 60 candidates, 20 in each bucket")
    expert_record = json.loads((folder / "expert_reference.json").read_text(encoding="utf-8"))
    hard = HardConstraintEngine(context)
    expert = hard.require_feasible(context.expert_reference_plan)
    if expert_record["plan_id"] != "EXPERT_REFERENCE" or expert_record["assignments"] != expert or expert_record["plan_hash"] != plan_hash(expert):
        raise ValueError("Persisted expert reference differs from the frozen M1 reference")
    distance, guard = PlanDistance.from_context(context), StationOverloadGuardrail(context)
    if config["representatives"]["high_load_station"] not in context.stations:
        raise ValueError("Configured high-load Station does not exist")
    for candidate in candidates:
        hard.require_feasible(candidate["assignments"])
        if guard.evaluate(candidate["assignments"])["guardrail_violation"]:
            raise ValueError("Persisted selected candidate fails frozen G1")
        raw = distance.raw(candidate["assignments"], expert)
        if raw != candidate["expert_distance"]["raw"] or raw/len(distance.movable_operation_ids) != candidate["expert_distance"]["normalized"]:
            raise ValueError("Persisted candidate expert distance differs from frozen distance implementation")
    with (folder / "selected_candidate_summary.csv").open(encoding="utf-8-sig", newline="") as handle:
        summary = list(csv.DictReader(handle))
    by_id = {c["plan_id"]: c for c in candidates}
    if len(summary) != len(candidates) or {r["plan_id"] for r in summary} != set(by_id):
        raise ValueError("Candidate summary does not match selected JSONL")
    for row in summary:
        candidate = by_id[row["plan_id"]]
        if row["plan_hash"] != candidate["plan_hash"] or float(row["total_soft_penalty"]) != candidate["soft"]["total_soft_penalty"]:
            raise ValueError("Candidate summary identity or score mismatch")
        if int(row["distance_to_expert_raw"]) != candidate["expert_distance"]["raw"] or row["bucket"] != candidate["expert_distance"]["bucket"]:
            raise ValueError("Candidate summary distance or bucket mismatch")
    with (folder / "selected_plan_assignments.csv").open(encoding="utf-8-sig", newline="") as handle:
        matrix = list(csv.DictReader(handle))
    if len(matrix) != len(context.operations) or {row["operation_id"] for row in matrix} != set(context.operations):
        raise ValueError("Assignment matrix does not cover Operations exactly once")
    for row in matrix:
        op = row["operation_id"]
        if row["expert_station"] != expert[op] or any(row[c["plan_id"]] != c["assignments"][op] for c in candidates):
            raise ValueError("Assignment matrix differs from frozen Plans")
    return candidates, expert, distance


def build_review_data(context, candidates, expert, distance, config):
    representatives, selection_audit = select_representatives(candidates, distance, config)
    source_by_id = {candidate["plan_id"]: candidate for candidate in candidates}
    plans = {record["internal_id"]: deepcopy(source_by_id[record["internal_id"]]) for record in representatives}
    score = SoftPriorEngine(context).evaluate(expert)
    plans["EXPERT_REFERENCE"] = {"plan_id": "EXPERT_REFERENCE", "assignments": dict(expert), "plan_hash": plan_hash(expert),
                                "expert_distance": {"raw": 0, "normalized": 0.0},
                                "soft": {"total_soft_penalty": score["total_soft_penalty"], "components": score["components"]}}
    mapping = assign_blind_ids(plans, config["blinding"]["plan_blinding_seed"])
    for record in representatives:
        record["blind_id"] = mapping["internal_to_blind"][record["internal_id"]]
    pairs, pair_audit = select_pairs(representatives, plans, distance, config=config)
    comparisons = blind_comparisons(pairs, mapping, config["blinding"]["comparison_blinding_seed"],
                                    shuffle_order=config["blinding"]["shuffle_comparison_order"])
    return representatives, selection_audit, plans, mapping, comparisons, pair_audit


def main(argv=None):
    parser = argparse.ArgumentParser(description="M3 v0.3 Human Review Pack from frozen 60 selected candidates")
    parser.add_argument("--input", type=Path, default=ROOT / "data/parts/P001/钣金加工数据表_P001.xlsx")
    parser.add_argument("--candidate-dir", type=Path, default=ROOT / "outputs/m3_candidates")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/m3_human_review")
    args = parser.parse_args(argv)
    folder = args.output.resolve()
    if not folder.is_relative_to((ROOT / "outputs").resolve()):
        raise ValueError("Review output must remain below project outputs/")
    manifest_path = folder / "internal/frozen_input_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else capture_review_manifest(ROOT)
    before = check_integrity(ROOT, manifest)
    if not before["all_unchanged"]:
        raise ValueError("Frozen input changed since review baseline")
    safe_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", root=folder)
    context = load_constraint_context(args.input)
    validation = validate(context.part_data)
    if not validation.passed:
        raise ValueError("Frozen M1 validation failed")
    config = load_review_config(args.config)
    candidates, expert, distance = read_source_batch(context, args.candidate_dir, config)
    representatives, selection_audit, plans, mapping, pairs, pair_audit = build_review_data(context, candidates, expert, distance, config)
    written = write_review_pack(folder, context, plans, representatives, selection_audit, pairs, pair_audit, mapping, config, project_root=ROOT)
    stats = review_statistics(representatives, pairs, plans, distance)
    integrity = check_integrity(ROOT, manifest)
    if not integrity["all_unchanged"]:
        raise ValueError("Review build changed a frozen source or candidate file")
    report = {"schema_version": "m3.v0.3", "audience": "internal_researcher", "status": "built",
              "representatives": representatives, "statistics": stats, "selection_audit": selection_audit,
              "comparison_pairs": pairs, "pair_design_audit": pair_audit,
              "blinding_audit": written["blinding_audit"], "comparison_forms": {"row_count": written["blank_form_count"], "all_answers_blank": written["all_answers_blank"]},
              "html_validation": {"source": written["html_source_validation"], "visual_preview": {"completed": False, "reason": "Build script does not perform browser visual preview."}},
              "comparison_readability": {"differing_operation_count": distribution(d["differing_operation_count"] for d in written["public_diffs"].values()),
                                         "bend_difference_count": distribution(len(d["bend_changes"]) for d in written["public_diffs"].values())},
              "configuration": config, "frozen_integrity": integrity,
              "provenance": {"selected_candidates_sha256": hashlib.sha256((args.candidate_dir / "selected_candidates.jsonl").read_bytes()).hexdigest(),
                             "workbook_sha256": context.metadata_sha256, "review_config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest()},
              "outputs": {"html": written["expert_facing_html"], "blank_comparison_csv": written["expert_facing_form"]},
              "labels_generated": False}
    safe_write(folder / "human_review_pack_report.json", json.dumps(report, ensure_ascii=False, indent=2)+"\n", root=folder)
    safe_write(folder / "human_review_pack_report.md", aggregate_report_markdown(report), root=folder)
    safe_write(folder / "internal/frozen_integrity_check.json", json.dumps(integrity, ensure_ascii=False, indent=2)+"\n", root=folder)
    safe_write(folder / "internal/blinding_leakage_check.json", json.dumps(written["blinding_audit"], ensure_ascii=False, indent=2)+"\n", root=folder)
    safe_write(folder / "internal/html_validation.json", json.dumps(report["html_validation"], ensure_ascii=False, indent=2)+"\n", root=folder)
    print(json.dumps({"representatives": len(representatives), "bucket_counts": stats["bucket_counts"], "comparisons": len(pairs),
                      "group_counts": stats["comparison_group_counts"], "blind_plan_count": len(mapping["blind_to_internal"]),
                      "blinding_pass": written["blinding_audit"]["passed"], "public_files_scanned": written["blinding_audit"]["file_count"],
                      "all_answers_blank": written["all_answers_blank"], "frozen_integrity": integrity["all_unchanged"], "outputs": report["outputs"]}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
