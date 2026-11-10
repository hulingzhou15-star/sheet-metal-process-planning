"""Read-only audit of user-exported CAD; all derived assets stay in this module."""
from pathlib import Path
import sys

MODULE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE / '.runtime/cad_inspect_deps'))

import hashlib
import json
import math
import re
from collections import Counter
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX, TopAbs_REVERSED
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRep import BRep_Tool
from OCP.TopLoc import TopLoc_Location

SOURCE = Path('C:/Users/hulin/Desktop/图纸文件夹/P001')
OUT = MODULE / 'outputs/cad_export_audit_20261102/P001'
OUT.mkdir(parents=True, exist_ok=True)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def indexed(shape, kind):
    result = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, kind, result)
    return [result.FindKey(i) for i in range(1, result.Extent()+1)]


def bounds(shape):
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    values = list(box.Get())
    return {'min_mm': values[:3], 'max_mm': values[3:],
            'size_mm': [values[i+3]-values[i] for i in range(3)]}


def prop(shape, volume=False):
    properties = GProp_GProps()
    if volume:
        BRepGProp.VolumeProperties_s(shape, properties)
    else:
        BRepGProp.SurfaceProperties_s(shape, properties)
    c = properties.CentreOfMass()
    return properties.Mass(), [c.X(), c.Y(), c.Z()]


def mesh(shape):
    builder = BRepMesh_IncrementalMesh(shape, 0.12, False, 0.25, True)
    if not builder.IsDone():
        raise RuntimeError('Meshing did not complete')
    chunks = []
    for raw in indexed(shape, TopAbs_FACE):
        face = TopoDS.Face_s(raw)
        location = TopLoc_Location()
        poly = BRep_Tool.Triangulation_s(face, location)
        if poly is None:
            continue
        transform = location.Transformation()
        vertices = []
        for i in range(1, poly.NbNodes()+1):
            p = poly.Node(i).Transformed(transform)
            vertices.append((p.X(), p.Y(), p.Z()))
        vertices = np.asarray(vertices)
        triangles = np.asarray([poly.Triangle(i).Get() for i in range(1, poly.NbTriangles()+1)], dtype=int)-1
        if face.Orientation() == TopAbs_REVERSED:
            triangles = triangles[:, [0, 2, 1]]
        chunks.append(vertices[triangles])
    return np.concatenate(chunks)


def font(size):
    for name in ['C:/Windows/Fonts/segoeui.ttf', 'C:/Windows/Fonts/arial.ttf']:
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def render_panel(draw, triangles, rect, eye, title, color):
    x0, y0, x1, y1 = rect
    draw.rounded_rectangle(rect, 12, fill='#ffffff', outline='#d9e1e8', width=2)
    draw.text((x0+20, y0+15), title, font=font(22), fill='#25374b')
    eye = np.asarray(eye, dtype=float)
    eye /= np.linalg.norm(eye)
    up = np.array([0., 0., 1.]) if abs(eye[2]) < 0.98 else np.array([0., 1., 0.])
    right = np.cross(up, eye)
    right /= np.linalg.norm(right)
    up = np.cross(eye, right)
    basis = np.array([right, up, eye]).T
    projected = triangles @ basis
    all_xy = projected[:, :, :2].reshape(-1, 2)
    low, high = all_xy.min(axis=0), all_xy.max(axis=0)
    width, height = x1-x0-60, y1-y0-100
    scale = min(width/max(high[0]-low[0], 1e-9), height/max(high[1]-low[1], 1e-9))
    center = (low+high)/2
    xy = (projected[:, :, :2]-center)*np.array([scale, -scale])
    xy += np.array([(x0+x1)/2, (y0+y1)/2+20])
    normals = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    norms = np.linalg.norm(normals, axis=1)
    normals /= np.maximum(norms[:, None], 1e-15)
    light = np.array([-.3, -.5, 1.])
    light /= np.linalg.norm(light)
    intensity = .52+.48*np.abs(normals@light)
    colors = np.clip(np.asarray(color)[None, :]*intensity[:, None], 0, 255).astype(int)
    order = np.argsort(projected[:, :, 2].mean(axis=1))
    for i in order:
        draw.polygon([tuple(v) for v in xy[i]], fill=tuple(colors[i]))


