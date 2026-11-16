"""Staged P004 material reconstructed from registered DXF and the supplied STEP."""
from collections import Counter
from itertools import combinations

import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Vec

from .allocation_material_states import event_schedule
from .artifacts import ROOT, sha, write
from .assets import bounds
from .band_material_solids import boxes_intersect, checked
from .local_collision import indexed, transformed, volume
from .local_forming import fuse, intersect, save_shape, subtract
from .material_removal import source_prism
from .meshing import mesh
from .p002_motion_reference import CHECK_CONFIG
from .p003_staged_material import apply, batch_boolean, charts, circular_patches, step_wires


REMOVALS = {'CIRCLE_PUNCH', 'IRREGULAR_PUNCH', 'PRE_PUNCH'}
FORMS = {'DIMPLE', 'FLANGING', 'MARKING'}
STEP = ROOT/'information_extract/sources/P004/originals/P004.stp'
MARK_FLOORS = {'O001': ('P533_537', 687), 'O077': ('P1_414', 116)}
DIMPLE_FACES = {
    'O043': (2, 5, 431, 435, 436), 'O044': (3, 6, 432, 437, 438),
    'O045': (4, 7, 430, 433, 434), 'O046': (8, 9, 427, 428, 429),
}
SMALL_PROFILE_OFFSETS = {'O006', 'O038', 'O042', 'O052'}
RELIEF_PROFILES = {'O068', 'O069', 'O070', 'O071'}


def source_faces(asset):
    if sha(STEP) != asset['asset_id']:
        raise ValueError('P004 STEP source differs from the topology asset')
    reader = STEPControl_Reader()
    if reader.ReadFile(str(STEP)) != IFSelect_RetDone:
        raise ValueError('Cannot import P004 STEP')
    reader.SetSystemLengthUnit(1.)
    if not reader.TransferRoots():
        raise ValueError('Cannot transfer P004 STEP solids')
    faces = indexed(reader.OneShape(), TopAbs_FACE)
    if faces.Extent() != asset['face_count']:
        raise ValueError('P004 STEP face indices changed')
    return faces


def common_prism_volume(a, b, thickness, coincident=False):
    common = volume(intersect(a,b))
    if coincident and common<=1e-7:
        # OCCT can return an empty common for the coincident O065 faces.
        # Both prisms have constant XY sections and the same Z limits.
        offset = 1e-5
        shift = np.eye(4)
        shift[2,3] = offset
        common = volume(intersect(transformed(a,shift),b))*thickness/(thickness-offset)
    return common


def match_cuts(oid, paths, registration, wires, solids, hints, thickness):
    parts = []
    for path in paths:
        if not path['closed']:
            return None
        tool = source_prism(path, registration, 0., np.eye(4), [0, 0, -thickness])
        box = np.asarray(bounds(tool))
        measured = volume(tool)
        hits = []
        for wire in wires:
            cell, bb = wire['cell'], wire['bb']
            if hints and cell not in hints:
                continue
            shift = ((bb[:2]+bb[3:5])-(box[:2]+box[3:5]))/2
            residual = float(np.linalg.norm(shift))
            size_error = float(np.max(np.abs((bb[3:5]-bb[:2])-(box[3:5]-box[:2]))))
            if residual > 2. or size_error > .08:
                continue
            placement = np.eye(4)
            placement[:3,3] = [*shift, bb[5]-box[5]]
            aligned = transformed(tool, placement)
            coincident = oid=='O065' and max(residual,size_error,abs(measured-wire['volume']))<1e-6
            common = common_prism_volume(aligned,wire['body'],thickness,coincident)
            delta = max(0., measured+wire['volume']-2*common)
            small = (oid in SMALL_PROFILE_OFFSETS and size_error<=.021 and residual<=.35 and
                     delta<=.11 and abs(min(measured,wire['volume'])-common)<=1e-5)
            if not small and delta > max(.08, wire['volume']*.012):
                continue
            patch = transformed(wire['body'], wire['chart']['inverse'])
            intrusion = volume(intersect(patch, solids[cell]))
            if intrusion <= .005:
                hits.append(dict(cell=cell, wire=wire['wire'], fill=patch, match_mm=residual,
                                 delta_mm3=delta, profile_size_difference_mm=size_error,
                                 nested_step_profile=small, preexisting_overlap_mm3=intrusion))
        if not hits:
            return None
        hits.sort(key=lambda row:(row['match_mm'], row['delta_mm3'], row['wire']))
        best = hits[0]
        for alternative in hits[1:]:
            if alternative['match_mm'] > best['match_mm']+.05:
                break
            if alternative['cell'] != best['cell']:
                return None
            a, b = best['fill'], alternative['fill']
            if oid=='O065':
                mat = next(w['chart']['matrix'] for w in wires if w['wire']==best['wire'])
                a,b = transformed(a,mat),transformed(b,mat)
            if volume(a)+volume(b)-2*common_prism_volume(a,b,thickness,oid=='O065') > 1e-5:
                return None
        parts.append(best)
    return parts


