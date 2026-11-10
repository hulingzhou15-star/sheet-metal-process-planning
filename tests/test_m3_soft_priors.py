from copy import deepcopy
from dataclasses import replace
import math
import unittest

from src.constraints import FixedAssignment, load_constraint_context
from src.constraints.config import load_config, validate_config
from src.constraints.soft_priors import SoftPriorEngine, bend_ordering_statistics, precedence_extra_gap, type_fragmentation
from src.data.schema import Operation, Station
from m3_fixtures import P001, bends, context


class AreaOrderingTests(unittest.TestCase):
    def statistics(self, c, plan=None):
        return bend_ordering_statistics(c, plan or {"A": "OP90", "B": "OP10"},
                                        metric="affected_area_proxy", significance_ratio=load_config()["bend_area_ordering"]["significance_ratio"])

    def test_area_inversion(self):
        r = self.statistics(bends())
        self.assertEqual((r["comparable_pair_count"], r["inversion_count"], r["inversion_rate"]), (1, 1, 1))

    def test_insignificant_ten_percent_difference(self):
        r = self.statistics(bends((110, 100)))
        self.assertEqual((r["comparable_pair_count"], r["inversion_rate"]), (0, 0))

    def test_more_than_fifteen_percent_is_compared(self):
        self.assertEqual(self.statistics(bends((116, 100)))["comparable_pair_count"], 1)

    def test_exact_fifteen_percent_is_not_compared(self):
        self.assertEqual(self.statistics(bends((115, 100)))["comparable_pair_count"], 0)

    def test_same_station_no_inversion_retains_comparable_denominator(self):
        r = self.statistics(bends(), {"A": "OP90", "B": "OP90"})
        self.assertEqual((r["comparable_pair_count"], r["inversion_count"]), (1, 0))

    def test_ancestor_descendant_excluded(self):
        r = self.statistics(bends(parents=(None, "R1")))
        self.assertEqual((r["excluded_hierarchy_pair_count"], r["comparable_pair_count"]), (1, 0))

    def test_transitive_ancestor_descendant_excluded(self):
        c = bends((400, 200, 100), parents=(None, "R1", "R2"))
        r = self.statistics(c, {"A": "OP80", "B": "OP10", "C": "OP90"})
        self.assertEqual((r["excluded_hierarchy_pair_count"], r["comparable_pair_count"]), (3, 0))

    def test_zero_area_no_division_failure(self):
        r = self.statistics(bends((100, 0)))
        self.assertEqual(r["inversion_rate"], 1)
        self.assertEqual(self.statistics(bends((0, 0)))["comparable_pair_count"], 0)


class ExtraGapTests(unittest.TestCase):
    def setUp(self):
        self.context = context((Operation("P", "A", "TRIM"), Operation("P", "B", "TRIM", required_predecessor_ids=("A",))))

    def penalty(self, a="OP90", b="OP10", context_override=None):
        return precedence_extra_gap(context_override or self.context, {"A": a, "B": b})

    def test_minimum_gap_zero(self):
        self.assertEqual(self.penalty()["value"], 0)

    def test_maximum_gap_one(self):
        self.assertEqual(self.penalty(b="OP05")["value"], 1)

    def test_intermediate_gap_rank_normalized(self):
        r = self.penalty(b="OP80")
        self.assertEqual(r["value"], .5)
        self.assertEqual([r["relations"][0][key] for key in ("g_min", "g_actual", "g_max")], [1, 2, 3])

    def test_fixed_station_affects_minimum_and_maximum(self):
        c = replace(self.context, fixed_assignments=(FixedAssignment("A", "OP10"),))
        r = self.penalty(a="OP10", b="OP05", context_override=c)
        self.assertEqual([r["relations"][0][key] for key in ("g_min", "g_actual", "g_max")], [1, 2, 2])
        self.assertEqual(r["value"], 1)

    def test_fixed_pair_min_equals_max_penalty_zero(self):
        c = replace(self.context, fixed_assignments=(FixedAssignment("A", "OP90"), FixedAssignment("B", "OP05")))
        r = self.penalty(b="OP05", context_override=c)
        self.assertEqual([r["relations"][0][key] for key in ("g_min", "g_actual", "g_max")], [3, 3, 3])
        self.assertEqual(r["value"], 0)

    def test_capability_restricts_local_station_combinations(self):
        ops = (Operation("P", "A", "PRE_PUNCH"), Operation("P", "B", "FLANGING", required_predecessor_ids=("A",)))
        stations = (Station("P", "late_name", 50, frozenset({"PRE_PUNCH"})),
                    Station("P", "early_name", 90, frozenset({"FLANGING"})))
        c = context(ops, stations=stations)
        r = precedence_extra_gap(c, {"A": "late_name", "B": "early_name"})
        self.assertEqual((r["relations"][0]["g_min"], r["relations"][0]["g_max"], r["value"]), (1, 1, 0))

    def test_no_relations_zero(self):
        c = bends()
        self.assertEqual(precedence_extra_gap(c, {"A": "OP90", "B": "OP90"})["value"], 0)

    def test_relation_multiplicity_ambiguity_not_silently_weighted(self):
        c = context((Operation("P", "A", "PRE_PUNCH", process_group_id="PG"),
                     Operation("P", "B", "FLANGING", required_predecessor_ids=("A",), process_group_id="PG")))
        with self.assertRaisesRegex(ValueError, "multiplicity"):
            precedence_extra_gap(c, {"A": "OP90", "B": "OP10"})


