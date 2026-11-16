"""Compile P004 rigid STEP cells, audited bend supports, and geometric P1 domains."""
from collections import Counter, defaultdict
from itertools import combinations
import math
from pathlib import Path

import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.TopTools import TopTools_ListOfShape
from OCP.TopoDS import TopoDS

from information_extract.extract_app.drawing import load_drawing
from information_extract.extract_app.workflow import compile_operation
from information_extract.scripts.build_p002_material_cells import axes, volume
from information_extract.scripts.build_p003_material_basis import planar_area
from information_extract.three_d.artifacts import ROOT, read, sha, source_manifest, verify_sources, write
from information_extract.three_d.assets import bounds, indexed
from information_extract.three_d.association import register
from information_extract.three_d.band_material_solids import boxes_intersect, checked
from information_extract.three_d.geometric_p1 import VERSION, check_basis, geometry_tree
from information_extract.three_d.local_forming import intersect, save_shape, subtract
from information_extract.three_d.local_motion import build_skeleton, oriented_normals, poses
from information_extract.three_d.material import infer_material, plane_distance
from information_extract.three_d.project_allocation_facts import extract_facts, solve_hard
from information_extract.three_d.project_geometry_scoring import geometry_context
from information_extract.three_d.state import transform_point
from src.neighborhood.context import digest

MODULE = ROOT/'information_extract'
OUT = MODULE/'outputs/p004_generation_v01'
STEP = MODULE/'sources/P004/originals/P004.stp'
PROJECT = MODULE/'projects/P004/project.json'
ASSETPATH = MODULE/'outputs/multi_part_step_v01/P004/asset.json'
RIB_JOINTS = ('J184', 'J209', 'J214', 'J279')
FLANGE_JOINTS = ('J182', 'J211', 'J215', 'J278')
FLANGE = 'P1_414'
ROOT_PANEL = 'P533_537'
DIMPLE_CAPS = ('P5_431', 'P6_432', 'P7_430', 'P8_429')


def supplement_tab(material, faces, thickness):
    """Recover the short O047 skin pair from its two directly adjacent bands."""
    skin_ids = [170, 172]
    skins = [faces[fid] for fid in skin_ids]
    region = 'P170_172'
    inner, outer = faces[75], faces[168]
    if not (abs(plane_distance(*skins)-thickness)<1e-7
            and abs(abs(np.dot(skins[0]['plane']['normal'], skins[1]['plane']['normal']))-1)<1e-7
            and set(inner['neighbor_face_ids']) & {414, 172} == {414, 172}
            and set(outer['neighbor_face_ids']) & {1, 170} == {1, 170}
            and abs(outer['cylinder']['radius_mm']-inner['cylinder']['radius_mm']-thickness)<1e-7):
        raise ValueError('The O047 STEP strip no longer identifies its opposed tab skins')
    normal = np.asarray(skins[0]['plane']['normal'])
    middle = np.mean([s['plane']['point_mm'] for s in skins], axis=0)
    parent = next(r for r in material['regions'] if r['region_id']==FLANGE)
    parent_normal = np.asarray(parent['midplane']['normal'])
    axis = np.array([1., 0., 0.])
    if max(abs(normal@parent_normal),abs(normal@axis),abs(parent_normal@axis))>1e-7:
        raise ValueError('The recovered tab does not define an X-directed orthogonal fold')
    point = np.linalg.solve([normal, parent_normal, axis],
        [normal@middle, parent_normal@parent['midplane']['point_mm'], axis@inner['cylinder']['point_mm']])
    material['regions'].append(dict(region_id=region, skin_face_ids=skin_ids,
        midplane=dict(point_mm=middle.tolist(), normal=normal.tolist()), status='STEP_CYLINDER_ADJACENT_SKIN_PAIR'))
    material['joints'].append(dict(joint_id='J75', region_ids=[FLANGE, region],
        hinge_point_mm=point.tolist(), axis_direction=axis.tolist(), unsigned_plane_angle_deg=90.,
        axis_model='MIDPLANE_INTERSECTION_APPROXIMATION', surface_evidence=[
            dict(cylinder_face_id=f['face_id'], contact_skin_faces={FLANGE:a,region:b},
                outer_or_inner_radius_mm=f['cylinder']['radius_mm'])
            for f,a,b in ((inner,414,172),(outer,1,170))]))


