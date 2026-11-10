from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ortools.sat.python import cp_model

from src.data.schema import Operation, Geometry, GeometrySegment, Station
from src.inference.input import InferenceContext, InferenceFacts, load_inference_context
from src.neighborhood import (build_neighborhood, build_neighborhood_overlay, load_config, measure_geometry,
                              explore_neighborhood_candidates, propose_pair_alternatives)
from src.neighborhood.context import _tier_arcs, validate_config
from src.neighborhood.candidates import choose_pairs
from src.constraints.v06 import HardConstraintEngine, SoftPriorEngine
from src.graph.builders import build_part_graph
from tests.test_m3_reference_free_v07 import workbook

ROOT = Path(__file__).resolve().parents[1]


def circle(op, x=0, y=0, radius=1):
    return Geometry("P", "G"+op, (GeometrySegment(1, "CIRCLE", center_x=x, center_y=y, radius=radius),))


def line(op, x=0, y=0, ex=None, ey=None):
    return Geometry("P", "G"+op, (GeometrySegment(1, "LINE", start_x=x, start_y=y,
                                                end_x=x+1 if ex is None else ex, end_y=y if ey is None else ey),))


def context_for(operations, geometries, stations=None):
    if stations is None:
        types = frozenset(o.operation_type for o in operations)
        stations = tuple(Station("P", name, order, types) for name, order in (("Z", 10), ("A", 40), ("M", 90)))
    return InferenceContext(InferenceFacts(tuple(operations), tuple(geometries), tuple(stations), ()))


def two_ops(*, precedence=False):
    return context_for([Operation("P", "A", "FLANGING", geometry_id="GA", cad_parent_operation_id="B" if precedence else None),
                        Operation("P", "B", "IRREGULAR_PUNCH", geometry_id="GB")], [circle("A"), line("B", 3)])


def small_config():
    c = load_config()
    c["generation"].update(base_pool_target=4, base_selection_target=2, base_max_attempts=6,
                           pair_budget_per_base=4, selected_target=3)
    return c