class FragmentationTests(unittest.TestCase):
    def setUp(self):
        self.context = context(tuple(Operation("P", chr(65+i), "FLANGING") for i in range(4)))

    def test_all_flanging_one_station_zero(self):
        r = type_fragmentation(self.context, {op: "OP05" for op in "ABCD"}, ["FLANGING"])
        self.assertEqual(r["value"], 0)
        self.assertEqual(r["types"][0]["actual_used_stations"], ["OP05"])

    def test_all_allowed_stations_one(self):
        plan = dict(zip("ABCD", ("OP90", "OP10", "OP80", "OP05")))
        self.assertEqual(type_fragmentation(self.context, plan, ["FLANGING"])["value"], 1)

    def test_non_enabled_type_does_not_participate(self):
        c = context((Operation("P", "A", "FLANGING"), Operation("P", "B", "BEND"), Operation("P", "C", "BEND")))
        r = type_fragmentation(c, {"A": "OP10", "B": "OP90", "C": "OP05"}, ["FLANGING"])
        self.assertEqual(r["value"], 0)
        self.assertEqual([row["operation_type"] for row in r["types"]], ["FLANGING"])

    def test_single_allowed_station_does_not_participate(self):
        c = context((Operation("P", "A", "FLANGING"),), stations=(Station("P", "X", 10, frozenset({"FLANGING"})),))
        r = type_fragmentation(c, {"A": "X"}, ["FLANGING"])
        self.assertEqual((r["value"], r["participating_type_count"]), (0, 0))

    def test_enabled_types_average(self):
        c = context((Operation("P", "A", "FLANGING"), Operation("P", "B", "BEND"),
                     Operation("P", "C", "BEND"), Operation("P", "D", "BEND"), Operation("P", "E", "BEND")))
        plan = {"A": "OP10", "B": "OP90", "C": "OP10", "D": "OP80", "E": "OP05"}
        self.assertEqual(type_fragmentation(c, plan, ["FLANGING", "BEND"])["value"], .5)


class GeneralSoftTests(unittest.TestCase):
    def test_components_total_and_weight_contributions(self):
        c = load_constraint_context(P001)
        result = SoftPriorEngine(c).evaluate(c.expert_reference_plan)
        for component in result["components"].values():
            self.assertTrue(0 <= component["raw_component"] <= 1)
        self.assertTrue(0 <= result["total_soft_penalty"] <= 1)
        self.assertEqual(math.fsum(r["weight"] for r in result["components"].values()), 1)
        self.assertEqual(result["total_soft_penalty"], math.fsum(r["weighted_contribution"] for r in result["components"].values()))

    def test_invalid_weight_sum_rejected_without_auto_tuning(self):
        config = load_config()
        config["soft_weights"]["bend_area_ordering"] = .5
        with self.assertRaisesRegex(ValueError, "sum to 1.0"):
            validate_config(config)

    def test_negative_nan_weights_and_false_hierarchy_exclusion_rejected(self):
        for mutate in (lambda c: c["soft_weights"].update(bend_area_ordering=-1),
                       lambda c: c["soft_weights"].update(bend_area_ordering=float("nan")),
                       lambda c: c["bend_area_ordering"].update(exclude_ancestor_descendant_pairs=False)):
            config = load_config()
            mutate(config)
            with self.assertRaises(ValueError):
                validate_config(config)

    def test_configuration_is_copied_not_mutated(self):
        config = load_config()
        before = deepcopy(config)
        c = bends()
        SoftPriorEngine(c, config).evaluate({"A": "OP90", "B": "OP10"})
        self.assertEqual(config, before)

    def test_infeasible_plan_rejected_before_scoring(self):
        c = bends()
        with self.assertRaisesRegex(ValueError, "hard-feasible"):
            SoftPriorEngine(c).evaluate({"A": "OP90"})
