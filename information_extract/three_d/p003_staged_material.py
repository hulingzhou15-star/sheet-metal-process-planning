"""P003 stage bodies from the delivered STEP skins and registered flat drawing."""

from collections import Counter, defaultdict
from itertools import combinations

import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.BRepTools import BRepTools
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE, TopAbs_FORWARD, TopAbs_WIRE
from OCP.TopTools import TopTools_ListOfShape
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Vec

from .allocation_material_states import event_schedule
from .artifacts import ROOT, sha, write
from .assets import bounds
from .band_material_solids import boxes_intersect, checked
from .local_collision import indexed, transformed, volume
from .local_forming import cylinder, intersect, save_shape, subtract
from .material_removal import source_prism
from .meshing import mesh
from .p002_motion_reference import CHECK_CONFIG


REMOVALS = {'CIRCLE_PUNCH', 'IRREGULAR_PUNCH', 'PRE_PUNCH'}
FORMS = {'DIMPLE', 'FLANGING'}
STEP = ROOT/'information_extract/sources/P003/originals/P003.stp'


def apply(matrix, points):
    return np.asarray(points) @ matrix[:3, :3].T + matrix[:3, 3]


def charts(model, asset):
    rows = {}
    for cell in model['cells']:
        cid = cell['cell_id']
        if cell['region_ids'] != [cid]:
            raise ValueError('P003 stage reconstruction requires a unique planar skin pair per cell')
        skins = [int(x) for x in cid[1:].split('_')]
        matrix = np.array(model['flat_transforms'][cid])
        faces = [asset['faces'][fid-1] for fid in skins]
        z_values = [float(apply(matrix, f['centroid_mm'])[2]) for f in faces]
        if any(abs((matrix[:3,:3] @ f['plane']['normal'])[2]) < .999999 for f in faces):
            raise ValueError('An unfolded skin is not horizontal')
        order = np.argsort(z_values)
        lo, hi = (z_values[i] for i in order)
        rows[cid] = {'cell': cid, 'matrix': matrix, 'inverse': np.linalg.inv(matrix),
                     'low': lo, 'high': hi, 'skins': [skins[i] for i in order]}
    return rows


def batch_boolean(kind, shape, tools):
    if not tools:
        return shape
    arguments, operands = TopTools_ListOfShape(), TopTools_ListOfShape()
    arguments.Append(shape)
    for tool in tools:
        operands.Append(tool)
    op = kind()
    op.SetArguments(arguments)
    op.SetTools(operands)
    op.SetFuzzyValue(1e-7)
    op.Build()
    if not op.IsDone():
        raise ValueError('P003 stage Boolean failed')
    return checked(op.Shape())


def step_wires(step, regions):
    rows = []
    for cid,r in regions.items():
        for fid in r['skins']:
            face = TopoDS.Face_s(step.FindKey(fid))
            outer = BRepTools.OuterWire_s(face)
            wires = indexed(face,TopAbs_WIRE)
            for k in range(1,wires.Extent()+1):
                w = TopoDS.Wire_s(wires.FindKey(k))
                if w.IsSame(outer):
                    continue
                filled = BRepBuilderAPI_MakeFace(TopoDS.Wire_s(w.Oriented(TopAbs_FORWARD)),True)
                if not filled.IsDone():
                    continue
                flat = transformed(filled.Face(),r['matrix'])
                dz = r['low']-r['high']
                if fid == r['skins'][0]:
                    dz = -dz
                blank = BRepPrimAPI_MakePrism(flat,gp_Vec(0,0,dz)).Shape()
                rows.append(dict(cell=cid,wire=f'{fid}/{k}',body=blank,bb=np.array(bounds(blank)),
                                 volume=volume(blank),chart=r))
    return rows


