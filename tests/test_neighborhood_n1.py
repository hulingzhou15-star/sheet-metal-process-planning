from copy import deepcopy
from dataclasses import replace
from itertools import product
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ortools.sat.python import cp_model

from src.candidate.distance import plan_hash
from src.constraints.context import FixedAssignment
from src.constraints.v06 import HardConstraintEngine, SoftPriorEngine
from src.data.schema import Operation, Station
from src.graph.builders import build_part_graph
from src.inference.input import InferenceContext, load_inference_context
from src.neighborhood import NeighborhoodContext, build_neighborhood
from src.neighborhood import load_config as neighborhood_config
from src.neighborhood_n1 import (collect_policy, audit_plan, load_config, joint_feasibility,
                                 generate_candidates, audit_cohort, compare_candidates)
from src.neighborhood_n1.policy import validate_config
from src.inference.generation import solver_for
from tests.test_neighborhood_v01 import two_ops, context_for, circle, line
from tests.test_m3_reference_free_v07 import workbook

ROOT = Path(__file__).resolve().parents[1]


def setup_context(c=None):
    c = two_ops() if c is None else c
    config = load_config()
    config["generation"].update(pool_target=8, selected_target=8, max_attempts=12)
    n = build_neighborhood(c, neighborhood_config())
    p = collect_policy(c, n, config)
    return c, n, p, config


def joint_conflict_context():
    # B forced to first Station by B<T1; C forced to second by T0<C.
    # F can be at either. F!=B and F!=C are individually feasible, jointly not.
    ops = [Operation("P", "F", "FLANGING", geometry_id="GF"),
           Operation("P", "B", "IRREGULAR_PUNCH", geometry_id="GB", cad_parent_operation_id="T1"),
           Operation("P", "C", "IRREGULAR_PUNCH", geometry_id="GC", required_predecessor_ids=("T0",)),
           Operation("P", "T0", "TRIM"), Operation("P", "T1", "TRIM")]
    types = frozenset({"FLANGING", "IRREGULAR_PUNCH", "TRIM"})
    c = context_for(ops, [circle("F"), line("B", 3), line("C", 4)],
                    [Station("P", "Z", 10, types), Station("P", "A", 50, types)])
    return InferenceContext(c.part_data, (FixedAssignment("T0", "Z"), FixedAssignment("T1", "A")))


class PolicyTests(unittest.TestCase):
    def test_scope_only_flange_to_irregular_priority(self):
        c, n, p, cfg = setup_context()
        self.assertEqual([r["pair_id"] for r in p["pairs"]], ["A__B"])
        self.assertEqual(p["pairs"][0]["flanging_operation_id"], "A")
        self.assertEqual(p["pairs"][0]["irregular_operation_id"], "B")
        self.assertEqual(p["policy"]["status"], "review_trial_only")

    def test_reverse_priority_does_not_override_flange_extended(self):
        c, n, _, cfg = setup_context()
        snapshot = n.to_dict()
        for r in snapshot["relations"]:
            for arc in r["arcs"]:
                arc["tier"] = "EXTENDED" if arc["anchor"] == "A" else "PRIORITY"
        p = collect_policy(c, NeighborhoodContext(snapshot), cfg)
        self.assertFalse(p["pairs"])
        self.assertEqual(p["excluded"][0]["reason"], "no_priority_arc_from_flanging_anchor")

    def test_own_pre_punch_and_other_pairs_not_in_n1(self):
        ops = [Operation("P", "F", "FLANGING", geometry_id="GF", process_group_id="G"),
               Operation("P", "P", "PRE_PUNCH", geometry_id="GP", process_group_id="G"),
               Operation("P", "B", "BEND", geometry_id="GB")]
        c = context_for(ops, [circle("F"), circle("P", radius=.5), line("B", 2)])
        c, n, p, cfg = setup_context(c)
        self.assertFalse(p["pairs"])
        self.assertTrue(any(r["reason"] == "internal_process_relation" for r in p["excluded"]))

    def test_same_station_fails_trial_but_still_passes_frozen_hard(self):
        c, _, p, _ = setup_context()
        same = {"A": "Z", "B": "Z"}
        self.assertTrue(HardConstraintEngine(c).evaluate(same).feasible)
        audit = audit_plan(c, p, same)
        self.assertFalse(audit["n1_pass"])
        self.assertEqual(audit["violation_count"], 1)
        self.assertEqual(audit["violations"][0]["station_ids"], ["Z", "Z"])
        self.assertIsNone(audit["quality_score"])

    def test_neither_order_nor_specific_station_required(self):
        c, _, p, _ = setup_context()
        for a, b in (("Z", "A"), ("M", "Z"), ("A", "M")):
            self.assertTrue(audit_plan(c, p, {"A": a, "B": b})["trial_admitted"])

    def test_hard_invalid_not_silently_admitted_or_scored(self):
        c, _, p, _ = setup_context()
        row = audit_plan(c, p, {"A": "Z"})
        self.assertFalse(row["hard"]["feasible"])
        self.assertFalse(row["n1_evaluated"] or row["trial_admitted"])
        self.assertIsNone(row["n1_pass"])

    def test_policy_fingerprint_and_foreign_facts_rejected(self):
        c, _, p, _ = setup_context()
        bad = deepcopy(p); bad["pairs"][0]["boundary_distance"] = 123
        with self.assertRaises(ValueError): audit_plan(c, bad, {"A": "Z", "B": "A"})
        c = InferenceContext(replace(c.part_data, operations=(replace(c.part_data.operations[0], surface_side="ROUGH"), c.part_data.operations[1])))
        with self.assertRaises(ValueError): audit_plan(c, p, {"A": "Z", "B": "A"})

    def test_scope_and_budget_validation(self):
        for change in (lambda c: c["policy"].update(tier="EXTENDED"), lambda c: c["policy"].update(neighbor_type="BEND"),
                       lambda c: c["solver"].update(max_time_seconds=True), lambda c: c["conflict_analysis"].update(max_deletion_checks=-1)):
            cfg = load_config(); change(cfg)
            with self.assertRaises(ValueError): validate_config(cfg)


