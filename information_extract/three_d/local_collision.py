"""Diagnostic solid intersections on the existing local kinematic hypothesis.

No manufacturing feasibility, continuous collision certificate, or H6 override.
All component pairs are checked, INCLUDING adjacent components. Shared boundary
contact is distinguished from positive-volume overlap, never blanket excluded.
"""
import itertools
import math
import numpy as np
from .assets import indexed, bounds  # also loads the isolated OCCT runtime
from .finite_bend import frames, panel_transforms, bend_point
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace, BRepBuilderAPI_Transform
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.BRepTools import BRepTools
from OCP.TopoDS import TopoDS
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.gp import gp_Pnt, gp_Vec, gp_Trsf

NAMES = ('root', 'middle', 'terminal', 'band_O009', 'band_O010')
ADJACENT = {frozenset(p) for p in [('root', 'band_O009'), ('band_O009', 'middle'),
                                 ('middle', 'band_O010'), ('band_O010', 'terminal')]}


def volume(shape):
    prop = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, prop)
    return abs(float(prop.Mass()))


def valid_solid(shape):
    if not BRepCheck_Analyzer(shape).IsValid() or indexed(shape, TopAbs_SOLID).Extent() != 1 or volume(shape) <= 0:
        raise ValueError('Invalid / empty / non-single solid; cannot report no intersection')
    return shape


def transformed(shape, matrix):
    tr = gp_Trsf()
    tr.SetValues(*np.asarray(matrix)[:3].flatten().tolist())
    return BRepBuilderAPI_Transform(shape, tr, True).Shape()


def panel_solids(face_map, regions, asset, thickness, tolerance):
    """Actual CAD outer wires, extruded toward the opposite skin. Holes filled.

This is a panel envelope, not the initial stock or a staged material-removal model.
"""
    solids, evidence = [], []
    for region in regions:
        fid = region['front_face_id']
        other = next(i for i in region['skin_face_ids'] if i != fid)
        front, back = [asset['faces'][i-1]['plane'] for i in (fid, other)]
        n = np.array(front['normal'], dtype=float)
        n /= np.linalg.norm(n)
        offset = float(np.dot(np.array(back['point_mm'])-front['point_mm'], n))
        if abs(abs(offset)-thickness) > tolerance:
            raise ValueError('CAD panel thickness differs from confirmed thickness')
        face = TopoDS.Face_s(face_map.FindKey(fid))
        filled = BRepBuilderAPI_MakeFace(BRepTools.OuterWire_s(face), True)
        if not filled.IsDone():
            raise ValueError('Cannot fill panel inner wires')
        solid = valid_solid(BRepPrimAPI_MakePrism(filled.Face(), gp_Vec(*(offset*n))).Shape())
        solids.append(solid)
        evidence.append({'region_id': region['region_id'], 'front_face_id': fid,
                         'back_face_id': other, 'extrusion_vector_mm': (offset*n).tolist(),
                         'volume_mm3': volume(solid), 'inner_wires_filled': True})
    return solids, evidence


def band_solid(model, zone, index, intervals, tolerance):
    b = model['bends'][index]
    points = [bend_point(**zone, depth=depth, u=float(u))
              for depth, us in [(0, np.linspace(0, 1, intervals+1)),
                                (model['thickness_mm'], np.linspace(1, 0, intervals+1))]
              for u in us]
    unique = []
    for p in points:
        if not unique or np.linalg.norm(p-unique[-1]) > tolerance:
            unique.append(p)
    if np.linalg.norm(unique[0]-unique[-1]) <= tolerance:
        unique.pop()
    poly = BRepBuilderAPI_MakePolygon()
    x0, x1 = b['x_limits_mm']
    for yz in unique:
        poly.Add(gp_Pnt(float(x0), *map(float, yz)))
    poly.Close()
    face = BRepBuilderAPI_MakeFace(poly.Wire(), True)
    if not face.IsDone():
        raise ValueError('Cannot build bend band section')
    return valid_solid(BRepPrimAPI_MakePrism(face.Face(), gp_Vec(float(x1-x0), 0, 0)).Shape())


def components(model, panels, fractions, intervals, tolerance):
    mats = panel_transforms(model, fractions)
    result = [transformed(s, m) for s, m in zip(panels, mats)]
    result.extend(band_solid(model, z, i, intervals, tolerance)
                  for i, z in enumerate(frames(model, fractions)['zones']))
    return dict(zip(NAMES, result))


def pair_check(a, b, contact_tolerance, volume_tolerance, adjacent=False):
    distance = BRepExtrema_DistShapeShape(a, b)
    if not distance.IsDone():
        raise ValueError('Distance computation failed')
    gap = float(distance.Value())
    overlap = 0.0
    if gap <= contact_tolerance:
        common = BRepAlgoAPI_Common(a, b)
        if not common.IsDone():
            raise ValueError('Intersection computation failed')
        overlap = volume(common.Shape())
    status = ('OVERLAP' if overlap > volume_tolerance else
              ('CONNECTED_CONTACT' if adjacent else 'NONADJACENT_CONTACT') if gap <= contact_tolerance
              else 'SEPARATED')
    return {'status': status, 'distance_mm': gap, 'intersection_volume_mm3': overlap,
            'adjacent': adjacent}


def inspect(solids, cfg):
    return [{'components': [a, b], **pair_check(solids[a], solids[b], cfg['contact_tolerance_mm'],
            cfg['intersection_volume_tolerance_mm3'], frozenset((a, b)) in ADJACENT)}
            for a, b in itertools.combinations(solids, 2)]


def trajectory(kind, intervals):
    if not isinstance(intervals, int) or intervals < 1:
        raise ValueError('Positive integer intervals required')
    if kind not in ('child_first', 'parent_first', 'simultaneous'):
        raise ValueError('Unknown trajectory')
    phases = 1 if kind == 'simultaneous' else 2
    result = []
    for phase in range(phases):
        for k in range(intervals+1):
            if phase and k == 0:
                continue
            t = k/intervals
            if kind == 'simultaneous':
                a, b = t, t
            elif kind == 'child_first':
                a, b = (0, t) if phase == 0 else (t, 1)
            else:
                a, b = (t, 0) if phase == 0 else (1, t)
            result.append({'phase': phase+1, 'phase_fraction': t,
                           'fractions': {'O009': a, 'O010': b}})
    return result
