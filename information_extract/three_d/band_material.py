"""Finite-strip geometric material states. Angles share one station parameter.

Constant thickness and CAD end sections are preserved. The input drawing's
developed lengths do NOT imply volume conservation; no forming verdict is made.
"""
import math
import numpy as np
from .state import rotation_about_axis, transform_point


def strip_point(bend, point, fraction):
    """Map a material XYZ point, with front at z=0 and thickness toward -z."""
    if not math.isfinite(fraction) or not 0<=fraction<=1:raise ValueError('Invalid fraction')
    start=np.asarray(bend['start_flat_mm']);d=np.asarray(bend['forward_flat']);e=np.asarray(bend['axis_flat'])
    p=np.asarray(point);v=p-start
    along=float(v@e);s=float(v@d);depth=-float(v[2])
    length=bend['width_mm'];u=s/length
    if not -1e-6<=u<=1+1e-6:raise ValueError('Material point outside bend strip')
    u=float(np.clip(u,0,1));angle=math.radians(bend['target_angle_deg'])*fraction
    alpha=angle*u;h=bend['geometric_offset_from_front_mm'];n=np.array([0.,0.,1.])
    dy=length*u*float(np.sinc(alpha/math.pi))
    dz=length*u*(alpha/2)*float(np.sinc(alpha/(2*math.pi)))**2
    now_normal=-math.sin(alpha)*d+math.cos(alpha)*n
    return start+along*e-h*n+dy*d+dz*n+(h-depth)*now_normal


def state_transforms(model,fractions):
    if set(fractions)!={b['joint_id'] for b in model['bends']}:raise ValueError('All bend fractions required exactly once')
    if any(not math.isfinite(v) or not 0<=v<=1 for v in fractions.values()):raise ValueError('Invalid bend fraction')
    result={model['root_region_id']:np.eye(4)}
    for b in model['bends']:
        if b['parent_region_id'] not in result:raise ValueError('Invalid material tree')
        alpha=b['target_angle_deg']*fractions[b['joint_id']]
        # cross(material forward, front normal) is the signed rotation axis.
        axis=np.cross(b['forward_flat'],[0.,0.,1.])
        relative=rotation_about_axis([0.,0.,0.],axis,alpha)
        flat_end=np.array(b['start_flat_mm'])+np.array(b['forward_flat'])*b['width_mm']
        current_end=strip_point(b,flat_end,fractions[b['joint_id']])
        relative[:3,3]=current_end-relative[:3,:3]@flat_end
        result[b['child_region_id']]=result[b['parent_region_id']]@relative
    return result


def station_fractions(model,assignments,orders,station_id,tau):
    if station_id not in orders or not math.isfinite(tau) or not 0<=tau<=1:raise ValueError('Invalid station/tau')
    current=orders[station_id];result={}
    for b in model['bends']:
        target=orders[assignments[b['operation_id']]]
        result[b['joint_id']]=1. if target<current else float(tau) if target==current else 0.
    return result


def map_points(model,component,points,fractions,transforms=None):
    ts=state_transforms(model,fractions) if transforms is None else transforms
    if component['kind']=='panel':
        m=ts[component['region_id']]
        return (np.asarray(points)@m[:3,:3].T+m[:3,3]).tolist()
    bend=next(b for b in model['bends'] if b['joint_id']==component['joint_id'])
    m=ts[bend['parent_region_id']]
    return [transform_point(m,strip_point(bend,p,fractions[bend['joint_id']])) for p in points]


def band_volume_ratio(bend,fraction,thickness):
    alpha=math.radians(bend['target_angle_deg'])*fraction
    return 1+alpha*(thickness/2-bend['geometric_offset_from_front_mm'])/bend['width_mm']
