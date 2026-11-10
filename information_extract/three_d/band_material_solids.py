"""Material domains, exact planar cuts, and checked faceted bend solids."""
import numpy as np
from itertools import combinations
from .local_collision import indexed,volume,transformed
from .assets import bounds
from .band_material import map_points,state_transforms
from OCP.TopoDS import TopoDS,TopoDS_Compound
from OCP.TopAbs import TopAbs_SOLID,TopAbs_FACE,TopAbs_SHELL
from OCP.BRep import BRep_Builder
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon,BRepBuilderAPI_MakeFace,BRepBuilderAPI_Sewing,BRepBuilderAPI_MakeSolid
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut,BRepAlgoAPI_Common
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.gp import gp_Pnt,gp_Vec


def compound(shapes):
    shape=TopoDS_Compound();builder=BRep_Builder();builder.MakeCompound(shape)
    for s in shapes:builder.Add(shape,s)
    return shape


def checked(shape,allow_empty=False):
    if not BRepCheck_Analyzer(shape).IsValid():raise ValueError('Invalid material topology')
    if not allow_empty and (indexed(shape,TopAbs_SOLID).Extent()<1 or volume(shape)<=1e-9):raise ValueError('Empty material')
    return shape


def subtract(base,tools):
    if not tools:return base
    result=base
    # Tools may overlap (a descendant footprint can contain its own descendants).
    # An overlapping compound is not a Boolean union; apply cuts individually.
    for tool in tools:
        cut=BRepAlgoAPI_Cut(result,tool)
        if not cut.IsDone():raise ValueError('Material cut kernel failure')
        result=checked(cut.Shape(),True)
    return result


def common_volume(a,b):
    c=BRepAlgoAPI_Common(a,b)
    if not c.IsDone():raise ValueError('Material common kernel failure')
    return volume(c.Shape())


def prism(points,thickness):
    polygon=BRepBuilderAPI_MakePolygon()
    for p in points:polygon.Add(gp_Pnt(*map(float,p)))
    polygon.Close();face=BRepBuilderAPI_MakeFace(polygon.Wire(),True)
    if not face.IsDone():raise ValueError('Cannot make material face')
    return checked(BRepPrimAPI_MakePrism(face.Face(),gp_Vec(0,0,-thickness)).Shape())


def boxes_intersect(a,b,tol=1e-6):
    return all(a[i]<=b[i+3]+tol and b[i]<=a[i+3]+tol for i in range(3))


def domain_overlaps(domains,tol=1e-6):
    boxes={k:bounds(v) for k,v in domains.items()};rows=[]
    for a,b in combinations(domains,2):
        if boxes_intersect(boxes[a],boxes[b]):
            v=common_volume(domains[a],domains[b])
            if v>tol:rows.append({'components':[a,b],'volume_mm3':v})
    return rows


def split_triangle_grid(triangle,bend,intervals):
    """Refine flat surface triangles along strip width, preserving cut boundaries."""
    start=np.array(bend['start_flat_mm']);d=np.array(bend['forward_flat']);length=bend['width_mm']
    tri=[np.array(p,dtype=float) for p in triangle]
    def value(p):return float((p-start)@d)
    lo=min(intervals-1,max(0,int(np.floor(min(value(p) for p in tri)/length*intervals))))
    hi=min(intervals-1,int(np.floor(max(value(p) for p in tri)/length*intervals)))
    result=[]
    for k in range(lo,hi+1):
        poly=tri
        for bound,sign in [(k*length/intervals,1),((k+1)*length/intervals,-1)]:
            output=[]
            for i,p in enumerate(poly):
                q=poly[(i+1)%len(poly)];a=sign*(value(p)-bound);b=sign*(value(q)-bound)
                if a>=-1e-10:output.append(p)
                if (a>1e-10 and b<-1e-10) or (a<-1e-10 and b>1e-10):output.append(p+(q-p)*a/(a-b))
            poly=output
            if len(poly)<3:break
        if len(poly)<3:continue
        for j in range(1,len(poly)-1):
            pts=[poly[0],poly[j],poly[j+1]]
            if np.linalg.norm(np.cross(pts[1]-pts[0],pts[2]-pts[0]))>1e-12:result.append([p.tolist() for p in pts])
    return result


def triangle_solid(triangles,tolerance=1e-6):
    """Collision B-rep from the very same mapped boundary triangles as display."""
    sewing=BRepBuilderAPI_Sewing(tolerance)
    count=0
    for t in triangles:
        p=np.asarray(t)
        longest=max(float(np.linalg.norm(p[i]-p[(i+1)%3])) for i in range(3))
        area2=float(np.linalg.norm(np.cross(p[1]-p[0],p[2]-p[0])))
        # At a CAD sharp inner edge, an entire inner skin collapses to a line.
        # Floating-point noise on a long strip must not create micron-thin faces.
        if longest<=tolerance or area2/longest<tolerance:continue
        polygon=BRepBuilderAPI_MakePolygon()
        for v in p:polygon.Add(gp_Pnt(*map(float,v)))
        polygon.Close();made=BRepBuilderAPI_MakeFace(polygon.Wire(),True)
        if not made.IsDone():raise ValueError('Cannot build triangle face')
        sewing.Add(made.Face());count+=1
    if not count:raise ValueError('Empty triangle material')
    sewing.Perform();shells=indexed(sewing.SewedShape(),TopAbs_SHELL);bodies=[]
    if not shells.Extent():raise ValueError('Mapped mesh did not form a closed shell')
    for i in range(1,shells.Extent()+1):
        shell=TopoDS.Shell_s(shells.FindKey(i))
        if not shell.Closed():raise ValueError('Mapped boundary shell is open')
        made=BRepBuilderAPI_MakeSolid(shell)
        if not made.IsDone():raise ValueError('Cannot close mapped solid')
        bodies.append(checked(made.Solid()))
    return checked(compound(bodies))


def component_at_state(model,component,flat_shape,fractions,flat_triangles=None):
    ts=state_transforms(model,fractions)
    if component['kind']=='panel':return transformed(flat_shape,ts[component['region_id']])
    if flat_triangles is None:raise ValueError('Refined band triangles required for checked collision geometry')
    points=[p for tri in flat_triangles for p in tri]
    mapped=map_points(model,component,points,fractions,ts)
    return triangle_solid([mapped[i:i+3] for i in range(0,len(mapped),3)])
