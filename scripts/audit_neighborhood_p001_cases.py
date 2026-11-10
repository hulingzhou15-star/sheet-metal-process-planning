"""Offline regression only. Human-highlighted P001 pairs never enter generation policy."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.candidate.distance import plan_hash
from src.inference.input import load_inference_context
from src.neighborhood import NeighborhoodContext, load_config, propose_pair_alternatives
from scripts.run_neighborhood_v01 import write_json, build_page


def main():
    out = ROOT/"outputs/neighborhood_v01"
    context = load_inference_context(ROOT/"data/parts/P001/钣金加工数据表_P001.xlsx")
    snapshot = json.loads((out/"neighborhood_context.json").read_text(encoding="utf-8"))
    saved = snapshot.pop("fingerprint")
    n = NeighborhoodContext(snapshot)
    assert n.fingerprint == saved
    config = load_config()
    source = ROOT/"outputs/m3_reference_free_v07/candidate_pool.jsonl"
    old = next(r for r in map(json.loads, source.read_text(encoding="utf-8").splitlines()) if r["plan_id"] == "RF_011")
    assert old["plan_hash"] == plan_hash(old["assignments"]) == "540a3a9eedef959c8bd348c0cb2e55b1dd29bd71fb86d3a8092b25e514e21eed"
    cases = [("O066", "O200"), ("O015", "O196"), ("O031", "O196"), ("O035", "O196")]
    trials, cases_out = [], []
    pool = {old["plan_hash"]: {"plan_hash": old["plan_hash"], "assignments": old["assignments"]}}
    for a, b in cases:
        key = "__".join(sorted((a, b)))
        assert old["assignments"][a] == old["assignments"][b] == "OP30"
        found = next((r for r in n.external_relations if r["pair_id"] == key), None)
        assert found, key
        rows = propose_pair_alternatives(context, n, old["assignments"], key, config)
        for r in rows:
            r["trial_id"] = f"KNOWN_{len(trials)+1:03d}"
            if "alternative" in r:
                pool[r["alternative"]["plan_hash"]] = r["alternative"]
            trials.append(r)
        cases_out.append({"pair_id": key, "detected_as_external_neighbor": True, "historical_same_station": "OP30",
                          "feasible_separation_branches": [r["branch"] for r in rows if "alternative" in r],
                          "all_changes_by_trial": [{"trial_id": r["trial_id"], "all_changes": r.get("all_changes", [])} for r in rows],
                          "preference_label_generated": False})
    result = {"purpose": "offline_regression_of_human_highlighted_cases_only", "used_in_new_generation": False,
              "historical_plan_id": old["plan_id"], "historical_plan_hash": old["plan_hash"],
              "historical_file_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "cases": cases_out, "trials": trials, "pool": list(pool.values()),
              "summary": {"cases": len(cases), "detected": len(cases_out),
                          "cases_with_legal_separation": sum(bool(r["feasible_separation_branches"]) for r in cases_out)}}
    write_json(out, "known_case_audit.json", result)
    # Render an independently named page, without touching the candidate pack.
    temp_page = out/"known_cases"
    temp_page.mkdir(exist_ok=True)
    scenes = json.loads((out/"sensitivity.json").read_text(encoding="utf-8"))
    build_page(context, config, scenes, result, temp_page)
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