class JointAndGenerationTests(unittest.TestCase):
    def test_joint_feasible_witness_verified(self):
        c, _, p, cfg = setup_context()
        result = joint_feasibility(c, p, cfg)
        self.assertEqual(result["state"], "joint_feasible")
        self.assertTrue(result["witness"]["audit"]["trial_admitted"])

    def test_individually_feasible_but_joint_infeasible_and_core_minimal(self):
        c, _, p, cfg = setup_context(joint_conflict_context())
        self.assertEqual(len(p["pairs"]), 2)
        result = joint_feasibility(c, p, cfg)
        self.assertEqual(result["state"], "joint_infeasible")
        self.assertEqual(set(result["conflict_pair_ids"]), {"B__F", "C__F"})
        self.assertTrue(result["deletion_minimal_verified"])
        self.assertFalse(result["minimum_cardinality_claimed"] or result["generation_allowed"])
        self.assertTrue(all(r["status"] in {"OPTIMAL", "FEASIBLE"} for r in result["deletion_checks"]))
        self.assertEqual(generate_candidates(c, p, cfg, result)[0], [])

    def test_conflict_deletion_budget_zero_reports_nonminimal(self):
        c, _, p, cfg = setup_context(joint_conflict_context())
        cfg["conflict_analysis"]["max_deletion_checks"] = 0
        result = joint_feasibility(c, p, cfg)
        self.assertFalse(result["deletion_minimal_verified"])
        self.assertTrue(result["n1_conflict_proven"])
        self.assertEqual(result["deletion_checks"], [])

    def test_hard_infeasibility_not_attributed_to_n1(self):
        c = two_ops()
        c = InferenceContext(replace(c.part_data, stations=tuple(replace(s, allowed_operation_types=frozenset()) for s in c.part_data.stations)))
        c, _, p, cfg = setup_context(c)
        result = joint_feasibility(c, p, cfg)
        self.assertEqual(result["state"], "hard_infeasible")
        self.assertFalse(result["n1_conflict_proven"] or result["generation_allowed"])

    def test_generation_matches_exhaustive_tiny_domain_and_reports_shortage(self):
        c, _, p, cfg = setup_context()
        joint = joint_feasibility(c, p, cfg)
        pool, run = generate_candidates(c, p, cfg, joint)
        expected = {plan_hash({"A": a, "B": b}) for a, b in product(c.stations, repeat=2) if a != b}
        self.assertEqual({r["plan_hash"] for r in pool}, expected)
        self.assertEqual(len(pool), 6)
        self.assertEqual(run["state"], "feasible_set_exhausted")
        self.assertFalse(run["uses_witness_warm_start"] or run["uses_expert"] or run["uses_soft_penalty"])

    def test_reproducibility_and_view_or_source_bytes_do_not_change_generation(self):
        c, n, p, cfg = setup_context()
        pool, run = generate_candidates(c, p, cfg, joint_feasibility(c, p, cfg))
        altered = InferenceContext(replace(c.part_data, source_sha256="ignored_expert_bytes", source_path=Path("elsewhere.xlsx")))
        cfg["view"]["default_station"] = "M"
        q = collect_policy(altered, n, cfg)
        other, audit = generate_candidates(altered, q, cfg, joint_feasibility(altered, q, cfg))
        self.assertEqual(p["fingerprint"], q["fingerprint"])
        self.assertEqual([r["plan_hash"] for r in pool], [r["plan_hash"] for r in other])
        self.assertEqual([r["seed"] for r in run["attempts"]], [r["seed"] for r in audit["attempts"]])

    def test_unknown_never_claimed_infeasible_or_relaxed(self):
        class Unknown:
            wall_time = best_objective_bound = 0
            response_proto = type("Response", (), {"deterministic_time": 0})()
            def solve(self, model): return cp_model.UNKNOWN
            def status_name(self, status): return "UNKNOWN"
        c, _, p, cfg = setup_context()
        with patch("src.neighborhood_n1.solver.solver_for", return_value=Unknown()):
            result = joint_feasibility(c, p, cfg)
            self.assertEqual(result["state"], "hard_feasibility_unknown")
            self.assertFalse(result["n1_conflict_proven"])
        actual = solver_for(cfg, 1)
        with patch("src.neighborhood_n1.solver.solver_for", side_effect=[actual, Unknown()]):
            result = joint_feasibility(c, p, cfg)
            self.assertEqual(result["state"], "joint_feasibility_unknown")
            self.assertFalse(result["generation_allowed"])
        with patch("src.neighborhood_n1.solver.solver_for", return_value=Unknown()):
            pool, run = generate_candidates(c, p, cfg, {"state": "joint_feasible", "generation_allowed": True})
            self.assertEqual(pool, [])
            self.assertEqual(run["state"], "no_solution_within_budget")

    def test_missing_or_invalid_expert_columns_ignored_end_to_end(self):
        results = []
        with tempfile.TemporaryDirectory() as tmp:
            for mode in ("valid", "blank", "absent", "invalid", "formula", "error"):
                path = Path(tmp)/(mode+".xlsx"); workbook(path, expert_mode=mode)
                c, _, p, cfg = setup_context(load_inference_context(path))
                pool, _ = generate_candidates(c, p, cfg, joint_feasibility(c, p, cfg))
                results.append((p["fingerprint"], [r["plan_hash"] for r in pool]))
        self.assertTrue(all(r == results[0] for r in results))

    def test_expert_access_trap_and_frozen_graph_soft_semantics(self):
        c, _, p, cfg = setup_context()
        same = {"A": "Z", "B": "Z"}
        old_soft = SoftPriorEngine(c).evaluate(same)
        graph = build_part_graph(c.part_data); fp = graph.fingerprint()
        with patch.object(InferenceContext, "expert_reference_plan", property(lambda _: (_ for _ in ()).throw(AssertionError("leak")))):
            pool, _ = generate_candidates(c, p, cfg, joint_feasibility(c, p, cfg))
        self.assertTrue(pool)
        self.assertEqual(SoftPriorEngine(c).evaluate(same), old_soft)
        self.assertEqual(graph.fingerprint(), fp)
        self.assertTrue(HardConstraintEngine(c).evaluate(same).feasible)


