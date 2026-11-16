"""DXF-calibrated O094 reference before O088 starts; curve motion fails closed."""
from collections import Counter
from copy import deepcopy
import numpy as np
from .artifacts import verify_sources
from .p002_motion_reference import frame as original_frame,make_samples
from .p002_stage_evidence import fraction
from .state import rotation_about_axis


def extend_model(model,evidence):
    verify_sources(evidence['inputs'])
    c=evidence['o094_carrier']
    if c['operation_id']!='O094' or c['cell_id']!='C007' or c['parent_operation_id']!='O088':raise ValueError('Wrong carrier')
    if len(c['free_rim_validation'])!=3 or max(r['max_residual_mm'] for r in c['free_rim_validation'])>1e-6:raise ValueError('Unverified stage endpoints')
    if c['independent_dimension']['residual_mm']>1e-6:raise ValueError('Side-view dimension mismatch')
    result=deepcopy(model)
    # Carry geometric parameters only; expert station identifiers never select states.
    result['o094_stage_reference']={k:deepcopy(c[k]) for k in ('operation_id','cell_id','parent_operation_id','local_joint',
        'unformed_parent_transform','initial_world_translation_correction_mm','trajectory_assumption','evidence_scope')}
    result['motion_method']='RIGID_HINGES_PLUS_DXF_CALIBRATED_O094_BEFORE_CURVE'
    result['limitations'][0]='Four curved deformations unknown; O094 has calibrated reference only while O088 has not started, or after O088 completes'
    result['limitations'].append('O094 endpoints validated against DXF; linear angle/compensation between endpoints is a reference assumption')
    return result


def frame(model,plan,station_id,phase,tau):
    state=original_frame(model,plan,station_id,phase,tau)
    cfg=model['o094_stage_reference'];orders={s['station_id']:s['order'] for s in model['stations']};a=plan['assignments']
    if orders[a['O094']]>=orders[a['O088']]:raise ValueError('O094 must precede O088 under existing H7')
    parent_fraction=fraction(orders,a,'O088',station_id,tau)
    if parent_fraction==0.:
        if state['unknown_cells'].get('C007')!=['O088']:raise ValueError('Unexpected local carrier dependencies')
        f=fraction(orders,a,'O094',station_id,tau);j=cfg['local_joint']
        pose=np.array(cfg['unformed_parent_transform'])@rotation_about_axis(j['hinge_point_mm'],j['axis_direction'],(f-1)*j['angle_deg'])
        pose[:3,3]+=(1-f)*np.array(cfg['initial_world_translation_correction_mm'])
        state['transforms']['C007']=pose.tolist();del state['unknown_cells']['C007']
        state['additional_reference_support']={'C007':dict(basis='DXF_STAGE_ENDPOINTS_WITH_ASSUMED_RIGID_INTERPOLATION',
            child_fraction=f,parent_fraction=parent_fraction,continuous_deformation_verified=False)}
    else:state['additional_reference_support']={}
    return state


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
