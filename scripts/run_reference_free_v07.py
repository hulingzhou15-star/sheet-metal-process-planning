"""Run steps 1-4: graph audit, expert-free input, candidates, local contrasts."""
import argparse
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.inference import load_inference_context, validate_facts, generate_candidates, select_diverse, sample_local_contrasts
from src.inference.generation import load_config, DEFAULT_CONFIG
from src.inference.audit import coverage_audit, near_pairs, supervision_audit
from src.inference.report import write_sampling_report
from src.constraints.v06 import HardConstraintEngine, SoftPriorEngine
from src.graph.builders import build_part_graph


def write_json(folder, name, value):
    path = folder / name
    if path.exists() and (path.is_symlink() or path.stat().st_nlink > 1):
        raise ValueError("Refuse aliased output")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def verify_expert_isolation(path, config):
    """Modify only temp ZIP copies; do not use Excel values as inference input."""
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(path) as z:
        members = {name: z.read(name) for name in z.namelist()}
    # Resolve Sheet1 through workbook relationships instead of assuming sheet1.xml.
    workbook = ET.fromstring(members["xl/workbook.xml"])
    sheet = next(s for s in workbook.find("s:sheets", ns) if s.attrib["name"] == "Sheet1")
    rel_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
    rel = next(r for r in ET.fromstring(members["xl/_rels/workbook.xml.rels"]) if r.attrib["Id"] == rel_id)
    target = rel.attrib["Target"]
    member = target.lstrip("/") if target.startswith("/") else "xl/"+target
    original = ET.fromstring(members[member])
    strings = []
    if "xl/sharedStrings.xml" in members:
        strings = ["".join(s.itertext()) for s in ET.fromstring(members["xl/sharedStrings.xml"])]
    def value(cell):
        if cell.attrib.get("t") == "inlineStr":
            return "".join(cell.itertext())
        v = cell.find("s:v", ns)
        return strings[int(v.text)] if cell.attrib.get("t") == "s" and v is not None else v.text if v is not None else None
    first = original.find("s:sheetData/s:row", ns)
    expert_cell = next((c for c in first if value(c) == "expert_station"), None)
    col = "".join(c for c in expert_cell.attrib["r"] if c.isalpha()) if expert_cell is not None else None
    settings = deepcopy(config)
    outcomes = {}
    with TemporaryDirectory(prefix="m3_expert_isolation_") as temp:
        for mode in ("original", "blank", "absent", "replaced"):
            if mode == "original" or col is None:
                variant = path
            else:
                tree = deepcopy(original)
                for row in tree.findall("s:sheetData/s:row", ns):
                    for cell in list(row):
                        cellcol = "".join(c for c in cell.attrib["r"] if c.isalpha())
                        if cellcol != col or (row.attrib["r"] == "1" and mode != "absent"):
                            continue
                        row.remove(cell)
                        if mode == "replaced":
                            replacement = ET.SubElement(row, "{"+ns["s"]+"}c", {"r": cell.attrib["r"], "t": "inlineStr"})
                            text = ET.SubElement(ET.SubElement(replacement, "{"+ns["s"]+"}is"), "{"+ns["s"]+"}t")
                            text.text = "INVALID_EXPERT_IGNORED"
                variant = Path(temp)/f"{mode}.xlsx"
                with ZipFile(variant, "w") as z:
                    for name, content in members.items():
                        z.writestr(name, ET.tostring(tree) if name == member else content)
            context = load_inference_context(variant)
            pool, run = generate_candidates(context, settings)
            selected, _ = select_diverse(context, pool, settings["generation"]["selected_target"])
            outcomes[mode] = {"facts_fingerprint": context.facts_fingerprint, "source_sha256": context.metadata_sha256,
                              "candidate_hashes": [r["plan_hash"] for r in pool],
                              "selected_hashes": [r["plan_hash"] for r in selected],
                              "seeds": [r["seed"] for r in run["attempts"]],
                              "random_objective_hashes": [r["costs_sha256"] for r in run["attempts"]]}
    original = outcomes["original"]
    checks = {key: all(v[key] == original[key] for v in outcomes.values())
              for key in original if key != "source_sha256"}
    return {"checks": checks, "passed": all(checks.values()), "outcomes": outcomes,
            "scope": "full configured candidate batch repeated per input variant; original workbook never written"}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=ROOT/"data/parts/P001/钣金加工数据表_P001.xlsx")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=ROOT/"outputs/m3_reference_free_v07")
    parser.add_argument("--historical-p001-audit", action="store_true",
                        help="Separate offline audit of old P001 reviews; never used by generation")
    args = parser.parse_args(argv)
    folder = args.output.resolve()
    if not folder.is_relative_to((ROOT/"outputs").resolve()):
        raise ValueError("Output must stay under outputs")
    folder.mkdir(parents=True, exist_ok=True)
    existing_pool = folder/"selected_candidates.jsonl"
    owner_report = folder/"generation_report.json"
    if existing_pool.exists() and (not owner_report.exists() or
            json.loads(owner_report.read_text(encoding="utf-8")).get("config", {}).get("schema_version") != "m3.reference_free.v0.7"):
        raise ValueError("Refuse to overwrite candidates from another version")
    config = load_config(args.config)
    context = load_inference_context(args.input)
    write_json(folder, "fact_validation.json", validate_facts(context.part_data).to_dict())
    progress = lambda event: print(json.dumps(event), flush=True)
    pool, run = generate_candidates(context, config, progress=progress)
    selected, selection = select_diverse(context, pool, config["generation"]["selected_target"])
    write_json(folder, "generation_report.json", {"run": run, "selection": selection, "config": config,
                                                "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
                                                "source_sha256_for_provenance_only": context.metadata_sha256})
    for name, records in (("candidate_pool.jsonl", pool), ("selected_candidates.jsonl", selected)):
        target = folder/name
        if target.exists() and (target.is_symlink() or target.stat().st_nlink > 1):
            raise ValueError("Refuse output alias")
        target.write_text("".join(json.dumps(r, ensure_ascii=False, separators=(",", ":"))+"\n" for r in records), encoding="utf-8")
    if not pool:
        write_json(folder, "local_contrasts.json", {"status": "skipped_no_hard_feasible_base", "comparisons": []})
        write_json(folder, "completion.json", {"generation_status": run["status"], "pool_count": 0,
                                              "local_sampling_status": "skipped_no_hard_feasible_base",
                                              "source_unchanged": hashlib.sha256(args.input.read_bytes()).hexdigest() == context.metadata_sha256})
        return 0
    static = build_part_graph(context.part_data)
    base = selected[0]["assignments"]
    rows = near_pairs(context, static, config["local_contrasts"]["analysis_k"])
    contrasts = sample_local_contrasts(context, base, rows, config, progress=progress)
    write_json(folder, "local_contrasts.json", contrasts)
    write_json(folder, "near_pair_catalogue.json", {"purpose": "observation_and_sampling_only", "pairs": rows})
    write_json(folder, "graph_coverage.json", coverage_audit(context, {selected[0]["plan_id"]: base}, static=static))
    soft = SoftPriorEngine(context)
    soft_rows = []
    for row in selected:
        evaluation = soft.evaluate(row["assignments"])
        soft_rows.append({"plan_id": row["plan_id"], "components": {k: {a: b for a, b in v.items() if a != "audit"}
                                                                  for k, v in evaluation["components"].items()},
                          "total_soft_penalty": evaluation["total_soft_penalty"],
                          "direction": evaluation["unweighted_components"], "used_in_selection": False})
    write_json(folder, "selected_soft_diagnostics.json", soft_rows)
    isolation = verify_expert_isolation(args.input, config)
    write_json(folder, "expert_isolation.json", isolation)
    if not isolation["passed"]:
        raise RuntimeError("Expert isolation verification failed")
    if args.historical_p001_audit:
        mapping = json.loads((ROOT/"outputs/m3_human_review/internal/blind_plan_mapping.json").read_text(encoding="utf-8"))
        old = {r["plan_id"]: r["assignments"] for r in (json.loads(line) for line in
               (ROOT/"outputs/m3_candidates/selected_candidates.jsonl").read_text(encoding="utf-8").splitlines())}
        plans = {blind: old[mapping["blind_to_internal"][blind]] for blind in ("PLAN_A02", "PLAN_A06", "PLAN_A08")}
        write_json(folder, "historical_graph_coverage.json", coverage_audit(context, plans, static=static))
        impact = json.loads((ROOT/"outputs/m3_rule_revision_v06/expert_review_24_pair_impact.json").read_text(encoding="utf-8"))
        provenance = impact["provenance"]
        source_checks = {"raw_sha256": context.metadata_sha256 == provenance["raw_sha256"]}
        for field, path in (("blind_mapping_sha256", ROOT/"outputs/m3_human_review/internal/blind_plan_mapping.json"),
                            ("original_form_sha256", ROOT/"outputs/m3_human_review/expert_facing/comparison_pairs.csv"),
                            ("image_sha256", Path(provenance["image_path"]))):
            source_checks[field] = hashlib.sha256(path.read_bytes()).hexdigest() == provenance[field] if path.exists() else None
        if any(value is False for value in source_checks.values()):
            raise ValueError(f"Historical review source has changed: {source_checks}")
        with (ROOT/"outputs/m3_human_review/expert_facing/comparison_pairs.csv").open(encoding="utf-8-sig", newline="") as stream:
            pairs = list(csv.DictReader(stream))
        supervision = supervision_audit(impact, mapping, pairs)
        supervision["source_hash_checks"] = source_checks
        write_json(folder, "historical_supervision_audit.json", supervision)
    write_json(folder, "completion.json", {"steps": ["graph_and_supervision_audit", "expert_free_input", "candidate_generation", "local_comparison_sampling"],
                                          "pool_count": len(pool), "selected_count": len(selected),
                                          "local_comparison_count": contrasts["comparison_count"],
                                          "all_new_plans_hard_feasible": all(HardConstraintEngine(context).evaluate(r["assignments"]).feasible for r in pool),
                                          "trained_model": False, "new_blind_review_pack": False,
                                          "source_unchanged": hashlib.sha256(args.input.read_bytes()).hexdigest() == context.metadata_sha256})
    write_sampling_report(folder, context, pool, selected, contrasts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
