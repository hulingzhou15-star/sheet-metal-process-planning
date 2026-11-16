"""Compile P003 carried STEP volumes and a shared physical joint for O069/O120."""
from collections import Counter
from copy import deepcopy
from itertools import combinations
import math
from pathlib import Path

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepGProp import BRepGProp
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.TopTools import TopTools_ListOfShape
from OCP.TopoDS import TopoDS

from information_extract.extract_app.drawing import load_drawing
from information_extract.extract_app.workflow import compile_operation
from information_extract.scripts.build_p002_material_cells import axes, volume
from information_extract.three_d.artifacts import ROOT, read, sha, verify_sources, write
from information_extract.three_d.assets import bounds, indexed
from information_extract.three_d.association import register
from information_extract.three_d.band_material_solids import boxes_intersect, checked
from information_extract.three_d.geometric_p1 import VERSION, check_basis, geometry_tree
from information_extract.three_d.local_forming import intersect, save_shape, subtract
from information_extract.three_d.local_motion import associate_bends, build_skeleton, oriented_normals
from information_extract.three_d.material import infer_material
from information_extract.three_d.project_allocation_facts import extract_facts, solve_hard
from information_extract.three_d.project_geometry_scoring import geometry_context
from src.neighborhood.context import digest

MODULE = ROOT/'information_extract'
ASSETPATH = MODULE/'outputs/multi_part_step_v01/P003/asset.json'
STEP = MODULE/'sources/P003/originals/P003.stp'
OUT = MODULE/'outputs/p003_generation_v01'
PROJECT = MODULE/'projects/P003/project.json'
FIRST, SECOND = 'O069', 'O120'


def shared_steps(operations, paths):
    first, second = operations[FIRST], operations[SECOND]
    if [first.get('surface_side'),second.get('surface_side')] != ['SMOOTH','ROUGH']:
        raise ValueError('Changed sides of the shared bend')
    if [first.get('bend_angle_deg'),second.get('bend_angle_deg')] != [135,45]:
        raise ValueError('Changed angles of the shared bend')
    sources = set(first['source_segment_ids'])
    if not sources or any(set(row.get(k,[])) != sources for row in (first,second)
                          for k in ('source_segment_ids','shared_source_segment_ids')):
        raise ValueError('Shared bend needs explicitly shared source geometry')
    if FIRST not in second['required_predecessor_ids'] or second.get('cad_parent_operation_id'):
        raise ValueError('Missing shared bend precedence or unexpected CAD parent')
    if paths[FIRST]['paths'][0]['segments'] != paths[SECOND]['paths'][0]['segments']:
        raise ValueError('Shared bend lines differ')
    return [
        dict(operation_id=FIRST, increment_deg=135, cumulative_deg=135),
        dict(operation_id=SECOND, increment_deg=-45, cumulative_deg=90),
    ]


def planar_area(solid, face):
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(intersect(solid,face),props)
    return float(props.Mass())