def line_gap(first, second, axis):
    return float(np.linalg.norm(np.cross(np.asarray(first)-second, axis)))


def common_axis(rows, faces):
    first = rows[0]
    direction = np.asarray(first['axis_direction'])
    origin = np.asarray(first['hinge_point_mm'])
    cylinders = [faces[e['cylinder_face_id']]['cylinder'] for row in rows for e in row['surface_evidence']]
    cylinder_origin = cylinders[0]['point_mm']
    midplane_residual = max(line_gap(row['hinge_point_mm'], origin,direction) for row in rows)
    cylinder_residual = max(line_gap(c['point_mm'],cylinder_origin,direction) for c in cylinders)
    if (midplane_residual>1e-7 or cylinder_residual>1e-7
            or any(np.linalg.norm(np.asarray(row['axis_direction'])-direction)>1e-8 for row in rows)
            or any(abs(abs(np.dot(c['direction'],direction))-1)>1e-8 for c in cylinders)):
        raise ValueError('P004 parallel support axes are not coincident')
    return dict(midplane_transverse_residual_mm=midplane_residual,
                cylinder_transverse_residual_mm=cylinder_residual, direction=direction.tolist())


def signed_turn(axis, first, second):
    return math.degrees(math.atan2(float(np.dot(axis, np.cross(first,second))),float(np.dot(first,second))))


def build_motion_tree(material, asset, reg, normals):
    raw = {j['joint_id']:j for j in material['joints']}
    faces = {f['face_id']:f for f in asset['faces']}
    ribs = [raw[jid] for jid in RIB_JOINTS]
    flanges = [raw[jid] for jid in FLANGE_JOINTS]
    rib_panels = {rid for row in ribs for rid in row['region_ids'] if rid!=ROOT_PANEL}
    if (len(rib_panels)!=4 or any(ROOT_PANEL not in j['region_ids'] for j in ribs)
            or {rid for row in flanges for rid in row['region_ids'] if rid!=FLANGE}!=rib_panels
            or any(FLANGE not in j['region_ids'] for j in flanges)):
        raise ValueError('Parallel support topology differs from P004')
    evidence = {op:common_axis(rows,faces) for op,rows in (('O013',ribs),('O014',flanges))}
    redundant = set(FLANGE_JOINTS[1:])
    reduced = {**material,
        'regions':[r for r in material['regions'] if r['region_id'] not in DIMPLE_CAPS],
        'joints':[j for j in material['joints'] if j['joint_id'] not in redundant]}
    skeleton = build_skeleton(reduced, asset, reg, normals)
    if skeleton['unconnected_region_proposal_ids']:
        raise ValueError(f"Unconnected material: {skeleton['unconnected_region_proposal_ids']}")
    joints = {j['joint_id']:j for j in skeleton['joints']}
    panels = {r['region_id']:r for r in skeleton['regions']}
    reference = joints[FLANGE_JOINTS[0]]
    if reference['child_region_id']!=FLANGE or reference['parent_region_id'] not in rib_panels:
        raise ValueError('Flange support orientation changed')
    for row in ribs:
        actual = joints[row['joint_id']]
        if (actual['parent_region_id']!=ROOT_PANEL
                or not math.isclose(actual['angle_deg'],joints[RIB_JOINTS[0]]['angle_deg'],abs_tol=1e-8,rel_tol=0)):
            raise ValueError('O013 ribs do not move by one identical signed rotation')
    supports = []
    for row in flanges:
        rib = next(rid for rid in row['region_ids'] if rid!=FLANGE)
        parent_front, flange_front = [panels[r]['front_face_id'] for r in (rib,FLANGE)]
        if any((e['contact_skin_faces'][rib]==parent_front)!=(e['contact_skin_faces'][FLANGE]==flange_front)
               for e in row['surface_evidence']):
            raise ValueError('O014 support skin-side orientation changed')
        angle = signed_turn(row['axis_direction'], normals[parent_front], normals[flange_front])
        if not math.isclose(angle,reference['angle_deg'],abs_tol=1e-8,rel_tol=0):
            raise ValueError('O014 supports have different signed turns')
        supports.append(dict(joint_id=row['joint_id'], parent_region_id=rib, child_region_id=FLANGE,
            hinge_point_mm=row['hinge_point_mm'], axis_direction=row['axis_direction'], angle_deg=angle,
            surface_evidence=row['surface_evidence']))
    evidence['O013'].update(joint_ids=list(RIB_JOINTS), signed_angle_deg=joints[RIB_JOINTS[0]]['angle_deg'])
    evidence['O014'].update(joint_ids=list(FLANGE_JOINTS), signed_angle_deg=reference['angle_deg'],
        representative_joint_id=reference['joint_id'], supports=supports,
        equality_scope='Coaxial equal signed transforms on co-moving O013 parent ribs for any O013/O014 progress')
    # The representative parent has the same transform as the other three ribs.
    # Preserve their individual cylinder intervals for the review outline.
    flat = poses(skeleton,{j['joint_id']:0 for j in skeleton['joints']})
    for row in supports[1:]:
        for surface in row['surface_evidence']:
            fid = surface['cylinder_face_id']
            face = faces[fid]
            ends = [transform_point(flat[row['parent_region_id']],
                [x,*row['hinge_point_mm'][1:]]) for x in (face['bounds_mm'][0],face['bounds_mm'][3])]
            reference['developed_axis_segment_evidence_mm'].append(dict(cylinder_face_id=fid,endpoints=ends))
    return skeleton, evidence


