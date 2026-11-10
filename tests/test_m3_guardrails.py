import unittest

from src.constraints import HardConstraintEngine, load_constraint_context
from src.constraints.config import load_config
from src.constraints.guardrails import StationOverloadGuardrail
from src.constraints.soft_priors import SoftPriorEngine
from src.data.schema import Operation
from m3_fixtures import P001, context


class GuardrailTests(unittest.TestCase):
    def setUp(self):
        ops = tuple(Operation("P", f"A{i}", "FLANGING") for i in range(8))
        self.context = context(ops, assignments={op.operation_id: "OP90" if i == 0 else "OP10" for i, op in enumerate(ops)})

    def plan(self, first_station_count):
        return {f"A{i}": "OP90" if i < first_station_count else "OP10" for i in range(8)}

    def test_expert_loads_ceil_absolute_and_empty_station(self):
        r = StationOverloadGuardrail(self.context).evaluate(self.context.expert_reference_plan)
        self.assertEqual([(s["expert_load"], s["guardrail_upper_bound"]) for s in r["stations"]], [(1, 6), (7, 13), (0, 5), (0, 5)])
        self.assertFalse(r["guardrail_violation"])

    def test_exceeds_limit_only_strictly(self):
        guard = StationOverloadGuardrail(self.context)
        self.assertFalse(guard.evaluate(self.plan(6))["guardrail_violation"])
        self.assertTrue(guard.evaluate(self.plan(7))["guardrail_violation"])

    def test_guardrail_violation_is_still_hard_feasible(self):
        plan = self.plan(8)
        self.assertTrue(HardConstraintEngine(self.context).evaluate(plan).feasible)
        self.assertTrue(StationOverloadGuardrail(self.context).evaluate(plan)["guardrail_violation"])

    def test_disabled_guardrail(self):
        config = load_config()
        config["station_overload_guardrail"]["enabled"] = False
        self.assertFalse(StationOverloadGuardrail(self.context, config).evaluate(self.plan(8))["guardrail_violation"])

    def test_configurable_limits(self):
        config = load_config()
        config["station_overload_guardrail"].update(relative_limit=2.0, absolute_slack=0)
        r = StationOverloadGuardrail(self.context, config).evaluate(self.context.expert_reference_plan)
        self.assertEqual([s["guardrail_upper_bound"] for s in r["stations"]], [2, 14, 0, 0])

    def test_guardrail_execution_and_config_do_not_affect_soft_penalty(self):
        plan, config = self.plan(8), load_config()
        before = SoftPriorEngine(self.context, config).evaluate(plan)
        guard = StationOverloadGuardrail(self.context, config).evaluate(plan)
        self.assertTrue(guard["guardrail_violation"])
        config["station_overload_guardrail"].update(relative_limit=100.0, absolute_slack=100)
        self.assertFalse(StationOverloadGuardrail(self.context, config).evaluate(plan)["guardrail_violation"])
        self.assertEqual(before, SoftPriorEngine(self.context, config).evaluate(plan))

    def test_p001_thresholds(self):
        c = load_constraint_context(P001)
        r = StationOverloadGuardrail(c).evaluate(c.expert_reference_plan)
        self.assertEqual({s["station_id"]: s["guardrail_upper_bound"] for s in r["stations"]},
                         {"OP10": 93, "OP20": 79, "OP30": 111, "OP40": 44, "OP50": 63, "OP60": 7, "OP70": 5})
