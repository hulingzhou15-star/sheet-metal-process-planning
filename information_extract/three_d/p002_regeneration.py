"""Check P002's staged solid and swept mesh geometry for full allocations."""
from collections import Counter
from functools import lru_cache
from hashlib import sha256
import gzip
import json

import numpy as np
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib

from .band_material_solids import triangle_solid
from .local_collision import transformed
from .local_forming import load_shape
from .p002_curve_motion import bind_vertices, frame, sweep
from .p002_motion_reference import CHECK_CONFIG, make_samples
from .p004_collision import exact_overlap
from .panel_collision import transformed_bounds
from .sampled_mesh_contact import contact, triangle_bounds


RELIEF_TIMINGS = {'O112': ('O069','O037','O017'), 'O115': ('O086',)}


def box_intersection_upper_volume(a, b):
    """An overlap cannot exceed the intersection of containing boxes in one frame."""
    lo = np.maximum(a[0],b[0])
    hi = np.minimum(a[1],b[1])
    roundoff = 64*np.finfo(float).eps*max(1.,float(np.max(np.abs([lo,hi]))))
    return float(np.prod(np.maximum(0.,hi-lo+roundoff)))


def unsupported_relief(model, assignments):
    """v05 only reconstructs these openings until their material starts folding."""
    order = {s['station_id']:s['order'] for s in model['stations']}
    return [hole for hole,bends in RELIEF_TIMINGS.items()
            if any(order[assignments[hole]] >= order[assignments[bend]] for bend in bends)]


