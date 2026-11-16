"""P002 completed-STEP reference under existing rigid-hinge kinematics.

This is deliberately NOT a staged material model. Unknown curved motion is
propagated to descendants; it never receives an invented stationary transform.
"""
from collections import Counter
from itertools import combinations
import math
import numpy as np

from .state import station_state
from .panel_collision import PanelChecker
from .local_forming import load_shape

CURVED=('O069','O088','O096','O104')
CHECK_CONFIG=dict(contact_tolerance_mm=1e-6,volume_tolerance_mm3=1e-6,
    relative_transform_cache_decimals=12,mesh_linear_deflection_mm=.12,mesh_angular_deflection_rad=.25)


def compile_model(cells,straight,proposals,facts):
    rows={c['cell_id']:c for c in cells['cells']}
    region_cell={r:c['cell_id'] for c in cells['cells'] for r in c['region_ids']}
    if sum(len(c['region_ids']) for c in cells['cells'])!=len(region_cell):raise ValueError('Duplicate region ownership')
    skeleton=straight['skeleton'];root=region_cell[skeleton['root_region_id']]
    mapping={r['joint_id']:r['operation_id'] for r in straight['association']['matches']}
    mapping.update({r['joint_id']:'O136' for r in cells['correspondence']['z_composite']})
    domains={r['operation_id']:r for r in cells['domains']}
    nodes={root:dict(region_id=root,parent_region_id=None)};joints=[]
    for j in skeleton['joints']:
        if j['joint_id'] not in mapping:continue # multiple curve hinges are NOT independent rigid motions
        child,parent=region_cell[j['child_region_id']],region_cell[j['parent_region_id']]
        if child in nodes:raise ValueError('Multiple hinges for one rigid cell')
        nodes[child]=dict(region_id=child,parent_region_id=parent,
            joint=dict(axis_point_mm=j['hinge_point_mm'],axis_direction=j['axis_direction']),
            joint_id=j['joint_id'],operation_id=mapping[j['joint_id']],target_angle_deg=j['angle_deg'])
        joints.append(dict(joint_id=j['joint_id'],operation_id=mapping[j['joint_id']],parent_cell_id=parent,child_cell_id=child,
            hinge_point_mm=j['hinge_point_mm'],axis_direction=j['axis_direction'],angle_deg=j['angle_deg']))
    for oid in CURVED:
        # A curve wall is one connected STEP body, not a set of rigid strips.
        ids=[c for c in domains[oid]['cell_ids'] if c not in nodes]
        if len(ids)!=1:raise ValueError('Ambiguous curve compound body')
        nodes[ids[0]]=dict(region_id=ids[0],parent_region_id=root,unknown_motion_operation_id=oid)
    if set(nodes)!=set(rows) or len(joints)!=14:raise ValueError('Incomplete STEP cell/joint coverage')
    for n in nodes.values():
        if n['parent_region_id'] is not None and n['parent_region_id'] not in nodes:raise ValueError('Missing parent cell')
    bend_ids={o['operation_id'] for o in facts['operations'] if o['operation_type']=='BEND'}
    if set(mapping.values())|set(CURVED)!=bend_ids:raise ValueError('Bend action coverage mismatch')
    return dict(part_id='P002',root_cell_id=root,cells=list(rows.values()),nodes=list(nodes.values()),joints=joints,
        curved_operation_ids=list(CURVED),stations=facts['stations'],operations=facts['operations'],
        motion_method='EXISTING_RIGID_MIDPLANE_HINGE_LINEAR_ANGLE',
        material_method='COMMON_COMPLETED_STEP_REFERENCE_ONLY',finite_bend_bands_included=False,
        same_station_semantics='Shared tau for every current action; no order within a station',
        z_policy='Both constituent hinges use O136 station and the same tau; no intra-action order inferred',
        limitations=['Four curved wall deformations unknown; unknown pose propagates to children',
            'No per-station removal or local-forming material switches; STEP completed reference throughout',
            'Rigid midplane hinge approximation; finite bend allowance not calibrated',
            'No finite bend bands, continuous local forming, tools or fixtures in pair checks'],
        full_process_model=False,manufacturing_safety_proved=False)


def make_samples(model,plan,steps):
    if type(steps)!=int or steps<2:raise ValueError('At least two subdivisions required')
    moving={plan['assignments'][o['operation_id']] for o in model['operations'] if o['operation_type']=='BEND'}
    rows=[]
    for s in model['stations']:
        sid=s['station_id'];rows.append((sid,'entry',0.))
        if sid in moving:rows.extend((sid,'motion',k/steps) for k in range(1,steps))
        rows.append((sid,'completed',1.))
    return rows