class AnalysisTests(unittest.TestCase):
    def test_cohort_metrics_and_exact_violations(self):
        c, n, p, _ = setup_context()
        plans = [{"plan_id": str(i), "assignments": q, "plan_hash": plan_hash(q)} for i, q in enumerate(
                 ({"A": "Z", "B": "Z"}, {"A": "Z", "B": "A"}, {"A": "M", "B": "Z"}))]
        audit = audit_cohort(c, n, p, plans, "test")
        self.assertEqual(audit["n1_pass_count"], 2)
        self.assertEqual(audit["per_pair_same_station_count"], {"A__B": 1})
        self.assertAlmostEqual(audit["pairwise_raw_hamming"]["mean"], 4/3)
        self.assertAlmostEqual(audit["pairwise_normalized_hamming"]["mean"], 2/3)
        pairs = compare_candidates(c, n, p, plans[:1], plans[1:])
        self.assertTrue(all(len(r["all_changes"]) == 1 and r["answer"] is None for r in pairs))

    def test_empty_filtered_pool_has_no_invented_statistics(self):
        c, n, p, _ = setup_context()
        audit = audit_cohort(c, n, p, [], "empty")
        self.assertEqual(audit["plan_count"], 0)
        self.assertIsNone(audit["pairwise_raw_hamming"]["mean"])
        self.assertIsNone(audit["n1_violation_distribution"]["mean"])