def match_bends(skeleton, operations, paths, reg, source, faces):
    joints = {row['joint_id']:row for row in skeleton['joints']}
    groups = {ids[0]:[joints[jid] for jid in ids] for ids in (RIB_JOINTS, (FLANGE_JOINTS[0],))}
    mapped = set(RIB_JOINTS) | {FLANGE_JOINTS[0]}
    groups.update({jid:[row] for jid,row in joints.items() if jid not in mapped})
    matrix = np.asarray(reg['matrix'])
    translation = np.asarray(reg['translation_mm'])
    matches = []
    for oid,op in operations.items():
        if op['operation_type']!='BEND':
            continue
        path, = paths[oid]['paths']
        segment, = path['segments']
        if path['role']!='BEND_REFERENCE' or segment['segment_type'] not in {'BEND_LINE','LINE'}:
            raise ValueError(f'Expected an audited straight bend reference for {oid}')
        target = sorted([matrix@np.array([segment[f'{end}_x'],segment[f'{end}_y']])+translation
                         for end in ('start','end')],key=lambda p:p[0])
        turn = 180-op['bend_angle_deg']
        admissible = []
        for role,rows in groups.items():
            intervals = [np.asarray(e['endpoints'])[:,:2] for row in rows
                         for e in row['developed_axis_segment_evidence_mm']]
            positions = np.vstack(intervals)
            axial_delta = max(abs(min(positions[:,0])-target[0][0]),abs(max(positions[:,0])-target[1][0]))
            transverse_delta = max(abs(positions[:,1]-target[0][1]))
            if (abs(abs(rows[0]['angle_deg'])-turn)<1e-7 and axial_delta<0.05 and transverse_delta<2.
                    and abs(target[0][1]-target[1][1])<1e-7):
                admissible.append((role,float(axial_delta),float(transverse_delta)))
        if len(admissible)!=1:
            raise ValueError(f'Expected unique bend support for {oid}, found {admissible}')
        role,along,across = admissible[0]
        supported = groups[role]
        if any('operation_id' in row for row in supported):
            raise ValueError('Two independent actions refer to the same support')
        for row in supported:
            row.update(operation_id=oid, recorded_angle_deg=op['bend_angle_deg'])
        matches.append(dict(operation_id=oid,joint_id=role,joint_ids=[r['joint_id'] for r in supported],
            recorded_angle_deg=op['bend_angle_deg'], physical_turn_deg=turn,
            axial_residual_mm=along, transverse_residual_mm=across))
    if any('operation_id' not in r for r in joints.values()):
        raise ValueError('Unmapped STEP bend support remains')
    if not all(joints[j]['operation_id']=='O013' for j in RIB_JOINTS) or joints[FLANGE_JOINTS[0]]['operation_id']!='O014':
        raise ValueError('Unexpected action identity for the parallel supports')
    # Reconcile O013's four machine drawing intervals against independent STEP faces.
    op = operations['O013']
    segments = [source[sid] for sid in op['source_segment_ids']]
    raw = {tuple(round(float(x),6) for x in sorted(
        (matrix@np.array([s[f'{end}_x'],s[f'{end}_y']])+translation)[0] for end in ('start','end')))
        for s in segments}
    cad = {tuple(round(faces[e['cylinder_face_id']]['bounds_mm'][i],6) for i in (0,3))
           for jid in RIB_JOINTS for e in joints[jid]['surface_evidence']}
    if len(raw)!=4 or raw!=cad:
        raise ValueError('O013 individual axial intervals mismatch')
    return matches


