"""One immutable local movable set for all expert and pairwise distances."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
import hashlib
import json

from src.constraints.hard_constraints import plan_pairs


def plan_hash(plan):
    pairs = plan_pairs(plan)
    if len({op for op, station in pairs}) != len(pairs):
        raise ValueError("Cannot hash a Plan with duplicate Operation IDs")
    if any(not isinstance(op, str) or not isinstance(station, str) for op, station in pairs):
        raise ValueError("Plan hash requires string Operation and Station IDs")
    canonical = json.dumps(sorted(pairs), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PlanDistance:
    operation_ids: tuple[str, ...]
    movable_operation_ids: tuple[str, ...]

    @classmethod
    def from_context(cls, context):
        all_ids = tuple(sorted(context.operations))
        movable = tuple(op for op in all_ids if len(context.allowed_stations(op)) >= 2)
        return cls(all_ids, movable)

    def assignments(self, plan):
        pairs = plan_pairs(plan)
        result = dict(pairs)
        if len(pairs) != len(result) or set(result) != set(self.operation_ids):
            raise ValueError("Distance requires complete unique Plans for the same Operations")
        return result

    def raw(self, a, b):
        a, b = self.assignments(a), self.assignments(b)
        return sum(a[op] != b[op] for op in self.movable_operation_ids)

    def normalized(self, a, b):
        if not self.movable_operation_ids:
            raise ValueError("Movable Operation set is empty; candidate generation cannot continue")
        return self.raw(a, b)/len(self.movable_operation_ids)


def build_distance_buckets(maximum, *, near_max_ratio, medium_max_ratio):
    if type(maximum) is not int or maximum < 0:
        raise ValueError("Maximum distance must be a nonnegative integer")
    if not 0 < near_max_ratio < medium_max_ratio < 1:
        raise ValueError("Bucket ratios must satisfy 0 < near < medium < 1")
    # Decimal(str(...)) implements floor of the configured decimal ratios,
    # without accidentally rounding a boundary such as 0.55 * 100 downward.
    near = int((Decimal(str(near_max_ratio))*maximum).to_integral_value(rounding=ROUND_FLOOR))
    medium = int((Decimal(str(medium_max_ratio))*maximum).to_integral_value(rounding=ROUND_FLOOR))
    return {"NEAR": {"lower": 1, "upper": near, "empty": near < 1},
            "MEDIUM": {"lower": near+1, "upper": medium, "empty": medium < near+1},
            "FAR": {"lower": medium+1, "upper": maximum, "empty": maximum < medium+1}}


def distance_bucket(raw, buckets):
    matches = [name for name, bounds in buckets.items() if bounds["lower"] <= raw <= bounds["upper"]]
    if len(matches) != 1:
        raise ValueError(f"Distance {raw} must belong to exactly one non-expert bucket")
    return matches[0]