class SavedP001Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = ROOT/"outputs/neighborhood_n1"
        cls.policy = json.loads((cls.out/"n1_policy.json").read_text(encoding="utf-8"))
        cls.cohorts = json.loads((cls.out/"cohort_audit.json").read_text(encoding="utf-8"))
        cls.pool = [json.loads(l) for l in (cls.out/"candidate_pool.jsonl").read_text(encoding="utf-8").splitlines()]
        cls.context = load_inference_context(ROOT/"data/parts/P001/钣金加工数据表_P001.xlsx")

    def test_exact_scope_22_and_four_known_cases(self):
        self.assertEqual(len(self.policy["pairs"]), 22)
        ids = {r["pair_id"] for r in self.policy["pairs"]}
        self.assertTrue({"O066__O200", "O015__O196", "O031__O196", "O035__O196"} <= ids)
        for r in self.policy["pairs"]:
            self.assertEqual(self.context.operations[r["source_arc"]["anchor"]].operation_type, "FLANGING")
            self.assertEqual(r["source_arc"]["tier"], "PRIORITY")

    def test_joint_and_baseline_real_failure_distribution(self):
        joint = json.loads((self.out/"joint_feasibility.json").read_text(encoding="utf-8"))
        self.assertEqual(joint["state"], "joint_feasible")
        self.assertEqual(self.cohorts["original_pool"]["plan_count"], 75)
        self.assertEqual(self.cohorts["original_pool"]["n1_pass_count"], 0)
        self.assertEqual(self.cohorts["filtered_original_pool"]["plan_count"], 0)
        self.assertEqual(self.cohorts["original_pool"]["n1_violation_distribution"]["min"], 4)

    def test_new_16_all_hard_legal_and_zero_n1_violations(self):
        self.assertEqual(len(self.pool), 16)
        self.assertEqual(len({p["plan_hash"] for p in self.pool}), 16)
        for p in self.pool:
            audit = audit_plan(self.context, self.policy, p["assignments"])
            self.assertTrue(audit["hard"]["feasible"] and audit["trial_admitted"])
            self.assertEqual(audit["violation_count"], 0)
            self.assertEqual(plan_hash(p["assignments"]), p["plan_hash"])
            self.assertIsNone(p["preference_label"])

    def test_comparison_changes_complete_and_known_counts_zero(self):
        comps = json.loads((self.out/"comparisons.json").read_text(encoding="utf-8"))
        old = {r["plan_hash"]: r for r in map(json.loads, (ROOT/"outputs/neighborhood_v01/candidate_pool.jsonl").read_text(encoding="utf-8").splitlines())}
        new = {r["plan_hash"]: r for r in self.pool}
        self.assertEqual(len(comps), 20)
        for c in comps:
            a, b = old[c["before_plan_hash"]]["assignments"], new[c["after_plan_hash"]]["assignments"]
            self.assertEqual({op for op in a if a[op] != b[op]}, {r["operation_id"] for r in c["all_changes"]})
        known = json.loads((self.out/"known_case_coverage.json").read_text(encoding="utf-8"))
        self.assertEqual(len(known), 4)
        self.assertTrue(all(r["covered_by_n1"] and r["new_same_station_count"] == 0 and not r["used_for_generation"] for r in known))


if __name__ == "__main__":
    unittest.main()
