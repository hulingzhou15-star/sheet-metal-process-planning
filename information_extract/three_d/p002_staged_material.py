"""Build P002 entry/completion variants from registered STEP features."""
from collections import defaultdict
from itertools import combinations

import numpy as np
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.BRepTools import BRepTools
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE, TopAbs_FORWARD, TopAbs_WIRE
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Vec

from .allocation_material_states import event_schedule
from .artifacts import ROOT,write
from .assets import bounds
from .local_collision import indexed, transformed, volume
from .local_forming import cylinder, fuse, intersect, save_shape, subtract
from .material_removal import source_prism
from .meshing import mesh
from .p002_curve_motion import bind_vertices, frame, sweep
from .p002_motion_reference import CHECK_CONFIG
from .state import rotation_about_axis
from .band_material_solids import checked, triangle_solid
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.TopTools import TopTools_ListOfShape

REMOVALS = {'CIRCLE_PUNCH','IRREGULAR_PUNCH','PRE_PUNCH'}
FORMS = {'DIMPLE','FLANGING','DRAW_FORM'}
THICKNESS = .8


def apply(matrix, points):
    return np.asarray(points) @ matrix[:3,:3].T + matrix[:3,3]


def charts(model, plan, asset):
    pose = frame(model,plan,model['stations'][0]['station_id'],'entry',0.)['transforms']
    rows = []
    for cell in model['cells']:
        cid = cell['cell_id']
        for region in cell['region_ids']:
            a,b = map(int,region[1:].split('_'))
            fa,fb = [asset['faces'][i-1] for i in (a,b)]
            if cid == 'C001' and region != 'P466_468' or region == 'P63_256':
                continue
            normal = np.asarray(fa['plane']['normal'])
            midpoint = (np.array(fa['plane']['point_mm'])+fb['plane']['point_mm'])/2
            if cid in model['curve_guides']:
                ref = model['curve_guides'][cid]
                pt = np.array(fa['centroid_mm'])[None]
                root,outward,_ = bind_vertices(pt,ref['guides'])
                outward = outward[0]
                if abs(normal[:2] @ outward) < .999:
                    continue
                axis_xy = root[0]+.4*outward
                point = [*axis_xy,-.4]
                tangent = np.array([-outward[1],outward[0],0.])
                mat = rotation_about_axis(point,tangent,ref['z_sign']*90)
                mat[:2,3] -= .4*outward
            else:
                mat = np.array(pose[cid])
            if abs((mat[:3,:3] @ normal)[2]) < .999:
                continue
            rows.append(dict(cell=cid,region=region,faces=(a,b),mat=mat,mid=float(apply(mat,midpoint)[2])))
    return rows


def step_wires(step, regions, faces):
    rows = []
    for item in regions:
        mat = item['mat']
        fid = max(item['faces'], key=lambda i: apply(mat, faces[i-1]['centroid_mm'])[2])
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
            flat = transformed(filled.Face(),mat)
            body = BRepPrimAPI_MakePrism(flat,gp_Vec(0,0,-THICKNESS)).Shape()
            bb = np.array(bounds(body))
            rows.append(dict(chart=item,wire=f'{fid}/{k}',shape=body,bb=bb,volume=volume(body)))
    return rows


def flat_prism(path,reg,z=0):
    return source_prism(path,reg,z,np.eye(4),[0,0,-THICKNESS])


def profile_crop(path,reg,margin,low,high):
    profile = flat_prism(path,reg)
    faces = indexed(profile,TopAbs_FACE)
    top = next(TopoDS.Face_s(faces.FindKey(i)) for i in range(1,faces.Extent()+1)
               if abs(bounds(faces.FindKey(i))[2])<1e-6)
    base = BRepTools.OuterWire_s(top)
    expanded = BRepOffsetAPI_MakeOffset(TopoDS.Wire_s(base.Oriented(TopAbs_FORWARD)))
    expanded.Perform(margin)
    if not expanded.IsDone():
        raise ValueError('Cannot offset STEP crop footprint')
    wires = indexed(expanded.Shape(),TopAbs_WIRE)
    if wires.Extent()!=1:
        raise ValueError('Ambiguous offset outline')
    face = BRepBuilderAPI_MakeFace(TopoDS.Wire_s(wires.FindKey(1)),True).Face()
    shift = np.eye(4)
    shift[2,3] = high
    return checked(BRepPrimAPI_MakePrism(transformed(face,shift),gp_Vec(0,0,low-high)).Shape())


