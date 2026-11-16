"""Rebuild material event states for arbitrary supported complete allocations.

Uses the audited, disjoint v0.3 material domains and exact removal tools.
No per-plan legacy material lookup and no order inside a station.
"""
from copy import deepcopy
from pathlib import Path
import os

from .local_forming import load_shape, save_shape, subtract
from .local_collision import volume
from .artifacts import read


def event_schedule(plans, stations, removals, forming, prerequisites):
    orders = {s['station_id']: s['order'] for s in stations}
    if len(orders) != len(stations) or len(set(orders.values())) != len(orders):
        raise ValueError('Duplicate station identities/orders')
    if len({p['plan_id'] for p in plans}) != len(plans):
        raise ValueError('Duplicate plan identities')
    records = []
    for plan in plans:
        a = plan['assignments']
        if not (set(removals) | set(forming)) <= a.keys() or not set(a.values()) <= orders.keys():
            raise ValueError('Missing event or unknown station')
        for form, pre in prerequisites.items():
            if orders[a[pre]] >= orders[a[form]]:
                raise ValueError('Pre-hole must strictly precede forming')
        for s in sorted(stations, key=lambda s: s['order']):
            for phase in ('entry', 'completed'):
                def executed(o):
                    return orders[a[o]] < s['order'] or (phase == 'completed' and orders[a[o]] == s['order'])
                records.append(dict(plan_id=plan['plan_id'], station_id=s['station_id'], phase=phase,
                    executed_removal_ids=sorted(o for o in removals if executed(o)),
                    executed_forming_ids=sorted(o for o in forming if executed(o))))
    return records


def rebuild(viewer, plans, source, destination, details=None):
    source, destination = Path(source), Path(destination)
    initial = [v for v in viewer['variants'] if not v['executed_removal_ids'] and not v['executed_forming_ids']]
    if len(initial) != 1:
        raise ValueError('Unique unprocessed material required')
    components = deepcopy(initial[0]['components'])
    if len({c['component_id'] for c in components}) != len(components):
        raise ValueError('Duplicate material component')
    coverage = viewer['removal_coverage']
    affected = {r['operation_id']: {c['component_id'] for c in r['components']} for r in coverage}
    tools = {o: load_shape(source/'brep'/f'tool_{o}.brep') for o in affected}
    forms = deepcopy(viewer['step_comparison'])
    flange = read(source/'flanging_step_inventory.json')['operations']
    prerequisites = {r['operation_id']: r['pre_punch_operation_id'] for r in flange}
    domains = {c['component_id']: load_shape(source/'brep'/('domain_'+c['component_id'].replace(':','_')+'.brep')) for c in components}
    from .local_forming import intersect
    extra_geometries={}
    for oid,p in (details or {}).items():
        rid=p['region_id'];key='DETAIL:'+oid
        if abs(volume(intersect(domains[rid],p['mask']))-volume(p['flat']))>1e-5:
            raise ValueError('Overlapping detail ownership')
        if any(volume(intersect(tool,p['mask']))>1e-7 for tool in tools.values()):
            raise ValueError('Detail crop intersects an independently scheduled punch')
        domains[rid]=subtract(domains[rid],[p['mask']]);domains[key]=p['flat']
        c=dict(component_id=key,kind='panel',region_id=rid,forming_operation_id=oid,local_forming=True)
        components.append(c)
        gid='DETAIL_G_'+oid;forms[oid]={**c,'geometry_id':gid}
        extra_geometries[gid]=dict(brep_file=f'detail_brep/{oid}_formed.brep',flat_volume_mm3=volume(p['formed']))
    records = event_schedule(plans, viewer['stations'], tools, forms, prerequisites)
    if any(not keys <= domains.keys() for keys in affected.values()):
        raise ValueError('Removal refers to missing material')
    # Formed patches may only replace their independently audited pre-hole.
    for o, c in forms.items():
        hits = {p for p, keys in affected.items() if c['component_id'] in keys}
        if hits != ({prerequisites[o]} if o in prerequisites else set()):
            raise ValueError('Unsupported removal through local forming patch')
    geometries, cache, variants, signatures = dict(extra_geometries), {}, [], {}
    for o,c in forms.items():
        if c['geometry_id'] in extra_geometries:continue
        g = deepcopy(viewer['geometries'][c['geometry_id']])
        g['brep_file'] = Path(os.path.relpath(source/g['brep_file'], destination)).as_posix()
        geometries[c['geometry_id']] = g
    def flat_geometry(key, cuts):
        sig = (key, tuple(o for o in cuts if key in affected[o]))
        if sig in cache:
            return cache[sig]
        shape = subtract(domains[key], [tools[o] for o in sig[1]])
        vol = volume(shape)
        gid = None
        if vol >= 1e-8:
            gid = f'ALLOC_G{len(cache):05d}'
            path = f'brep/{gid}.brep'
            save_shape(destination/path, shape)
            geometries[gid] = dict(brep_file=path, flat_volume_mm3=vol)
        cache[sig] = gid
        return gid
    for rec in records:
        cuts, formed = rec['executed_removal_ids'], rec['executed_forming_ids']
        sig = (tuple(cuts), tuple(formed))
        if sig not in signatures:
            result = []
            for c in components:
                key, foid = c['component_id'], c.get('forming_operation_id')
                fg = flat_geometry(key, cuts)
                gid = forms[foid]['geometry_id'] if foid in formed else fg
                if gid is None:
                    continue
                row = {**c, 'geometry_id':gid, 'flat_geometry_id':fg,
                       'flat_volume_mm3':geometries[gid]['flat_volume_mm3']}
                if foid:
                    row['forming_status'] = 'FORMED' if foid in formed else 'UNFORMED'
                result.append(row)
            mid = f'ALLOC_M{len(variants):03d}'
            signatures[sig] = mid
            variants.append(dict(material_id=mid, executed_removal_ids=cuts,
                executed_forming_ids=formed, components=result,
                flat_volume_mm3=sum(c['flat_volume_mm3'] for c in result)))
            print('MATERIAL', mid, len(cuts), 'cuts', len(formed), 'forms', flush=True)
        rec['material_id'] = signatures[sig]
    rebuilt = {**viewer, 'plans':plans, 'variants':variants, 'material_states':records, 'geometries':geometries,'step_comparison':forms,
        'summary':{**viewer['summary'], 'plan_count':len(plans), 'material_variant_count':len(variants),
                   'diagnostic_brep_only':True}}
    return rebuilt