def partition(material, skeleton, asset, thickness):
    reader = STEPControl_Reader()
    if reader.ReadFile(str(STEP))!=IFSelect_RetDone:
        raise ValueError('P004 STEP import failed')
    reader.SetSystemLengthUnit(1.)
    if not reader.TransferRoots():
        raise ValueError('P004 STEP transfer failed')
    cad = reader.OneShape()
    faces = indexed(cad,TopAbs_FACE)
    if faces.Extent()!=asset['face_count']:
        raise ValueError('STEP face index changed')
    args, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    args.Append(cad)
    records = []
    for joint in material['joints']:
        evidence = joint['surface_evidence']
        radius = max(e['outer_or_inner_radius_mm'] for e in evidence)
        inner = radius-thickness
        if inner<0 or any(min(abs(e['outer_or_inner_radius_mm']-r) for r in (radius,inner))>1e-7 for e in evidence):
            raise ValueError(f"Unpaired band radii for {joint['joint_id']}")
        for e in evidence:
            if abs(e['outer_or_inner_radius_mm']-radius)>1e-7:
                continue
            fid = e['cylinder_face_id']
            surface = BRepAdaptor_Surface(TopoDS.Face_s(faces.FindKey(fid)),True)
            u0,u1,v0,v1 = surface.FirstUParameter(),surface.LastUParameter(),surface.FirstVParameter(),surface.LastVParameter()
            axis = axes(surface.Cylinder().Position(),u0,v0)
            shell = BRepPrimAPI_MakeCylinder(axis,radius,v1-v0,u1-u0).Shape()
            if inner>1e-7:
                shell = subtract(shell,[BRepPrimAPI_MakeCylinder(axis,inner,v1-v0,u1-u0).Shape()])
            band_volume = volume(intersect(cad,shell))
            if band_volume<=1e-7:
                raise ValueError(f'Empty bend band on face {fid}')
            tools.Append(shell)
            records.append(dict(joint_id=joint['joint_id'],step_face_id=fid,volume_mm3=band_volume,
                                inner_radius_mm=inner,outer_radius_mm=radius))
    cut = BRepAlgoAPI_Cut()
    cut.SetArguments(args)
    cut.SetTools(tools)
    cut.SetFuzzyValue(1e-7)
    cut.Build()
    if not cut.IsDone():
        raise ValueError('Finite band separation failed')
    remaining = checked(cut.Shape())
    solids = indexed(remaining,TopAbs_SOLID)
    if solids.Extent()!=len(skeleton['regions']):
        raise ValueError(f"Expected {len(skeleton['regions'])} cells, found {solids.Extent()}")
    by_region = {}
    owned_faces = set()
    for index in range(1,solids.Extent()+1):
        body = solids.FindKey(index)
        box = bounds(body)
        owner = [r['region_id'] for r in skeleton['regions'] if all(
            boxes_intersect(box,asset['faces'][fid-1]['bounds_mm'])
            and planar_area(body,faces.FindKey(fid))>1e-6 for fid in r['skin_face_ids'])]
        if len(owner)!=1 or owner[0] in by_region:
            raise ValueError(f'A STEP cell lacks unique skin ownership: {owner}')
        by_region[owner[0]] = body
        body_faces = indexed(body,TopAbs_FACE)
        owned_faces.update(body_faces.FindKey(i) for i in range(1,body_faces.Extent()+1))
    if len(owned_faces)!=indexed(remaining,TopAbs_FACE).Extent():
        raise ValueError('Unexpected non-solid residual material')
    caps = {r['region_id']:r['skin_face_ids'] for r in material['regions'] if r['region_id'] in DIMPLE_CAPS}
    cap_evidence = {}
    for rid, ids in caps.items():
        areas = {fid:planar_area(by_region[FLANGE],faces.FindKey(fid)) for fid in ids}
        if min(areas.values())<=1e-6:
            raise ValueError(f'Dimple cap {rid} is not included in the flange cell')
        cap_evidence[rid] = dict(cell_id=FLANGE,skin_intersection_areas_mm2=areas)
    boxes = {rid:bounds(s) for rid,s in by_region.items()}
    overlap = max((volume(intersect(by_region[a],by_region[b])) for a,b in combinations(by_region,2)
        if boxes_intersect(boxes[a],boxes[b])),default=0.)
    values = {rid:volume(s) for rid,s in by_region.items()}
    original, final = volume(cad),volume(remaining)
    assembly_error = abs(final-math.fsum(values.values()))
    band_error = abs(original-final-math.fsum(r['volume_mm3'] for r in records))
    if overlap>1e-6 or max(assembly_error,band_error)>1e-5*original or min(values.values())<=1e-7:
        raise ValueError('STEP material partition volume validation failed')
    return by_region,dict(thickness_mm=thickness,bands=records,region_volumes_mm3=values,
        original_volume_mm3=original,remaining_volume_mm3=final,max_pair_overlap_mm3=overlap,
        partition_volume_residual_mm3=assembly_error,band_volume_residual_mm3=band_error,
        all_partition_faces_owned_by_solids=True,dimple_cap_ownership=cap_evidence)


