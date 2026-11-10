"""Generate, validate and audit one reproducible M3 v0.2 candidate batch."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ortools

from src.candidate import CandidatePlanGenerator, GenerationConsistencyError, load_generator_config, plan_hash
from src.candidate.config import DEFAULT_CONFIG_PATH
from src.candidate.report import candidate_statistics, generation_markdown
from src.candidate.serialization import write_candidate_outputs
from src.constraints import load_constraint_context, load_config
from src.constraints.config import DEFAULT_CONFIG_PATH as CONSTRAINT_CONFIG
from src.data import validate


def frozen_paths(root):
    paths = [root / name for name in ("PROJECT_SPEC.md", "README.md", "requirements.txt", "src/__init__.py",
                                     "configs/m3_constraints.yaml", "scripts/evaluate_plan_constraints.py")]
    for folder in ("src/data", "src/graph", "src/constraints"):
        paths.extend((root / folder).rglob("*.py"))
    paths.extend(p for p in (root / "tests").glob("*.py") if not p.name.startswith("test_m3_candidate") and p.name != "candidate_fixtures.py")
    paths.extend(p for p in (root / "scripts").glob("*.py") if p.name != "generate_candidate_plans.py")
    paths.extend(p for p in (root / "docs").glob("*.md") if p.name != "MILESTONE_3_CANDIDATE_GENERATOR.md")
    paths.extend(p for p in (root / "data/parts/P001").glob("*.xlsx") if not p.name.startswith("~"))
    paths.extend(p for p in (root / "outputs/m3_diagnostics").glob("*") if p.is_file())
    return sorted(set(p for p in paths if p.is_file()))


def capture_manifest(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in frozen_paths(root)}


def check_integrity(root, manifest):
    records = [{"path": path, "before_sha256": digest,
                "after_sha256": hashlib.sha256((root / path).read_bytes()).hexdigest() if (root / path).exists() else None}
               for path, digest in manifest.items()]
    for record in records:
        record["unchanged"] = record["before_sha256"] == record["after_sha256"]
    return {"checked_file_count": len(records), "all_unchanged": all(r["unchanged"] for r in records), "files": records}


def main(argv=None):
    parser = argparse.ArgumentParser(description="M3 v0.2 CP-SAT candidate generation; no preference labels")
    parser.add_argument("--input", type=Path, default=ROOT / "data/parts/P001/钣金加工数据表_P001.xlsx")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--constraint-config", type=Path, default=CONSTRAINT_CONFIG)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/m3_candidates")
    args = parser.parse_args(argv)
    folder = args.output.resolve()
    if not folder.is_relative_to((ROOT / "outputs").resolve()):
        raise ValueError("Candidate outputs must stay inside project outputs/")
    folder.mkdir(parents=True, exist_ok=True)
    manifest_path = folder / "frozen_input_manifest.json"
    if manifest_path.is_symlink() or (manifest_path.exists() and manifest_path.stat().st_nlink > 1):
        raise ValueError("Refuse a manifest alias")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else capture_manifest(ROOT)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    initial = check_integrity(ROOT, manifest)
    if not initial["all_unchanged"]:
        raise RuntimeError("Frozen files changed since baseline; inspect frozen manifest")
    config, constraint_config = load_generator_config(args.config), load_config(args.constraint_config)
    context = load_constraint_context(args.input)
    validation = validate(context.part_data)
    if not validation.passed:
        raise ValueError(f"Frozen M1 validation failed: {validation.to_dict()}")
    try:
        generator = CandidatePlanGenerator(context, config, constraint_config)
        pool, selected, run = generator.run(progress=lambda event: print(json.dumps(event), flush=True))
        stats = candidate_statistics(generator, pool, selected, run)
        for candidate in selected:
            generator.hard.require_feasible(candidate["assignments"])
            guard = generator.guard.evaluate(candidate["assignments"])
            if generator.guardrail_enforced and guard["guardrail_violation"]:
                raise GenerationConsistencyError("Selected candidate fails G1", guard)
        integrity = check_integrity(ROOT, manifest)
        (folder / "frozen_integrity_check.json").write_text(json.dumps(integrity, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        if not integrity["all_unchanged"]:
            raise RuntimeError("Generation changed a frozen input")
        report = {"schema_version": "m3.v0.2", "status": "generated", "run": run, "statistics": stats,
                  "expert_reference": {"plan_id": "EXPERT_REFERENCE", "assignments": generator.expert,
                                       "plan_hash": plan_hash(generator.expert), "counted_as_candidate": False},
                  "runtime": {"ortools_version": ortools.__version__, "python_version": sys.version.split()[0]},
                  "provenance": {"input_path": str(args.input.resolve()), "input_sha256": context.metadata_sha256,
                                 "generator_config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
                                 "constraint_config_sha256": hashlib.sha256(args.constraint_config.read_bytes()).hexdigest()},
                  "frozen_integrity": integrity,
                  "interpretation": "Candidate Plans only. Heuristic penalties are not quality, preference labels or GNN labels."}
        paths = write_candidate_outputs(folder, pool, selected, report, project_root=ROOT)
    except (GenerationConsistencyError, ValueError) as exc:
        report = {"status": "stopped", "error": str(exc), "details": getattr(exc, "details", None),
                  "frozen_integrity": check_integrity(ROOT, manifest)}
        (folder / "candidate_generation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        (folder / "candidate_generation_report.md").write_text(generation_markdown(report), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=True))
        return 1
    print(json.dumps({"status": "generated", "movable": run["movable_operation_count"],
                      "maximum_distance": {k: v for k, v in run["maximum_distance"].items() if k != "assignments"},
                      "pool_size": len(pool), "selected_count": len(selected),
                      "bucket_selected_counts": {b: r["selected_count"] for b, r in stats["buckets"].items()},
                      "frozen_integrity": integrity["all_unchanged"], "outputs": paths}, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
