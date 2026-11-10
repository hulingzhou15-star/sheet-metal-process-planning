"""Read-only display boundary recovery from saved HTML/SVG, never CAD facts.

Trace exact recorded endpoint components; retain exterior closed contours.
Saved cubic ARC paths are reused rather than guessing unsigned source sweep.
"""
from collections import Counter, defaultdict
from functools import lru_cache
from html.parser import HTMLParser
import hashlib
import json
import math
from pathlib import Path
import re


class _SourceParser(HTMLParser):
    def __init__(self):
        super().__init__();self.rows=[];self.metadata=[];self.in_metadata=False
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=="metadata":self.in_metadata=True
        if attrs.get("data-role")=="segment" and attrs.get("data-segment-type") in ("LINE","ARC"):
            if tag!="path":raise ValueError("Recorded LINE/ARC must be a saved SVG path")
            self.rows.append(attrs)
    def handle_endtag(self,tag):
        if tag=="metadata":self.in_metadata=False
    def handle_data(self,text):
        if self.in_metadata:self.metadata.append(text)


def _point(row,which):
    return tuple(float(row[f"data-local-{which}-{axis}"]) for axis in ("x","y"))


def _inverse_screen(point,display):
    a,b,c,d=display["local_to_view_linear"];det=a*d-b*c
    if not math.isfinite(det) or det==0:raise ValueError("Source display matrix is singular")
    scale=display["pixels_per_mm"];margin=display["margin_px"]
    if not math.isfinite(scale) or scale<=0:raise ValueError("Invalid saved source scale")
    u=(point[0]-margin)/scale+display["view_bounds"][0]
    v=display["view_bounds"][3]-(point[1]-margin)/scale
    x,y=(d*u-b*v)/det,(-c*u+a*v)/det
    if not all(math.isfinite(z) for z in (x,y)):raise ValueError("Nonfinite saved coordinate")
    return (x,-y)


def saved_path_shapes(path_data, display):
    """Whitelist absolute M/L/C; convert all original cubic controls together."""
    token_pattern=r"[MLC]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?"
    tokens=re.findall(token_pattern,path_data)
    residue=re.sub(token_pattern,"",path_data)
    if residue.strip(" ,\t\r\n"):raise ValueError("Unsupported saved SVG path command")
    i=0;current=None;result=[]
    while i<len(tokens):
        command=tokens[i];i+=1
        count={"M":2,"L":2,"C":6}.get(command)
        if count is None or i+count>len(tokens):raise ValueError("Malformed saved SVG path")
        values=[float(v) for v in tokens[i:i+count]];i+=count
        points=[_inverse_screen(tuple(values[j:j+2]),display) for j in range(0,count,2)]
        if command=="M":
            if current is not None:raise ValueError("Multiple subpaths within one recorded segment")
            current=points[0]
        else:
            if current is None:raise ValueError("Saved path has no initial M")
            row={"kind":"line" if command=="L" else "cubic","start":current,"end":points[-1]}
            if command=="C":row.update(control1=points[0],control2=points[1])
            result.append(row);current=points[-1]
    if not result:raise ValueError("Saved path contains no drawable segment")
    return result


def _reverse_shapes(shapes):
    return [{**shape,"start":shape["end"],"end":shape["start"],
             **({"control1":shape["control2"],"control2":shape["control1"]} if shape["kind"]=="cubic" else {})}
            for shape in reversed(shapes)]


def _mid(a,b):return ((a[0]+b[0])/2,(a[1]+b[1])/2)


def flatten_shape(shape,tolerance):
    """Display containment/raster aid only; exported vector curves are untouched."""
    if shape["kind"]=="line":return [shape["start"],shape["end"]]
    def walk(p0,p1,p2,p3,depth=0):
        chord=math.dist(p0,p3)
        error=math.dist(p0,p1)+math.dist(p1,p2)+math.dist(p2,p3)-chord
        if error<=tolerance or depth>=20:return [p0,p3]
        a,b,c=_mid(p0,p1),_mid(p1,p2),_mid(p2,p3);d,e=_mid(a,b),_mid(b,c);m=_mid(d,e)
        return walk(p0,a,d,m,depth+1)[:-1]+walk(m,e,c,p3,depth+1)
    return walk(shape["start"],shape["control1"],shape["control2"],shape["end"])


def _inside(point,polygon):
    x,y=point;inside=False
    for a,b in zip(polygon,polygon[1:]+polygon[:1]):
        if (a[1]>y)!=(b[1]>y) and x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:inside=not inside
    return inside


def _box(points):
    return (min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points))


