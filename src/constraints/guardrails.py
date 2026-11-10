"""G1 is a broad future generator filter, never feasibility or quality."""
from collections import Counter
import math

from .config import load_config, validate_config
from .hard_constraints import HardConstraintEngine


class StationOverloadGuardrail:
    def __init__(self, context, config=None):
        self.context = context
        self.config = load_config() if config is None else validate_config(config)
        expert = HardConstraintEngine(context).require_feasible(context.expert_reference_plan)
        self.expert_loads = Counter(expert.values())

    def evaluate(self, plan):
        assignments = HardConstraintEngine(self.context).require_feasible(plan)
        actual = Counter(assignments.values())
        guard = self.config["station_overload_guardrail"]
        records = []
        for station in self.context.ordered_stations:
            load = self.expert_loads[station.station_id]
            upper = max(load+guard["absolute_slack"], math.ceil(guard["relative_limit"]*load))
            records.append({"station_id": station.station_id, "expert_load": load,
                            "guardrail_upper_bound": upper, "plan_load": actual[station.station_id],
                            "guardrail_violation": guard["enabled"] and actual[station.station_id] > upper})
        return {"guardrail_type": "G1_EXTREME_STATION_OVERLOAD", "enabled": guard["enabled"],
                "affects_soft_penalty": False, "is_station_capacity": False,
                "guardrail_violation": any(r["guardrail_violation"] for r in records), "stations": records}
