"""Auditable base-plane registration and unconfirmed local bend proposals.

Only circular punch contours and edges of the largest horizontal CAD face are
registration anchors. No influence region, expert assignment or CAD order is
used to decide motion. Registration only establishes the base frame.
"""
import itertools
import math
import numpy as np


def apply_transform(point, matrix, translation):
    return (np.asarray(matrix) @ np.asarray(point) + np.asarray(translation)).tolist()


def register(facts, asset, config):
    cfg = config['registration']
    planes = [f for f in asset['faces'] if f.get('plane')
              and abs(abs(f['plane']['normal'][2])-1) < cfg['axis_tolerance']]
    if not planes:
        return {'status': 'UNRESOLVED', 'reason': 'No horizontal base-plane candidate'}
    base = max(planes, key=lambda f: f['area_mm2'])
    circles = []
    for c in asset['circles']:
        if base['face_id'] not in c['face_ids']:
            continue
        if abs(c['parameter_span_rad']-2*math.pi) > cfg['axis_tolerance']:
            continue
        if not any(np.linalg.norm(np.array(c['center_mm'])-d['center_mm']) < cfg['position_tolerance_mm']
                   and abs(c['radius_mm']-d['radius_mm']) < cfg['radius_tolerance_mm'] for d in circles):
            circles.append(c)
    source = []
    for op in facts['operations']:
        if op['operation_type'] != 'CIRCLE_PUNCH' or len(op['segments']) != 1:
            continue
        s = op['segments'][0]
        if s['segment_type'] == 'CIRCLE':
            source.append({'operation_id': op['operation_id'], 'center': [s['center_x'],s['center_y']], 'radius': s['radius']})
    solutions = []
    for swapped, sx, sy in itertools.product((False, True), (-1,1), (-1,1)):
        matrix = np.array([[0,sx],[sy,0]] if swapped else [[sx,0],[0,sy]])
        translations = {}
        for s in source:
            for c in circles:
                if abs(s['radius']-c['radius_mm']) <= cfg['radius_tolerance_mm']:
                    t = np.array(c['center_mm'][:2])-matrix@np.array(s['center'])
                    translations[tuple(np.round(t, 4))] = t
        for translation in translations.values():
            matched, used = [], set()
            for s in source:
                p = matrix@np.array(s['center'])+translation
                ranked = sorted((float(np.linalg.norm(p-np.array(c['center_mm'][:2]))), i)
                                for i,c in enumerate(circles)
                                if abs(c['radius_mm']-s['radius']) <= cfg['radius_tolerance_mm'] and i not in used)
                if ranked and ranked[0][0] <= cfg['position_tolerance_mm']:
                    error, i = ranked[0]
                    used.add(i)
                    matched.append({'operation_id': s['operation_id'], 'edge_id': circles[i]['edge_id'],
                                    'face_ids': circles[i]['face_ids'], 'source_xy_mm': s['center'],
                                    'target_xyz_mm': circles[i]['center_mm'], 'residual_mm': error})
            solutions.append({'matrix': matrix.tolist(), 'translation_mm': translation.tolist(), 'anchors': matched})
    solutions.sort(key=lambda s: (-len(s['anchors']), sum(a['residual_mm'] for a in s['anchors'])))
    if not solutions:
        return {'status': 'UNRESOLVED', 'reason': 'No same-radius source/target circle pairs', 'base_face_id': base['face_id']}
    # Translation candidates within the inlier tolerance represent one solution.
    distinct = []
    for s in solutions:
        if not any(s['matrix'] == d['matrix'] and np.linalg.norm(np.array(s['translation_mm'])-d['translation_mm'])
                   < cfg['position_tolerance_mm'] for d in distinct):
            distinct.append(s)
    best = distinct[0]
    count = len(best['anchors'])
    spread = max((abs(float(np.linalg.det(np.array([np.array(b['source_xy_mm'])-a['source_xy_mm'],
                                                   np.array(c['source_xy_mm'])-a['source_xy_mm']]))))
                  for a,b,c in itertools.combinations(best['anchors'],3)), default=0)
    runner = len(distinct[1]['anchors']) if len(distinct)>1 else 0
    accepted = count >= cfg['minimum_anchor_count'] and spread > cfg['noncollinear_tolerance_mm2'] and count-runner >= cfg['competing_solution_match_margin']
    # Refit only translation; the unit-preserving orientation is held fixed.
    if best['anchors']:
        matrix = np.array(best['matrix'])
        translation = np.mean([np.array(a['target_xyz_mm'][:2])-matrix@a['source_xy_mm'] for a in best['anchors']], axis=0)
        best['translation_mm'] = translation.tolist()
        for a in best['anchors']:
            a['residual_mm'] = float(np.linalg.norm(matrix@a['source_xy_mm']+translation-a['target_xyz_mm'][:2]))
    return {'status': 'BASE_FRAME_SUPPORTED' if accepted else 'AMBIGUOUS', **best,
            'base_face_id': base['face_id'], 'base_plane_z_mm': base['centroid_mm'][2],
            'determinant': float(np.linalg.det(best['matrix'])),
            'anchor_count': count, 'runner_up_anchor_count': runner, 'noncollinear_area_proxy_mm2': spread,
            'max_residual_mm': max((a['residual_mm'] for a in best['anchors']), default=None),
            'scope': 'Base frame only. Not a mapping of all unfolded material to the formed body.',
            'candidates': [{'matrix': s['matrix'], 'translation_mm': s['translation_mm'], 'anchor_count': len(s['anchors'])} for s in distinct[:8]]}