def match_removal(paths, reg, wires, solids):
    found = []
    for path in paths:
        if not path['closed']:
            return None
        tool = flat_prism(path,reg)
        bb = np.array(bounds(tool))
        size = bb[3:5]-bb[:2]
        center = (bb[:2]+bb[3:5])/2
        hits = []
        for item in wires:
            box_ = item['bb']
            shift = (box_[:2]+box_[3:5])/2-center
            residual = float(np.linalg.norm(shift))
            if residual > 2.0 or max(abs(box_[3:5]-box_[:2]-size))>.055:
                continue
            mat = np.eye(4)
            mat[:3,3] = [*shift,box_[5]-bb[5]]
            aligned = transformed(tool,mat)
            delta = max(0.,volume(aligned)+item['volume']-2*volume(intersect(aligned,item['shape'])))
            if delta > max(.08,item['volume']*.012):
                continue
            chart = item['chart']
            patch = transformed(item['shape'],np.linalg.inv(chart['mat']))
            if volume(intersect(patch,solids[chart['cell']])) > .005:
                continue
            hits.append(dict(cell=chart['cell'],wire=item['wire'],fill=patch,match_mm=residual,delta_mm3=delta))
        if len(hits) != 1:
            return None
        found.extend(hits)
    return found


def local_form(op, paths, reg, asset, regions, solids, parent_cell, pre_path):
    oid = op['operation_id']
    kind = op['operation_type']
    segment_list = [s for p in paths for s in p['segments']]
    circles = [s for s in segment_list if s['segment_type']=='CIRCLE']
    hosts = [r for r in regions if r['cell']==parent_cell]
    if oid == 'O092':
        hosts = [r for r in regions if r['cell']=='C004']
    if kind == 'DRAW_FORM' and oid != 'O063' or oid in {'O070','O071'}:
        profiles = [p for p in paths if p['role']!='INTEGRATED_HOLE']
        footprint = max(profiles,key=lambda p:volume(flat_prism(p,reg)))
        host = hosts[0]
        mask = profile_crop(footprint,reg,.22,-3.5,11.5 if oid=='O105' else 3.5)
        slab = profile_crop(footprint,reg,.22,-.8,0)
        match = dict(mask_xy_bounds_mm=bounds(mask),footprint_path_id=footprint['path_id'])
    else:
        if not circles:
            return None
        center = np.array(reg['registration_matrix'])@np.array([circles[0]['center_x'],circles[0]['center_y']])+reg['registration_translation_mm']
        hits = []
        for host in hosts:
            mat = host['mat']
            for c in asset['circles']:
                if c['parameter_span_rad'] < 6.2 or abs((mat[:3,:3]@c['normal'])[2])<.999:
                    continue
                if min(abs(c['radius_mm']-s['radius']) for s in circles)>.36:
                    continue
                p = apply(mat,c['center_mm'])
                d = float(np.linalg.norm(p[:2]-center))
                if d <= 2. and abs(p[2]-host['mid']) < 4.8:
                    hits.append((d,host,p,c))
        if not hits:
            return None
        hits.sort(key=lambda t:t[0])
        dist,host,p,edge = hits[0]
        if any(h['region']!=host['region'] and d < dist+.05 for d,h,_,_ in hits[1:]):
            return None
        xy = p[:2]
        coaxial = [c['radius_mm'] for c in asset['circles']
                   if np.linalg.norm(apply(host['mat'],c['center_mm'])[:2]-xy)<1e-5
                   and abs((host['mat'][:3,:3]@c['normal'])[2])>.999
                   and abs(apply(host['mat'],c['center_mm'])[2]-host['mid'])<5]
        rad = max(coaxial)+.12
        mid = host['mid']
        mask = cylinder(*xy,rad,mid-4.4,mid+4.4)
        slab = cylinder(*xy,rad,mid-.4,mid+.4)
        match = dict(step_edge_id=edge['edge_id'],center_residual_mm=dist,crop_radius_mm=rad)
    inv = np.linalg.inv(host['mat'])
    mask, slab = transformed(mask,inv),transformed(slab,inv)
    completed = intersect(solids[host['cell']],mask)
    delta = volume(subtract(completed,[slab])) + volume(subtract(slab,[completed]))
    if delta < .02 or volume(completed) < 1:
        return None
    if volume(intersect(completed,slab)) < .02*volume(slab):
        return None
    pre_tool = None
    if pre_path:
        if oid in {'O070','O071'}:
            pre_tool = flat_prism(pre_path,reg)
        else:
            seg = pre_path['segments'][0]
            if len(pre_path['segments'])!=1 or seg['segment_type']!='CIRCLE' or seg['radius'] >= rad:
                return None
            pre_tool = transformed(cylinder(*xy,seg['radius'],mid-.4,mid+.4),inv)
    return dict(cell=host['cell'],mask=mask,flat=slab,pre_tool=pre_tool,
                delta_mm3=delta,match=match)


