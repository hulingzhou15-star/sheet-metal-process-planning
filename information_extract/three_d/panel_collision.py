"""All-pair diagnostics for thick CAD panel envelopes under rigid kinematics.

No joints are exempt from overlap checks. Missing bend bands, filled holes and
zero-width hinges prevent interpreting results as physical collision verdicts.
"""
from itertools import combinations, product
from collections import Counter
import numpy as np
from .assets import bounds
from .local_collision import transformed, volume
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepCheck import BRepCheck_Analyzer


def corners(box):
    return np.array(list(product(*zip(box[:3], box[3:]))), dtype=float)


def transformed_bounds(box, matrix):
    points = corners(box) @ np.asarray(matrix)[:3,:3].T + np.asarray(matrix)[:3,3]
    return np.r_[points.min(axis=0),points.max(axis=0)].tolist()


def aabb_gap(a,b):
    """Conservative lower bound; zero is NEVER a collision verdict."""
    gap = np.maximum(0,np.maximum(np.asarray(a[:3])-b[3:],np.asarray(b[:3])-a[3:]))
    return float(np.linalg.norm(gap))


def narrow_pair(a,b,cfg):
    dist = BRepExtrema_DistShapeShape(a,b)
    if not dist.IsDone():
        raise ValueError('OCCT distance computation not done')
    d = float(dist.Value())
    if not np.isfinite(d):
        raise ValueError('Nonfinite distance')
    result = {'distance_mm':d,'intersection_volume_mm3':0.0,'status':'SEPARATED'}
    if d > cfg['contact_tolerance_mm']:
        return result
    common = BRepAlgoAPI_Common(a,b)
    if not common.IsDone() or not BRepCheck_Analyzer(common.Shape()).IsValid():
        raise ValueError('OCCT intersection failed or returned invalid topology')
    v = volume(common.Shape())
    if not np.isfinite(v):
        raise ValueError('Nonfinite intersection volume')
    result.update(intersection_volume_mm3=v,status='OVERLAP' if v>cfg['volume_tolerance_mm3'] else 'CONTACT')
    if result['status']=='OVERLAP':
        result['intersection_bounds_in_first_panel_frame_mm'] = bounds(common.Shape())
    return result


class PanelChecker:
    def __init__(self,solids,adjacent,cfg):
        self.solids=solids; self.ids=sorted(solids); self.cfg=cfg
        self.adjacent={frozenset(pair) for pair in adjacent}
        self.bounds={r:bounds(s) for r,s in solids.items()}
        self.cache={};self.kernel_evaluations=0

    def check(self,transforms):
        if set(transforms)!=set(self.ids):
            raise ValueError('Every supported panel transform must be supplied exactly once')
        if any(not np.isfinite(t).all() for t in transforms.values()):
            raise ValueError('Nonfinite panel transform')
        boxes={r:transformed_bounds(self.bounds[r],transforms[r]) for r in self.ids}
        inverses={r:np.linalg.inv(transforms[r]) for r in self.ids}
        rows=[];counts=Counter();reuse=0
        for a,b in combinations(self.ids,2):
            if aabb_gap(boxes[a],boxes[b]) > self.cfg['contact_tolerance_mm']:
                counts['AABB_SEPARATED']+=1
                continue
            relative=inverses[a]@transforms[b]
            key=(a,b,*np.round(relative,self.cfg['relative_transform_cache_decimals']).flat)
            if key not in self.cache:
                try:
                    result=narrow_pair(self.solids[a],transformed(self.solids[b],relative),self.cfg)
                except Exception as exc:
                    result={'status':'UNKNOWN_KERNEL_FAILURE','message':str(exc)}
                self.cache[key]=result;self.kernel_evaluations+=1
            else:
                reuse+=1
            row={'region_ids':[a,b],'cad_hinge_adjacent':frozenset((a,b)) in self.adjacent,**self.cache[key]}
            if row['status']=='OVERLAP':
                row['intersection_world_aabb_mm']=transformed_bounds(row['intersection_bounds_in_first_panel_frame_mm'],transforms[a])
            rows.append(row);counts[row['status']]+=1
        return {'pair_count':len(self.ids)*(len(self.ids)-1)//2,'counts':dict(counts),
                'narrow_phase_pairs':rows,'cache_reuses':reuse,
                'status':'UNKNOWN_KERNEL_FAILURE' if counts['UNKNOWN_KERNEL_FAILURE'] else
                         'MODEL_OVERLAP_DETECTED' if counts['OVERLAP'] else 'NO_OVERLAP_AT_THIS_SAMPLE'}
