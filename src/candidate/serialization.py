"""Portable candidate records and an Operation-by-candidate comparison matrix."""
import csv
import json
from pathlib import Path

from .distance import plan_hash
from .report import generation_markdown


def write_jsonl(path, records):
    with Path(path).open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            if plan_hash(record["assignments"]) != record["plan_hash"]:
                raise ValueError("Candidate hash does not match assignments")
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)+"\n")


def read_jsonl(path):
    records = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    for record in records:
        if plan_hash(record["assignments"]) != record["plan_hash"]:
            raise ValueError("Serialized candidate hash does not match assignments")
    return records


def write_assignment_matrix(path, operation_ids, expert, selected):
    if len({c["plan_id"] for c in selected}) != len(selected):
        raise ValueError("Assignment matrix needs unique candidate IDs")
    for assignments in [expert, *(c["assignments"] for c in selected)]:
        if set(assignments) != set(operation_ids):
            raise ValueError("Assignment matrix needs complete Plans for every Operation")
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["operation_id", "expert_station", *(c["plan_id"] for c in selected)])
        writer.writerows([op, expert[op], *(c["assignments"][op] for c in selected)] for op in operation_ids)


def write_candidate_outputs(output_dir, pool, selected, report, *, project_root):
    root, folder = Path(project_root).resolve(), Path(output_dir).resolve()
    if not folder.is_relative_to((root / "outputs").resolve()):
        raise ValueError("Candidate outputs must stay inside project outputs/")
    names = ("candidate_pool.jsonl", "selected_candidates.jsonl", "selected_candidate_summary.csv",
             "selected_plan_assignments.csv", "candidate_generation_report.json", "candidate_generation_report.md",
             "expert_reference.json", "operation_movement_frequency.csv")
    for name in names:
        target = folder / name
        if target.is_symlink() or (target.exists() and target.stat().st_nlink > 1):
            raise ValueError("Refuse an output alias or hard link")
    folder.mkdir(parents=True, exist_ok=True)
    write_jsonl(folder / names[0], pool)
    write_jsonl(folder / names[1], selected)
    fields = ["plan_id", "bucket", "distance_to_expert_raw", "distance_to_expert_norm", "P1", "P2", "P3",
              "total_soft_penalty", "guardrail_pass", "station_load_summary", "plan_hash"]
    with (folder / names[2]).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for candidate in selected:
            writer.writerow({"plan_id": candidate["plan_id"], "bucket": candidate["expert_distance"]["bucket"],
                             "distance_to_expert_raw": candidate["expert_distance"]["raw"],
                             "distance_to_expert_norm": candidate["expert_distance"]["normalized"],
                             **{f"P{i}": candidate["soft"]["components"][name]["raw_component"] for i, name in enumerate(
                                 ("bend_area_ordering", "precedence_extra_gap", "type_fragmentation"), 1)},
                             "total_soft_penalty": candidate["soft"]["total_soft_penalty"], "guardrail_pass": candidate["guardrail"]["pass"],
                             "station_load_summary": json.dumps({r["station_id"]: r["plan_load"] for r in candidate["guardrail"]["stations"]},
                                                                sort_keys=True, separators=(",", ":")),
                             "plan_hash": candidate["plan_hash"]})
    expert = report["expert_reference"]
    write_assignment_matrix(folder / names[3], sorted(expert["assignments"]), expert["assignments"], selected)
    (folder / names[4]).write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    (folder / names[5]).write_text(generation_markdown(report), encoding="utf-8")
    (folder / names[6]).write_text(json.dumps(expert, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    rows = report["statistics"]["operation_movement_frequency"]
    movement_fields = ["operation_id", "operation_type", "expert_station", "changed_count", "changed_fraction", "selected_station_distribution"]
    with (folder / names[7]).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=movement_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "selected_station_distribution": json.dumps(row["selected_station_distribution"], sort_keys=True)})
    return [str(folder / name) for name in names]