class NeighborhoodRuleTests(unittest.TestCase):
    def test_novel_part_needs_no_saved_distance_audit_or_expert(self):
        context = two_ops()
        with patch.object(InferenceContext, "expert_reference_plan", property(lambda _: (_ for _ in ()).throw(AssertionError("leak")))):
            n = build_neighborhood(context, load_config())
        self.assertEqual(n.snapshot["summary"]["external_pairs"], 1)
        self.assertFalse(hasattr(context.part_data, "expert_reference_plan"))

    def test_internal_group_not_in_external_relative_order(self):
        context = context_for([Operation("P", "F", "FLANGING", process_group_id="G", geometry_id="GF"),
                               Operation("P", "P", "PRE_PUNCH", process_group_id="G", geometry_id="GP"),
                               Operation("P", "I", "IRREGULAR_PUNCH", geometry_id="GI")],
                              [circle("F"), circle("P", radius=.5), line("I", 3)])
        n = build_neighborhood(context, load_config())
        self.assertEqual(n.snapshot["summary"]["internal_pairs"], 1)
        self.assertEqual(n.snapshot["operations"]["F"]["internal_pair_ids"], ["F__P"])
        self.assertTrue(all("F__P" not in g["pair_ids"] for g in n.snapshot["relative_groups"]))
        self.assertEqual(set(context.operations), {"F", "P", "I"})

    def test_missing_geometry_does_not_delete_explicit_internal_process_relation(self):
        c = context_for([Operation("P", "F", "FLANGING", process_group_id="G"),
                         Operation("P", "P", "PRE_PUNCH", process_group_id="G")], [])
        n = build_neighborhood(c, load_config())
        self.assertEqual(n.snapshot["summary"]["internal_pairs"], 1)
        self.assertEqual(n.snapshot["summary"]["all_geometry_pairs"], 0)
        self.assertEqual(n.snapshot["operations"]["F"]["internal_pair_ids"], ["F__P"])
        self.assertIsNone(n.snapshot["relations"][0]["boundary_distance"])
        self.assertEqual(n.snapshot["operations"]["F"]["policy"], "MISSING_GEOMETRY")

    def test_finite_bend_segment_not_infinite_line(self):
        c = context_for([Operation("P", "F", "FLANGING", geometry_id="GF"), Operation("P", "B", "BEND", geometry_id="GB")],
                        [circle("F", 10, 2), line("B", 0, 0, 2, 0)])
        n = build_neighborhood(c, load_config())
        self.assertEqual(n.snapshot["pair_assessments"][0]["status"], "OUTSIDE_RANGE")
        self.assertGreater(n.snapshot["pair_assessments"][0]["gates"][0]["measurement"], 8)

    def test_one_flange_direction_sufficient_no_averaged_diameter(self):
        c = context_for([Operation("P", "A", "FLANGING", geometry_id="GA"), Operation("P", "B", "FLANGING", geometry_id="GB")],
                        [circle("A", radius=1), circle("B", 9, radius=5)])
        row = build_neighborhood(c, load_config()).external_relations[0]
        self.assertEqual([g["passes"] for g in row["gates"]], [False, True])
        self.assertEqual([(a["anchor"], a["neighbor"]) for a in row["arcs"]], [("B", "A")])

    def test_all_absolute_families_use_explicit_trial_threshold(self):
        for ta, tb in (("BEND", "BEND"), ("BEND", "IRREGULAR_PUNCH"), ("IRREGULAR_PUNCH", "IRREGULAR_PUNCH")):
            c = context_for([Operation("P", "A", ta, geometry_id="GA"), Operation("P", "B", tb, geometry_id="GB")],
                            [line("A", 0), line("B", 9)])
            cfg = load_config(); n = build_neighborhood(c, cfg)
            self.assertEqual(n.snapshot["summary"]["external_pairs"], 1)
            self.assertEqual(n.external_relations[0]["gates"][0]["parameter_status"], "UNCALIBRATED_ABSOLUTE_TRIAL")
            cfg["rules"]["absolute_thresholds"] = {k: 5 for k in cfg["rules"]["absolute_thresholds"]}
            self.assertEqual(build_neighborhood(c, cfg).snapshot["summary"]["external_pairs"], 0)

    def test_relative_break_keeps_extended_and_does_not_force_five(self):
        ops = [Operation("P", "F", "FLANGING", geometry_id="GF")]
        geoms = [circle("F", radius=2)]
        for op, x in (("I1", 3), ("I2", 4), ("I3", 10), ("FAR", 30)):
            ops.append(Operation("P", op, "IRREGULAR_PUNCH", geometry_id="G"+op)); geoms.append(line(op, x))
        n = build_neighborhood(context_for(ops, geoms), load_config())
        arcs = {a["neighbor"]: a for r in n.external_relations for a in r["arcs"] if a["anchor"] == "F"}
        self.assertEqual(set(arcs), {"I1", "I2", "I3"})
        self.assertEqual([arcs[k]["tier"] for k in ("I1", "I2", "I3")], ["PRIORITY", "PRIORITY", "EXTENDED"])

    def test_zero_and_tie_buckets_never_divided_or_split(self):
        rows = [{"pair_id": str(i), "kind": "EXTERNAL_NEIGHBOR", "family": "BEND_BEND", "arcs": [
            {"anchor": "A", "neighbor": str(i), "metric": "RECORDED_BOUNDARY", "measurement": d}]}
                for i, d in enumerate((0, 1e-12, 1, 1+1e-11, 4))]
        groups = _tier_arcs(rows, 3)
        self.assertIsNone(groups[0]["gaps"][1]["ratio"])
        self.assertEqual(groups[0]["gaps"][1]["status"], "ZERO_TO_POSITIVE")
        self.assertEqual(groups[0]["first_cut_after_rank"], 4)
        self.assertEqual([r["arcs"][0]["tier"] for r in rows], ["PRIORITY"]*4+["EXTENDED"])

    def test_cannot_mix_metric_families_for_relative_break(self):
        rows = [{"pair_id": str(i), "kind": "EXTERNAL_NEIGHBOR", "family": f, "arcs": [
            {"anchor": "A", "neighbor": str(i), "metric": m, "measurement": d}]}
                for i, (f, m, d) in enumerate((("BEND_CIRCULAR", "CENTER_TO_FINITE_BEND", 1),
                                               ("IRREGULAR_CIRCULAR", "RECORDED_BOUNDARY", 8)))]
        groups = _tier_arcs(rows, 3)
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(g["first_cut_after_rank"] is None for g in groups))

    def test_isolated_simple_and_missing_pending_have_distinct_states(self):
        c = context_for([Operation("P", "F", "FLANGING", geometry_id="GF"), Operation("P", "C", "CIRCLE_PUNCH", geometry_id="GC"),
                         Operation("P", "D", "DIMPLE", geometry_id="GD"), Operation("P", "T", "TRIM")],
                        [circle("F"), circle("C", 100), circle("D", 200)])
        ops = build_neighborhood(c, load_config()).snapshot["operations"]
        self.assertEqual(ops["F"]["external_match_state"], "NO_EXTERNAL_MATCH_UNDER_TRIAL")
        self.assertEqual(ops["C"]["policy"], "TARGET_ONLY")
        self.assertEqual(ops["D"]["policy"], "TYPE_POLICY_PENDING")
        self.assertEqual(ops["T"]["policy"], "MISSING_GEOMETRY")
        self.assertTrue(all(o["process_neighbor_need"] == "NOT_DETERMINED_BY_GEOMETRY" for o in ops.values()))

    def test_threshold_uses_m2_numerical_buckets_only(self):
        c = context_for([Operation("P", "A", "BEND", geometry_id="GA"), Operation("P", "B", "BEND", geometry_id="GB")],
                        [line("A", 0, 0, 1, 0), line("B", 0, 10+1e-11, 1, 10+1e-11)])
        self.assertEqual(build_neighborhood(c, load_config()).snapshot["summary"]["external_pairs"], 1)

    def test_cache_missing_pair_stale_facts_duplicate_rejected(self):
        c, cfg = two_ops(), load_config(); e = measure_geometry(c, cfg)
        for mutate in (lambda e: e["source"].update(facts_fingerprint="wrong"), lambda e: e["pairs"].clear(),
                       lambda e: e["pairs"].append(deepcopy(e["pairs"][0]))):
            bad = deepcopy(e); mutate(bad)
            with self.assertRaises(ValueError): build_neighborhood(c, cfg, evidence=bad)

    def test_source_path_and_expert_bytes_do_not_change_neighborhood(self):
        c, cfg = two_ops(), load_config(); e = measure_geometry(c, cfg)
        altered = InferenceContext(replace(c.part_data, source_sha256="only_expert_changed", source_path=Path("elsewhere.xlsx")))
        self.assertEqual(build_neighborhood(c, cfg, evidence=e).fingerprint, build_neighborhood(altered, cfg, evidence=e).fingerprint)

    def test_invalid_unconfirmed_configuration_rejected(self):
        for value in (-1, True, float("nan"), None):
            cfg = load_config(); cfg["rules"]["absolute_thresholds"]["BEND_BEND"] = value
            with self.assertRaises(ValueError): validate_config(cfg)
        cfg = load_config(); cfg["rules"]["active_types"].append("DIMPLE")
        with self.assertRaises(ValueError): validate_config(cfg)


