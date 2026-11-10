"""Read-only provenance audit and kinematic diagnostics. H7 is never relaxed."""
from collections import defaultdict, deque
import numpy as np
from .local_motion import poses


def find_path(edges, start, end):
    graph = defaultdict(set)
    for a, b in edges:
        graph[a].add(b)
    todo = deque([(start, [start])]); seen = {start}
    while todo:
        node, path = todo.popleft()
        for child in sorted(graph[node]):
            if child == end:
                return path+[child]
            if child not in seen:
                todo.append((child, path+[child])); seen.add(child)
    return None


def relation_coverage(relations):
    h7 = {(r.predecessor_id, r.operation_id) for r in relations if r.constraint_type == 'H7'}
    other = {(r.predecessor_id, r.operation_id) for r in relations if r.constraint_type != 'H6'}
    result = []
    for r in relations:
        if r.constraint_type != 'H6':
            continue
        a, b = r.predecessor_id, r.operation_id
        path = find_path(h7, a, b)
        result.append({'child_operation_id': a, 'parent_operation_id': b,
                       'sheet5_region_id': r.origin_ref,
                       'coverage': 'H7_DIRECT' if (a,b) in h7 else 'H7_TRANSITIVE' if path else 'NOT_COVERED_BY_H7',
                       'h7_path': path, 'path_without_h6': find_path(other, a, b)})
    return result


def descendants(skeleton, root):
    children = defaultdict(list)
    for j in skeleton['joints']:
        children[j['parent_region_id']].append(j['child_region_id'])
    seen = {root}; todo = [root]
    for r in todo:
        for c in children[r]:
            if c not in seen:
                seen.add(c); todo.append(c)
    return sorted(seen)


def fractions_for_stage(skeleton, assignment, orders, station_id, tau):
    if not 0 <= tau <= 1:
        raise ValueError('Stage fraction out of bounds')
    current = orders[station_id]
    result = {}
    for j in skeleton['joints']:
        op = skeleton['joint_to_operation'][j['joint_id']]
        rank = orders[assignment[op]]
        result[j['joint_id']] = 1.0 if rank < current else tau if rank == current else 0.0
    return result


def representative_sources(operations):
    """Wire AABB center, explicitly a position indicator, NOT minimum clearance."""
    mapped, missing = [], []
    for op in operations:
        points = [p for path in op['formed_paths'] for p in path]
        if op['region_id'] is None or not points:
            missing.append(op['operation_id'])
        else:
            xyz = np.asarray(points, dtype=float)
            mapped.append({'operation_id': op['operation_id'], 'region_id': op['region_id'],
                           'point': (xyz.min(axis=0)+xyz.max(axis=0))/2,
                           'mapping_status': op['mapping_status']})
    return mapped, missing


def locations(skeleton, fractions, sources):
    transforms = poses(skeleton, fractions)
    points = np.asarray([(transforms[o['region_id']] @ [*o['point'], 1])[:3] for o in sources])
    return transforms, points


def pair_changes(sources, before, after, active_ids, moved_ids, limit):
    """Largest position-distance changes across regions, display cap only.

Not nearest-neighbour selection, clearance, penalty, or risk classification.
Only already-executed actions (including this station) enter these examples.
"""
    rows = []
    for i, a in enumerate(sources):
        for k in range(i+1, len(sources)):
            b = sources[k]
            if a['region_id'] == b['region_id']:
                continue
            ids = {a['operation_id'], b['operation_id']}
            if not ids <= active_ids or not ids & moved_ids:
                continue
            x, y = float(np.linalg.norm(before[i]-before[k])), float(np.linalg.norm(after[i]-after[k]))
            rows.append({'operation_ids': [a['operation_id'], b['operation_id']],
                         'before_indicator_distance_mm': x, 'after_indicator_distance_mm': y,
                         'change_mm': y-x})
    return sorted(rows, key=lambda r: (-abs(r['change_mm']), r['operation_ids']))[:limit]