@lru_cache(maxsize=4)
def _recover(path_string,digest,roundtrip_tolerance,flatten_tolerance):
    path=Path(path_string);source=path.read_bytes()
    if hashlib.sha256(source).hexdigest()!=digest:raise ValueError("Outline source changed during read")
    parser=_SourceParser();parser.feed(source.decode("utf-8-sig"))
    metadata=json.loads("".join(parser.metadata));display=metadata["display"]
    rows=parser.rows
    if not rows:raise ValueError("No saved boundary paths")
    ids=[r.get("id") for r in rows]
    if None in ids or len(ids)!=len(set(ids)):raise ValueError("Source segment IDs are not unique")
    shapes=[];adj=defaultdict(list);max_error=0
    for i,row in enumerate(rows):
        curve=saved_path_shapes(row["d"],display);start,end=_point(row,"start"),_point(row,"end")
        errors=(math.dist(curve[0]["start"],(start[0],-start[1])),math.dist(curve[-1]["end"],(end[0],-end[1])))
        max_error=max(max_error,*errors)
        if max(errors)>roundtrip_tolerance:raise ValueError("Saved SVG and local endpoint coordinates do not align")
        shapes.append(curve)
        for point in (start,end):adj[point].append(i)
    seen=set();loops=[];incomplete=[]
    for seed in range(len(rows)):
        if seed in seen:continue
        pending=[seed];edges=set();points=set()
        while pending:
            index=pending.pop()
            if index in edges:continue
            edges.add(index);seen.add(index)
            for p in (_point(rows[index],"start"),_point(rows[index],"end")):
                points.add(p);pending.extend(j for j in adj[p] if j not in edges)
        if any(len(adj[p])!=2 for p in points):
            incomplete.append({"source_segment_ids":[rows[j]["id"] for j in sorted(edges)],"reason":"Endpoint component is open or branched; no gap filling was performed"});continue
        point=_point(rows[min(edges)],"start");origin=point;used=set();ordered=[];ordered_ids=[]
        while len(used)<len(edges):
            choices=[j for j in adj[point] if j not in used]
            if not choices:raise ValueError("Closed boundary traversal failed")
            j=min(choices);start,end=_point(rows[j],"start"),_point(rows[j],"end")
            ordered.extend(shapes[j] if point==start else _reverse_shapes(shapes[j]))
            ordered_ids.append(rows[j]["id"]);used.add(j);point=end if point==start else start
        if point!=origin:raise ValueError("Endpoint component did not close")
        polygon=[p for shape in ordered for p in flatten_shape(shape,flatten_tolerance)[:-1]]
        area=abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(polygon,polygon[1:]+polygon[:1]))/2)
        if area==0:
            incomplete.append({"source_segment_ids":ordered_ids,"reason":"Closed component has zero display area"});continue
        loops.append({"shapes":ordered,"polygon":polygon,"bbox":_box(polygon),"area":area,"source_segment_ids":ordered_ids})
    exterior=[];nested=[]
    for i,loop in enumerate(loops):
        box=loop["bbox"];parents=[]
        for j,parent in enumerate(loops):
            b=parent["bbox"]
            if i==j or parent["area"]<=loop["area"] or not(b[0]<=box[0] and b[1]<=box[1] and b[2]>=box[2] and b[3]>=box[3]):continue
            if all(_inside(p,parent["polygon"]) for p in loop["polygon"]):parents.append(j)
        (nested if parents else exterior).append(i)
    if not exterior:raise ValueError("No stable exterior closed contour recovered")
    native_bbox=_box([p for i in exterior for shape in loops[i]["shapes"] for p in [shape["start"],shape["end"],*([shape["control1"],shape["control2"]] if shape["kind"]=="cubic" else [])]])
    public={"loops":[{"shapes":loops[i]["shapes"]} for i in exterior],"native_svg_bbox":native_bbox}
    source_by_id={r["id"]:i for i,r in enumerate(rows)}
    exterior_polygons=[loops[i]["polygon"] for i in exterior]
    interior_open=[];unresolved_exterior=[]
    for component in incomplete:
        points=[p for id in component["source_segment_ids"] for shape in shapes[source_by_id[id]] for p in flatten_shape(shape,flatten_tolerance)]
        if all(any(_inside(p,polygon) for polygon in exterior_polygons) for p in points):
            interior_open.append(component)
        else:unresolved_exterior.append(component)
    audit={"status":"available" if not unresolved_exterior else "partial","source_path":str(path),"source_sha256":digest,
           "method":"Read saved visible M/L/C paths, invert saved display affine, trace exact local-endpoint cycles, retain closed contours not contained in another source cycle",
           "boundary_model":"Multiple source exterior contours; no physical union, gap closure or new board shape",
           "source_display_rotation_degrees":metadata.get("block_rotation_degrees"),"source_line_arc_segments":len(rows),
           "closed_component_count":len(loops),"exterior_contour_count":len(exterior),"excluded_inner_contour_count":len(nested),
           "selected_source_segment_count":sum(len(loops[i]["source_segment_ids"]) for i in exterior),
           "selected_source_segment_ids":[id for i in exterior for id in loops[i]["source_segment_ids"]],
           "excluded_interior_open_component_count":len(interior_open),"excluded_interior_open_components":interior_open,
           "unresolved_components":unresolved_exterior,"max_local_svg_endpoint_error":max_error,
           "roundtrip_tolerance":roundtrip_tolerance,"containment_flatten_tolerance":flatten_tolerance,
           "fallback_used":False,"display_only":True,"native_svg_bbox":native_bbox}
    return public,audit


def load_display_outline(config,project_root,part_id):
    settings=config["outline"]
    if not settings["enabled"] or part_id!=settings["part_id"]:
        return None,{"status":"not_requested","display_only":True,"reason":"Disabled or different part"}
    path=(Path(project_root)/settings["source_path"]).resolve()
    try:
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        return _recover(str(path),digest,settings["roundtrip_tolerance"],settings["containment_flatten_tolerance"])
    except (OSError,ValueError,KeyError) as exc:
        # Missing or ambiguous evidence is explicit; never substitute a convex hull.
        return None,{"status":"unavailable","display_only":True,"source_path":str(path),"reason":str(exc),
                     "fallback_used":True,"fallback":"Operation-only background; original HTML retained for static source review, no invented board boundary"}