class CandidateTests(unittest.TestCase):
    def test_move_each_endpoint_and_unlabeled_alternatives(self):
        c, cfg = two_ops(), small_config(); n = build_neighborhood(c, cfg)
        trials = propose_pair_alternatives(c, n, {"A": "Z", "B": "Z"}, "A__B", cfg)
        self.assertEqual(len(trials), 2)
        for t in trials:
            self.assertEqual(t["changed_operation_count"], 1)
            self.assertTrue(t["minimum_changes_proven"])
            self.assertIsNone(t["preference_label"])
            p = t["alternative"]["assignments"]
            self.assertTrue(HardConstraintEngine(c).evaluate(p).feasible)
            self.assertNotEqual(p["A"], p["B"])
            self.assertEqual(p[t["held_operation"]], "Z")

    def test_linked_operation_movement_fully_disclosed(self):
        c = two_ops()
        ops = (replace(c.part_data.operations[0], required_predecessor_ids=("T",)), c.part_data.operations[1], Operation("P", "T", "TRIM"))
        stations = tuple(replace(s, allowed_operation_types=(s.allowed_operation_types-{"IRREGULAR_PUNCH"})|{"TRIM"})
                         if s.station_id != "A" else replace(s, allowed_operation_types=s.allowed_operation_types|{"TRIM"}) for s in c.part_data.stations)
        c = context_for(ops, c.part_data.geometries, stations)
        cfg = small_config(); n = build_neighborhood(c, cfg)
        trials = propose_pair_alternatives(c, n, {"A": "M", "B": "A", "T": "A"}, "A__B", cfg)
        row = trials[0]
        self.assertEqual(row["collateral_operation_ids"], ["T"])
        self.assertEqual({r["operation_id"] for r in row["all_changes"]}, {"A", "T"})
        self.assertEqual(row["changed_operation_count"], 2)

    def test_joint_fallback_when_neither_endpoint_can_join_held_station(self):
        c = two_ops()
        stations = (Station("P", "Z", 10, frozenset({"FLANGING"})), Station("P", "A", 40, frozenset({"FLANGING", "IRREGULAR_PUNCH"})),
                    Station("P", "M", 90, frozenset({"IRREGULAR_PUNCH"})))
        c = context_for(c.part_data.operations, c.part_data.geometries, stations)
        cfg = small_config(); n = build_neighborhood(c, cfg)
        t = propose_pair_alternatives(c, n, {"A": "Z", "B": "M"}, "A__B", cfg)
        self.assertEqual([r["status"] for r in t], ["INFEASIBLE", "INFEASIBLE", "OPTIMAL"])
        self.assertEqual(t[2]["alternative"]["assignments"], {"A": "A", "B": "A"})
        self.assertEqual(t[2]["changed_operation_count"], 2)

    def test_hard_forced_different_not_relaxed_for_same_branch(self):
        c, cfg = two_ops(precedence=True), small_config(); n = build_neighborhood(c, cfg)
        base = {"A": "Z", "B": "A"}
        t = propose_pair_alternatives(c, n, base, "A__B", cfg)
        self.assertTrue(all(r["status"] == "INFEASIBLE" for r in t))
        chosen, audit = choose_pairs(c, n, base, 4)
        self.assertFalse(chosen)
        self.assertEqual(audit["excluded"][0]["reason"], "same_station_already_hard_impossible")

    def test_unknown_is_budget_failure_never_infeasible(self):
        class Solver:
            wall_time = best_objective_bound = 0
            response_proto = type("Response", (), {"deterministic_time": 0})()
            def solve(self, model): return cp_model.UNKNOWN
            def status_name(self, status): return "UNKNOWN"
        c, cfg = two_ops(), small_config(); n = build_neighborhood(c, cfg)
        with patch("src.neighborhood.candidates.solver_for", return_value=Solver()):
            t = propose_pair_alternatives(c, n, {"A": "Z", "B": "Z"}, "A__B", cfg)
        self.assertEqual(len(t), 3)
        self.assertTrue(all(r["skip_reason"] == "no_solution_within_budget" for r in t))

    def test_dedup_reproducibility_and_no_labels_scores_or_reference(self):
        c, cfg = two_ops(), small_config(); n = build_neighborhood(c, cfg)
        with patch.object(InferenceContext, "expert_reference_plan", property(lambda _: (_ for _ in ()).throw(AssertionError("leak")))):
            a = explore_neighborhood_candidates(c, n, cfg)
            b = explore_neighborhood_candidates(c, n, cfg)
        self.assertEqual([r["plan_hash"] for r in a["pool"]], [r["plan_hash"] for r in b["pool"]])
        self.assertEqual(len(a["pool"]), len({r["plan_hash"] for r in a["pool"]}))
        self.assertTrue(all(HardConstraintEngine(c).evaluate(p["assignments"]).feasible for p in a["pool"]))
        self.assertTrue(all(r["answer"] is None for r in a["comparisons"]))
        self.assertFalse(a["expert_used"] or a["soft_penalty_used"] or a["ready_for_expert_blind_review"])

    def test_no_sampling_budget_still_valid_base_pool(self):
        c, cfg = two_ops(), small_config(); cfg["generation"]["pair_budget_per_base"] = 0
        run = explore_neighborhood_candidates(c, build_neighborhood(c, cfg), cfg)
        self.assertFalse(run["trials"])
        self.assertEqual(run["summary"]["pool_count"], 4)

    def test_overlay_rank_not_station_string_and_soft_graph_unchanged(self):
        c, cfg = two_ops(), small_config(); n = build_neighborhood(c, cfg)
        graph = build_part_graph(c.part_data); fingerprint = graph.fingerprint()
        base = {"A": "Z", "B": "M"}
        before = SoftPriorEngine(c).evaluate(base)
        row = build_neighborhood_overlay(c, n, base)
        self.assertEqual(row["relations"][0]["station_rank_delta_a_to_b"], 2)
        self.assertIsNone(row["quality_score"])
        propose_pair_alternatives(c, n, base, "A__B", cfg)
        self.assertEqual(before, SoftPriorEngine(c).evaluate(base))
        self.assertEqual(graph.fingerprint(), fingerprint)

    def test_ignored_expert_columns_do_not_change_neighborhood_or_candidates(self):
        outcomes = []
        with tempfile.TemporaryDirectory() as tmp:
            for mode in ("valid", "blank", "absent", "invalid", "formula", "error"):
                path = Path(tmp)/(mode+".xlsx"); workbook(path, expert_mode=mode)
                c = load_inference_context(path); cfg = small_config(); n = build_neighborhood(c, cfg)
                run = explore_neighborhood_candidates(c, n, cfg)
                outcomes.append((n.fingerprint, [r["plan_hash"] for r in run["pool"]]))
        self.assertTrue(all(r == outcomes[0] for r in outcomes))