class Screen:
    def __init__(self, model, material, directory, geometry, shared_pairs=None):
        self.model = model
        self.material = material
        self.directory = directory
        self.geometry = {key: np.asarray(value['triangles']) for key,value in geometry.items()}
        self.pairs = {} if shared_pairs is None else shared_pairs
        self.ids = sorted(row['cell_id'] for row in model['cells'])
        self.first, self.second = np.triu_indices(len(self.ids),1)
        self.adjacent = {frozenset((row['region_id'],row['parent_region_id'])) for row in model['nodes']
                         if row['parent_region_id']}
        self.base = {}
        self.binding = {}
        self.bounds = {}
        self.descriptor = {}
        self.scope = directory.name
        self.identity = np.eye(4)
        self.volume_calls = 0
        self.mesh_calls = 0

    def solid(self,gid):
        if gid not in self.base:
            self.base[gid] = load_shape(self.directory/'brep'/f'{gid}.brep')
        return self.base[gid]

    def cell_bounds(self,gid):
        if gid not in self.bounds:
            box = Bnd_Box()
            BRepBndLib.AddOptimal_s(self.solid(gid),box,False,True)
            self.bounds[gid] = np.asarray(box.Get()).reshape(2,3)
        return self.bounds[gid]

    def digest(self,gid):
        if gid not in self.descriptor:
            self.descriptor[gid] = sha256((self.directory/'brep'/f'{gid}.brep').read_bytes()).hexdigest()
        return self.descriptor[gid]

    @lru_cache(maxsize=180)
    def deformed(self,cid,gid,progress):
        reference = self.model['curve_guides'][cid]
        key = cid,gid
        if key not in self.binding:
            triangles = self.geometry[gid]
            points,index = np.unique(triangles.reshape(-1,3).round(10),axis=0,return_inverse=True)
            self.binding[key] = points,index,bind_vertices(points,reference['guides'])
        points,index,bound = self.binding[key]
        moved = sweep(points,bound,progress,reference['z_sign'])
        return moved[index].reshape(self.geometry[gid].shape).round(7)

    @lru_cache(maxsize=32)
    def swept_solid(self,cid,gid,progress):
        return triangle_solid(self.deformed(cid,gid,progress))

    def _local_collision_upper_volume(self,a,b,sa,sb,ma,mb,inva,invb):
        ag,af = sa
        bg,bf = sb
        if af is None and bf is not None:
            pose = inva@mb
            points = self.deformed(b,bg,bf).reshape(-1,3)@pose[:3,:3].T+pose[:3,3]
            return box_intersection_upper_volume(self.cell_bounds(ag),(points.min(0),points.max(0)))
        if af is not None and bf is None:
            pose = invb@ma
            points = self.deformed(a,ag,af).reshape(-1,3)@pose[:3,:3].T+pose[:3,3]
            return box_intersection_upper_volume(self.cell_bounds(bg),(points.min(0),points.max(0)))
        return float('inf')

    def _pair(self, a,b, sa,sb, ma,mb,inv_a,inv_b):
        ag, af = sa
        bg, bf = sb
        relative = inv_a @ mb
        key = (self.digest(ag),af,self.digest(bg),bf,*relative.round(10).flat)
        if key not in self.pairs:
            try:
                upper_bound = self._local_collision_upper_volume(a,b,sa,sb,ma,mb,inv_a,inv_b)
                if upper_bound <= CHECK_CONFIG['volume_tolerance_mm3']:
                    checked = dict(status='BOX_BOUND_BELOW_VOLUME_THRESHOLD',intersection_volume_upper_bound_mm3=upper_bound)
                    method = 'SWEEP_AND_SOLID_CONTAINING_BOXES_IN_RIGID_LOCAL_FRAME'
                elif af is None and bf is None:
                    left,right = self.solid(ag),transformed(self.solid(bg),relative)
                    self.volume_calls += 1
                    checked = exact_overlap(left,right,CHECK_CONFIG)
                    method = 'RIGID_BREP_COMMON'
                else:
                    ta = self.geometry[ag] if af is None else self.deformed(a,ag,af)
                    tb = self.geometry[bg] if bf is None else self.deformed(b,bg,bf)
                    moved_b = tb@relative[:3,:3].T+relative[:3,3]
                    self.mesh_calls += 1
                    probe = contact(ta,moved_b,triangle_bounds(ta),triangle_bounds(moved_b))
                    if probe in {'AABB_SEPARATED','MESH_SEPARATED_AT_SAMPLE'}:
                        checked = dict(status='SWEPT_MESH_SCREENED_CLEAR')
                        method = 'SWEPT_MESH_SURFACE_AND_CONTAINMENT_SCREEN'
                    else:
                        left = self.solid(ag) if af is None else self.swept_solid(a,ag,af)
                        right = self.solid(bg) if bf is None else self.swept_solid(b,bg,bf)
                        self.volume_calls += 1
                        checked = exact_overlap(left,transformed(right,relative),CHECK_CONFIG)
                        checked['mesh_indication'] = probe
                        method = 'FACETED_SWEEP_COMMON'
            except Exception as exc:
                checked = dict(status='UNKNOWN_KERNEL_FAILURE',message=str(exc))
                method = 'PAIR_EVALUATION_UNRESOLVED'
            self.pairs[key] = {**checked,'method':method}
        result = self.pairs[key]
        if result['status']=='OVERLAP':
            return dict(result,intersection_world_aabb_mm=transformed_bounds(
                result['intersection_bounds_in_first_panel_frame_mm'],ma))
        return result

    def sample(self,plan,sid,phase,tau):
        state = frame(self.model,plan,sid,phase,tau)
        sig = tuple(state['executed_removal_ids']),tuple(state['executed_forming_ids'])
        variant = self.material[sig]
        known = set(state['transforms']) | state['curve_fractions'].keys()
        if state['unknown_cells'] or variant['cells'].keys()!=known:
            raise ValueError('Incomplete P002 material/pose coverage')
        gids,lo,hi,poses = {},[],[],[]
        specs = []
        for cid in self.ids:
            gid = variant['cells'][cid]
            progress = state['curve_fractions'].get(cid)
            specs.append((gid,progress))
            if progress is None:
                m = np.asarray(state['transforms'][cid])
                low,high = self.cell_bounds(gid)
                center = m[:3,:3]@((low+high)*.5)+m[:3,3]
                extent = np.abs(m[:3,:3])@((high-low)*.5)
                lo.append(center-extent)
                hi.append(center+extent)
                gids[cid] = self.replay_id(gid)
            else:
                m = self.identity
                points = self.deformed(cid,gid,progress).reshape(-1,3)
                lo.append(points.min(0))
                hi.append(points.max(0))
                gid = f'{gid}_sweep_{progress:.9f}'
                gids[cid] = self.replay_id(gid)
                state['transforms'][cid] = m.tolist()
            poses.append(m)
        lo,hi,poses = np.asarray(lo),np.asarray(hi),np.asarray(poses)
        inverses = np.linalg.inv(poses)
        gap = np.maximum(0.,np.maximum(lo[self.first]-hi[self.second],lo[self.second]-hi[self.first]))
        near = np.flatnonzero(np.linalg.norm(gap,axis=1)<=CHECK_CONFIG['contact_tolerance_mm'])
        counts = Counter(AABB_SEPARATED=len(self.first)-len(near))
        issues = []
        max_box_volume = None
        for i in near:
            ai,bi = self.first[i],self.second[i]
            a,b = self.ids[ai],self.ids[bi]
            result = self._pair(a,b,specs[ai],specs[bi],poses[ai],poses[bi],inverses[ai],inverses[bi])
            status = result['status']
            counts[status] += 1
            if status == 'BOX_BOUND_BELOW_VOLUME_THRESHOLD':
                max_box_volume = max(max_box_volume or 0.,result['intersection_volume_upper_bound_mm3'])
            if status in {'OVERLAP','UNKNOWN_KERNEL_FAILURE'}:
                issues.append(dict(result,region_ids=[a,b],cad_hinge_adjacent=frozenset((a,b)) in self.adjacent))
        state.update(geometry_ids=gids,material_id=self.replay_id(variant['material_id']),
                     unapplied_material_event_ids=variant['pending_unmodelled'],
                     reference_geometry=dict(counts=dict(counts),pair_count=len(self.first),narrow_phase_pairs=issues,
                         max_bounding_box_overlap_volume_mm3=max_box_volume))
        return state,specs

    def run(self,plan,steps,output=None,progress=None):
        result_counts = Counter()
        first_overlaps = []
        first_unknown = None
        runs = []
        samples = 0
        for sid,phase,tau in make_samples(self.model,plan,steps):
            state,specs = self.sample(plan,sid,phase,tau)
            check = state['reference_geometry']
            result_counts.update(check['counts'])
            samples += 1
            if progress and (samples == 1 or phase == 'completed'):
                progress(sid,phase,samples)
            if output is not None:
                runs.append(state)
                for cid,(gid,p) in zip(self.ids,specs):
                    self.store_mesh(output,self.replay_id(gid),self.geometry[gid])
                    if p is not None:
                        self.store_mesh(output,state['geometry_ids'][cid],self.deformed(cid,gid,p))
            for issue in check['narrow_phase_pairs']:
                witness = dict(issue,station_id=sid,phase=phase,tau=tau)
                if issue['status']=='OVERLAP' and len(first_overlaps)<6:
                    first_overlaps.append(witness)
                if issue['status']=='UNKNOWN_KERNEL_FAILURE' and first_unknown is None:
                    first_unknown = witness
            if state['unapplied_material_event_ids'] and first_unknown is None:
                first_unknown = dict(station_id=sid,phase=phase,tau=tau,
                                     pending_material=state['unapplied_material_event_ids'])
            if first_overlaps and output is None:
                break
        status = 'collision' if first_overlaps else 'unknown' if first_unknown else 'passed'
        return dict(status=status,sample_count=samples,subdivisions=steps,counts=dict(result_counts),
                    overlaps=first_overlaps,first_unknown=first_unknown,runs=runs)

    def replay_id(self,gid):
        return f'{self.scope}_{gid}'

    @staticmethod
    def store_mesh(folder,gid,data):
        path = folder/f'{gid}.json.gz'
        if not path.exists():
            with gzip.open(path,'wt',encoding='utf8',compresslevel=2) as stream:
                json.dump(dict(triangles=data.tolist()),stream,separators=(',',':'),allow_nan=False)
