"""H1-H6 unchanged; H7 is supplied by the explicit v0.6 context."""
from collections import Counter

from ..hard_constraints import HardConstraintEngine as LegacyEngine, HardConstraintResult
from .context import RULE_VERSION, RuleContext


class RuleConstraintResult(HardConstraintResult):
    def to_dict(self):
        result = super().to_dict()
        result["rule_version"] = RULE_VERSION
        result["constraint_names"] = {"H7": "FEATURE_TREE_PRECEDENCE"}
        result["violation_breakdown"]["H7"] = Counter(v.constraint_type for v in self.violations)["H7"]
        return result


class HardConstraintEngine(LegacyEngine):
    def __init__(self, context):
        if not isinstance(context, RuleContext):
            raise TypeError("v0.6 engine requires RuleContext; use RuleContext.from_context explicitly")
        super().__init__(context)

    def evaluate(self, plan):
        result = super().evaluate(plan)
        return RuleConstraintResult(result.feasible, result.violations)