def frame(model,plan,station_id,phase,tau):
    orders={s['station_id']:s['order'] for s in model['stations']};a=plan['assignments']
    if station_id not in orders or phase not in ('entry','motion','completed') or not math.isfinite(tau) or not 0<=tau<=1:raise ValueError('Invalid sample')
    if phase=='entry' and tau!=0 or phase=='completed' and tau!=1:raise ValueError('Endpoint/phase mismatch')
    expected={o['operation_id'] for o in model['operations']}
    if set(a)!=expected or not set(a.values())<=orders.keys():raise ValueError('Incomplete assignment')
    current=orders[station_id]
    def completed(o):return orders[a[o]]<current or (phase=='completed' and orders[a[o]]==current)
    def fraction(o):return 1. if orders[a[o]]<current else (tau if orders[a[o]]==current else 0.)
    index={n['region_id']:n for n in model['nodes']};blocked={};visiting=set()
    def blockers(cid):
        if cid in blocked:return blocked[cid]
        if cid in visiting:raise ValueError('Cyclic cell topology')
        visiting.add(cid);n=index[cid];causes=set(blockers(n['parent_region_id'])) if n['parent_region_id'] else set()
        if 'unknown_motion_operation_id' in n and not completed(n['unknown_motion_operation_id']):causes.add(n['unknown_motion_operation_id'])
        visiting.remove(cid);blocked[cid]=sorted(causes);return blocked[cid]
    for cid in index:blockers(cid)
    known=[n for n in model['nodes'] if not blocked[n['region_id']]]
    functions={n['region_id']:(lambda t,n=n:(fraction(n['operation_id'])-1)*n['target_angle_deg']) for n in known if 'joint' in n}
    state=station_state(known,functions,tau)
    local_types={'FLANGING','DIMPLE','DRAW_FORM','BRIDGE','MARKING'};remove_types={'CIRCLE_PUNCH','PRE_PUNCH','IRREGULAR_PUNCH'}
    executed=sorted(o for o in expected if completed(o))
    return dict(station_id=station_id,phase=phase,tau=tau,transforms=state['transforms'],
        unknown_cells={c:why for c,why in blocked.items() if why},
        executed_operation_ids=executed,current_operation_ids=sorted(o for o in expected if orders[a[o]]==current),
        executed_removal_ids=sorted(o['operation_id'] for o in model['operations'] if o['operation_type'] in remove_types and completed(o['operation_id'])),
        executed_forming_ids=sorted(o['operation_id'] for o in model['operations'] if o['operation_type'] in local_types and completed(o['operation_id'])),
        unexecuted_material_event_ids=sorted(o['operation_id'] for o in model['operations'] if o['operation_type']!='BEND' and not completed(o['operation_id'])),
        executed_material_events_applied_to_geometry=False,
        joint_fractions={j['joint_id']:fraction(j['operation_id']) for j in model['joints']},
        material_scope='COMMON_COMPLETED_STEP_REFERENCE',process_check_status='INCOMPLETE_MODEL')


class ReferenceChecker:
    def __init__(self,model,geometry_root):
        self.model=model;self.solids={c['cell_id']:load_shape(geometry_root/c['brep_file']) for c in model['cells']}
        self.cache={};self.checkers={}
        self.adjacent=[(n['region_id'],n['parent_region_id']) for n in model['nodes'] if n['parent_region_id']]

    @property
    def kernel_calls(self):return sum(c.kernel_evaluations for c in self.checkers.values())

    def check(self,state):
        ids=tuple(sorted(state['transforms']))
        if set(ids)&set(state['unknown_cells']) or set(ids)|set(state['unknown_cells'])!=set(self.solids):raise ValueError('Pose coverage mismatch')
        if ids not in self.checkers:
            self.checkers[ids]=PanelChecker({c:self.solids[c] for c in ids},self.adjacent,CHECK_CONFIG)
            self.checkers[ids].cache=self.cache
        result=self.checkers[ids].check({c:np.asarray(m) for c,m in state['transforms'].items()})
        unknown_pairs=[list(p) for p in combinations(sorted(self.solids),2) if any(c in state['unknown_cells'] for c in p)]
        return dict(reference_geometry=result,unknown_motion_pairs=unknown_pairs,
            total_reference_pairs=len(self.solids)*(len(self.solids)-1)//2,
            overall_process_status='UNKNOWN_INCOMPLETE_MODEL',candidate_filter_enabled=False,
            manufacturing_safety_proved=False)


def run_plan(model,plan,checker,steps,progress=None):
    rows=[];counts=Counter();unknown=0
    for i,(sid,phase,tau) in enumerate(make_samples(model,plan,steps)):
        s=frame(model,plan,sid,phase,tau);check=checker.check(s)
        counts.update(check['reference_geometry']['counts']);unknown+=len(check['unknown_motion_pairs'])
        rows.append(dict(**s,**check))
        if progress and (phase=='completed' or i%24==0):progress(i+1,sid,phase,dict(counts))
    return dict(plan_id=plan['plan_id'],assignments=plan['assignments'],sample_count=len(rows),subdivisions=steps,runs=rows,
        summary=dict(reference_pair_states=sum(r['reference_geometry']['pair_count'] for r in rows),
            unknown_motion_pair_states=unknown,reference_counts=dict(counts),full_process_status='UNKNOWN_INCOMPLETE_MODEL'),
        full_process_collision_checked=False,material_scope=model['material_method'],manufacturing_safety_proved=False)
