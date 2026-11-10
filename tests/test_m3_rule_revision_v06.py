from copy import deepcopy
from dataclasses import replace
from itertools import product
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ortools.sat.python import cp_model

from src.constraints import HardConstraintEngine as LegacyHard, ContextValidationError, FixedAssignment
from src.constraints.v06 import RuleContext, load_rule_context, HardConstraintEngine, SoftPriorEngine, load_config
from src.constraints.v06.config import validate_config
from src.constraints.v06.feasibility import build_feasibility_model, DirectionFragmentation, BoundComputationError, make_solver
from src.constraints.v06.proximity import proximity_statistics
from src.constraints.v06.soft_priors import area_ordering, precedence_extra_gap
from src.data.schema import Operation, Station
from src.graph.builders import build_part_graph
from src.graph.geometry_distance import geometry_distance
from scripts.evaluate_rule_revision_v06 import evaluate_expert, revalidate_candidates
from m3_fixtures import P001, context, region


def rule_context(operations, *args, **kwargs):
    return RuleContext.from_context(context(operations, *args, **kwargs))


def bend(op, *, side="SMOOTH", parent=None, predecessors=()):
    return Operation("P", op, "BEND", surface_side=side, cad_parent_operation_id=parent,
                     required_predecessor_ids=predecessors)


def direction(context):
    config = load_config()
    return DirectionFragmentation(context, enabled_directions=config["bend_direction_fragmentation"]["enabled_directions"],
                                  solver_settings=config["solver"])


