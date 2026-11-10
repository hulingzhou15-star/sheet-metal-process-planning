"""Checked OCCT solid to display triangles; shared by current and historical views."""
from information_extract.three_d.local_collision import panel_solids, indexed
from information_extract.three_d.assets import xyz
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopoDS import TopoDS
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRep import BRep_Tool
from OCP.TopLoc import TopLoc_Location


def mesh(shape, cfg):
    mesher = BRepMesh_IncrementalMesh(shape, cfg['mesh_linear_deflection_mm'], False,
                                    cfg['mesh_angular_deflection_rad'], True)
    if not mesher.IsDone():
        raise ValueError('Cannot mesh checked solid')
    triangles = []
    faces = indexed(shape, TopAbs_FACE)
    for i in range(1, faces.Extent()+1):
        face = TopoDS.Face_s(faces.FindKey(i)); loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is None:
            raise ValueError('Missing checked-solid triangulation')
        for j in range(1, tri.NbTriangles()+1):
            ids = list(tri.Triangle(j).Get())
            if face.Orientation() == TopAbs_REVERSED:
                ids.reverse()
            triangles.append([xyz(tri.Node(k).Transformed(loc.Transformation())) for k in ids])
    return {'triangles': triangles}

