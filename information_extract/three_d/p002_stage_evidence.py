"""Projection measurements, with no expert schedule input to candidate inference."""
import numpy as np


def distances(points, primitives):
    """Exact XY distance to finite LINE / three-point ARC / CIRCLE entities."""
    p=np.asarray(points,dtype=float);best=np.full(len(p),np.inf)
    for g in primitives:
        q=np.asarray(g['points'],dtype=float);kind=g['kind']
        if kind=='LINE':
            v=q[1]-q[0];vv=v@v
            t=np.clip((p-q[0])@v/vv,0,1) if vv>1e-20 else np.zeros(len(p))
            d=np.linalg.norm(p-q[0]-t[:,None]*v,axis=1)
        elif kind=='CIRCLE':d=np.abs(np.linalg.norm(p-q[0],axis=1)-g['radius'])
        elif kind=='ARC':
            # DXF evidence stores start, end, a point halfway along the arc.
            a,b,c=q
            try:ctr=np.linalg.solve(2*np.array([b-a,c-a]),np.array([b@b-a@a,c@c-a@a]))
            except np.linalg.LinAlgError:continue
            r=np.linalg.norm(a-ctr);angles=np.arctan2((q-ctr)[:,1],(q-ctr)[:,0])
            span=(angles[1]-angles[0])%(2*np.pi);mid=(angles[2]-angles[0])%(2*np.pi)
            theta=(np.arctan2((p-ctr)[:,1],(p-ctr)[:,0])-angles[0])%(2*np.pi)
            inside=theta<=span+1e-8 if mid<=span else theta>=span-1e-8
            d=np.where(inside,np.abs(np.linalg.norm(p-ctr,axis=1)-r),np.minimum(np.linalg.norm(p-a,axis=1),np.linalg.norm(p-b,axis=1)))
        else:continue
        best=np.minimum(best,d)
    return best


def registered_geometry(frame,registration):
    m=np.array(frame['matrix']);t=np.array(frame['translation'])
    rm=np.array(registration['matrix']);rt=np.array(registration['translation_mm'])
    return [dict(g,points=(((np.array(g['points'])-t)@m)@rm.T+rt).tolist()) for g in frame['targets']]


def fraction(orders,assignments,oid,station_id,tau):
    return 1. if orders[assignments[oid]]<orders[station_id] else tau if assignments[oid]==station_id else 0.
