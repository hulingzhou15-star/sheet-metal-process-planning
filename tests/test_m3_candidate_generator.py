from copy import deepcopy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.generate_candidate_plans import check_integrity
from src.candidate import CandidatePlanGenerator, GenerationConsistencyError
from src.candidate.cp_sat_model import solve_model
from src.candidate.distance import plan_hash
from src.candidate.report import candidate_statistics, generation_markdown
from src.candidate.serialization import read_jsonl, write_assignment_matrix, write_candidate_outputs, write_jsonl
from src.constraints import HardConstraintEngine, load_constraint_context
from src.constraints.config import load_config
from m3_fixtures import P001, ROOT
from candidate_fixtures import movable_context, small_config


class CandidateGeneratorTests(unittest.TestCase):
    def test_same_seed_reproducibility_and_different_seed_exploration(self):
        c = movable_context()
        a = CandidatePlanGenerator(c, small_config(42)).run()
        b = CandidatePlanGenerator(c, small_config(42)).run()
        other = CandidatePlanGenerator(c, small_config(43)).run()
        self.assertEqual(a, b)
        self.assertNotEqual([p["plan_hash"] for p in a[0]], [p["plan_hash"] for p in other[0]])

    def test_pool_selected_feasibility_guardrail_expert_exclusion_and_no_duplicates(self):
        g = CandidatePlanGenerator(movable_context(), small_config())
        pool, selected, run = g.run()
        self.assertEqual((len(pool), len(selected)), (9, 3))
        self.assertEqual(len({c["plan_hash"] for c in pool}), 9)
        self.assertEqual(len({c["plan_hash"] for c in selected}), 3)
        for candidate in pool+selected:
            self.assertTrue(g.hard.evaluate(candidate["assignments"]).feasible)
            self.assertTrue(candidate["guardrail"]["pass"])
            self.assertNotEqual(candidate["plan_hash"], plan_hash(g.expert))
            self.assertFalse(candidate["generation"]["objective"]["contains_soft_priors"])
            if not candidate["generation"]["fallback_used"]:
                self.assertEqual(candidate["expert_distance"]["raw"], candidate["generation"]["target_distance"])

    def test_existing_soft_evaluator_called_post_hoc_and_weights_unchanged(self):
        config = load_config()
        before = deepcopy(config)
        g = CandidatePlanGenerator(movable_context(), small_config(), config)
        with patch.object(g.soft, "evaluate", wraps=g.soft.evaluate) as evaluate:
            pool, run = g.generate_pool()
        self.assertEqual(evaluate.call_count, len(pool))
        self.assertEqual(config, before)
        self.assertEqual(g.constraint_config, before)
        for candidate in pool:
            existing = g.soft.evaluate(candidate["assignments"])
            self.assertEqual(candidate["soft"]["total_soft_penalty"], existing["total_soft_penalty"])
            self.assertEqual([r["weight"] for r in candidate["soft"]["components"].values()], [.45, .2, .35])

    def test_infeasible_solver_output_is_fatal(self):
        g = CandidatePlanGenerator(movable_context(), small_config())
        with self.assertRaisesRegex(GenerationConsistencyError, "H1-H6"):
            g.validate_solver_output({"assignments": {"A0": "OP90"}, "expert_distance_raw": 0})

    def test_guardrail_mismatch_is_fatal(self):
        g = CandidatePlanGenerator(movable_context(count=12), small_config())
        plan = {f"A{i}": "OP10" for i in range(12)}
        with self.assertRaisesRegex(GenerationConsistencyError, "G1"):
            g.validate_solver_output({"assignments": plan, "expert_distance_raw": 12})

    def test_feasible_maximum_does_not_claim_proven_dmax(self):
        g = CandidatePlanGenerator(movable_context(), small_config())
        result = solve_model(g.build_model(), seed=1, timeout_seconds=1)
        result["solver_status"] = "FEASIBLE"
        with patch("src.candidate.generator.solve_model", return_value=result):
            maximum = g.maximum_expert_distance()
        self.assertFalse(maximum["proven_optimal"])
        self.assertIsNone(maximum["D_max"])
        self.assertEqual(maximum["best_known_D_max"], result["expert_distance_raw"])

    def test_exact_infeasible_fallback_stays_inside_bucket(self):
        config = small_config()
        config["generation"].update(pool_target_per_bucket=1)
        g = CandidatePlanGenerator(movable_context(count=8), config)
        calls = 0

        def simulated(cp, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                return {"solver_status": "INFEASIBLE", "assignments": None, "objective_value": None,
                        "best_objective_bound": 0, "expert_distance_raw": None}
            return solve_model(cp, **kwargs)

        with patch("src.candidate.generator.solve_model", side_effect=simulated):
            pool, run = g.generate_pool()
        first = pool[0]
        self.assertTrue(first["generation"]["fallback_used"])
        low, high = first["generation"]["fallback_range"]
        self.assertTrue(low <= first["expert_distance"]["raw"] <= high)
        self.assertEqual(first["expert_distance"]["bucket"], "NEAR")

    def test_duplicate_return_is_discarded_and_counted(self):
        g = CandidatePlanGenerator(movable_context(), small_config())
        cached = None
        calls = 0

        def repeat(cp, **kwargs):
            nonlocal calls, cached
            calls += 1
            if calls == 3:
                return deepcopy(cached)
            result = solve_model(cp, **kwargs)
            if calls == 2:
                cached = deepcopy(result)
            return result

        with patch("src.candidate.generator.solve_model", side_effect=repeat):
            pool, run = g.generate_pool()
        self.assertEqual(sum(r["duplicate_rejections"] for r in run["bucket_generation"].values()), 1)
        self.assertEqual(len(pool), len({p["plan_hash"] for p in pool}))

    def test_insufficient_space_is_reported_without_copies(self):
        config = small_config()
        config["generation"]["max_attempts_per_bucket"] = 3
        pool, selected, run = CandidatePlanGenerator(movable_context(count=1, station_count=2), config).run()
        self.assertEqual((len(pool), len(selected)), (1, 1))
        self.assertGreater(run["unfilled_pool_total"], 0)
        self.assertEqual(run["selection"]["unfilled_total"], 2)

    def test_jsonl_round_trip_and_tamper_detection(self):
        pool, selected, run = CandidatePlanGenerator(movable_context(), small_config()).run()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "selected.jsonl"
            write_jsonl(path, selected)
            self.assertEqual(read_jsonl(path), selected)
            records = json.loads(path.read_text().splitlines()[0])
            records["assignments"]["A0"] = "missing"
            path.write_text(json.dumps(records)+"\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash"):
                read_jsonl(path)

    def test_assignment_matrix_has_every_operation_and_selected_id(self):
        g = CandidatePlanGenerator(movable_context(), small_config())
        pool, selected, run = g.run()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assignments.csv"
            write_assignment_matrix(path, g.distance.operation_ids, g.expert, selected)
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["operation_id"] for row in rows], list(g.distance.operation_ids))
            self.assertEqual(set(rows[0]), {"operation_id", "expert_station", *(c["plan_id"] for c in selected)})

    def test_p001_small_batch_post_selection_report_and_frozen_integrity(self):
        manifest = json.loads((ROOT / "outputs/m3_candidates/frozen_input_manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(check_integrity(ROOT, manifest)["all_unchanged"])
        c = load_constraint_context(P001)
        config = small_config()
        config["generation"].update(pool_target_per_bucket=2, final_target_per_bucket=1)
        config["diagnostics"]["include_d3"] = True
        g = CandidatePlanGenerator(c, config)
        pool, selected, run = g.run()
        stats = candidate_statistics(g, pool, selected, run)
        self.assertEqual((len(pool), len(selected)), (6, 3))
        self.assertEqual(stats["candidate_diagnostics"]["D3"]["assigned_operation_count_per_plan"], 186)
        self.assertEqual(stats["candidate_diagnostics"]["D2"]["root_region_count"], 27)
        self.assertEqual(stats["diversity"]["all_pairs"]["count"], 3)
        report = {"status": "generated", "run": run, "statistics": stats,
                  "expert_reference": {"plan_id": "EXPERT_REFERENCE", "assignments": g.expert},
                  "runtime": {"ortools_version": "9.15.6755"}, "frozen_integrity": check_integrity(ROOT, manifest)}
        self.assertIn("Pairwise diversity", generation_markdown(report))
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_candidate_outputs(Path(tmp) / "outputs", pool, selected, report, project_root=tmp)
            self.assertEqual(len(paths), 8)
            self.assertEqual(len(read_jsonl(paths[1])), 3)
        self.assertTrue(check_integrity(ROOT, manifest)["all_unchanged"])
