"""Rigid-region straight-hinge prototype with explicit model boundaries.

NOT an inferred P001 motion model. Requires confirmed material connectivity,
reference geometry, angle convention and trajectories. Evaluates all joint
angles at the same station parameter before composing material transforms.
"""
import math
import numpy as np


def rotation_about_axis(point, direction, angle_deg):
    p, d = np.asarray(point, dtype=float), np.asarray(direction, dtype=float)
    if p.shape != (3,) or d.shape != (3,) or not np.isfinite(np.r_[p,d,angle_deg]).all() or not np.linalg.norm(d):
        raise ValueError('Finite axis and angle required')
    x,y,z = d/np.linalg.norm(d)
    k = np.array([[0,-z,y],[z,0,-x],[-y,x,0]])
    angle = math.radians(angle_deg)
    r = np.eye(3)+math.sin(angle)*k+(1-math.cos(angle))*(k@k)
    result = np.eye(4)
    result[:3,:3], result[:3,3] = r, p-r@p
    return result


def transform_point(matrix, point):
    return (np.asarray(matrix)@np.r_[point,1])[:3].tolist()


def station_state(regions, angle_functions, tau, global_pose=None):
    """Angles are relative to the recorded reference, NOT final inner angles.

    region keys: region_id, parent_region_id, reference_transform (region to
    parent at zero joint angle), joint (axis in parent reference frame),
    operation_ids. Region graph may be a forest. No operation order is used.
    """
    if not math.isfinite(tau) or not 0<=tau<=1:
        raise ValueError('tau must be in [0,1]')
    index = {r['region_id']: r for r in regions}
    if len(index) != len(regions):
        raise ValueError('Duplicate region id')
    if set(angle_functions)-set(index):
        raise ValueError('Trajectory references missing region')
    angles = {key: fn(tau) for key,fn in angle_functions.items()}
    if not all(math.isfinite(v) for v in angles.values()):
        raise ValueError('Nonfinite motion')
    world = np.eye(4) if global_pose is None else np.asarray(global_pose, dtype=float)
    def valid_pose(matrix):
        return matrix.shape==(4,4) and np.isfinite(matrix).all() and np.allclose(matrix[3],[0,0,0,1]) and np.allclose(matrix[:3,:3].T@matrix[:3,:3],np.eye(3)) and np.isclose(np.linalg.det(matrix[:3,:3]),1)
    if not valid_pose(world):
        raise ValueError('Invalid global rigid pose')
    resolved, active = {}, set()
    def visit(key):
        if key in resolved:
            return resolved[key]
        if key in active:
            raise ValueError('Cyclic material connectivity')
        if key not in index:
            raise ValueError('Missing material parent')
        active.add(key)
        region = index[key]
        reference = np.asarray(region.get('reference_transform',np.eye(4)),dtype=float)
        if not valid_pose(reference):
            raise ValueError('Invalid region reference pose')
        parent = visit(region['parent_region_id']) if region.get('parent_region_id') else world
        joint = region.get('joint')
        if joint and key not in angles:
            raise ValueError(f'Missing trajectory for {key}; unknown is not stationary')
        if key in angles and not joint:
            raise ValueError('Trajectory without a joint')
        move = rotation_about_axis(joint['axis_point_mm'],joint['axis_direction'],angles[key]) if joint else np.eye(4)
        resolved[key] = parent@move@reference
        active.remove(key)
        return resolved[key]
    for key in index:
        visit(key)
    return {'tau':tau,'execution':'SIMULTANEOUS','model':'RIGID_REGIONS_STRAIGHT_HINGES',
            'transforms':{k:v.tolist() for k,v in resolved.items()},
            'angles_deg':angles,'geometry_check_status':'NOT_EVALUATED',
            'limitations':['No bend-zone deformation, springback, cutting topology or tools.']}


def readiness(facts, bindings, motion_definitions=None, initial_material=None):
    """Fail closed on missing information; this function does not reject a plan."""
    motion_definitions = motion_definitions or {}
    missing_bindings = [o['operation_id'] for o in facts['operations']
                        if bindings.get(o['operation_id'],{}).get('status')!='CONFIRMED']
    deforming = {'BEND','FLANGING','DIMPLE','BRIDGE','DRAW_FORM'}
    missing_motion = [o['operation_id'] for o in facts['operations']
                      if o['operation_type'] in deforming and o['operation_id'] not in motion_definitions]
    reasons = []
    if initial_material is None:
        reasons.append('INITIAL_MATERIAL_UNKNOWN')
    if missing_bindings:
        reasons.append('OPERATION_BINDINGS_UNCONFIRMED')
    if missing_motion:
        reasons.append('DEFORMATION_MODELS_UNKNOWN')
    # Coverage alone is necessary but not a complete physical validation.
    return {'ready_for_candidate_3d_filter':False, 'status':'UNKNOWN',
            'blockers':reasons or ['FULL_MODEL_VALIDATION_REQUIRED'],
            'missing_bindings':missing_bindings,'missing_motion':missing_motion,
            'meaning':'Data/model readiness; not an infeasibility verdict about any plan.'}
