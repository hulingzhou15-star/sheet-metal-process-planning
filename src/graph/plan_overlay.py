"""A complete arbitrary Plan, checked for structure rather than feasibility."""
from collections.abc import Mapping
from src.data.schema import ExpertReferencePlan
from .schema import NodePlanFeatures, PlanOverlay


def build_plan_overlay(static, plan):
    if isinstance(plan, ExpertReferencePlan):
        pairs = [(a.operation_id, a.station_id) for a in plan.assignments]
    elif isinstance(plan, Mapping):
        pairs = list(plan.items())
    else:
        pairs = list(plan)
    ids = [op for op, station in pairs]
    if len(ids) != len(set(ids)) or set(ids) != set(static.operation_ids):
        raise ValueError("Plan must cover exactly these Operations once each")
    assignments = dict(pairs)
    features = []
    for op_id in static.operation_ids:
        s = static.station_context.get(assignments[op_id])
        features.append(NodePlanFeatures(s.station_id, s.order, s.relative_order, s.capability))
    return PlanOverlay(static.operation_ids, static.station_context, tuple(features))