def partition(asset, skeleton, thickness):
    if sha(STEP) != asset['asset_id']:
        raise ValueError('STEP source does not match topology')
    reader = STEPControl_Reader()
    if reader.ReadFile(str(STEP)) != IFSelect_RetDone:
        raise ValueError('STEP import failed')
    reader.SetSystemLengthUnit(1.)
    if not reader.TransferRoots():
        raise ValueError('STEP units or transfer failed')
    cad = reader.OneShape()
    faces = indexed(cad,TopAbs_FACE)
    if faces.Extent()!=asset['face_count']:
        raise ValueError('STEP face count changed')
    records, bands = [], []
    for joint in skeleton['joints']:
        evidence = joint['surface_evidence']
        radius = max(e['outer_or_inner_radius_mm'] for e in evidence)
        outer = [e['cylinder_face_id'] for e in evidence if abs(e['outer_or_inner_radius_mm']-radius)<1e-7]
        if any(min(abs(e['outer_or_inner_radius_mm']-r) for r in (radius,radius-thickness))>1e-7 for e in evidence):
            raise ValueError('Bend has mixed unmatched radii')
        for fid in outer:
            a = BRepAdaptor_Surface(TopoDS.Face_s(faces.FindKey(fid)),True)
            u0,u1,v0,v1 = a.FirstUParameter(),a.LastUParameter(),a.FirstVParameter(),a.LastVParameter()
            ax = axes(a.Cylinder().Position(),u0,v0)
            shell = BRepPrimAPI_MakeCylinder(ax,radius,v1-v0,u1-u0).Shape()
            inner = radius-thickness
            if inner>1e-7:
                shell = subtract(shell,[BRepPrimAPI_MakeCylinder(ax,inner,v1-v0,u1-u0).Shape()])
            band = intersect(cad,shell)
            v = volume(band)
            if v<=1e-7:
                raise ValueError(f'Empty bend band face {fid}')
            bands.append(shell)
            records.append(dict(joint_id=joint['joint_id'],step_face_id=fid,volume_mm3=v,
                                outer_radius_mm=radius,inner_radius_mm=max(0.,inner)))
    args, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    args.Append(cad)
    for band in bands:
        tools.Append(band)
    cut = BRepAlgoAPI_Cut()
    cut.SetArguments(args)
    cut.SetTools(tools)
    cut.SetFuzzyValue(1e-7)
    cut.Build()
    if not cut.IsDone():
        raise ValueError('Boolean partition at the finite bend bands failed')
    remaining = checked(cut.Shape())
    solids = indexed(remaining,TopAbs_SOLID)
    if solids.Extent()!=len(skeleton['regions']):
        raise ValueError(f'Partition made {solids.Extent()} solids for {len(skeleton["regions"])} skin regions')
    by_region = {}
    all_faces = set()
    for i in range(1,solids.Extent()+1):
        solid = solids.FindKey(i)
        owners = [r['region_id'] for r in skeleton['regions']
                  if all(planar_area(solid,faces.FindKey(f))>1e-6 for f in r['skin_face_ids'])]
        if len(owners)!=1 or owners[0] in by_region:
            raise ValueError(f'A material cell lacks unique skin ownership: {owners}')
        by_region[owners[0]] = solid
        solid_faces = indexed(solid,TopAbs_FACE)
        all_faces.update(solid_faces.FindKey(k) for k in range(1,solid_faces.Extent()+1))
    if len(all_faces)!=indexed(remaining,TopAbs_FACE).Extent():
        raise ValueError('Unexpected non-solid partition faces')
    boxes = {rid:bounds(s) for rid,s in by_region.items()}
    max_overlap = max((volume(intersect(by_region[a],by_region[b])) for a,b in combinations(by_region,2)
                       if boxes_intersect(boxes[a],boxes[b])),default=0.)
    if max_overlap>1e-6:
        raise ValueError('Material ownership overlaps')
    original_volume, remaining_volume = volume(cad),volume(remaining)
    region_volume = {r:volume(s) for r,s in by_region.items()}
    residual = abs(remaining_volume-math.fsum(region_volume.values()))
    if residual>1e-5*remaining_volume:
        raise ValueError('Partition volume integration differs')
    band_residual = abs(original_volume-remaining_volume-math.fsum(row['volume_mm3'] for row in records))
    if band_residual>1e-5*original_volume:
        raise ValueError('Cut volume differs from the measured bend bands')
    return by_region,dict(thickness_mm=thickness,bands=records,region_volumes_mm3=region_volume,
        original_volume_mm3=original_volume,remaining_volume_mm3=remaining_volume,
        max_pair_overlap_mm3=max_overlap,partition_volume_residual_mm3=residual,
        band_volume_residual_mm3=band_residual,
        all_partition_faces_owned_by_solids=True)


def compile_basis(context, skeleton, evidence, steps, step_sha):
    subtree = geometry_tree({**skeleton,'bends':skeleton['joints']})
    volumes = evidence['region_volumes_mm3']
    if set(volumes)!=set(skeleton['region_order']):
        raise ValueError('Missing physical-cell volume')
    domains = []
    for joint in skeleton['joints']:
        child = joint['child_region_id']
        regions = sorted(subtree(child))
        domain = dict(operation_id=joint['operation_id'],joint_id=joint['joint_id'],region_ids=regions,
                      carried_volume_mm3=math.fsum(volumes[r] for r in regions))
        if joint['operation_id']==FIRST:
            domains.extend([{**deepcopy(domain),**step,'shared_physical_joint':True} for step in steps])
        else:
            domains.append(domain)
    body = dict(schema_version=VERSION,part_id='P003',facts_fingerprint=context.facts_fingerprint,
        domains=sorted(domains,key=lambda d:d['operation_id']),metric='carried_volume_mm3',
        material_id=step_sha,region_volumes_mm3=volumes,physical_joint_count=len(skeleton['joints']),
        domain_definition='Completed STEP child subtree excluding bend bands; successive operations on the same physical joint refer to one set of solids',
        reference_state='COMMON_COMPLETED_STEP_WITH_FINITE_BEND_BANDS_REMOVED',
        expert_used=False,sheet5_used=False)
    basis = {**body,'fingerprint':digest(body)}
    check_basis(context,basis)
    return basis