def compile_basis(context, skeleton, evidence):
    # The tree API requires one bend ID per row. Use geometry joint identities
    # for its structural check and group the synchronized supports by action.
    bends = [{**row,'operation_id':row['joint_id']} for row in skeleton['joints']]
    subtree = geometry_tree({**skeleton,'bends':bends})
    groups = defaultdict(list)
    for row in skeleton['joints']:
        groups[row['operation_id']].append(row)
    volumes = evidence['region_volumes_mm3']
    domains = []
    for oid,rows in sorted(groups.items()):
        regions = sorted(set().union(*(subtree(row['child_region_id']) for row in rows)))
        ids = [row['joint_id'] for row in rows]
        domains.append(dict(operation_id=oid,joint_id=ids[0],
            joint_ids=list(FLANGE_JOINTS) if oid=='O014' else ids,region_ids=regions,
            carried_volume_mm3=math.fsum(volumes[r] for r in regions)))
    body = dict(schema_version=VERSION,part_id='P004',facts_fingerprint=context.facts_fingerprint,
        domains=domains,metric='carried_volume_mm3',material_id=sha(STEP),region_volumes_mm3=volumes,
        physical_joint_count=len(evidence['bands']),motion_joint_count=len(skeleton['joints']),
        domain_definition='STEP child subtree volumes; synchronous O013 supports counted by cell union',
        reference_state='COMMON_COMPLETED_STEP_WITH_FINITE_BEND_BANDS_REMOVED',expert_used=False,sheet5_used=False)
    basis = {**body,'fingerprint':digest(body)}
    check_basis(context,basis)
    return basis