class FeatureTreeTests(unittest.TestCase):
    def test_current_p001_expert_and_corrected_relations(self):
        c = load_rule_context(P001)
        r = HardConstraintEngine(c).evaluate(c.expert_reference_plan)
        self.assertTrue(r.feasible)
        self.assertEqual(r.to_dict()["violation_breakdown"], {f"H{i}": 0 for i in range(1, 8)})
        self.assertEqual(len(c.feature_tree_relations), 153)
        self.assertEqual(len(c.precedence_relations), 265)
        self.assertEqual(len({(r.predecessor_id, r.operation_id) for r in c.precedence_relations}), 187)
        self.assertEqual((c.operations["O024"].cad_parent_operation_id, c.operations["O047"].cad_parent_operation_id), ("O023", "O046"))

    def test_child_before_parent_station_order_not_name(self):
        c = rule_context((bend("A", parent="B"), bend("B")))
        self.assertTrue(HardConstraintEngine(c).evaluate({"A": "OP90", "B": "OP10"}).feasible)
        for plan in ({"A": "OP90", "B": "OP90"}, {"A": "OP10", "B": "OP90"}):
            result = HardConstraintEngine(c).evaluate(plan)
            self.assertEqual({v.constraint_type for v in result.violations}, {"H7"})
            self.assertEqual(result.to_dict()["violation_breakdown"]["H7"], 1)
            self.assertEqual(result.violations[0].context["relation_source"], "FEATURE_TREE_PRECEDENCE")

    def test_all_types_not_bend_only(self):
        c = rule_context((Operation("P", "A", "PRE_PUNCH", cad_parent_operation_id="B"), Operation("P", "B", "FLANGING")))
        self.assertFalse(HardConstraintEngine(c).evaluate({"A": "OP90", "B": "OP90"}).feasible)

    def test_legacy_context_still_allows_cad_same_station(self):
        c = context((bend("A", parent="B"), bend("B")))
        self.assertTrue(LegacyHard(c).evaluate({"A": "OP90", "B": "OP90"}).feasible)
        with self.assertRaises(TypeError):
            HardConstraintEngine(c)

    def test_invalid_and_self_parent(self):
        for parent in ("missing", "A"):
            with self.subTest(parent=parent), self.assertRaises(ContextValidationError):
                rule_context((bend("A", parent=parent), bend("B")))

    def test_cad_cycle(self):
        with self.assertRaises(ContextValidationError) as caught:
            rule_context((bend("A", parent="B"), bend("B", parent="A")))
        self.assertEqual(caught.exception.issues[0].code, "HARD_PRECEDENCE_CYCLE")

    def test_combined_explicit_and_cad_cycle(self):
        with self.assertRaises(ContextValidationError):
            rule_context((bend("A", parent="B", predecessors=("B",)), bend("B")))

    def test_transitive_combined_cycle(self):
        with self.assertRaises(ContextValidationError):
            rule_context((bend("A", parent="B", predecessors=("C",)), bend("B", parent="C"), bend("C")))

    def test_cycle_between_h6_and_cad(self):
        with self.assertRaises(ContextValidationError):
            rule_context((bend("A", parent="B"), bend("B")), (region("A", "RA"), region("B", "RB", parent="RA")))

    def test_h7_provenance_cell(self):
        c = load_rule_context(P001)
        relation = next(r for r in c.feature_tree_relations if r.predecessor_id == "O024")
        self.assertEqual((relation.operation_id, relation.source.sheet, relation.source.row, relation.source.field),
                         ("O023", "Sheet1", 25, "cad_parent_operation_id"))

    def test_cached_precedence_facts_are_read_only(self):
        c = rule_context((bend("A", parent="B"), bend("B")))
        with self.assertRaises(TypeError):
            c.precedence_reachability["A"] = frozenset()
        with self.assertRaises(TypeError):
            c.precedence_successors["A"] = frozenset()

    def test_O062_O025_same_station_rejected(self):
        c = load_rule_context(P001)
        plan = {a.operation_id: a.station_id for a in c.expert_reference_plan.assignments}
        plan["O062"] = plan["O025"]
        result = HardConstraintEngine(c).evaluate(plan)
        self.assertTrue(any(v.constraint_type == "H7" and v.operation_ids == ("O062", "O025") for v in result.violations))

    def test_solver_and_engine_agree_exhaustively(self):
        stations = tuple(Station("P", name, order, frozenset({"BEND"})) for name, order in (("Z", 10), ("A", 40), ("M", 99)))
        c = rule_context((bend("x", parent="y"), bend("y"), bend("z", predecessors=("x",))), stations=stations)
        for values in product(("Z", "A", "M"), repeat=3):
            plan = dict(zip(("x", "y", "z"), values))
            model, ranks = build_feasibility_model(c)
            for op, station in plan.items():
                model.add(ranks[op] == c.station_ranks[station])
            status = make_solver(load_config()["solver"]).solve(model)
            self.assertEqual(status in (cp_model.OPTIMAL, cp_model.FEASIBLE), HardConstraintEngine(c).evaluate(plan).feasible)

    def test_acyclic_but_capability_infeasible(self):
        stations = (Station("P", "ONLY", 1, frozenset({"BEND"})),)
        c = rule_context((bend("A", parent="B"), bend("B")), stations=stations)
        model, _ = build_feasibility_model(c)
        self.assertEqual(make_solver(load_config()["solver"]).solve(model), cp_model.INFEASIBLE)
        with self.assertRaises(BoundComputationError):
            direction(c).bounds()

    def test_solver_enforces_fixed_assignment(self):
        c = rule_context((Operation("P", "T", "TRIM"),), fixed=(FixedAssignment("T", "OP10"),))
        model, ranks = build_feasibility_model(c)
        model.add(ranks["T"] == c.station_ranks["OP90"])
        self.assertEqual(make_solver(load_config()["solver"]).solve(model), cp_model.INFEASIBLE)

    def test_solver_does_not_read_expert(self):
        c = rule_context((bend("A", parent="B"), bend("B")))
        c = replace(c, part_data=replace(c.part_data, expert_reference_plan=None))
        model, _ = build_feasibility_model(c)
        self.assertEqual(make_solver(load_config()["solver"]).solve(model), cp_model.OPTIMAL)
        self.assertEqual(direction(c).bounds()[0]["K_min"], 2)


