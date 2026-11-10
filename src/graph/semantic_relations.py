"""Only confirmed facts: explicit precedence, process groups and CAD ancestry."""
from dataclasses import replace
from itertools import combinations

from src.data.schema import OperationType
from .schema import RelationSource, RelationType, SemanticEdge


def add_semantic_relations(data, static):
    index = static.node_index
    operations = {op.operation_id: op for op in data.operations}
    if set(operations) != set(index):
        raise ValueError("Semantic input must match the static graph")
    edges = []

    def pair(a, b, forward, backward, source, origin, location=None):
        if a not in index or b not in index or a == b:
            raise ValueError(f"Invalid semantic relation: {a} -> {b}")
        edges.append(SemanticEdge(index[a], index[b], forward, source, origin, False, location))
        edges.append(SemanticEdge(index[b], index[a], backward, source, origin, True, location))

    for relation in data.predecessors:
        pair(relation.predecessor_id, relation.operation_id, RelationType.PRECEDES, RelationType.SUCCEEDS,
             RelationSource.EXPLICIT_PREDECESSOR, relation.operation_id, relation.source)
    for relation in data.cad_parents:
        pair(relation.parent_id, relation.operation_id, RelationType.CAD_PARENT, RelationType.CAD_CHILD,
             RelationSource.CAD_PARENT_REFERENCE, relation.operation_id, relation.source)
    for group in sorted(data.process_groups, key=lambda g: g.process_group_id):
        members = sorted(group.operation_ids)
        if len(members) != len(set(members)) or any(op not in index for op in members):
            raise ValueError("Invalid process group membership")
        if any(operations[op].process_group_id != group.process_group_id for op in members):
            raise ValueError("ProcessGroup and Operation membership must agree")
        for a, b in combinations(members, 2):
            # Symmetric membership has no privileged business direction.
            for source_id, target_id in ((a, b), (b, a)):
                edges.append(SemanticEdge(index[source_id], index[target_id], RelationType.SAME_PROCESS_GROUP,
                                          RelationSource.PROCESS_GROUP_MEMBERSHIP, group.process_group_id))
        pre = [op for op in members if operations[op].operation_type == OperationType.PRE_PUNCH]
        flange = [op for op in members if operations[op].operation_type == OperationType.FLANGING]
        for a in pre:
            for b in flange:
                pair(a, b, RelationType.PRECEDES, RelationType.SUCCEEDS,
                     RelationSource.PROCESS_GROUP_PRECEDENCE, group.process_group_id)
    edges.sort(key=lambda e: (e.source_index, e.target_index, e.relation_type.value,
                              e.relation_source.value, e.origin_ref, e.reverse_message))
    return replace(static, semantic_edges=tuple(edges))