def main():
    cfg_path = MODULE/'configs/P003.json'
    reg_path = MODULE/'configs/multi_part_step_v01.json'
    mat_path = MODULE/'configs/material_state_v01.json'
    cfg, mat_cfg, project = read(cfg_path),read(mat_path),read(PROJECT)
    dxf = MODULE/cfg['source_file']
    sources = {dxf,STEP,ASSETPATH,PROJECT,cfg_path,reg_path,mat_path,Path(__file__)}
    sources.update((MODULE/'three_d').glob('*.py'))
    sources.update((MODULE/'extract_app').rglob('*.py'))
    sources.update((ROOT/'src').rglob('*.py'))
    sources.add(MODULE/'scripts/build_p002_material_cells.py')
    hashes = {p.relative_to(ROOT).as_posix():sha(p) for p in sorted(sources)}
    drawing = load_drawing(dxf,cfg)
    if drawing['source']['sha256']!=project['source_sha256']:
        raise ValueError('Stale annotation source')
    facts = extract_facts(project,drawing)
    hard_context, witness = solve_hard(facts)
    if not witness['feasible'] or facts['not_later_relations']:
        raise ValueError('Unsupported hard-allocation context')
    asset = read(ASSETPATH)
    source = {s['id']:s for s in drawing['segments']}
    paths, refs = {}, []
    ops = {o['operation_id']:o for o in facts['operations']}
    for oid,op in ops.items():
        segments = [source[s] for s in op['source_segment_ids']]
        rec = compile_operation(op,segments,cfg)
        paths[oid] = dict(feature_type=op['feature_type'],paths=rec['paths'])
        if op['operation_type']=='BEND':
            if len(rec['paths'])!=1 or rec['paths'][0]['role']!='BEND_REFERENCE':
                raise ValueError(f'Unsupported finite bend path {oid}')
            if oid!=SECOND:
                refs.append(dict(operation_id=oid,operation_type='BEND',segments=rec['paths'][0]['segments']))
    steps = shared_steps(ops,paths)
    context, _ = geometry_context(facts,paths,{'rows':[]})
    anchors = [dict(operation_id=o['operation_id'],operation_type='CIRCLE_PUNCH',
                    segments=[source[s] for s in o['source_segment_ids']])
               for o in ops.values() if o['feature_type']=='CIRCLE_PUNCH']
    reg = register({'operations':anchors},asset,{'registration':read(reg_path)['registration']})
    if reg['status']!='BASE_FRAME_SUPPORTED':
        raise ValueError('DXF base registration unresolved')
    material = infer_material(asset,mat_cfg)
    skeleton = build_skeleton(material,asset,reg,oriented_normals(STEP,asset))
    matches = associate_bends(skeleton,{'operations':refs},reg,mat_cfg)
    if (skeleton['unconnected_region_proposal_ids'] or len(matches['matches'])!=len(refs)
        or matches['all_candidate_count']!=len(refs) or matches['unmatched_joint_ids']):
        raise ValueError('Not every physical bend jointly matches the STEP and DXF')
    associates = {r['joint_id']:r['operation_id'] for r in matches['matches']}
    for j in skeleton['joints']:
        j['operation_id'] = associates[j['joint_id']]
        if abs(abs(j['angle_deg'])-90)>1e-6:
            raise ValueError('Completed angle differs from operation semantics')
    print('P003',project['revision'],len(ops),'actions',len(refs),'physical joints',flush=True)
    solids, evidence = partition(asset,skeleton,material['thickness_estimate']['value_mm'])
    basis = compile_basis(context,skeleton,evidence,steps,sha(STEP))
    for region,solid in solids.items():
        save_shape(OUT/'brep'/f'{region}.brep',solid)
    verify_sources(hashes)
    write(OUT/'facts.json',facts)
    write(OUT/'geometry_paths.json',paths)
    write(OUT/'registration.json',reg)
    write(OUT/'skeleton.json',skeleton)
    write(OUT/'hard_witness.json',witness)
    write(OUT/'material_basis.json',basis)
    write(OUT/'partition.json',dict(**evidence,association=matches,source_revision=project['revision'],
          shared_operation_steps=steps,precedence_counts=dict(Counter(r.constraint_type for r in hard_context.precedence_relations))))
    hashes.update({p.relative_to(ROOT).as_posix():sha(p) for p in OUT.glob('brep/*.brep')})
    write(OUT/'inputs.json',dict(files=hashes,annotation_facts_fingerprint=facts['fingerprint'],
        facts_fingerprint=context.facts_fingerprint,basis_fingerprint=basis['fingerprint']))
    print('Material volumes',evidence['region_volumes_mm3'],flush=True)


if __name__=='__main__':
    main()