def mark_floor(oid, paths, registration, faces, asset, regions, solids):
    cell, fid = MARK_FLOORS[oid]
    region = regions[cell]
    evidence = asset['faces'][fid-1]
    normal = region['matrix'][:3,:3]@np.asarray(evidence['plane']['normal'])
    if abs(normal[2]) < .999999:
        raise ValueError('Mark floor is not parallel to its carrier skin')
    floor = TopoDS.Face_s(faces.FindKey(fid))
    level = float(apply(region['matrix'], evidence['centroid_mm'])[2])
    # OCCT support planes need not follow the oriented face normal, so use the
    # proven STEP face position relative to the two sheet surfaces.
    surface = region['high'] if oid == 'O001' else region['low']
    dz = surface-level
    if abs(abs(dz)-.3)>1e-6:
        raise ValueError('Mark depth differs from the measured STEP groove')
    endpoints = []
    for path in paths:
        for s in path['segments']:
            if s['segment_type'] != 'LINE':
                raise ValueError('Unexpected marking outline segment')
            endpoints.extend(([s['start_x'],s['start_y']], [s['end_x'],s['end_y']]))
    xy = np.asarray(endpoints)@np.asarray(registration['registration_matrix']).T+registration['registration_translation_mm']
    flat_face = transformed(floor,region['matrix'])
    bbox = np.asarray(bounds(flat_face))
    residual = float(np.max(np.abs(np.r_[xy.min(0),xy.max(0)]-bbox[[0,1,3,4]])))
    if residual > .5:
        raise ValueError('Registered DXF marking does not cover the STEP floor')
    flat_fill = BRepPrimAPI_MakePrism(flat_face,gp_Vec(0,0,dz)).Shape()
    patch = checked(transformed(flat_fill,region['inverse']))
    v = volume(patch)
    intrusion = volume(intersect(patch,solids[cell]))
    if abs(v-evidence['area_mm2']*abs(dz))>1e-5 or intrusion>1e-6:
        raise ValueError('STEP marking fill is inconsistent with its traced floor')
    return dict(cell=cell, floor_face_id=fid, fill=patch, match_mm=residual, groove_depth_mm=abs(dz),
                restored_volume_mm3=v, preexisting_overlap_mm3=intrusion)


def bend_relief(paths, registration, regions, flat_solids, root):
    if len(paths)!=1 or not paths[0]['closed']:
        raise ValueError('Expected a closed registered relief profile')
    chart = regions[root]
    thickness = chart['high']-chart['low']
    tool = source_prism(paths[0], registration, chart['high'], np.eye(4), [0,0,-thickness])
    bb = bounds(tool)
    nearby = {cid:s for cid,s in flat_solids.items() if boxes_intersect(bb,bounds(s))}
    if root not in nearby or len(nearby)<2:
        raise ValueError('Relief footprint does not straddle the registered material cells')
    retained = {cid:volume(intersect(s,tool)) for cid,s in nearby.items()}
    retained = {cid:v for cid,v in retained.items() if v>1e-7}
    fill = batch_boolean(BRepAlgoAPI_Cut,tool,list(nearby.values()))
    restored = volume(fill)
    if restored<=0 or abs(volume(tool)-restored-sum(retained.values()))>1e-5:
        raise ValueError('Restored relief volume overlaps the independently retained material')
    base = flat_solids[root]
    joined = fuse(base,fill)
    if indexed(joined,TopAbs_SOLID).Extent()!=1 or abs(volume(joined)-volume(base)-restored)>1e-5:
        raise ValueError('Restored relief does not attach to the base slab without overlap')
    return dict(cell=root,fill=transformed(fill,chart['inverse']),source_volume_mm3=volume(tool),
                restored_volume_mm3=restored,retained_volumes_mm3=retained,
                relieved_cells=sorted(nearby))


