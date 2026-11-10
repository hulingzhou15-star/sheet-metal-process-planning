from copy import deepcopy
from dataclasses import replace
from itertools import product
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import openpyxl
from ortools.sat.python import cp_model

from src.data.loader import HEADERS
from src.data.schema import Operation, Station, Geometry, GeometrySegment
from src.constraints.context import METADATA_HEADERS, FixedAssignment, ContextValidationError
from src.constraints.v06 import HardConstraintEngine
from src.inference import (InferenceFacts, InferenceContext, load_inference_context,
                           generate_candidates, select_diverse, sample_local_contrasts)
from src.inference.input import validate_facts
from src.inference.generation import load_config, validate_config, solver_for, derived_seed
from src.inference.audit import coverage_audit, near_pairs, supervision_audit
from src.graph.builders import build_part_graph, build_graph2
from src.graph.plan_overlay import build_plan_overlay
from scripts.run_reference_free_v07 import verify_expert_isolation

ROOT = Path(__file__).resolve().parents[1]
P001 = next((ROOT/"data/parts/P001").glob("*P001.xlsx"))


def facts_context(*, precedence=False, fixed=False, geometry=False, one_station=False):
    operations = (Operation("P", "A", "FLANGING", geometry_id="GA" if geometry else None,
                            cad_parent_operation_id="B" if precedence else None),
                  Operation("P", "B", "IRREGULAR_PUNCH", geometry_id="GB" if geometry else None),
                  Operation("P", "T", "TRIM"))
    stations = tuple(Station("P", name, order, frozenset({"FLANGING", "IRREGULAR_PUNCH", "TRIM"}))
                     for name, order in (("Z", 10), ("A", 40), ("M", 90))[:1 if one_station else 3])
    geoms = () if not geometry else (
        Geometry("P", "GA", (GeometrySegment(1, "CIRCLE", center_x=0, center_y=0, radius=1),)),
        Geometry("P", "GB", (GeometrySegment(1, "LINE", start_x=2, start_y=0, end_x=3, end_y=0),)))
    facts = InferenceFacts(operations, geoms, stations, ())
    return InferenceContext(facts, (FixedAssignment("T", "Z"),) if fixed else ())


def small_config():
    cfg = load_config()
    cfg["generation"].update(pool_target=5, selected_target=3, max_attempts=8)
    cfg["local_contrasts"]["max_pairs"] = 3
    return cfg


def workbook(path, *, expert_mode="valid"):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, header in {**HEADERS, **METADATA_HEADERS}.items():
        ws = wb.create_sheet(name)
        ws.append(list(header))
    wb["Sheet1"].append(["P", "A", "test", "CIRCLE_PUNCH", "NOT_APPLICABLE", None, None, None, "Z", None])
    wb["Sheet3"].append(["P", "Z", 10, "CIRCLE_PUNCH"])
    wb["Sheet3"].append(["P", "A", 40, "CIRCLE_PUNCH"])
    if expert_mode == "blank":
        wb["Sheet1"]["I2"] = None
    elif expert_mode == "absent":
        wb["Sheet1"].delete_cols(9)
    elif expert_mode == "invalid":
        wb["Sheet1"]["I2"] = "NOT_A_STATION"
    elif expert_mode == "formula":
        wb["Sheet1"]["I2"] = "=1/0"
    elif expert_mode == "error":
        wb["Sheet1"]["I2"] = "#REF!"
    wb.save(path)
    wb.close()


