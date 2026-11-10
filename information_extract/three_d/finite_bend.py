"""Local finite-width geometric bend reconstruction, NOT a forming solver.

Neutral reference length is taken from paired DXF boundary lines. The equivalent
offset is derived from final CAD geometry; it is not an identified material K.
Constant thickness interpolation does not enforce local volume conservation.
"""
import math
import numpy as np


def bend_point(start, phi, angle, length, h, depth=0.0, u=1.0):
    """YZ position at fraction u of bend, depth measured below the top skin."""
    if length <= 0 or not 0 <= u <= 1:
        raise ValueError('Positive developed length and u in [0,1] required')
    d = np.array([math.cos(phi), math.sin(phi)])
    n = np.array([-math.sin(phi), math.cos(phi)])
    alpha = angle * u
    # Stable sinc expressions; continuous at zero angle, no epsilon angle jump.
    dy = length * u * float(np.sinc(alpha / math.pi))
    dz = length * u * (alpha / 2) * float(np.sinc(alpha / (2 * math.pi))) ** 2
    new_n = np.array([-math.sin(phi + alpha), math.cos(phi + alpha)])
    return np.asarray(start) - h * n + dy * d + dz * n + (h - depth) * new_n


def frames(model, fractions):
    if set(fractions) != {b['operation_id'] for b in model['bends']}:
        raise ValueError('All local bend fractions required')
    if any(not math.isfinite(v) or not 0 <= v <= 1 for v in fractions.values()):
        raise ValueError('Fractions must be finite and in [0,1]')
    phi = 0.0
    p = np.array(model['root_start_top_yz_mm'], dtype=float)
    result, zones = [], []
    for i, b in enumerate(model['bends']):
        angle = math.radians(b['target_angle_deg']) * fractions[b['operation_id']]
        h = b['neutral_offset_from_top_mm']
        zones.append({'start': p.tolist(), 'phi': phi, 'angle': angle, 'length': b['developed_width_mm'], 'h': h})
        p = bend_point(p, phi, angle, b['developed_width_mm'], h)
        phi += angle
        result.append({'start_top_yz_mm': p.tolist(), 'phi': phi})
        if i == 0:
            p += model['middle_straight_length_mm'] * np.array([math.cos(phi), math.sin(phi)])
    return {'panels': result, 'zones': zones}


def panel_transforms(model, fractions):
    current = frames(model, fractions)['panels']
    final = frames(model, {b['operation_id']: 1.0 for b in model['bends']})['panels']
    result = [np.eye(4)]
    for now, end in zip(current, final):
        a = now['phi'] - end['phi']; c, s = math.cos(a), math.sin(a)
        m = np.eye(4); m[:3, :3] = [[1, 0, 0], [0, c, -s], [0, s, c]]
        m[:3, 3] = np.array([0, *now['start_top_yz_mm']]) - m[:3, :3] @ [0, *end['start_top_yz_mm']]
        result.append(m)
    return result


def band_area(model, index, fraction):
    """YZ area of the interpolated band, exposing volume inconsistency."""
    b = model['bends'][index]; t = model['thickness_mm']
    a = math.radians(b['target_angle_deg']) * fraction
    return t * (b['developed_width_mm'] + a * (t / 2 - b['neutral_offset_from_top_mm']))