def derive_features(model, asset, solids, paths, registration):
    reg = dict(registration_matrix=registration['matrix'], registration_translation_mm=registration['translation_mm'])
    regions = charts(model, asset)
    thicknesses = {round(r['high']-r['low'],7) for r in regions.values()}
    if len(thicknesses)!=1 or min(thicknesses)<=0:
        raise ValueError('P004 staging requires one positive measured sheet thickness')
    thickness = next(iter(thicknesses))
    faces = source_faces(asset)
    wires = step_wires(faces, regions)
    ops = {o['operation_id']:o for o in model['operations']}
    removes = {oid for oid,op in ops.items() if op['operation_type'] in REMOVALS}
    forms = {oid for oid,op in ops.items() if op['operation_type'] in FORMS}
    pre = {ops[oid]['cad_parent_operation_id']:oid for oid in removes if ops[oid]['operation_type']=='PRE_PUNCH'}
    if set(pre)!={oid for oid in forms if ops[oid]['operation_type']=='FLANGING'}:
        raise ValueError('P004 pre-punch and flange prerequisites are incomplete')
    parent_cell = {j['operation_id']:j['child_cell_id'] for j in model['joints']}
    flat_solids = {cid:transformed(s,regions[cid]['matrix']) for cid,s in solids.items()}
    audit, patches, cutters = {}, {}, {}
    for oid in sorted(forms):
        op = ops[oid]
        owner = parent_cell.get(op.get('cad_parent_operation_id'))
        hints = {owner} if owner else set()
        try:
            if op['operation_type']=='MARKING':
                part = mark_floor(oid, paths[oid]['paths'], reg, faces, asset, regions, solids)
                cutters[oid] = [part]
                audit[oid] = dict(status='STEP_ENDPOINT', cell=part['cell'],
                    parts=[{key:value for key,value in part.items() if key!='fill'}])
                continue
            pre_paths = paths[pre[oid]]['paths'] if oid in pre else None
            patch = circular_patches(oid, paths[oid]['paths'], pre_paths, reg,regions,solids,asset,hints)
            if patch is None:
                raise ValueError('No unique circular STEP endpoint on its carrier')
            if oid in DIMPLE_FACES:
                ids = DIMPLE_FACES[oid]
                edge = asset['edges'][patch['match']['step_edge_id']-1]
                if set(ids).isdisjoint(edge['face_ids']):
                    raise ValueError('Matched edge differs from the independent dimple face evidence')
                patch['match']['step_face_ids'] = list(ids)
            patches[oid] = patch
            audit[oid] = dict(status='STEP_ENDPOINT',cell=patch['cell'],match=patch['match'],delta_mm3=patch['delta_mm3'])
            if oid in pre:
                audit[pre[oid]] = dict(status='PREHOLE_IN_FLAT_PATCH',cell=patch['cell'])
        except (RuntimeError, ValueError) as error:
            audit[oid] = dict(status='UNAPPLIED',reason=str(error))
    for oid in sorted(removes-set(pre.values())):
        owner = parent_cell.get(ops[oid].get('cad_parent_operation_id'))
        try:
            if oid in RELIEF_PROFILES:
                parts = [bend_relief(paths[oid]['paths'],reg,regions,flat_solids,model['root_cell_id'])]
                status = 'DXF_RELIEF_WITH_RETAINED_TONGUES'
            else:
                parts = match_cuts(oid,paths[oid]['paths'],reg,wires,solids,{owner} if owner else set(),thickness)
                status = 'RESTORE_STEP_HOLE'
            if not parts:
                raise ValueError('No unique STEP through-wire for the registered contour')
            cutters[oid] = parts
            audit[oid] = dict(status=status,
                parts=[{key:value for key,value in item.items() if key!='fill'} for item in parts])
        except (RuntimeError, ValueError) as error:
            audit[oid] = dict(status='UNAPPLIED',reason=str(error))
    crops = [(oid,row['cell'],row['mask']) for oid,row in patches.items()]
    crops.extend((oid,p['cell'],p['fill']) for oid,parts in cutters.items() for p in parts)
    boxes = {id(shape):bounds(shape) for _,_,shape in crops}
    conflicted = set()
    for (oid,cell,a),(other,other_cell,b) in combinations(crops,2):
        if oid!=other and cell==other_cell and boxes_intersect(boxes[id(a)],boxes[id(b)]) and volume(intersect(a,b))>1e-6:
            conflicted.update((oid,other))
    for oid in conflicted:
        patches.pop(oid,None)
        cutters.pop(oid,None)
        audit[oid] = dict(status='UNAPPLIED',reason='Overlapping independently timed endpoint crops')
        if oid in pre:
            audit.pop(pre[oid],None)
    for oid in removes|forms:
        audit.setdefault(oid,dict(status='UNAPPLIED',reason='Owning flange endpoint unresolved'))
    return removes,forms,pre,patches,cutters,audit