class FactInputTests(unittest.TestCase):
    def test_no_expert_object_or_placeholder(self):
        c = facts_context()
        self.assertFalse(hasattr(c.part_data, "expert_reference_plan"))
        self.assertFalse(hasattr(c, "expert_reference_plan"))
        with self.assertRaises(TypeError):
            InferenceContext(object())

    def test_blank_missing_invalid_formula_error_expert_columns_ignored(self):
        outcomes = []
        with tempfile.TemporaryDirectory() as temp:
            for mode in ("valid", "blank", "absent", "invalid", "formula", "error"):
                path = Path(temp)/f"{mode}.xlsx"
                workbook(path, expert_mode=mode)
                c = load_inference_context(path)
                pool, run = generate_candidates(c, small_config())
                outcomes.append((c.facts_fingerprint, [r["plan_hash"] for r in pool], [r["seed"] for r in run["attempts"]]))
        self.assertTrue(all(row == outcomes[0] for row in outcomes))

    def test_fact_fingerprint_excludes_location_and_raw_file_hash(self):
        c = facts_context()
        d = replace(c.part_data, source_sha256="changed", source_path=Path("other.xlsx"),
                    operations=tuple(replace(op, source=None) for op in c.part_data.operations))
        self.assertEqual(c.facts_fingerprint, InferenceContext(d).facts_fingerprint)

    def test_fact_change_changes_fingerprint(self):
        c = facts_context()
        d = replace(c.part_data, operations=(replace(c.part_data.operations[0], surface_side="ROUGH"), *c.part_data.operations[1:]))
        self.assertNotEqual(c.facts_fingerprint, InferenceContext(d).facts_fingerprint)

    def test_missing_required_sheet(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"test.xlsx"
            workbook(path)
            wb = openpyxl.load_workbook(path)
            del wb["Sheet5"]
            wb.save(path); wb.close()
            with self.assertRaises(ContextValidationError):
                load_inference_context(path)

    def test_missing_fact_header_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"test.xlsx"
            workbook(path)
            wb = openpyxl.load_workbook(path)
            wb["Sheet1"]["D1"] = "wrong"
            wb.save(path); wb.close()
            with self.assertRaises(ContextValidationError):
                load_inference_context(path)

    def test_invalid_fact_type_and_geometry_reported(self):
        c = facts_context()
        bad = replace(c.part_data, operations=(replace(c.part_data.operations[0], operation_type="UNKNOWN", geometry_id="MISSING"),))
        report = validate_facts(bad)
        self.assertFalse(report.passed)
        self.assertTrue({"OPERATION_TYPE_INVALID", "GEOMETRY_REFERENCE_INVALID"} <= {i.code for i in report.issues})

    def test_fact_validation_never_invokes_expert_hooks(self):
        with patch("src.data.validator._Validator.check_expert_plan", side_effect=AssertionError("expert leak")):
            self.assertTrue(validate_facts(facts_context().part_data).passed)

    def test_p001_fact_loader_matches_frozen_fact_objects(self):
        from src.constraints.v06 import load_rule_context
        c, old = load_inference_context(P001), load_rule_context(P001)
        for name in ("operations", "geometries", "stations", "process_groups"):
            self.assertEqual(getattr(c.part_data, name), getattr(old.part_data, name))
        self.assertEqual(c.precedence_relations, old.precedence_relations)
        self.assertEqual(c.fixed_assignments, old.fixed_assignments)
        self.assertEqual(c.bend_influences, old.bend_influences)


class GenerationTests(unittest.TestCase):
    def test_generated_satisfy_cad_and_fixed_and_capability(self):
        c = facts_context(precedence=True, fixed=True)
        pool, run = generate_candidates(c, small_config())
        self.assertEqual(len(pool), 3)  # Three strict ordered station combinations; T fixed.
        self.assertEqual(run["status"], "feasible_set_exhausted")
        for row in pool:
            self.assertTrue(HardConstraintEngine(c).evaluate(row["assignments"]).feasible)
            self.assertEqual(row["assignments"]["T"], "Z")
            self.assertLess(c.stations[row["assignments"]["A"]].order, c.stations[row["assignments"]["B"]].order)

    def test_exhaustive_tiny_domain_matches_independent_hard_evaluator(self):
        c = facts_context(precedence=True, fixed=True)
        expected = {tuple(p) for p in product(c.stations, repeat=3)
                    if HardConstraintEngine(c).evaluate(dict(zip(("A", "B", "T"), p))).feasible}
        pool, _ = generate_candidates(c, small_config())
        self.assertEqual({tuple(row["assignments"][op] for op in ("A", "B", "T")) for row in pool}, expected)

    def test_same_facts_same_candidate_results(self):
        c, cfg = facts_context(), small_config()
        a, _ = generate_candidates(c, cfg)
        b, _ = generate_candidates(c, cfg)
        self.assertEqual([r["plan_hash"] for r in a], [r["plan_hash"] for r in b])
        self.assertEqual(len({r["plan_hash"] for r in a}), len(a))

    def test_empty_capability_intersection_is_infeasible(self):
        c = facts_context()
        c = InferenceContext(replace(c.part_data, stations=tuple(replace(s, allowed_operation_types=frozenset()) for s in c.part_data.stations)))
        pool, run = generate_candidates(c, small_config())
        self.assertFalse(pool)
        self.assertEqual(run["status"], "hard_infeasible")

    def test_no_movable_operations_returns_single_plan(self):
        c = facts_context(one_station=True)
        pool, run = generate_candidates(c, small_config())
        selected, report = select_diverse(c, pool, 3)
        self.assertEqual((len(pool), len(selected), report["movable_operation_count"]), (1, 1, 0))
        self.assertEqual(run["status"], "feasible_set_exhausted")

    def test_unknown_status_is_budget_exhaustion_not_infeasible(self):
        class UnknownSolver:
            wall_time = 0
            best_objective_bound = 0
            response_proto = type("Response", (), {"deterministic_time": 0})()
            def solve(self, model): return cp_model.UNKNOWN
            def status_name(self, status): return "UNKNOWN"
        with patch("src.inference.generation.solver_for", return_value=UnknownSolver()):
            pool, run = generate_candidates(facts_context(), small_config())
        self.assertEqual(run["status"], "no_solution_within_budget")
        self.assertFalse(pool)

    def test_diversity_selects_farthest_not_input_order(self):
        c = facts_context()
        pool, _ = generate_candidates(c, small_config())
        a, _ = select_diverse(c, pool, 3)
        b, report = select_diverse(c, list(reversed(pool)), 3)
        self.assertEqual([r["plan_hash"] for r in a], [r["plan_hash"] for r in b])
        self.assertFalse(report["soft_prefilter_enabled"])

    def test_solver_budget_and_worker_configured(self):
        cfg = small_config()
        solver = solver_for(cfg, 123)
        self.assertEqual(solver.parameters.random_seed, 123)
        self.assertEqual(solver.parameters.max_deterministic_time, cfg["solver"]["max_deterministic_time"])
        self.assertEqual(solver.parameters.num_search_workers, 1)

    def test_config_rejects_unconfirmed_types_and_soft_prefilter(self):
        for mutate in (lambda c: c["local_contrasts"].update(type_pair=["BEND", "BEND"]),
                       lambda c: c["selection"].update(soft_prefilter_enabled=True),
                       lambda c: c["generation"].update(pool_target=0)):
            cfg = small_config(); mutate(cfg)
            with self.assertRaises(ValueError): validate_config(cfg)


class LocalAndGraphTests(unittest.TestCase):
    def row(self):
        return {"operation_ids": ["A", "B"], "type_pair": ["FLANGING", "IRREGULAR_PUNCH"],
                "boundary_distance": 1.0, "ranks": {"A": 1, "B": 1}}

    def test_local_pair_same_to_different_is_one_change_and_unlabeled(self):
        c = facts_context()
        base = {"A": "Z", "B": "Z", "T": "Z"}
        result = sample_local_contrasts(c, base, [self.row()], small_config())
        self.assertEqual(result["comparison_count"], 1)
        pair, trial = result["comparisons"][0], result["trials"][0]
        self.assertIsNone(pair["answer"])
        self.assertIsNone(pair["reason_note"])
        self.assertEqual(len(pair["all_changes"]), 1)
        self.assertTrue(trial["minimum_change_proven"])
        for key in ("same_station_plan", "different_station_plan"):
            self.assertTrue(HardConstraintEngine(c).evaluate(pair[key]["assignments"]).feasible)

    def test_hard_precedence_same_branch_skipped(self):
        c = facts_context(precedence=True)
        result = sample_local_contrasts(c, {"A": "Z", "B": "A", "T": "Z"}, [self.row()], small_config())
        self.assertEqual(result["comparison_count"], 0)
        self.assertEqual(result["trials"][0]["status"], "INFEASIBLE")

    def test_linked_movements_are_all_disclosed(self):
        c = facts_context()
        # T before A forces movement of T when A must join B at Z; B cannot move.
        ops = (replace(c.part_data.operations[0], required_predecessor_ids=("T",)), *c.part_data.operations[1:])
        # Use base A at M, B at A; joining B requires T to move from A to Z.
        c = InferenceContext(replace(c.part_data, operations=ops), ())
        # Either moving B to M or moving A/T can solve; force B to A by capability.
        stations = tuple(replace(s, allowed_operation_types=(s.allowed_operation_types-{"IRREGULAR_PUNCH"})
                                 if s.station_id != "A" else s.allowed_operation_types) for s in c.part_data.stations)
        c = InferenceContext(replace(c.part_data, stations=stations))
        result = sample_local_contrasts(c, {"A": "M", "B": "A", "T": "A"}, [self.row()], small_config())
        self.assertEqual(result["trials"][0]["changed_operation_count"], 2)
        self.assertEqual(result["comparisons"][0]["change_isolation"], "multiple_operations")
        self.assertEqual({r["operation_id"] for r in result["comparisons"][0]["all_changes"]}, {"A", "T"})

    def test_type_filter_and_zero_sampling_budget(self):
        c = facts_context(); cfg = small_config(); cfg["local_contrasts"]["max_pairs"] = 0
        result = sample_local_contrasts(c, {"A": "Z", "B": "Z", "T": "Z"}, [self.row()], cfg)
        self.assertFalse(result["comparisons"])

    def test_duplicate_comparison_preserves_all_focus_pairs(self):
        c = facts_context()
        # Duplicate catalogue rows still produce one assignment comparison.
        result = sample_local_contrasts(c, {"A": "Z", "B": "Z", "T": "Z"},
                                        [self.row(), self.row()], small_config())
        self.assertEqual(result["comparison_count"], 1)
        self.assertEqual(result["trials"][0]["contrast_id"], result["trials"][1]["contrast_id"])
        self.assertEqual(len(result["comparisons"][0]["focus_pairs"]), 2)

    def test_historical_review_direction_mismatch_rejected(self):
        impact = {"comparisons": [{"comparison_id": "C1", "plan_A": "A", "plan_B": "B"}]}
        with self.assertRaises(ValueError):
            supervision_audit(impact, {}, [{"comparison_id": "C1", "plan_A": "B", "plan_B": "A"}])

    def test_edge_dynamic_attributes_change_static_graph_unchanged(self):
        c = facts_context(geometry=True)
        static = build_part_graph(c.part_data)
        before = static.fingerprint()
        same = build_graph2(static, build_plan_overlay(static, {"A": "Z", "B": "Z", "T": "Z"}))
        diff = build_graph2(static, build_plan_overlay(static, {"A": "Z", "B": "A", "T": "Z"}))
        self.assertEqual(same.plan_edge_features[0].same_station, 1)
        self.assertEqual(diff.plan_edge_features[0].same_station, 0)
        self.assertNotEqual(diff.plan_edge_features[0].station_delta, 0)
        self.assertEqual(static.fingerprint(), before)
        self.assertEqual(len(near_pairs(c, static, 5)), 1)

    def test_missing_geometry_not_fabricated(self):
        c = facts_context()
        static = build_part_graph(c.part_data)
        self.assertEqual(near_pairs(c, static, 5), [])

    def test_p001_known_near_pair_graph_edge_distance_and_overlay(self):
        c = load_inference_context(P001)
        static = build_part_graph(c.part_data)
        row = next(r for r in near_pairs(c, static, 5) if r["operation_ids"] == ["O131", "O203"])
        self.assertEqual(row["ranks"], {"O131": 2, "O203": 3})
        self.assertAlmostEqual(row["boundary_distance"], 2.1, places=6)

    def test_p001_real_input_isolation_smoke(self):
        self.assertTrue(verify_expert_isolation(P001, small_config())["passed"])


if __name__ == "__main__":
    unittest.main()
