"""Sample P001's staged, disjoint rigid regions through synchronized finite-band kinematics."""
from collections import Counter, defaultdict
from itertools import combinations

import numpy as np
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib

from .band_material import state_transforms, station_fractions
from .local_collision import transformed
from .local_forming import load_shape
from .p004_collision import exact_overlap
from .panel_collision import transformed_bounds


TOLERANCE = 1e-6


class StagedMotionChecker:
    def __init__(self, viewer, base_directory):
        self.viewer = viewer
        self.base_directory = base_directory
        self.model = viewer['model']
        self.stations = {s['station_id']:s['order'] for s in viewer['stations']}
        self.regions = tuple(sorted(r['region_id'] for r in self.model['regions']))
        self.region_index = {rid:i for i,rid in enumerate(self.regions)}
        self.first,self.second = np.triu_indices(len(self.regions),1)
        self.adjacent = {frozenset((b['parent_region_id'],b['child_region_id'])) for b in self.model['bends']}
        self.materials = {v['material_id']:v for v in viewer['variants']}
        self.states = {(r['plan_id'],r['station_id'],r['phase']):r['material_id'] for r in viewer['material_states']}
        self.shape_data = {}
        self.layouts = {}
        self.transforms = {}
        self.pairs = {}
        self.kernel_calls = 0

    def _geometry(self, gid):
        if gid not in self.shape_data:
            shape = load_shape(self.base_directory/self.viewer['geometries'][gid]['brep_file'])
            box = Bnd_Box()
            BRepBndLib.AddOptimal_s(shape,box,False,True)
            lo,hi = np.split(np.asarray(box.Get()),2)
            self.shape_data[gid] = shape,(lo+hi)/2,(hi-lo)/2
        return self.shape_data[gid]

    def _layout(self, material_id):
        if material_id not in self.layouts:
            material = self.materials[material_id]
            groups = defaultdict(list)
            for c in material['components']:
                if c['kind']=='panel':
                    groups[c['region_id']].append(c)
            if groups.keys()!=self.region_index.keys():
                raise ValueError(f'Material {material_id} has an incomplete rigid-region partition')
            components = [component for rid in self.regions for component in groups[rid]]
            owners = np.array([self.region_index[c['region_id']] for c in components])
            starts = np.searchsorted(owners,np.arange(len(self.regions)))
            ends = [*starts[1:],len(owners)]
            _,centers,sizes = zip(*(self._geometry(c['geometry_id']) for c in components))
            self.layouts[material_id] = components,owners,starts,ends,np.stack(centers),np.stack(sizes)
        return self.layouts[material_id]

    def _check_sample(self, plan, station, phase, tau):
        material_id = self.states[(plan['plan_id'],station,'completed' if phase=='completed' else 'entry')]
        fractions = station_fractions(self.model,plan['assignments'],self.stations,station,tau)
        key = tuple(fractions.items())
        if key not in self.transforms:
            transforms = state_transforms(self.model,fractions)
            poses = np.stack([transforms[rid] for rid in self.regions])
            self.transforms[key] = poses,np.linalg.inv(poses)
        poses,inverses = self.transforms[key]
        components,owners,starts,ends,centers,sizes = self._layout(material_id)
        rotations = poses[owners,:3,:3]
        coords = np.einsum('nij,nj->ni',rotations,centers)+poses[owners,:3,3]
        extent = np.einsum('nij,nj->ni',np.abs(rotations),sizes)
        lo,hi = coords-extent,coords+extent
        group_lo = np.minimum.reduceat(lo,starts,axis=0)
        group_hi = np.maximum.reduceat(hi,starts,axis=0)
        gaps = np.maximum(0.,np.maximum(group_lo[self.first]-group_hi[self.second],
                                       group_lo[self.second]-group_hi[self.first]))
        possible = np.flatnonzero(np.linalg.norm(gaps,axis=1)<=TOLERANCE)
        counts = Counter(AABB_SEPARATED=len(self.first)-len(possible))
        issues = []
        for index in possible:
            a,b = self.first[index],self.second[index]
            relative = inverses[a]@poses[b]
            pair_motion = tuple(np.round(relative,10).flat)
            overlap, errors, volume = [],[],0.
            for i,j in ((i,j) for i in range(starts[a],ends[a]) for j in range(starts[b],ends[b])):
                if np.linalg.norm(np.maximum(0.,np.maximum(lo[i]-hi[j],lo[j]-hi[i])))>TOLERANCE:
                    continue
                first,second = components[i],components[j]
                ga,gb = first['geometry_id'],second['geometry_id']
                cache_key = ga,gb,pair_motion
                if cache_key not in self.pairs:
                    try:
                        sa,sb = self._geometry(ga)[0],self._geometry(gb)[0]
                        self.pairs[cache_key] = exact_overlap(sa,transformed(sb,relative),
                                                            {'volume_tolerance_mm3':TOLERANCE})
                    except (RuntimeError,ValueError) as exc:
                        self.pairs[cache_key] = {'error':str(exc)}
                    self.kernel_calls += 1
                result = self.pairs[cache_key]
                pair = [first['component_id'],second['component_id']]
                if 'error' in result:
                    errors.append(dict(components=pair,error=result['error']))
                    continue
                volume += result['intersection_volume_mm3']
                if result['status']=='OVERLAP':
                    overlap.append(dict(components=pair,volume_mm3=result['intersection_volume_mm3'],
                        intersection_world_aabb_mm=transformed_bounds(
                            result['intersection_bounds_in_first_panel_frame_mm'],poses[a])))
            status = 'OVERLAP' if volume>TOLERANCE else 'UNKNOWN_KERNEL_FAILURE' if errors else 'NO_POSITIVE_VOLUME_OVERLAP'
            counts[status] += 1
            if volume>TOLERANCE or errors:
                regions = [self.regions[a],self.regions[b]]
                issues.append(dict(region_ids=regions,status=status,intersection_volume_mm3=volume,
                    cad_hinge_adjacent=frozenset(regions) in self.adjacent,component_overlaps=overlap,errors=errors))
        material = self.materials[material_id]
        return dict(station_id=station,phase=phase,tau=tau,material_id=material_id,
            executed_forming_ids=material['executed_forming_ids'],pair_count=len(self.first),counts=dict(counts),issues=issues)

    def evaluate(self, plan, steps, *, stop_on_overlap=False):
        from information_extract.scripts.check_local_forming_collision import make_samples

        counts = Counter()
        started = self.kernel_calls
        rows, witness = [],None
        status = 'passed'
        for station,phase,tau in make_samples(self.viewer,plan,steps):
            row = self._check_sample(plan,station,phase,tau)
            rows.append(row)
            counts.update(row['counts'])
            for issue in row['issues']:
                if issue['status']=='OVERLAP' or (issue['status']=='UNKNOWN_KERNEL_FAILURE' and status=='passed'):
                    status = 'collision' if issue['status']=='OVERLAP' else 'unknown'
                    if witness is None or issue['status']=='OVERLAP' or status!='collision':
                        witness = dict(station_id=station,phase=phase,tau=tau,material_id=row['material_id'],**issue)
            if stop_on_overlap and status=='collision':
                break
        return dict(plan_id=plan['plan_id'],assignments=plan['assignments'],motion_subdivisions=steps,
            status=status,witness=witness,sample_count=len(rows),pair_states=sum(counts.values()),counts=dict(counts),
            kernel_calls=self.kernel_calls-started,runs=rows)