def material_states(model, plans, asset, solids, paths, registration, out, geometries):
    removals, forms, pre, patches, cutters, audit = derive_features(model,asset,solids,paths,registration)
    unsupported = {o for o,row in audit.items() if row['status']=='UNAPPLIED'}
    write(out/'material_audit.json',dict(operations=audit,unapplied_operation_ids=sorted(unsupported),
          counts=dict(Counter(row['status'] for row in audit.values())),source_step_sha256=asset['asset_id']))
    print('P004 staged features',len(audit)-len(unsupported),'/',len(audit),'unapplied',sorted(unsupported),flush=True)
    orders = {s['station_id']:s['order'] for s in model['stations']}
    bends = [o['operation_id'] for o in model['operations'] if o['operation_type']=='BEND']
    for plan in plans:
        assignment = plan['assignments']
        first_bend = min(orders[assignment[oid]] for oid in bends)
        if any(orders[assignment[oid]]>=first_bend for oid in RELIEF_PROFILES):
            raise ValueError('A flat relief restoration remains scheduled during bending')
    records = event_schedule(plans, model['stations'], removals, forms, pre)
    variants, cache = {}, {}
    for record in records:
        sig = tuple(record['executed_removal_ids']), tuple(record['executed_forming_ids'])
        if sig not in variants:
            done = set(sig[0])|set(sig[1])
            cells = {}
            for cid,base in solids.items():
                active_forms = tuple((oid,pre.get(oid) in done) for oid,p in patches.items()
                                     if p['cell']==cid and oid not in done)
                active_fills = tuple((oid,i) for oid,parts in cutters.items() for i,p in enumerate(parts)
                                     if p['cell']==cid and oid not in done)
                if not active_forms and not active_fills:
                    cells[cid] = cid
                    continue
                key = cid,active_forms,active_fills
                if key not in cache:
                    masks, restored = [], []
                    for oid, has_pre in active_forms:
                        p = patches[oid]
                        masks.append(p['mask'])
                        restored.append(subtract(p['flat'],[p['pre_tool']]) if has_pre else p['flat'])
                    restored.extend(cutters[oid][i]['fill'] for oid,i in active_fills)
                    shape = batch_boolean(BRepAlgoAPI_Cut,base,masks)
                    shape = batch_boolean(BRepAlgoAPI_Fuse,shape,restored)
                    if volume(shape)<=0 or indexed(shape,TopAbs_SOLID).Extent()!=1:
                        raise ValueError(f'P004 {cid} staged material is not a single valid solid')
                    gid = f'{cid}_material_{len(cache):03d}'
                    save_shape(out/'brep'/f'{gid}.brep',shape)
                    geometries[gid] = mesh(shape,CHECK_CONFIG)
                    cache[key] = gid
                cells[cid] = cache[key]
            variants[sig] = dict(material_id=f'M{len(variants):03d}',cells=cells,pending_unmodelled=sorted(unsupported-done))
        record['material_id'] = variants[sig]['material_id']
    write(out/'material_states.json',dict(records=records,variants=list(variants.values())))
    return variants