def bend_opening(oid, model, plans, solids, geometries, path, reg):
    """Restore the flat cutout while preserving its independently retained tongues."""
    protected_cells = {'O112': ('C005','C018','C019'), 'O115': ('C017',)}[oid]
    bends = {'O112': ('O069','O037','O017'), 'O115': ('O086',)}[oid]
    orders = {s['station_id']:s['order'] for s in model['stations']}
    for plan in plans:
        a = plan['assignments']
        if any(orders[a[oid]] >= orders[a[bend]] for bend in bends):
            return None
    flat = frame(model,plans[0],model['stations'][0]['station_id'],'entry',0.)
    cutout = flat_prism(path,reg)
    retained = []
    for cid in protected_cells:
        if cid in model['curve_guides']:
            ref = model['curve_guides'][cid]
            tri = np.array(geometries[cid]['triangles'])
            points = tri.reshape(-1,3)
            mapped = sweep(points,bind_vertices(points,ref['guides']),0.,ref['z_sign'])
            retained.append(triangle_solid(mapped.reshape(tri.shape)))
        else:
            retained.append(transformed(solids[cid],np.array(flat['transforms'][cid])))
    fill = checked(subtract(cutout,retained))
    if volume(intersect(fill,solids['C001'])) > 1e-6:
        return None
    if any(volume(intersect(fill,s)) > 1e-6 for s in retained):
        raise ValueError(f'{oid} restoration still intersects retained tongue')
    evidence = dict(status='DXF_CUTOUT_WITH_RETAINED_TONGUES',cell='C001',
        profile_volume_mm3=volume(cutout),restored_volume_mm3=volume(fill),
        retained_cells=list(protected_cells),before_bends=list(bends))
    return [dict(cell='C001',fill=fill)],evidence