class RevisedSoftTests(unittest.TestCase):
    def test_multiple_sources_count_once_and_all_retained(self):
        c = rule_context((Operation("P", "A", "PRE_PUNCH", cad_parent_operation_id="B", process_group_id="PG"),
                          Operation("P", "B", "FLANGING", required_predecessor_ids=("A",), process_group_id="PG")))
        r = precedence_extra_gap(c, {"A": "OP90", "B": "OP80"})
        self.assertEqual((r["raw_relation_count"], r["relation_count"], r["duplicate_source_record_count"]), (3, 1, 2))
        self.assertEqual(r["value"], .5)
        self.assertEqual(len(r["relations"][0]["all_sources"]), 3)
        self.assertEqual(set(r["relations"][0]["constraint_types"]), {"H3", "H4", "H7"})
        self.assertEqual(sum(x["component_contribution"] for x in r["exclusive_source_sets"].values()), r["value"])

    def test_gap_min_max_intermediate(self):
        c = rule_context((bend("A", parent="B"), bend("B")))
        for station, value in (("OP10", 0), ("OP80", .5), ("OP05", 1)):
            self.assertEqual(precedence_extra_gap(c, {"A": "OP90", "B": station})["value"], value)

    def test_fixed_gap_no_extra_penalty(self):
        c = rule_context((Operation("P", "A", "TRIM", cad_parent_operation_id="B"), Operation("P", "B", "TRIM")),
                         fixed=(FixedAssignment("A", "OP90"), FixedAssignment("B", "OP80")))
        r = precedence_extra_gap(c, {"A": "OP90", "B": "OP80"})
        self.assertEqual((r["relations"][0]["g_min"], r["relations"][0]["g_max"], r["value"]), (2, 2, 0))

    def test_area_excludes_direct_hard_order(self):
        c = rule_context((bend("A", parent="B"), bend("B")), (region("A", "RA", area=200), region("B", "RB", area=100)))
        r = area_ordering(c, {"A": "OP90", "B": "OP10"}, 1.15)
        self.assertEqual((r["comparable_pair_count"], r["excluded_additional_hard_precedence_pair_count"], r["inversion_rate"]), (0, 1, 0))

    def test_area_excludes_transitive_hard_order(self):
        c = rule_context((bend("A", parent="B"), bend("B", parent="C"), bend("C")),
                         tuple(region(op, "R"+op, area=100) for op in "ABC"))
        r = area_ordering(c, {"A": "OP90", "B": "OP10", "C": "OP80"}, 1.15)
        self.assertEqual(r["excluded_additional_hard_precedence_pair_count"], 3)
        self.assertIn(["A", "B", "C"], [p["hard_precedence_path"] for p in r["excluded_additional_hard_precedence_pairs"]])

    def test_hierarchy_not_double_counted_as_additional_exclusion(self):
        c = rule_context((bend("A"), bend("B", parent="A")), (region("A", "RA"), region("B", "RB", parent="RA")))
        r = area_ordering(c, {"A": "OP10", "B": "OP90"}, 1.15)
        self.assertEqual((r["excluded_hierarchy_pair_count"], r["excluded_additional_hard_precedence_pair_count"]), (1, 0))

    def test_independent_area_pairs_keep_old_definition(self):
        for area, count in ((110, 0), (115, 0), (116, 1)):
            c = rule_context((bend("A"), bend("B")), (region("A", "RA", area=area), region("B", "RB", area=100)))
            r = area_ordering(c, {"A": "OP90", "B": "OP10"}, 1.15)
            self.assertEqual(r["comparable_pair_count"], count)
        self.assertEqual(area_ordering(c, {"A": "OP90", "B": "OP90"}, 1.15)["inversion_count"], 0)

    def test_expert_components_bounded_and_direction_unweighted(self):
        c = load_rule_context(P001)
        r = SoftPriorEngine(c).evaluate(c.expert_reference_plan)
        self.assertEqual(r["total_soft_penalty"], sum(x["weighted_contribution"] for x in r["components"].values()))
        self.assertTrue(all(0 <= x["raw_component"] <= 1 for x in r["components"].values()))
        self.assertTrue(0 <= r["total_soft_penalty"] <= 1)
        self.assertEqual(sum(x["weight"] for x in r["components"].values()), 1)
        self.assertIsNone(r["unweighted_components"]["bend_direction_fragmentation"]["weight"])
        self.assertFalse(r["unweighted_components"]["bend_direction_fragmentation"]["included_in_total"])

    def test_invalid_plan_rejected_before_bounds(self):
        c = rule_context((bend("A", parent="B"), bend("B")))
        with patch.object(DirectionFragmentation, "bounds", side_effect=AssertionError("must not execute")):
            with self.assertRaisesRegex(ValueError, "hard-feasible"):
                SoftPriorEngine(c).evaluate({"A": "OP90", "B": "OP90"})


