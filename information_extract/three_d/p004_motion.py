"""Synchronized P004 folds on the verified STEP material tree."""
from copy import deepcopy
from collections import defaultdict
import math

import numpy as np

from .p003_motion import FORMS, REMOVALS, motion


def build_model(facts, skeleton, basis):
    operations = {o['operation_id']:o for o in facts['operations']}
    bends = {oid for oid,o in operations.items() if o['operation_type']=='BEND'}
    domains = {row['operation_id']:row['joint_id'] for row in basis['domains']}
    nodes = {r['region_id']:dict(region_id=r['region_id'], parent_region_id=r['parent_region_id'])
             for r in skeleton['regions']}
    joints = []
    by_action = defaultdict(list)
    for row in skeleton['joints']:
        oid, jid = row['operation_id'], row['joint_id']
        if oid not in bends:
            raise ValueError('STEP joint and P004 fold assignments disagree')
        inner = operations[oid]['bend_angle_deg']
        turn = row['angle_deg']
        if not math.isclose(abs(turn), 180.-inner, abs_tol=.05, rel_tol=0):
            raise ValueError(f'{oid} has {inner}° annotated included angle but {abs(turn)}° STEP turn')
        parent, child = row['parent_region_id'], row['child_region_id']
        if nodes[child]['parent_region_id']!=parent or 'joint' in nodes[child]:
            raise ValueError('Material connectivity and joint orientation disagree')
        nodes[child]['joint'] = dict(axis_point_mm=row['hinge_point_mm'], axis_direction=row['axis_direction'])
        nodes[child]['operation_id'] = oid
        joints.append({**row, 'parent_cell_id':parent, 'child_cell_id':child, 'annotated_included_angle_deg':inner,
            'steps':[dict(operation_id=oid,increment_deg=turn)], 'completed_angle_deg':turn})
        by_action[oid].append(row)
    if by_action.keys()!=bends or set(domains)!=bends or len(joints)!=len(nodes)-1:
        raise ValueError('Incomplete P004 fold/material correspondence')
    for oid, rows in by_action.items():
        if domains[oid] not in {row['joint_id'] for row in rows}:
            raise ValueError(f'{oid} has no carried-volume joint in the motion tree')
        first = rows[0]
        axis = np.asarray(first['axis_direction'])
        point = np.asarray(first['hinge_point_mm'])
        for row in rows[1:]:
            if (row['parent_region_id']!=first['parent_region_id']
                    or np.linalg.norm(np.cross(np.asarray(row['hinge_point_mm'])-point,axis))>1e-5
                    or not np.allclose(np.asarray(row['axis_direction'])*row['angle_deg'],
                                       axis*first['angle_deg'],atol=1e-6,rtol=0)):
                raise ValueError(f'{oid} has inconsistent physical hinge supports')
    if {o['operation_type'] for o in operations.values()}-{'BEND'}-REMOVALS-FORMS:
        raise ValueError('Unsupported P004 material action')
    model = dict(part_id='P004', stations=facts['stations'], operations=facts['operations'], joints=joints,
        nodes=list(nodes.values()), cells=[dict(cell_id=cell,region_ids=[cell]) for cell in nodes],
        root_cell_id=skeleton['root_region_id'], curve_guides={},
        motion_method='SIMULTANEOUS_SIGNED_STEP_TURN',
        material_method='STATION_SCHEDULED_MATERIAL_ENDPOINTS', manufacturing_safety_proved=False)
    model['flat_transforms'] = motion(model,{oid:0. for oid in bends})['transforms']
    end = motion(model,{oid:1. for oid in bends})['transforms']
    if any(not np.allclose(pose,np.eye(4),atol=1e-10,rtol=0) for pose in end.values()):
        raise ValueError('Completed orientations do not reproduce the STEP reference')
    return model


