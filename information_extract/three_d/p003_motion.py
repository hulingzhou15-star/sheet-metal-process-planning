"""P003 cumulative rigid-hinge motion from the registered STEP skeleton."""
import math

import numpy as np

from .state import station_state

REMOVALS = {'CIRCLE_PUNCH', 'IRREGULAR_PUNCH', 'PRE_PUNCH'}
FORMS = {'FLANGING', 'DIMPLE', 'DRAW_FORM', 'BRIDGE', 'MARKING'}


def build_model(facts, skeleton, basis, partition):
    operations = {o['operation_id']:o for o in facts['operations']}
    domains = {d['operation_id']:d for d in basis['domains']}
    nodes = [dict(region_id=r['region_id'],parent_region_id=r['parent_region_id'])
             for r in skeleton['regions']]
    node_by_id = {r['region_id']:r for r in nodes}
    joints = []
    all_steps = set()
    for row in skeleton['joints']:
        oid = row['operation_id']
        jid = row['joint_id']
        associated = [d for d in domains.values() if d['joint_id']==jid]
        if len(associated)==1:
            if associated[0]['operation_id']!=oid:
                raise ValueError('Joint action does not match its material domain')
            increments = [dict(operation_id=oid,increment_deg=row['angle_deg'])]
            if not math.isclose(abs(row['angle_deg']),operations[oid]['bend_angle_deg'],abs_tol=1e-6):
                raise ValueError('Single-action angle differs from STEP')
        else:
            if oid!='O069' or {d['operation_id'] for d in associated}!={'O069','O120'}:
                raise ValueError('Unsupported shared physical joint')
            expected = [('O069',135),('O120',-45)]
            given = [(s['operation_id'],s['increment_deg']) for s in partition['shared_operation_steps']]
            if given!=expected or 'O069' not in operations['O120']['required_predecessor_ids']:
                raise ValueError('Shared joint step declaration changed')
            sign = math.copysign(1.,row['angle_deg'])
            increments = [dict(operation_id=i,increment_deg=sign*angle) for i,angle in given]
        for s in increments:
            if s['operation_id'] in all_steps:
                raise ValueError('Two physical joints assigned to the same action')
            all_steps.add(s['operation_id'])
        final = math.fsum(s['increment_deg'] for s in increments)
        if not math.isclose(final,row['angle_deg'],abs_tol=1e-6):
            raise ValueError('Cumulative motion fails to reach the STEP angle')
        j = {**row,'parent_cell_id':row['parent_region_id'],'child_cell_id':row['child_region_id'],
             'steps':increments,'completed_angle_deg':final}
        joints.append(j)
        node_by_id[j['child_cell_id']]['joint'] = dict(axis_point_mm=row['hinge_point_mm'],axis_direction=row['axis_direction'])
        node_by_id[j['child_cell_id']]['operation_id'] = oid
    if all_steps!={oid for oid,o in operations.items() if o['operation_type']=='BEND'}:
        raise ValueError('Incomplete bend action coverage')
    if {o['operation_type'] for o in facts['operations']}-{'BEND'}-REMOVALS-FORMS:
        raise ValueError('Nonbend operation lacks a material-event class')
    model = dict(part_id='P003',stations=facts['stations'],operations=facts['operations'],joints=joints,
        nodes=nodes,cells=[dict(cell_id=r['region_id'],region_ids=[r['region_id']]) for r in nodes],
        root_cell_id=skeleton['root_region_id'],curve_guides={},
        motion_method='LINEAR_SIGNED_INCREMENT_ON_SHARED_PHYSICAL_HINGE',
        material_method='PLAN_SCHEDULED_RIGID_MATERIAL_ENDPOINTS',
        manufacturing_safety_proved=False)
    zero = {oid:0. for oid in all_steps}
    model['flat_transforms'] = motion(model,zero)['transforms']
    end = motion(model,{oid:1. for oid in all_steps})['transforms']
    if any(not np.allclose(m,np.eye(4),atol=1e-10,rtol=0) for m in end.values()):
        raise ValueError('Final rigid poses are not the completed STEP reference')
    return model


def motion(model, progress):
    signed = {}
    local = {}
    for j in model['joints']:
        actual = math.fsum(progress[s['operation_id']]*s['increment_deg'] for s in j['steps'])
        signed[j['joint_id']] = actual
        local[j['child_cell_id']] = lambda _, delta=actual-j['completed_angle_deg']:delta
    state = station_state(model['nodes'],local,0.)
    return dict(transforms=state['transforms'],joint_angles_deg=signed)


def checked_assignments(model, plan):
    a = plan['assignments']
    orders = {s['station_id']:s['order'] for s in model['stations']}
    if set(a)!={o['operation_id'] for o in model['operations']} or not set(a.values())<=orders.keys():
        raise ValueError('Plan assignments differ from operation/station scope')
    if orders[a['O069']]>=orders[a['O120']]:
        raise ValueError('O069 must strictly precede O120')
    return a,orders


def samples(model, plan, steps):
    if type(steps) is not int or steps < 2:
        raise ValueError('Two or more motion subdivisions required')
    a,orders = checked_assignments(model,plan)
    bend_stations = {a[o['operation_id']] for o in model['operations'] if o['operation_type']=='BEND'}
    for sid in sorted(orders,key=orders.get):
        yield sid,'entry',0.
        if sid in bend_stations:
            for n in range(1,steps):
                yield sid,'motion',n/steps
        yield sid,'completed',1.


def frame(model, plan, sid, phase, tau):
    a,orders = checked_assignments(model,plan)
    if sid not in orders or phase not in ('entry','motion','completed') or not math.isfinite(tau):
        raise ValueError('Unknown motion sample')
    if not ((phase=='entry' and tau==0.) or (phase=='completed' and tau==1.) or (phase=='motion' and 0<tau<1)):
        raise ValueError('Invalid phase/progress combination')
    now = orders[sid]
    progress = {oid:1. if orders[s]<now else tau if s==sid else 0. for oid,s in a.items()}
    done = {oid for oid,s in a.items() if orders[s]<now or (s==sid and phase=='completed')}
    removed = sorted(o['operation_id'] for o in model['operations']
                     if o['operation_type'] in REMOVALS and o['operation_id'] in done)
    formed = sorted(o['operation_id'] for o in model['operations']
                    if o['operation_type'] in FORMS and o['operation_id'] in done)
    return dict(station_id=sid,phase=phase,tau=tau,**motion(model,progress),curve_fractions={},
        current_operation_ids=sorted(oid for oid,s in a.items() if s==sid),
        executed_operation_ids=sorted(done),executed_removal_ids=removed,executed_forming_ids=formed,
        unknown_cells={},unknown_motion_pairs=[])