def exact_cuts(op, paths, reg, wires, solids, hints):
    parts = []
    for path in paths:
        if not path['closed']:
            return None
        tool = source_prism(path,reg,0,np.eye(4),[0,0,-1.2])
        box = np.array(bounds(tool))
        size = box[3:5]-box[:2]
        center = (box[:2]+box[3:5])/2
        hits = []
        for w in wires:
            if hints and w['cell'] not in hints:
                continue
            bb = w['bb']
            shift = (bb[:2]+bb[3:5])/2-center
            distance = float(np.linalg.norm(shift))
            if distance>2.0:
                continue
            discrepancy = op=='O112'
            if not discrepancy and np.max(np.abs(bb[3:5]-bb[:2]-size))>.055:
                continue
            if discrepancy and (distance>.08 or np.max(bb[3:5]-bb[:2])>1.6*np.max(size)):
                continue
            placement = np.eye(4)
            placement[:3,3] = [*shift,bb[5]-box[5]]
            aligned = transformed(tool,placement)
            delta = max(0.,volume(aligned)+w['volume']-2*volume(intersect(aligned,w['body'])))
            if not discrepancy and delta > max(.08,w['volume']*.012):
                continue
            patch = transformed(w['body'],w['chart']['inverse'])
            intrusion = volume(intersect(patch,solids[w['cell']]))
            if intrusion>.005:
                continue
            hits.append(dict(cell=w['cell'],wire=w['wire'],fill=patch,match_mm=distance,
                             delta_mm3=delta,preexisting_overlap_mm3=intrusion,
                             shape_discrepancy=discrepancy))
        if not hits:
            return None
        hits.sort(key=lambda r:(r['match_mm'],r['delta_mm3'],r['wire']))
        best = hits[0]
        # Two complete skin wires of the same through hole can match equally.
        for other in hits[1:]:
            if other['match_mm'] > best['match_mm']+.05:
                break
            if other['cell'] != best['cell']:
                return None
            vb, vo = volume(best['fill']),volume(other['fill'])
            error = max(0.,vb+vo-2*volume(intersect(best['fill'],other['fill'])))
            if error>1e-5:
                return None
        parts.append(best)
    return parts


def circular_patches(op, path, pre_path, reg, regions, solids, asset, hints):
    circles = [s for row in path for s in row['segments'] if s['segment_type']=='CIRCLE']
    if not circles:
        return None
    center = np.array(reg['registration_matrix']) @ [circles[0]['center_x'],circles[0]['center_y']]
    center += reg['registration_translation_mm']
    possible = []
    for cid,r in regions.items():
        if hints and cid not in hints:
            continue
        m = r['matrix']
        for edge in asset['circles']:
            if abs((m[:3,:3] @ edge['normal'])[2]) < .9999:
                continue
            xyz = apply(m,edge['center_mm'])
            if not r['low']-3.6 < xyz[2] < r['high']+3.6:
                continue
            dx = float(np.linalg.norm(xyz[:2]-center))
            dr = min(abs(s['radius']-edge['radius_mm']) for s in circles)
            if dx <= 2.0 and dr <= .7:
                possible.append((dx,dr,cid,edge,xyz))
    possible.sort(key=lambda q:(q[0],q[1]))
    for dx,dr,cid,edge,xyz in possible:
        r = regions[cid]
        m = r['matrix']
        cx,cy = xyz[:2]
        if any(q[2]!=cid and q[0]<dx+.05 for q in possible):
            return None
        radii = [e['radius_mm'] for e in asset['circles']
                 if abs((m[:3,:3]@e['normal'])[2])>.9999
                 and np.linalg.norm(apply(m,e['center_mm'])[:2]-xyz[:2])<1e-5
                 and r['low']-3.6<apply(m,e['center_mm'])[2]<r['high']+3.6]
        rad = max(radii)+.15
        low,high = r['low'],r['high']
        mask = transformed(cylinder(cx,cy,rad,low-3.6,high+3.6),r['inverse'])
        flat = transformed(cylinder(cx,cy,rad,low,high),r['inverse'])
        existing = intersect(solids[cid],mask)
        same = volume(intersect(existing,flat))
        delta = volume(existing)+volume(flat)-2*same
        if delta<.03 or same<.15*volume(flat):
            continue
        # The crop needs a full sheet collar to avoid restoring board edges.
        inside = transformed(cylinder(cx,cy,rad-.07,low,high),r['inverse'])
        collar = subtract(flat,[inside])
        if volume(collar)-volume(intersect(collar,solids[cid]))>5e-5:
            continue
        cutter = None
        if pre_path:
            seg = pre_path[0]['segments']
            if len(seg)!=1 or seg[0]['segment_type']!='CIRCLE' or seg[0]['radius']>=rad:
                return None
            cutter = transformed(cylinder(cx,cy,seg[0]['radius'],low,high),r['inverse'])
        return dict(cell=cid,mask=mask,flat=flat,pre_tool=cutter,
                    delta_mm3=delta,match=dict(step_edge_id=edge['edge_id'],center_residual_mm=dx,
                                               radius_residual_mm=dr,crop_radius_mm=rad))
    return None