def main():
    cfg_path = MODULE/'configs/P004.json'
    reg_path = MODULE/'configs/multi_part_step_v01.json'
    mat_path = MODULE/'configs/material_state_v01.json'
    cfg, settings, project = read(cfg_path), read(mat_path), read(PROJECT)
    dxf = MODULE/cfg['source_file']
    files = {dxf,STEP,ASSETPATH,PROJECT,cfg_path,reg_path,mat_path,Path(__file__)}
    files.update(MODULE/p for p in (cfg['confirmed_geometry_supplement'],cfg['confirmed_geometry_correction'],
                                     cfg['confirmed_geometry_revision']))
    files.update(MODULE/'scripts'/p for p in ('build_p002_material_cells.py','build_p003_material_basis.py'))
    for path in (MODULE/'three_d', MODULE/'extract_app', ROOT/'src'):
        files.update(path.rglob('*.py'))
    # The basis feeds the P004 motion and staged-material consumers.
    # Their implementation is fingerprinted at their own entry points.
    files.difference_update(MODULE/'three_d'/name for name in ('p004_motion.py','p004_staged_material.py'))
    sources = source_manifest(sorted(files))
    drawing, asset = load_drawing(dxf,cfg), read(ASSETPATH)
    if drawing['source']['sha256']!=project['source_sha256'] or sha(STEP)!=asset['asset_id']:
        raise ValueError('Stale P004 drawing annotation or STEP topology')
    facts = extract_facts(project,drawing)
    hard_context,witness = solve_hard(facts)
    if not witness['feasible'] or facts['not_later_relations']:
        raise ValueError('P004 hard sequence is unsupported')
    source = {row['id']:row for row in drawing['segments']}
    operations = {row['operation_id']:row for row in facts['operations']}
    paths = {oid:dict(feature_type=op['feature_type'],paths=compile_operation(
        op,[source[sid] for sid in op['source_segment_ids']],cfg)['paths']) for oid,op in operations.items()}
    context,_ = geometry_context(facts,paths,{'rows':[]})
    anchors = [dict(operation_id=oid,operation_type='CIRCLE_PUNCH',
                    segments=[source[sid] for sid in op['source_segment_ids']])
        for oid,op in operations.items() if op['operation_type']=='CIRCLE_PUNCH']
    reg = register({'operations':anchors},asset,{'registration':read(reg_path)['registration']})
    if (reg.get('anchor_count')!=3 or reg['runner_up_anchor_count']>=3 or reg['max_residual_mm']>1e-7
            or reg['noncollinear_area_proxy_mm2']<=1. or reg['base_face_id']!=533):
        raise ValueError('P004 three-anchor base registration could not be reproduced')
    print('P004 registration',reg['translation_mm'],flush=True)
    settings['plane_pairing']['thickness_support_area_fraction'] = .001
    material = infer_material(asset,settings)
    thickness = material['thickness_estimate']['value_mm']
    if abs(thickness-1.)>1e-7:
        raise ValueError('P004 STEP thickness has changed')
    faces = {f['face_id']:f for f in asset['faces']}
    supplement_tab(material,faces,thickness)
    skeleton, supports = build_motion_tree(material,asset,reg,oriented_normals(STEP,asset))
    matches = match_bends(skeleton,operations,paths,reg,source,faces)
    reg.update(status='BASE_FRAME_SUPPORTED',registration_method='THREE_NONCOLLINEAR_EXACT_CIRCLES_AND_21_UNIQUE_BEND_AXIAL_MATCHES',
               bend_anchor_count=len(matches), largest_bend_transverse_residual_mm=max(r['transverse_residual_mm'] for r in matches))
    write(OUT/'facts.json',facts)
    write(OUT/'geometry_paths.json',paths)
    write(OUT/'registration.json',reg)
    write(OUT/'skeleton.json',skeleton)
    write(OUT/'hard_witness.json',witness)
    print('P004 skeleton',len(skeleton['regions']),'cells',len(matches),'bend actions',flush=True)
    solids,evidence = partition(material,skeleton,asset,thickness)
    basis = compile_basis(context,skeleton,evidence)
    for rid,solid in solids.items():
        save_shape(OUT/'brep'/f'{rid}.brep',solid)
    verify_sources(sources)
    write(OUT/'material_basis.json',basis)
    write(OUT/'partition.json',dict(**evidence,association=dict(matches=matches),parallel_supports=supports,
        source_revision=project['revision'],precedence_counts=dict(Counter(r.constraint_type for r in hard_context.precedence_relations))))
    sources.update(source_manifest((OUT/'brep').glob('*.brep')))
    write(OUT/'inputs.json',dict(files=sources,annotation_facts_fingerprint=facts['fingerprint'],
        facts_fingerprint=context.facts_fingerprint,basis_fingerprint=basis['fingerprint']))
    print('P004 material',len(solids),'cells',len(evidence['bands']),'bands','volume residual',
          evidence['band_volume_residual_mm3'],flush=True)


if __name__=='__main__':
    main()