class DirectionTests(unittest.TestCase):
    def test_min_intermediate_max(self):
        c = rule_context(tuple(bend(o, side="ROUGH") for o in "ABC"))
        engine = direction(c)
        plans = [({o: "OP90" for o in "ABC"}, 0), ({"A": "OP90", "B": "OP90", "C": "OP10"}, .5),
                 ({"A": "OP90", "B": "OP10", "C": "OP80"}, 1)]
        for plan, value in plans:
            self.assertEqual(engine.evaluate(plan)["value"], value)

    def test_mandatory_chain_not_penalized(self):
        c = rule_context((bend("A", parent="B"), bend("B", parent="C"), bend("C")))
        r = direction(c).evaluate({"A": "OP90", "B": "OP10", "C": "OP80"})
        row = r["directions"][0]
        self.assertEqual((row["K_min"], row["K_max"], row["fragmentation_penalty"], r["value"]), (3, 3, 0, 0))
        self.assertFalse(row["participating"])

    def test_fixed_direction_does_not_dilute_adjustable_direction(self):
        c = rule_context((bend("A", parent="B"), bend("B", parent="C"), bend("C"),
                          bend("D", side="ROUGH"), bend("E", side="ROUGH")))
        r = direction(c).evaluate({"A": "OP90", "B": "OP10", "C": "OP80", "D": "OP90", "E": "OP10"})
        self.assertEqual((r["value"], r["participating_direction_count"]), (1, 1))

    def test_absent_bends_zero(self):
        c = rule_context((Operation("P", "T", "TRIM"),))
        self.assertEqual(direction(c).evaluate({"T": "OP90"})["value"], 0)

    def test_invalid_side_reported(self):
        c = rule_context((bend("A", side="NOT_APPLICABLE"),))
        with self.assertRaisesRegex(BoundComputationError, "direction unavailable"):
            direction(c).bounds()

    def test_unknown_bound_never_used(self):
        c = rule_context((bend("A"),))
        fake = unittest.mock.Mock()
        fake.solve.return_value = cp_model.FEASIBLE
        fake.status_name.return_value = "FEASIBLE"
        with patch("src.constraints.v06.feasibility.make_solver", return_value=fake):
            with self.assertRaisesRegex(BoundComputationError, "not proven"):
                direction(c).bounds()

    def test_bound_cache_and_returned_data_isolation(self):
        c = rule_context((bend("A"), bend("B")))
        engine = direction(c)
        bounds = engine.bounds()
        bounds[0]["K_min"] = 99
        with patch("src.constraints.v06.feasibility.make_solver", side_effect=AssertionError("cache must be reused")):
            self.assertEqual(engine.bounds()[0]["K_min"], 1)

    def test_p001_exact_bounds(self):
        c = load_rule_context(P001)
        rows = {r["direction"]: r for r in direction(c).bounds()}
        self.assertEqual((rows["SMOOTH"]["K_min"], rows["SMOOTH"]["K_max"]), (3, 3))
        self.assertEqual((rows["ROUGH"]["K_min"], rows["ROUGH"]["K_max"]), (1, 2))


class ProximityAndWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = load_rule_context(P001)
        cls.graph = build_part_graph(cls.context.part_data)
        cls.stats = proximity_statistics(cls.context, cls.context.expert_reference_plan, k=5, static=cls.graph)

    def test_k5_union_and_rank_counts(self):
        r = self.stats
        self.assertEqual((r["directed_count"], r["undirected_union_count"], r["mutual_pair_count"]), (1055, 680, 375))
        self.assertEqual({row["neighbor_rank"] for row in r["directed_pairs"]}, {1, 2, 3, 4, 5})
        self.assertEqual(sum(row["pair_count"] for row in r["undirected_type_statistics"]), 680)

    def test_expert_O131_O203_is_included_without_cad_parent(self):
        r = self.stats["expert_example_O131_O203"]
        self.assertEqual(r["ranks"], {"O131": 2, "O203": 3})
        self.assertAlmostEqual(r["boundary_distance"], 2.1)
        self.assertEqual(r["station_ids"], ["OP30", "OP20"])
        self.assertFalse(r["irregular_bend_parent_facts"]["O203"])
        self.assertIn("折弯异形孔", r["source_feature_refs"]["O203"])

    def test_directed_and_one_sided_union_preserved(self):
        row = next(r for r in self.stats["undirected_pairs"] if r["operation_ids"] == ["O041", "O073"])
        self.assertEqual(row["ranks"], {"O073": 5})
        self.assertTrue(row["same_station"])

    def test_reuses_exact_M2_distance(self):
        for a, b in (("O131", "O203"), ("O041", "O073")):
            expected = geometry_distance(self.context.part_data.geometry_for(self.context.operations[a]),
                                         self.context.part_data.geometry_for(self.context.operations[b]))
            actual = next(r["boundary_distance"] for r in self.stats["undirected_pairs"] if r["operation_ids"] == [a, b])
            self.assertAlmostEqual(actual, expected)

    def test_conflict_score_not_enabled_and_graph_unchanged(self):
        before = self.graph.fingerprint()
        self.assertFalse(self.stats["included_in_total"])
        self.assertEqual(self.graph.knn_diagnostics.k, 8)
        self.assertEqual(before, self.graph.fingerprint())
        self.assertNotIn("penalty", self.stats)

    def test_uncached_k_rejected(self):
        with self.assertRaises(ValueError):
            proximity_statistics(self.context, self.context.expert_reference_plan, k=9, static=self.graph)

    def test_cached_graph_with_different_geometry_rejected(self):
        geometries = list(self.graph.raw_geometries)
        geometries[self.graph.node_index["O131"]] = None
        bad = replace(self.graph, raw_geometries=tuple(geometries))
        with self.assertRaisesRegex(ValueError, "geometry"):
            proximity_statistics(self.context, self.context.expert_reference_plan, k=5, static=bad)

    def test_expert_conflict_stops_following_scores(self):
        expert = self.context.expert_reference_plan
        assignments = tuple(replace(a, station_id="OP60") if a.operation_id == "O062" else a for a in expert.assignments)
        c = replace(self.context, part_data=replace(self.context.part_data, expert_reference_plan=replace(expert, assignments=assignments)))
        report = evaluate_expert(c, load_config())
        self.assertNotEqual(report["status"], "evaluated")
        self.assertNotIn("soft_priors", report)

    def test_candidate_audit_leaves_original_bytes_and_skips_invalid_scores(self):
        c = rule_context((bend("A", parent="B"), bend("B")))
        rows = [{"plan_id": "good", "plan_hash": "identity-1", "assignments": {"A": "OP90", "B": "OP10"}},
                {"plan_id": "bad", "plan_hash": "identity-2", "assignments": {"A": "OP90", "B": "OP90"}}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"plans.jsonl"
            path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            before = path.read_bytes()
            result = revalidate_candidates(c, load_config(), {"selected": path})["collections"]["selected"]
            self.assertEqual((result["feasible_count"], result["infeasible_count"]), (1, 1))
            self.assertNotIn("v06_soft_priors", result["plans"][1])
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual([r["plan_id"] for r in result["plans"]], ["good", "bad"])


class ConfigTests(unittest.TestCase):
    def test_config_copied_and_weights_unchanged(self):
        c = load_config()
        copy = deepcopy(c)
        validate_config(c)
        self.assertEqual(c, copy)
        self.assertEqual(c["soft_weights"], {"bend_area_ordering": .45, "precedence_extra_gap": .20, "type_fragmentation": .35})

    def test_new_weight_or_changed_original_weight_rejected(self):
        for key in ("bend_direction_fragmentation", "soft_weights"):
            config = load_config()
            if key == "soft_weights":
                config[key]["bend_area_ordering"] = .5
            else:
                config[key]["weight"] = .1
            with self.assertRaises(ValueError):
                validate_config(config)

    def test_invalid_k_solver_or_area(self):
        for section, key, value in (("proximity_diagnostic", "k", 0), ("solver", "max_time_seconds", float("nan")),
                                    ("solver", "num_search_workers", 0), ("bend_area_ordering", "significance_ratio", 1)):
            config = load_config()
            config[section][key] = value
            with self.assertRaises(ValueError):
                validate_config(config)


if __name__ == "__main__":
    unittest.main()
