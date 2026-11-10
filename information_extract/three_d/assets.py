"""OCCT boundary representation inventory, mesh and actual topological adjacency."""
from collections import Counter
from pathlib import Path
import math
import sys
import numpy as np

MODULE = Path(__file__).resolve().parents[1]

from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_SOLID, TopAbs_FACE, TopAbs_EDGE, TopAbs_REVERSED
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRep import BRep_Tool
from OCP.TopLoc import TopLoc_Location
from .facts import sha256, fingerprint


def xyz(point):
    return [point.X(), point.Y(), point.Z()]


def indexed(shape, kind):
    values = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, kind, values)
    return values


def bounds(shape):
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    return list(box.Get())


def build_asset(path, config):
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone or not reader.TransferRoots():
        raise ValueError('STEP import failed')
    shape = reader.OneShape()
    solids = indexed(shape, TopAbs_SOLID)
    if solids.Extent() != 1 or not BRepCheck_Analyzer(shape, True).IsValid():
        raise ValueError('Expected one valid solid')
    asset_id = sha256(path)
    face_map, edge_map = indexed(shape, TopAbs_FACE), indexed(shape, TopAbs_EDGE)
    mesh = BRepMesh_IncrementalMesh(shape, config['mesh']['linear_deflection_mm'], False,
                                   config['mesh']['angular_deflection_rad'], True)
    if not mesh.IsDone():
        raise ValueError('Meshing failed')
    face_rows, triangles, triangle_faces = [], [], []
    edge_faces = {i: [] for i in range(1, edge_map.Extent()+1)}
    for index in range(1, face_map.Extent()+1):
        face = TopoDS.Face_s(face_map.FindKey(index))
        adaptor = BRepAdaptor_Surface(face, True)
        kind = str(adaptor.GetType()).split('.')[-1].replace('GeomAbs_', '')
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        own_edges = indexed(face, TopAbs_EDGE)
        edges = sorted({edge_map.FindIndex(own_edges.FindKey(i)) for i in range(1, own_edges.Extent()+1)})
        for edge_id in edges:
            edge_faces[edge_id].append(index)
        row = {'face_id': index, 'surface_type': kind, 'area_mm2': props.Mass(),
               'centroid_mm': xyz(props.CentreOfMass()), 'bounds_mm': bounds(face), 'edge_ids': edges}
        if kind == 'Plane':
            plane = adaptor.Plane()
            row['plane'] = {'point_mm': xyz(plane.Location()), 'normal': xyz(plane.Axis().Direction())}
        elif kind == 'Cylinder':
            cylinder = adaptor.Cylinder()
            row['cylinder'] = {'point_mm': xyz(cylinder.Axis().Location()),
                               'direction': xyz(cylinder.Axis().Direction()), 'radius_mm': cylinder.Radius()}
        location = TopLoc_Location()
        poly = BRep_Tool.Triangulation_s(face, location)
        row['triangle_start'] = len(triangles)
        if poly is not None:
            vertices = np.array([xyz(poly.Node(i).Transformed(location.Transformation()))
                                 for i in range(1, poly.NbNodes()+1)])
            ids = np.array([poly.Triangle(i).Get() for i in range(1, poly.NbTriangles()+1)], dtype=int)-1
            if face.Orientation() == TopAbs_REVERSED:
                ids = ids[:, [0,2,1]]
            tris = vertices[ids]
            triangles.extend(tris.tolist())
            triangle_faces.extend([index]*len(tris))
        row['triangle_count'] = len(triangles)-row['triangle_start']
        face_rows.append(row)
    edges, circles = [], []
    for i in range(1, edge_map.Extent()+1):
        curve = BRepAdaptor_Curve(TopoDS.Edge_s(edge_map.FindKey(i)))
        kind = str(curve.GetType()).split('.')[-1].replace('GeomAbs_', '')
        lo, hi = curve.FirstParameter(), curve.LastParameter()
        if not math.isfinite(lo+hi):
            raise ValueError('Unbounded edge in a solid')
        count = 1 if kind == 'Line' else config['edge_sample_count']
        points = [xyz(curve.Value(lo+(hi-lo)*j/count)) for j in range(count+1)]
        row = {'edge_id': i, 'curve_type': kind, 'face_ids': sorted(set(edge_faces[i])), 'points': points}
        if kind == 'Circle':
            circle = curve.Circle()
            row['circle'] = {'center_mm': xyz(circle.Location()), 'normal': xyz(circle.Axis().Direction()),
                             'radius_mm': circle.Radius(), 'parameter_span_rad': hi-lo}
            circles.append({'edge_id': i, 'face_ids': row['face_ids'], **row['circle']})
        edges.append(row)
    for row in face_rows:
        row['neighbor_face_ids'] = sorted({f for edge in row['edge_ids'] for f in edge_faces[edge]}-{row['face_id']})
    topology_fingerprint = fingerprint([{k:v for k,v in face.items() if not k.startswith('triangle_')}
                                        for face in face_rows])
    return {'asset_id': asset_id, 'topology_fingerprint':topology_fingerprint,
            'unit': config['unit'], 'solid_count': 1, 'brep_valid': True,
            'bounds_mm': bounds(shape), 'face_count': len(face_rows), 'edge_count': len(edges),
            'surface_counts': dict(Counter(f['surface_type'] for f in face_rows)),
            'faces': face_rows, 'edges': edges, 'circles': circles,
            'mesh': {'positions': np.round(np.asarray(triangles).reshape(-1), 6).tolist(),
                     'triangle_face_ids': triangle_faces},
            'face_id_scope': 'asset_id + topology_fingerprint + import index; not stable across re-export/import changes',
            'mesh_config': config['mesh']}
