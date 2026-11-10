"""Station metadata and capabilities in the prescribed OperationType order."""
from src.data.schema import OperationType
from .schema import OPERATION_TYPE_ORDER, StationContext, StationEntry


def build_station_context(stations):
    ordered = sorted(stations, key=lambda s: (s.order, s.station_id))
    if not ordered or len({s.station_id for s in ordered}) != len(ordered):
        raise ValueError("Stations must be nonempty and unique")
    if len({s.order for s in ordered}) != len(ordered) or any(type(s.order) is not int or s.order < 1 for s in ordered):
        raise ValueError("Station order must be a unique positive integer")
    if len({s.part_id for s in ordered}) != 1:
        raise ValueError("Stations must belong to one part")
    entries = []
    for s in ordered:
        allowed = frozenset(OperationType(t) for t in s.allowed_operation_types)
        relative = (s.order-1)/(len(ordered)-1) if len(ordered) > 1 else 0.0
        entries.append(StationEntry(s.station_id, s.order, relative, tuple(t for t in OPERATION_TYPE_ORDER if t in allowed),
                                    tuple(int(t in allowed) for t in OPERATION_TYPE_ORDER)))
    return StationContext(tuple(entries))
