"""Read via M1 once, validate, then call the object-only graph builder."""
import argparse
import hashlib
import json
from pathlib import Path

from src.data import load_excel, validate
from . import build_part_graph, build_plan_overlay
from .report import build_statistics, write_graph_outputs


def main(argv=None):
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Milestone 2 P001 graph export (no training)")
    parser.add_argument("--input", type=Path, default=root / "data/parts/P001/钣金加工数据表_P001.xlsx")
    parser.add_argument("--plan", type=Path, help="Optional complete Operation -> Station JSON mapping; default is expert reference")
    parser.add_argument("--output", type=Path, default=root / "outputs/milestone2")
    parser.add_argument("--k", type=int, default=8)
    args = parser.parse_args(argv)
    data = load_excel(args.input)
    validation = validate(data)
    if not validation.passed:
        print(json.dumps({"passed": False, "validation": validation.to_dict()}, ensure_ascii=True))
        return 1
    static = build_part_graph(data, k=args.k)
    plan = json.loads(args.plan.read_text(encoding="utf-8")) if args.plan else data.expert_reference_plan
    if args.plan and not isinstance(plan, dict):
        parser.error("--plan must contain a JSON object mapping Operation IDs to Station IDs")
    overlay = build_plan_overlay(static, plan)
    after_sha256 = hashlib.sha256(args.input.read_bytes()).hexdigest()
    if after_sha256 != data.source_sha256:
        raise RuntimeError("Source workbook bytes changed during graph construction")
    provenance = {"source_path": str(data.source_path), "source_sha256_before": data.source_sha256,
                  "source_sha256_after": after_sha256, "raw_unchanged": True,
                  "milestone1_validation_counts": validation.counts,
                  "plan_source": str(args.plan) if args.plan else "ExpertReferencePlan"}
    write_graph_outputs(static, overlay, args.output, project_root=root, provenance=provenance)
    stats = build_statistics(static, overlay)
    print(json.dumps({"graphs": stats["graphs"], "semantic_edge_counts": stats["semantic_edge_counts"],
                      "knn_tie_nodes": len(static.knn_diagnostics.nodes_with_distance_ties),
                      "knn_cutoff_tie_nodes": len(static.knn_diagnostics.nodes_with_cutoff_ties),
                      "raw_unchanged": True}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
