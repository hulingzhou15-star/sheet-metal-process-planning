"""Angle-driven sweep of the STEP wall mesh along its registered bend guides."""
import numpy as np
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.TopAbs import TopAbs_IN, TopAbs_OUT
from OCP.gp import gp_Pnt

from .p002_stage_motion import frame as stage_frame
from .p002_stage_evidence import fraction
from .state import rotation_about_axis

CURVE_CELLS = {'O069': 'C005', 'O088': 'C004', 'O096': 'C003', 'O104': 'C002'}


def compile_guides(asset, sides, base):
    classifier = BRepClass3d_SolidClassifier(base)
    result = {}
    for row in sides['rows']:
        guides = []
        for match in row['matches']:
            edge = asset['edges'][match['step_edge_ids'][0] - 1]
            if not {466, 468}.intersection(edge['face_ids']):
                raise ValueError('Expected STEP base boundary')
            xy = np.asarray(edge['points'])[:, :2]
            a, b = xy[0], xy[-1]
            circle = edge.get('circle')
            if circle:
                center = np.array(circle['center_mm'][:2])
                radial = (a - center) / circle['radius_mm']
                mid = xy[len(xy) // 2]
                delta = xy[len(xy) // 2 + 1] - xy[len(xy) // 2 - 1]
                ccw = np.sign(radial[0] * (mid-center)[1] - radial[1] * (mid-center)[0])
                guide = dict(center=center.tolist(), radius=circle['radius_mm'],
                             span=ccw * circle['parameter_span_rad'])
            else:
                mid, delta = (a + b) / 2, b - a
                guide = {}
            left = np.array([-delta[1], delta[0]]) / np.linalg.norm(delta)
            def where(direction):
                p = mid + direction * .9 * left
                classifier.Perform(gp_Pnt(float(p[0]), float(p[1]), -.4), 1e-7)
                return classifier.State()

            states = where(1), where(-1)
            if set(states) != {TopAbs_IN, TopAbs_OUT}:
                raise ValueError(f'Ambiguous material side on edge {edge["edge_id"]}')
            guide.update(a=a.tolist(), b=b.tolist(), left_sign=1 if states[0] == TopAbs_OUT else -1,
                         step_edge_id=edge['edge_id'], source_segment_id=match['source_segment_id'])
            guides.append(guide)
        oid = row['operation_id']
        result[CURVE_CELLS[oid]] = dict(operation_id=oid, guides=guides, z_sign=row['base_protrusion_sign'])
    if set(result) != set(CURVE_CELLS.values()):
        raise ValueError('Incomplete curved wall guides')
    return result


def bind_vertices(points, guides):
    """Find the closest finite STEP line or arc and its outward normal."""
    xy = np.asarray(points)[:, :2]
    best = np.full(len(xy), np.inf)
    origins = np.empty_like(xy)
    normals = np.empty_like(xy)
    for g in guides:
        a, b = np.array(g['a']), np.array(g['b'])
        side = g['left_sign']
        if 'center' in g:
            center = np.array(g['center'])
            v = a - center
            w = xy - center
            sense = np.sign(g['span'])
            angle = np.mod(sense * np.arctan2(v[0]*w[:,1] - v[1]*w[:,0], w@v), 2*np.pi)
            span = abs(g['span'])
            end = np.linalg.norm(xy-b, axis=1) < np.linalg.norm(xy-a, axis=1)
            angle = sense * np.where(angle <= span, angle, np.where(end, span, 0))
            c, s = np.cos(angle), np.sin(angle)
            radial = np.column_stack((v[0]*c-v[1]*s, v[0]*s+v[1]*c)) / np.linalg.norm(v)
            closest = center + g['radius'] * radial
            outward = -side * sense * radial
        else:
            v = b-a
            closest = a + np.clip((xy-a)@v/(v@v), 0, 1)[:,None]*v
            outward = np.broadcast_to(side * np.array([-v[1], v[0]]) / np.linalg.norm(v), xy.shape)
        d = np.linalg.norm(xy-closest, axis=1)
        better = d < best
        best[better] = d[better]
        origins[better] = closest[better]
        normals[better] = outward[better]
    return origins, normals, best


def sweep(points, binding, progress, z_sign, flat_setback=.4):
    """Keep tangential coordinates, turn the normal section, ease the setback to zero."""
    p = np.asarray(points)
    if progress == 1:
        return p.copy()
    origin, normal, _ = binding
    angle = (1-progress)*np.pi/2
    c, s = np.cos(angle), np.sin(angle)
    axis = origin + .4*normal
    normal_coordinate = np.sum((p[:,:2]-axis)*normal, axis=1)
    height = z_sign*(p[:,2]+.4)
    offset = (c-1)*normal_coordinate + s*height - (1-progress)*flat_setback
    return np.column_stack((p[:,:2] + offset[:,None]*normal,
                            -.4 + z_sign*(c*height-s*normal_coordinate)))


def frame(model, plan, station_id, phase, tau):
    state = stage_frame(model, plan, station_id, phase, tau)
    orders = {s['station_id']: s['order'] for s in model['stations']}
    assignments = plan['assignments']
    curves = {}
    for cell, spec in model['curve_guides'].items():
        progress = fraction(orders, assignments, spec['operation_id'], station_id, tau)
        if progress < 1:
            curves[cell] = progress
            state['unknown_cells'].pop(cell)
    progress = fraction(orders, assignments, 'O088', station_id, tau)
    if 0 < progress < 1:
        cfg = model['o094_stage_reference']
        carrier = cfg['carrier_joint']
        pose = rotation_about_axis(carrier['hinge_point_mm'], carrier['axis_direction'], (progress-1)*90)
        pose[:3,3] += (1-progress)*np.asarray(cfg['carrier_flat_shift_mm'])
        state['transforms']['C007'] = pose.tolist()
        state['unknown_cells'].pop('C007')
    state['curve_fractions'] = curves
    if state['unknown_cells']:
        raise ValueError('Unexpected unsupported pose')
    return state
