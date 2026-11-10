"""Confirmed child-before-parent manufacturing facts, separate from CAD edges."""
from dataclasses import dataclass, replace
from functools import cached_property
from graphlib import CycleError, TopologicalSorter
from types import MappingProxyType

from ..context import (ConstraintContext, ContextValidationError, HardPrecedenceRelation,
                       MetadataIssue, load_constraint_context)

RULE_VERSION = "m3.v0.6"


@dataclass(frozen=True)
class RuleContext(ConstraintContext):
    def __post_init__(self):
        super().__post_init__()
        operations = self.operations
        issues = []
        for op in operations.values():
            parent = op.cad_parent_operation_id
            if parent is not None and (parent not in operations or parent == op.operation_id):
                issues.append(MetadataIssue("CAD_PARENT_REFERENCE_INVALID", "CAD parent must exist and differ from child",
                                            {"child": op.operation_id, "parent": parent}, op.source))
        if issues:
            raise ContextValidationError(issues)
        predecessors = {op: set() for op in operations}
        for r in self.precedence_relations:
            predecessors[r.operation_id].add(r.predecessor_id)
        try:
            tuple(TopologicalSorter(predecessors).static_order())
        except CycleError as exc:
            raise ContextValidationError((MetadataIssue("HARD_PRECEDENCE_CYCLE", "Combined H3/H4/H6/H7 precedence is cyclic",
                                                        {"cycle": exc.args[1]}),)) from exc

    @classmethod
    def from_context(cls, context):
        return cls(context.part_data, context.fixed_assignments, context.bend_influences, context.metadata_sha256)

    @property
    def feature_tree_relations(self):
        rows = []
        for op in self.part_data.operations:
            if op.cad_parent_operation_id is not None:
                source = (replace(op.source, field="cad_parent_operation_id") if op.source
                          else None)
                rows.append(HardPrecedenceRelation(op.operation_id, op.cad_parent_operation_id, "H7",
                                                   "FEATURE_TREE_PRECEDENCE", op.operation_id, source))
        return tuple(sorted(rows, key=lambda r: (r.predecessor_id, r.operation_id)))

    @property
    def precedence_relations(self):
        return (*super().precedence_relations, *self.feature_tree_relations)

    @cached_property
    def precedence_successors(self):
        adjacent = {op: set() for op in self.operations}
        for r in self.precedence_relations:
            adjacent[r.predecessor_id].add(r.operation_id)
        return MappingProxyType({op: frozenset(next_ids) for op, next_ids in adjacent.items()})

    @cached_property
    def precedence_reachability(self):
        result = {}
        for start in self.operations:
            seen, todo = set(), list(self.precedence_successors[start])
            while todo:
                current = todo.pop()
                if current not in seen:
                    seen.add(current)
                    todo.extend(self.precedence_successors[current]-seen)
            result[start] = frozenset(seen)
        return MappingProxyType(result)

    def precedence_path(self, before, after):
        todo = [(before, [before])]
        seen = {before}
        for current, path in todo:
            if current == after:
                return path
            for successor in sorted(self.precedence_successors[current]-seen):
                seen.add(successor)
                todo.append((successor, [*path, successor]))
        return []


def load_rule_context(path):
    return RuleContext.from_context(load_constraint_context(path))