def bend_relief(oid, paths, reg, model, regions, flat_solids):
    """Register a relief that crosses a bend rather than appearing as an inner wire."""
    binding = {'O109':'J297', 'O110':'J297', 'O111':'J203'}
    if oid not in binding or len(paths)!=1 or not paths[0]['closed']:
        return None
    joint = next(j for j in model['joints'] if j['joint_id']==binding[oid])
    parent,child = joint['parent_cell_id'],joint['child_cell_id']
    r = regions[parent]
    r2 = regions[child]
    if abs(r['high']-r2['high'])>1e-6 or abs(r['low']-r2['low'])>1e-6:
        return None
    section = source_prism(paths[0],reg,r['high'],np.eye(4),[0,0,r['low']-r['high']])
    bb = np.array(bounds(section))
    ends = np.asarray(joint['developed_axis_endpoints_mm'])[:,:2]
    # P003 relief axes are parallel to X/Y in the isolated unfolded chart.
    along = int(np.argmax(abs(ends[1]-ends[0])))
    across = 1-along
    extent = sorted(ends[:,along])
    axis = float(np.mean(ends[:,across]))
    if (np.ptp(ends[:,across])>1e-6 or not bb[across] < axis < bb[3+across]
        or min(extent[1],bb[3+along])-max(extent[0],bb[along])<=0):
        return None
    nearby = {cid:s for cid,s in flat_solids.items() if boxes_intersect(bb,bounds(s))}
    if set(nearby)!={parent,child}:
        return None
    retained = {cid:volume(intersect(section,s)) for cid,s in nearby.items()}
    fill = batch_boolean(BRepAlgoAPI_Cut,section,list(nearby.values()))
    if volume(fill)<=1e-6 or abs(volume(section)-volume(fill)-sum(retained.values()))>1e-5:
        return None
    if any(volume(intersect(fill,s))>1e-6 for s in nearby.values()):
        return None
    return [dict(cell=parent,fill=transformed(fill,r['inverse']),joint_id=joint['joint_id'],
                 profile_volume_mm3=volume(section),restored_volume_mm3=volume(fill),
                 retained_volumes_mm3=retained)]


def derive_features(model, asset, solids, paths, registration):
    reg = dict(registration_matrix=registration['matrix'],registration_translation_mm=registration['translation_mm'])
    regions = charts(model,asset)
    if sha(STEP)!=asset['asset_id']:
        raise ValueError('P003 STEP source has changed')
    reader = STEPControl_Reader()
    if reader.ReadFile(str(STEP))!=IFSelect_RetDone:
        raise ValueError('STEP read failure')
    reader.SetSystemLengthUnit(1.)
    if not reader.TransferRoots():
        raise ValueError('STEP transfer failure')
    faces = indexed(reader.OneShape(),TopAbs_FACE)
    if faces.Extent()!=asset['face_count']:
        raise ValueError('STEP face indices changed')
    wires = step_wires(faces,regions)
    ops = {o['operation_id']:o for o in model['operations']}
    removes = {o for o,r in ops.items() if r['operation_type'] in REMOVALS}
    forms = {o for o,r in ops.items() if r['operation_type'] in FORMS}
    pre = {ops[o]['cad_parent_operation_id']:o for o in removes if ops[o]['operation_type']=='PRE_PUNCH'}
    if len(pre)!=29 or set(pre) != {o for o in forms if ops[o]['operation_type']=='FLANGING'}:
        raise ValueError('P003 pre-punch/flanging facts no longer match')
    parent_cell = {j['operation_id']:j['child_cell_id'] for j in model['joints']}
    flat_solids = {cid:transformed(base,regions[cid]['matrix']) for cid,base in solids.items()}
    audit, patches, cutters = {}, {}, {}
    for oid in sorted(forms):
        op = ops[oid]
        candidate = parent_cell.get(op.get('cad_parent_operation_id'))
        try:
            patch = circular_patches(oid,paths[oid]['paths'],paths[pre[oid]]['paths'] if oid in pre else None,
                                     reg,regions,solids,asset,{candidate} if candidate else set())
        except Exception as exc:
            audit[oid] = dict(status='UNAPPLIED',reason=str(exc))
            continue
        if patch is None:
            audit[oid] = dict(status='UNAPPLIED',reason='No uniquely supported bounded STEP endpoint')
            continue
        patches[oid] = patch
        audit[oid] = dict(status='STEP_ENDPOINT',cell=patch['cell'],match=patch['match'],delta_mm3=patch['delta_mm3'])
        if oid in pre:
            audit[pre[oid]] = dict(status='PREHOLE_IN_FLAT_PATCH',cell=patch['cell'])
    for oid in sorted(removes-set(pre.values())):
        candidate = parent_cell.get(ops[oid].get('cad_parent_operation_id'))
        try:
            parts = exact_cuts(oid,paths[oid]['paths'],reg,wires,solids,{candidate} if candidate else set())
            if not parts:
                parts = bend_relief(oid,paths[oid]['paths'],reg,model,regions,flat_solids)
        except Exception as exc:
            audit[oid] = dict(status='UNAPPLIED',reason=str(exc))
            continue
        if not parts:
            audit[oid] = dict(status='UNAPPLIED',reason='No unique STEP inner wire for registered outline')
            continue
        cutters[oid] = parts
        status = ('RESTORE_STEP_SLOT_BY_LOCATION' if oid=='O112' else
                  'DXF_RELIEF_WITH_RETAINED_TONGUES' if 'joint_id' in parts[0] else 'RESTORE_STEP_HOLE')
        audit[oid] = dict(status=status,
                          parts=[{k:v for k,v in p.items() if k!='fill'} for p in parts])
    # Events on the same cell must be spatially distinct for endpoint replacement.
    masks = [(oid,p['cell'],p['mask']) for oid,p in patches.items()]
    masks.extend((o,p['cell'],p['fill']) for o,parts in cutters.items() for p in parts)
    bounds_by_op = {id(s):bounds(s) for _,_,s in masks}
    blocked = defaultdict(list)
    for (a,ca,sa),(b,cb,sb) in combinations(masks,2):
        if a!=b and ca==cb and boxes_intersect(bounds_by_op[id(sa)],bounds_by_op[id(sb)]):
            if volume(intersect(sa,sb))>1e-6:
                blocked[a].append(b)
                blocked[b].append(a)
    for oid,others in blocked.items():
        cutters.pop(oid,None)
        patches.pop(oid,None)
        audit[oid] = dict(status='UNAPPLIED',reason=f'Overlapping independently timed crops: {sorted(others)}')
        if oid in pre:
            audit.pop(pre[oid],None)
    for oid in removes|forms:
        audit.setdefault(oid,dict(status='UNAPPLIED',reason='Owning flange endpoint unresolved'))
    return removes,forms,pre,patches,cutters,audit