class SavedP001Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = ROOT/"outputs/neighborhood_v01"
        cls.n = json.loads((out/"neighborhood_context.json").read_text(encoding="utf-8"))
        cls.scenes = json.loads((out/"sensitivity.json").read_text(encoding="utf-8"))
        cls.exploration = json.loads((out/"candidate_exploration.json").read_text(encoding="utf-8"))
        cls.c = load_inference_context(next((ROOT/"data/parts/P001").glob("*P001.xlsx")))

    def test_known_neighbors_without_manual_pair_list_in_generation(self):
        pairs = {r["pair_id"] for r in self.n["relations"] if r["arcs"]}
        for pair in ("O066__O200", "O015__O196", "O031__O196", "O035__O196", "O025__O066", "O025__O196", "O009__O010"):
            self.assertIn(pair, pairs)
        self.assertNotIn("O035__O066", pairs)
        self.assertEqual(self.n["operations"]["O066"]["internal_pair_ids"], ["O066__O067"])
        self.assertEqual(len(self.n["operations"]["O066"]["outgoing_pair_ids"]), 3)

    def test_coverage_and_isolated_cases(self):
        self.assertEqual(self.n["summary"]["operations"], 224)
        self.assertEqual(self.n["summary"]["internal_pairs"], 46)
        self.assertEqual(self.n["summary"]["all_geometry_pairs"], 22155)
        self.assertEqual(self.n["operations"]["O184"]["outgoing_pair_ids"], [])
        self.assertEqual(self.n["operations"]["O186"]["policy"], "TARGET_ONLY")
        self.assertEqual(self.n["summary"]["policy_counts"]["MISSING_GEOMETRY"], 13)

    def test_sensitivity_nested_and_not_altering_candidate_roles(self):
        scenes = {s["scenario_id"]: {r["pair_id"] for r in s["relations"] if r["arcs"]} for s in self.scenes}
        self.assertTrue(scenes["size_2"] <= scenes["primary"] <= scenes["size_4"])
        self.assertTrue(scenes["absolute_5"] <= scenes["primary"] <= scenes["absolute_15"])
        self.assertFalse(self.exploration["expert_used"] or self.exploration["soft_penalty_used"])

    def test_all_saved_candidates_complete_hard_legal_and_unique(self):
        hashes = set()
        for r in self.exploration["pool"]:
            self.assertNotIn(r["plan_hash"], hashes); hashes.add(r["plan_hash"])
            self.assertEqual(set(r["assignments"]), set(self.c.operations))
            self.assertTrue(HardConstraintEngine(self.c).evaluate(r["assignments"]).feasible)
        self.assertEqual(self.exploration["summary"]["base_pool_count"], 16)
        self.assertEqual(len(self.exploration["selected"]), 16)

    def test_saved_local_targets_holds_changes_and_labels(self):
        bases = {r["plan_hash"]: r["assignments"] for r in self.exploration["pool"]}
        for t in self.exploration["trials"]:
            if "alternative" not in t: continue
            a, b = t["operation_ids"]; base = bases[t["base_plan_hash"]]; alt = t["alternative"]["assignments"]
            self.assertEqual(alt[a] == alt[b], t["target_same_station"])
            self.assertEqual({op for op in base if base[op] != alt[op]}, {c["operation_id"] for c in t["all_changes"]})
            if t["held_operation"]: self.assertEqual(base[t["held_operation"]], alt[t["held_operation"]])
            self.assertIsNone(t["preference_label"])
        self.assertTrue(all(c["answer"] is None for c in self.exploration["comparisons"]))


if __name__ == "__main__":
    unittest.main()