def main():
    files = {}
    for name in ['P001_formed.stp', 'P001_formed.x_t']:
        p = SOURCE/name
        files[name] = {'path': str(p), 'bytes': p.stat().st_size, 'sha256': digest(p)}
    step_path = SOURCE/'P001_formed.stp'
    raw_step = step_path.read_text(encoding='utf-8')
    xt_raw = (SOURCE/'P001_formed.x_t').read_bytes()
    xt_header = xt_raw[:3000].decode('latin-1')
    counts = Counter(re.findall(r'#\d+\s*=\s*([A-Z][A-Z0-9_]*)\s*\(', raw_step))
    step_count_keys = ['MANIFOLD_SOLID_BREP', 'CLOSED_SHELL', 'OPEN_SHELL', 'ADVANCED_FACE',
                       'EDGE_CURVE', 'VERTEX_POINT', 'PLANE', 'CYLINDRICAL_SURFACE', 'TOROIDAL_SURFACE',
                       'B_SPLINE_SURFACE_WITH_KNOTS', 'PRODUCT', 'GEOMETRIC_CURVE_SET']
    reader = STEPControl_Reader()
    # OCCT native APIs can have non-ASCII path limitations. Stage a byte-identical
    # ASCII-path copy only inside the task output directory, leaving originals intact.
    staged = OUT/'source_for_read.stp'
    staged.write_bytes(step_path.read_bytes())
    status = reader.ReadFile(str(staged))
    if status != IFSelect_RetDone:
        raise RuntimeError(f'STEP ReadFile failed: {status}')
    transferred = reader.TransferRoots()
    shape = reader.OneShape()
    solids = indexed(shape, TopAbs_SOLID)
    report = {'part_folder': 'P001', 'files': files,
              'step': {'complete_exchange_file': raw_step.rstrip().endswith('END-ISO-10303-21;'),
                       'schema': 'AP214', 'length_unit_from_file': 'mm',
                       'text_entity_counts': {k: counts[k] for k in step_count_keys},
                       'read_status': str(status), 'roots_for_transfer': reader.NbRootsForTransfer(),
                       'transferred_root_count': transferred, 'transferred_shape_count': reader.NbShapes(),
                       'solid_count': len(solids), 'whole_bounds': bounds(shape)},
              'parasolid': {'header_recognized': '**PARASOLID' in xt_header,
                            'text_format': 'FORMAT=text;' in xt_header,
                            'application': 'VISI Application' if 'APPL=VISI Application;' in xt_header else None,
                            'schema': re.search(r'SCH=([^;]+);', xt_header).group(1),
                            'native_geometry_parsed': False,
                            'limitation': 'No licensed Parasolid reader is installed; header/file checks only. No STEP/X_T equivalence claim.'},
              'solids': []}
    meshes = []
    for i, solid in enumerate(solids, 1):
        faces = indexed(solid, TopAbs_FACE)
        kinds = Counter()
        cylinders = []
        face_records = []
        for j, raw in enumerate(faces, 1):
            face = TopoDS.Face_s(raw)
            adaptor = BRepAdaptor_Surface(face, True)
            kind = str(adaptor.GetType()).split('.')[-1]
            kinds[kind] += 1
            area, centroid = prop(face)
            row = {'face_index': j, 'surface_type': kind, 'area_mm2': area, 'centroid_mm': centroid}
            if 'Cylinder' in kind:
                cy = adaptor.Cylinder()
                axis = cy.Axis()
                p, d = axis.Location(), axis.Direction()
                row['cylinder'] = {'radius_mm': cy.Radius(), 'axis_point_mm': [p.X(),p.Y(),p.Z()],
                                   'axis_direction': [d.X(),d.Y(),d.Z()]}
                cylinders.append(cy.Radius())
            face_records.append(row)
        volume, center = prop(solid, True)
        area, _ = prop(solid)
        vertices = []
        for vertex in indexed(solid, TopAbs_VERTEX):
            p = BRep_Tool.Pnt_s(TopoDS.Vertex_s(vertex))
            vertices.append([p.X(),p.Y(),p.Z()])
        centered_vertices = np.asarray(vertices)-np.asarray(center)
        normalized_vertex_digest = hashlib.sha256(json.dumps(sorted([tuple(round(float(x),5) for x in v) for v in centered_vertices])).encode()).hexdigest()
        row = {'solid_index': i, 'brep_valid': BRepCheck_Analyzer(solid, True).IsValid(),
               'shell_count': len(indexed(solid, TopAbs_SHELL)), 'face_count': len(faces),
               'edge_count': len(indexed(solid, TopAbs_EDGE)), 'vertex_count': len(vertices),
               'bounds': bounds(solid), 'volume_mm3': volume, 'surface_area_mm2': area,
               'center_of_mass_mm': center, 'surface_types': dict(kinds),
               'cylinder_radii_mm': sorted(set(round(x, 8) for x in cylinders)),
               'centered_vertex_signature_1e_5_mm': normalized_vertex_digest}
        (OUT/f'solid_{i}_faces.json').write_text(json.dumps(face_records,ensure_ascii=False,indent=2),encoding='utf-8')
        tri = mesh(solid)
        row['preview_triangle_count'] = len(tri)
        meshes.append(tri)
        report['solids'].append(row)
        print(json.dumps({'stage': 'solid', **row},ensure_ascii=False),flush=True)
    if len(solids) == 2:
        a,b = report['solids']
        report['two_solid_comparison'] = {
            'volume_difference_mm3': b['volume_mm3']-a['volume_mm3'],
            'surface_area_difference_mm2': b['surface_area_mm2']-a['surface_area_mm2'],
            'centroid_translation_mm': [b['center_of_mass_mm'][i]-a['center_of_mass_mm'][i] for i in range(3)],
            'centered_vertex_signatures_equal': a['centered_vertex_signature_1e_5_mm']==b['centered_vertex_signature_1e_5_mm'],
            'note': 'Matching scalar/topological signatures are evidence, not an exact shape-equality proof.'}
    for i,tri in enumerate(meshes,1):
        image = Image.new('RGB',(1600,1050),'#f1f5f9')
        draw = ImageDraw.Draw(image)
        draw.text((32,18),f'P001 exported STEP / Solid {i}',font=font(30),fill='#20364e')
        dims=report['solids'][i-1]['bounds']['size_mm']
        draw.text((32,62),'Bounds: '+' x '.join(f'{x:.3f}' for x in dims)+' mm',font=font(20),fill='#52667a')
        color=[112,165,211] if i==1 else [129,179,161]
        render_panel(draw,tri,(24,110,1030,1025),(1,-1,1.15),'Isometric (derived mesh preview)',color)
        render_panel(draw,tri,(1050,110,1575,640),(0,0,1),'XY view',color)
        render_panel(draw,tri,(1050,660,1575,1025),(0,-1,0),'XZ view',color)
        image.save(OUT/f'solid_{i}_preview.png')
    report['source_hashes_unchanged'] = all(digest(Path(v['path']))==v['sha256'] for v in files.values())
    if not report['source_hashes_unchanged']:
        raise RuntimeError('Source files changed during inspection')
    (OUT/'inspection.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# P001 CAD 导出检查','','本次只读检查，不修改原 CAD、Excel 或现有工序规则。','',
           '## STEP','','- 完整 AP214 交换文件；长度单位为 mm。',
           f'- OCCT 读取成功；实体数 {len(solids)}。',
           '- BRep 有效性为内核几何/拓扑检查，不代表全部特征齐全或工艺可行。','',
           '| 实体 | BRep有效 | 面 | 边 | 顶点 | 尺寸mm | 体积mm³ |','|---|---|---:|---:|---:|---|---:|']
    for r in report['solids']:
        lines.append(f"| {r['solid_index']} | {r['brep_valid']} | {r['face_count']} | {r['edge_count']} | {r['vertex_count']} | {' × '.join(f'{x:.6f}' for x in r['bounds']['size_mm'])} | {r['volume_mm3']:.6f} |")
    lines+=['','## Parasolid','','- 文件头识别成功，为 VISI 导出的文本 Parasolid。',
            '- 当前无原生 Parasolid 读取器，未完成其实体有效性和 STEP/X_T 等价性验证。','',
            '## 可用范围','','已获得可由脚本读取的三维 BRep，可提取面、边、曲面参数及制作预览。',
            '动作与三维几何的对应、板厚/折弯参数识别、运动侧/轨迹、工具及装夹等仍待后续建模。',
            '若包含多个实体，需先确认其身份；本次没有自动删重、合并或选择其中之一。','',
            '原始文件 SHA256 检查：未改变。','']
    (OUT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'output':str(OUT),'solid_count':len(solids),'source_unchanged':report['source_hashes_unchanged']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