def bend_proposals(facts, asset, registration, config):
    if registration['status'] != 'BASE_FRAME_SUPPORTED':
        return []
    cfg = config['bend_proposals']
    base = asset['faces'][registration['base_face_id']-1]
    paths = {base['face_id']: [base['face_id']]}
    for _ in range(cfg['maximum_base_adjacency_hops']):
        for face_id, path in list(paths.items()):
            for neighbor in asset['faces'][face_id-1]['neighbor_face_ids']:
                if neighbor not in paths:
                    paths[neighbor] = path+[neighbor]
    rows = []
    for op in facts['operations']:
        if op['operation_type'] != 'BEND':
            continue
        # A multi-line or curved definition needs its own modeling decision.
        if len(op['segments']) != 1 or op['segments'][0]['segment_type'] not in ('LINE','BEND_LINE'):
            rows.append({'operation_id': op['operation_id'], 'status': 'UNSUPPORTED_DEFINITION', 'candidates': []})
            continue
        s = op['segments'][0]
        p, q = [np.array(apply_transform([s[f'{e}_x'],s[f'{e}_y']], registration['matrix'],registration['translation_mm'])) for e in ('start','end')]
        delta = q-p
        length = np.linalg.norm(delta)
        if not length:
            continue
        direction = delta/length
        proposals = []
        for face in asset['faces']:
            cylinder = face.get('cylinder')
            if not cylinder:
                continue
            d = np.array(cylinder['direction'])
            if abs(d[2]) > cfg['axis_parallel_tolerance'] or abs(abs(float(d[:2]@direction))-1)>cfg['axis_parallel_tolerance']:
                continue
            # Only base-adjacent cylinders have an interpretable relation to
            # the registered flat base. Nested candidates require local frames.
            if face['face_id'] not in paths:
                continue
            point = np.array(cylinder['point_mm'][:2])
            offset = abs(float((point-p)[0]*direction[1]-(point-p)[1]*direction[0]))
            if offset > cfg['maximum_axis_offset_mm']:
                continue
            box = face['bounds_mm']
            projections = [np.array([x,y])@direction for x in (box[0],box[3]) for y in (box[1],box[4])]
            low,high = sorted([p@direction,q@direction])
            overlap = max(0,min(high,max(projections))-max(low,min(projections)))/length
            if overlap < cfg['minimum_overlap_ratio']:
                continue
            proposals.append({'face_id': face['face_id'], 'axis_point_mm': cylinder['point_mm'],
                              'axis_direction': cylinder['direction'], 'radius_mm': cylinder['radius_mm'],
                              'axis_offset_mm': offset, 'projected_overlap_ratio': float(overlap),
                              'neighbor_face_ids': face['neighbor_face_ids'],
                              'base_adjacency_path': paths[face['face_id']],
                              'status': 'PROPOSAL_NOT_MOTION', 'moving_side': None, 'angle_deg': None})
        proposals.sort(key=lambda c: (c['axis_offset_mm'], -c['projected_overlap_ratio'], c['face_id']))
        rows.append({'operation_id': op['operation_id'], 'status': 'NEEDS_CONFIRMATION' if proposals else 'LOCAL_FRAME_REQUIRED',
                     'registered_line_xy_mm': [p.tolist(),q.tolist()],
                     'candidates': proposals[:cfg['maximum_candidates_per_operation']]})
    return rows