def material_states(model,plans,asset,solids,paths,registration,out,geometries):
    removes,forms,pre,patches,cutters,audit = derive_features(model,asset,solids,paths,registration)
    unsupported = {o for o,r in audit.items() if r['status']=='UNAPPLIED'}
    write(out/'material_audit.json',dict(operations=audit,unapplied_operation_ids=sorted(unsupported),
          counts=dict(Counter(row['status'] for row in audit.values())),source_step_sha256=asset['asset_id']))
    print('P003 staged features',len(audit)-len(unsupported),'/',len(audit),'unapplied',sorted(unsupported),flush=True)
    records = event_schedule(plans,model['stations'],removes,forms,pre)
    # Relief proxies straddle a not-yet-formed bend. Their material must
    # disappear while both neighbouring regions still have the flat pose.
    station_order = {s['station_id']:s['order'] for s in model['stations']}
    bend_ops = [o['operation_id'] for o in model['operations'] if o['operation_type']=='BEND']
    for plan in plans:
        assignment = plan['assignments']
        first_bend = min(station_order[assignment[o]] for o in bend_ops)
        if any(station_order[assignment[o]] >= first_bend for o,row in audit.items()
               if row['status']=='DXF_RELIEF_WITH_RETAINED_TONGUES'):
            raise ValueError('Fold-start before relief removal invalidates its flat material proxy')
    variants,cache = {},{}
    for record in records:
        sig = tuple(record['executed_removal_ids']),tuple(record['executed_forming_ids'])
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
                    masks,restored = [],[]
                    for oid,has_pre in active_forms:
                        p = patches[oid]
                        masks.append(p['mask'])
                        restored.append(subtract(p['flat'],[p['pre_tool']]) if has_pre else p['flat'])
                    restored.extend(cutters[oid][i]['fill'] for oid,i in active_fills)
                    shape = batch_boolean(BRepAlgoAPI_Cut,base,masks)
                    shape = batch_boolean(BRepAlgoAPI_Fuse,shape,restored)
                    if volume(shape)<=0:
                        raise ValueError('Staged material cell is empty')
                    gid = f'{cid}_material_{len(cache):03d}'
                    save_shape(out/'brep'/f'{gid}.brep',shape)
                    geometries[gid] = mesh(shape,CHECK_CONFIG)
                    cache[key] = gid
                cells[cid] = cache[key]
            variants[sig] = dict(material_id=f'M{len(variants):03d}',cells=cells,
                                 pending_unmodelled=sorted(unsupported-done))
        record['material_id'] = variants[sig]['material_id']
    write(out/'material_states.json',dict(records=records,variants=list(variants.values())))
    return variants