def compare_materials(viewer, source, rebuilt, destination, details=None):
    """Independent symmetric-difference check for every shared event signature.

Includes unprocessed and all-completed material; tests rebuilt Boolean results,
not just volume totals or copied material IDs.
"""
    details=details or {}
    def sig(v): return (tuple(sorted(v['executed_removal_ids'])), tuple(sorted(set(v['executed_forming_ids'])-details.keys())))
    old = {sig(v):v for v in viewer['variants']}
    comparisons = []
    for v in rebuilt['variants']:
        if sig(v) not in old:
            continue
        a = {c['component_id']:c for c in old[sig(v)]['components']}
        b = {c['component_id']:c for c in v['components']}
        if a.keys() | {'DETAIL:'+o for o in details} != b.keys():
            raise ValueError('Rebuilt component coverage differs')
        max_error = 0.
        for key,c in a.items():
            pa = Path(source)/viewer['geometries'][c['geometry_id']]['brep_file']
            pb = Path(destination)/rebuilt['geometries'][b[key]['geometry_id']]['brep_file']
            if pa.resolve() == pb.resolve():
                continue
            sa,sb = load_shape(pa),load_shape(pb)
            sa=subtract(sa,[p['mask'] for p in details.values() if p['region_id']==key])
            error = volume(subtract(sa,[sb])) + volume(subtract(sb,[sa]))
            max_error = max(error,max_error)
            if error > 1e-5:
                raise ValueError(f'Material regression {v["material_id"]}/{key}: {error}')
        for oid,p in details.items():
            c=b['DETAIL:'+oid];actual=load_shape(Path(destination)/rebuilt['geometries'][c['geometry_id']]['brep_file'])
            expected=p['formed'] if oid in v['executed_forming_ids'] else p['flat']
            error=volume(subtract(actual,[expected]))+volume(subtract(expected,[actual]))
            if error>1e-5:raise ValueError('Wrong detail endpoint: '+oid)
            max_error=max(max_error,error)
        comparisons.append(dict(material_id=v['material_id'], old_material_id=old[sig(v)]['material_id'],
            components=len(a), maximum_symmetric_difference_mm3=max_error))
    last = max(rebuilt['stations'], key=lambda s:s['order'])['station_id']
    final_ids = {r['material_id'] for r in rebuilt['material_states'] if r['station_id']==last and r['phase']=='completed'}
    if len(final_ids)!=1 or not final_ids <= {r['material_id'] for r in comparisons}:
        raise ValueError('Final material not common and independently compared')
    return comparisons