def material_states(model,plans,asset,solids,paths,registration,out,geometries):
    reg = dict(registration_matrix=registration['matrix'],registration_translation_mm=registration['translation_mm'])
    regions = charts(model,plans[0],asset)
    reader = STEPControl_Reader()
    if reader.ReadFile(str(ROOT/'information_extract/sources/P002/originals/P002.stp'))!=IFSelect_RetDone:
        raise ValueError('STEP read failure')
    reader.SetSystemLengthUnit(1.)
    if not reader.TransferRoots():
        raise ValueError('STEP transfer failure')
    src_faces = indexed(reader.OneShape(),TopAbs_FACE)
    if src_faces.Extent()!=asset['face_count']:
        raise ValueError('STEP face indices changed')
    wires = step_wires(src_faces,regions,asset['faces'])
    ops = {o['operation_id']:o for o in model['operations']}
    removes = {o for o,r in ops.items() if r['operation_type'] in REMOVALS}
    forms = {o for o,r in ops.items() if r['operation_type'] in FORMS}
    relief_cuts = {'O070','O071'}
    pre = {ops[o]['cad_parent_operation_id']:o for o in removes if ops[o]['operation_type']=='PRE_PUNCH'}
    pre.update(O070='O113',O071='O114')
    parent_cells = {n.get('operation_id'):n['region_id'] for n in model['nodes'] if 'operation_id' in n}
    parent_cells.update({ref['operation_id']:cid for cid,ref in model['curve_guides'].items()})
    audited = {}
    patches = {}
    for oid in sorted(forms|relief_cuts):
        op = ops[oid]
        owner = parent_cells.get(op.get('cad_parent_operation_id'),'C001')
        try:
            patch = local_form(op,paths[oid]['paths'],reg,asset,regions,solids,owner,
                               paths[pre[oid]]['paths'][0] if oid in pre else None)
        except Exception as exc:
            audited[oid] = dict(status='UNAPPLIED',reason=str(exc))
            continue
        if patch is None:
            audited[oid] = dict(status='UNAPPLIED',reason='No unique bounded STEP relief crop')
            continue
        patches[oid] = patch
        audited[oid] = dict(status='STEP_ENDPOINT',cell=patch['cell'],match=patch['match'],delta_mm3=patch['delta_mm3'])
        if oid in pre:
            audited[pre[oid]] = dict(status='PREHOLE_IN_FLAT_PATCH',cell=patch['cell'])
    cutters = {}
    used_wires = defaultdict(list)
    for oid in sorted(removes - set(pre.values()) - relief_cuts):
        try:
            candidates = match_removal(paths[oid]['paths'],reg,wires,solids)
        except Exception as exc:
            audited[oid] = dict(status='UNAPPLIED',reason=str(exc))
            continue
        if not candidates:
            audited[oid] = dict(status='UNAPPLIED',reason='Incomplete or nonunique exact-profile STEP wire match')
            continue
        cutters[oid] = candidates
        for row in candidates:
            used_wires[row['wire']].append(oid)
        audited[oid] = dict(status='RESTORE_STEP_HOLE',parts=[{k:v for k,v in r.items() if k!='fill'} for r in candidates])
    for duplicate in used_wires.values():
        if len(duplicate)>1:
            for oid in duplicate:
                cutters.pop(oid,None)
                audited[oid] = dict(status='UNAPPLIED',reason='Multiple actions compete for one STEP wire')
    for oid in ('O112','O115'):
        if oid in cutters:
            continue
        result = bend_opening(oid,model,plans,solids,geometries,paths[oid]['paths'][0],reg)
        if result is not None:
            cutters[oid],audited[oid] = result
    # Reject intersecting changes so simultaneous events cannot overwrite each other.
    masks = [(oid,p['cell'],p['mask']) for oid,p in patches.items()]
    masks += [(oid,p['cell'],p['fill']) for oid,rows in cutters.items() for p in rows]
    rejected = set()
    for (a,ca,sa),(b,cb,sb) in combinations(masks,2):
        if a==b or ca!=cb:
            continue
        ba,bb = np.array(bounds(sa)),np.array(bounds(sb))
        if np.all(ba[:3]<bb[3:]) and np.all(bb[:3]<ba[3:]) and volume(intersect(sa,sb))>1e-6:
            rejected.update((a,b))
    for oid in rejected:
        cutters.pop(oid,None)
        patches.pop(oid,None)
        audited[oid] = dict(status='UNAPPLIED',reason='Overlapping footprint with independently scheduled feature')
        if oid in pre:
            audited.pop(pre[oid],None)
    for oid in removes|forms:
        audited.setdefault(oid,dict(status='UNAPPLIED',reason='Owning flange endpoint unresolved'))
    records = event_schedule(plans,model['stations'],removes,forms,{o:p for o,p in pre.items() if o not in relief_cuts})
    variants, cache = {}, {}
    unsupported = {o for o,r in audited.items() if r['status']=='UNAPPLIED'}
    write(out/'material_audit.json',dict(operations=audited,unapplied_operation_ids=sorted(unsupported)))
    print('Material correspondences',len(audited)-len(unsupported),'/',len(audited),flush=True)
    for r in records:
        sig = tuple(r['executed_removal_ids']),tuple(r['executed_forming_ids'])
        if sig not in variants:
            done = set(sig[0])|set(sig[1])
            variants[sig] = dict(material_id=f'M{len(variants):03d}',cells={},
                                 pending_unmodelled=sorted(unsupported-done))
            for cid,solid in solids.items():
                active_forms = tuple((o,pre.get(o) in done) for o,p in patches.items() if p['cell']==cid and o not in done)
                fills = tuple((o,i) for o,parts in cutters.items() for i,p in enumerate(parts) if p['cell']==cid and o not in done)
                if not active_forms and not fills:
                    gid = cid
                else:
                    key = cid, active_forms, fills
                    if key not in cache:
                        shape = solid
                        for oid,has_pre in active_forms:
                            p = patches[oid]
                            flat = subtract(p['flat'],[p['pre_tool']]) if has_pre else p['flat']
                            shape = fuse(subtract(shape,[p['mask']]),flat)
                        if fills:
                            args, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
                            args.Append(shape)
                            for oid,i in fills:
                                tools.Append(cutters[oid][i]['fill'])
                            op = BRepAlgoAPI_Fuse()
                            op.SetArguments(args)
                            op.SetTools(tools)
                            op.Build()
                            if not op.IsDone():
                                raise ValueError('Hole restoration failed')
                            shape = checked(op.Shape())
                        gid = f'{cid}_material_{len(cache):03d}'
                        save_shape(out/'brep'/f'{gid}.brep',shape)
                        geometries[gid] = mesh(shape,CHECK_CONFIG)
                        cache[key] = gid
                    gid = cache[key]
                variants[sig]['cells'][cid] = gid
        r['material_id'] = variants[sig]['material_id']
    write(out/'material_states.json',dict(records=records,variants=list(variants.values())))
    return variants
