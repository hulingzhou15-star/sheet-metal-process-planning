"""Uncalibrated geometric observables, NOT replacement penalties or labels.

Clearance excludes direct material neighbours and station-relative rigid pairs
only in this soft observable. The separate collision diagnostic excludes neither.
All station motions share tau; material is the entry material throughout motion.
"""
from collections import defaultdict
from itertools import combinations
import math
import numpy as np
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from .assets import bounds
from .local_forming import load_shape
from .local_collision import transformed
from .panel_collision import aabb_gap, transformed_bounds
from .band_material import state_transforms, station_fractions


def exact_distance(a, b):
    dist = BRepExtrema_DistShapeShape(a, b)
    if not dist.IsDone() or not math.isfinite(dist.Value()):
        raise ValueError('Distance unresolved')
    return float(dist.Value())


def relative_changes(a, b, transforms):
    start = np.linalg.inv(transforms[0][a]) @ transforms[0][b]
    return any(not np.allclose(start, np.linalg.inv(t[a]) @ t[b], atol=1e-9, rtol=0)
               for t in transforms[1:])


class MetricProbe:
    def __init__(self, viewer, geometry_root):
        self.v = viewer
        self.root = geometry_root
        self.model = viewer['model']
        self.variants = {m['material_id']: m for m in viewer['variants']}
        self.orders = {s['station_id']: s['order'] for s in viewer['stations']}
        self.adjacent = {frozenset((b['parent_region_id'], b['child_region_id'])) for b in self.model['bends']}
        self.shapes, self.boxes, self.mass, self.cache = {}, {}, {}, {}
        self.kernel_calls = 0

    def geometry(self, gid):
        if gid not in self.shapes:
            shape = load_shape(self.root / self.v['geometries'][gid]['brep_file'])
            self.shapes[gid] = shape
            self.boxes[gid] = bounds(shape)
            prop = GProp_GProps()
            BRepGProp.VolumeProperties_s(shape, prop)
            centre = prop.CentreOfMass()
            self.mass[gid] = (abs(prop.Mass()), np.array([centre.X(), centre.Y(), centre.Z()]))
        return self.shapes[gid]

    def distance(self, ga, gb, relative):
        key = (ga, gb, *np.round(relative, 10).flat)
        if key not in self.cache:
            try:
                d = exact_distance(self.geometry(ga), transformed(self.geometry(gb), relative))
                self.cache[key] = dict(distance_mm=d)
            except Exception as exc:
                self.cache[key] = dict(error=str(exc))
            self.kernel_calls += 1
        return self.cache[key]

    def evaluate(self, plan, steps=24):
        if steps < 2:
            raise ValueError('At least two subdivisions')
        thickness = self.model['thickness_mm']
        thresholds = [.5*thickness, thickness, 2*thickness, 5*thickness]
        cap = thresholds[-1]
        moving_stations = {plan['assignments'][b['operation_id']] for b in self.model['bends']}
        rows, stations = [], []
        path_integral, volume_normalizer = 0., 0.
        calls_before = self.kernel_calls
        for sid in sorted(moving_stations, key=self.orders.get):
            mid = next(r['material_id'] for r in self.v['material_states']
                       if r['plan_id'] == plan['plan_id'] and r['station_id'] == sid and r['phase'] == 'entry')
            components = [c for c in self.variants[mid]['components'] if c['kind'] == 'panel']
            groups = defaultdict(list)
            for c in components:
                self.geometry(c['geometry_id'])
                groups[c['region_id']].append(c)
            ts = [state_transforms(self.model, station_fractions(self.model, plan['assignments'], self.orders, sid, k/steps))
                  for k in range(steps+1)]
            checks = [ts[0], ts[steps//2], ts[-1]]
            moving = {r for r in groups if any(not np.allclose(ts[0][r], t[r], atol=1e-9, rtol=0) for t in checks[1:])}
            pairs = [(a, b) for a, b in combinations(sorted(groups), 2)
                     if frozenset((a, b)) not in self.adjacent and relative_changes(a, b, checks)]
            total_volume = sum(self.mass[c['geometry_id']][0] for c in components)
            moving_volume = sum(self.mass[c['geometry_id']][0] for c in components if c['region_id'] in moving)
            volume_normalizer = max(volume_normalizer, total_volume)
            station_integral = 0.
            for c in components:
                weight, point = self.mass[c['geometry_id']]
                positions = np.array([t[c['region_id']][:3, :3] @ point + t[c['region_id']][:3, 3] for t in ts])
                station_integral += weight * float(np.linalg.norm(np.diff(positions, axis=0), axis=1).sum())
            path_integral += station_integral
            stations.append(dict(station_id=sid, material_id=mid, moving_region_count=len(moving),
                eligible_clearance_pairs=len(pairs), moving_volume_mm3=moving_volume,
                moving_volume_fraction=moving_volume/total_volume,
                volume_centroid_travel_integral_mm4=station_integral))
            for k, t in enumerate(ts):
                world = {c['component_id']: transformed_bounds(self.boxes[c['geometry_id']], t[c['region_id']]) for c in components}
                group_bounds = {r: [min(world[c['component_id']][j] for c in cs) for j in range(3)] +
                                   [max(world[c['component_id']][j+3] for c in cs) for j in range(3)] for r, cs in groups.items()}
                close_counts = [0]*len(thresholds)
                observed_min, witness, errors = None, None, []
                for a, b in pairs:
                    if aabb_gap(group_bounds[a], group_bounds[b]) > cap+1e-7:
                        continue
                    relative = np.linalg.inv(t[a]) @ t[b]
                    minimum = math.inf
                    for ca in groups[a]:
                        for cb in groups[b]:
                            if aabb_gap(world[ca['component_id']], world[cb['component_id']]) > cap+1e-7:
                                continue
                            result = self.distance(ca['geometry_id'], cb['geometry_id'], relative)
                            if 'error' in result:
                                errors.append(dict(region_ids=[a, b], components=[ca['component_id'], cb['component_id']], **result))
                            else:
                                minimum = min(minimum, result['distance_mm'])
                    if minimum <= cap+1e-7:
                        if observed_min is None or minimum < observed_min:
                            observed_min, witness = minimum, [a, b]
                        close_counts = [n+int(minimum <= limit+1e-7) for n, limit in zip(close_counts, thresholds)]
                rows.append(dict(station_id=sid, tau=k/steps, observed_min_within_cap_mm=observed_min,
                    closest_regions=witness, close_pair_counts=close_counts, errors=errors))
        finite = [r for r in rows if r['observed_min_within_cap_mm'] is not None]
        nearest = min(finite, key=lambda r: r['observed_min_within_cap_mm']) if finite else None
        return dict(plan_id=plan['plan_id'], thresholds_mm=thresholds, thresholds_are_diagnostics_not_safety_limits=True,
            motion_subdivisions=steps, sampled_states=len(rows), kernel_calls=self.kernel_calls-calls_before,
            error_count=sum(len(r['errors']) for r in rows),
            minimum_observed_clearance_mm=nearest['observed_min_within_cap_mm'] if nearest else None,
            closest_witness=nearest,
            max_close_pairs=[max(r['close_pair_counts'][i] for r in rows) for i in range(len(thresholds))],
            peak_moving_volume_fraction=max(s['moving_volume_fraction'] for s in stations),
            volume_weighted_centroid_travel_mm=path_integral/volume_normalizer,
            travel_normalizer_mm3=volume_normalizer, stations=stations, samples=rows,
            validated_replacement_penalty=None, score_weights=None,
            coverage='Rigid panels/local endpoint patches only; no finite bend bands/tools/fixtures/continuous local forming. Clearance omits direct material neighbours and station-relative rigid pairs; collision checks must not inherit this omission.')