def cylinder_axis_model(model, asset):
    """Construct the fixed-radius STEP axis alternative with independent coaxial checks."""
    shifted = deepcopy(model)
    nodes = {n['region_id']:n for n in shifted['nodes']}
    offsets = {}
    for j in model['joints']:
        point = np.asarray(j['hinge_point_mm'])
        axis = np.asarray(j['axis_direction'])
        evidence = [asset['faces'][e['cylinder_face_id']-1]['cylinder'] for e in j['surface_evidence']]
        points = []
        for cylinder in evidence:
            if not math.isclose(abs(np.dot(cylinder['direction'],axis)),1.,abs_tol=1e-6,rel_tol=0):
                raise ValueError(f"Inconsistent STEP axis direction for {j['joint_id']}")
            center = np.asarray(cylinder['point_mm'])
            points.append(center+axis*np.dot(point-center,axis))
        if not points or any(not np.allclose(p,points[0],atol=1e-5,rtol=0) for p in points):
            raise ValueError(f"Unresolved coaxial curves for {j['joint_id']}")
        nodes[j['child_cell_id']]['joint']['axis_point_mm'] = points[0].tolist()
        offsets[j['joint_id']] = (points[0]-point).tolist()
    return shifted, offsets


class Schedule:
    def __init__(self, model, plan):
        self.model = model
        self.assignment = plan['assignments']
        self.order = {s['station_id']:s['order'] for s in model['stations']}
        self.operations = {o['operation_id']:o['operation_type'] for o in model['operations']}
        if self.assignment.keys()!=self.operations.keys() or not set(self.assignment.values())<=self.order.keys():
            raise ValueError('P004 plan does not cover its operations and stations')
        self.bends = tuple(sorted(oid for oid,kind in self.operations.items() if kind=='BEND'))
        self.removals = tuple(sorted(oid for oid,kind in self.operations.items() if kind in REMOVALS))
        self.forming = tuple(sorted(oid for oid,kind in self.operations.items() if kind in FORMS))
        self.bend_stations = {self.assignment[oid] for oid in self.bends}
        self.ord = {oid:self.order[station] for oid,station in self.assignment.items()}
        self.current = {s:sorted(oid for oid,station in self.assignment.items() if station==s) for s in self.order}

    def samples(self, steps):
        if type(steps) is not int or steps<2:
            raise ValueError('At least two subdivisions are required')
        for station in sorted(self.order,key=self.order.get):
            yield station,'entry',0.
            if station in self.bend_stations:
                for i in range(1,steps):
                    yield station,'motion',i/steps
            yield station,'completed',1.

    def progress(self, station, tau):
        order = self.order[station]
        return tuple(1. if self.ord[oid]<order else tau if self.ord[oid]==order else 0. for oid in self.bends)

    def material_key(self, station, phase):
        limit = self.order[station]+(phase=='completed')
        return (tuple(oid for oid in self.removals if self.ord[oid]<limit),
                tuple(oid for oid in self.forming if self.ord[oid]<limit))

    def frame(self, station, phase, tau, pose=None):
        if not ((phase=='entry' and tau==0.) or (phase=='motion' and 0.<tau<1.)
                or (phase=='completed' and tau==1.)):
            raise ValueError('Invalid process sample')
        limit = self.order[station]+(phase=='completed')
        removal, forming = self.material_key(station,phase)
        if pose is None:
            pose = motion(self.model,dict(zip(self.bends,self.progress(station,tau))))
        return dict(station_id=station,phase=phase,tau=tau,current_operation_ids=self.current[station],
            executed_operation_ids=sorted(oid for oid in self.operations if self.ord[oid]<limit),
            executed_removal_ids=list(removal),executed_forming_ids=list(forming),curve_fractions={},
            unknown_cells={},unknown_motion_pairs=[],**pose)


def samples(model, plan, steps):
    return Schedule(model,plan).samples(steps)


def frame(model, plan, sid, phase, tau):
    return Schedule(model,plan).frame(sid,phase,tau)
