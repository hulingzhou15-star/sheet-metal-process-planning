"""Conservative oriented-box rejection before exact P004 pair checks."""
from collections import Counter
import math

import numpy as np
from OCP.Bnd import Bnd_Box
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TopTools import TopTools_ListOfShape

from .assets import bounds
from .local_collision import transformed, volume
from .local_forming import load_shape
from .p002_motion_reference import CHECK_CONFIG
from .panel_collision import transformed_bounds


def exact_overlap(a, b, config):
    """Test the requested solid volume without solving the closest-point distance."""
    a_list, b_list = TopTools_ListOfShape(), TopTools_ListOfShape()
    a_list.Append(a)
    b_list.Append(b)
    common = BRepAlgoAPI_Common()
    common.SetArguments(a_list)
    common.SetTools(b_list)
    common.SetUseOBB(True)
    common.Build()
    if not common.IsDone() or not BRepCheck_Analyzer(common.Shape()).IsValid():
        raise ValueError('Invalid P004 common shape')
    shape = common.Shape()
    v = volume(shape)
    if not math.isfinite(v) or v < -config['volume_tolerance_mm3']:
        raise ValueError('Invalid P004 common volume')
    if v<=config['volume_tolerance_mm3']:
        return dict(status='NO_POSITIVE_VOLUME', intersection_volume_mm3=max(0.,v))
    return dict(status='OVERLAP', intersection_volume_mm3=v,
                intersection_bounds_in_first_panel_frame_mm=bounds(shape))


def oriented_box_separation(centers, half_sizes, rotations, first, second, margin):
    """Test only face normals. Missed separating axes proceed to OCCT."""
    displacement = centers[second]-centers[first]
    axes_first, axes_second = rotations[first], rotations[second]
    alignment = np.abs(axes_first.transpose(0, 2, 1) @ axes_second)
    extents_first, extents_second = half_sizes[first], half_sizes[second]
    distance_first = np.abs(np.einsum('ni,nij->nj', displacement, axes_first))
    distance_second = np.abs(np.einsum('ni,nij->nj', displacement, axes_second))
    limit_first = extents_first + np.einsum('nij,nj->ni', alignment, extents_second)
    limit_second = extents_second + np.einsum('nij,ni->nj', alignment, extents_first)
    return np.any(distance_first > limit_first+margin, axis=1) | np.any(
        distance_second > limit_second+margin, axis=1,
    )


class P004VariantChecker:
    """Check all pairs and retain OCCT results across unchanged material variants."""

    def __init__(self, model, directory, config=None):
        self.directory = directory
        self.config = CHECK_CONFIG if config is None else config
        self.tolerance = self.config['contact_tolerance_mm']
        self.margin = max(1e-5, self.tolerance)
        self.ids = tuple(sorted(c['cell_id'] for c in model['cells']))
        self.first, self.second = np.triu_indices(len(self.ids), 1)
        self.adjacent = [(j['parent_cell_id'],j['child_cell_id']) for j in model['joints']]
        self.flat = {cid:np.asarray(model['flat_transforms'][cid], dtype=float) for cid in self.ids}
        self.inverse_flat = np.stack([np.linalg.inv(self.flat[cid]) for cid in self.ids])
        self.geometry = {}
        self.variants = {}
        self.cache = {}
        self.evaluations = 0

    def _load_geometry(self, cell, gid):
        if gid not in self.geometry:
            shape = load_shape(self.directory/'brep'/f'{gid}.brep')
            world_bounds = np.asarray(bounds(shape))
            fitted = transformed(shape, self.flat[cell])
            box = Bnd_Box()
            # Include the analytic curved-face bounds and native BRep tolerances.
            BRepBndLib.AddOptimal_s(fitted, box, False, True)
            tight = np.asarray(box.Get())
            self.geometry[gid] = (
                cell, shape, (world_bounds[:3]+world_bounds[3:])/2,
                (world_bounds[3:]-world_bounds[:3])/2,
                (tight[:3]+tight[3:])/2, (tight[3:]-tight[:3])/2,
            )
        owner, *geometry = self.geometry[gid]
        if owner != cell:
            raise ValueError(f'Geometry {gid} has more than one cell owner')
        return geometry

    def _variant(self, cells):
        key = tuple(cells[cid] for cid in self.ids)
        if key not in self.variants:
            data = [self._load_geometry(cid,gid) for cid,gid in zip(self.ids,key)]
            solids, world_centers, world_sizes, flat_centers, flat_sizes = zip(*data)
            self.variants[key] = (solids, *(np.stack(values) for values in (
                world_centers,world_sizes,flat_centers,flat_sizes,
            )))
        return key, self.variants[key]

    def check(self, cells, transforms):
        if cells.keys() != transforms.keys() or cells.keys() != set(self.ids):
            raise ValueError('Every P004 cell and pose must be supplied exactly once')
        matrices = np.asarray([transforms[cid] for cid in self.ids], dtype=float)
        if matrices.shape != (len(self.ids),4,4) or not np.isfinite(matrices).all():
            raise ValueError('Invalid P004 pose matrices')
        gids, (solids, world_centers, world_sizes, flat_centers, flat_sizes) = self._variant(cells)
        rotation, translation = matrices[:,:3,:3], matrices[:,:3,3]
        world_centers = np.einsum('nij,nj->ni', rotation, world_centers)+translation
        world_sizes = np.einsum('nij,nj->ni', np.abs(rotation), world_sizes)
        gaps = np.maximum(0., np.abs(world_centers[self.first]-world_centers[self.second])
                -world_sizes[self.first]-world_sizes[self.second])
        candidates = np.flatnonzero(np.linalg.norm(gaps, axis=1) <= self.tolerance)
        counts = Counter(AABB_SEPARATED=len(self.first)-len(candidates))
        chart_poses = matrices @ self.inverse_flat
        chart_rotations = chart_poses[:,:3,:3]
        chart_centers = np.einsum('nij,nj->ni', chart_rotations, flat_centers)+chart_poses[:,:3,3]
        separated = oriented_box_separation(chart_centers, flat_sizes, chart_rotations,
            self.first[candidates], self.second[candidates], self.margin)
        remaining = candidates[~separated]
        if len(remaining) != len(candidates):
            counts['OBB_SEPARATED'] = len(candidates)-len(remaining)
        adjacent = {frozenset(p) for p in self.adjacent}
        rows, reused = [], 0
        if len(remaining):
            inverses = np.linalg.inv(matrices)
        for index in remaining:
            i,j = self.first[index],self.second[index]
            cid,other = self.ids[i],self.ids[j]
            relative = inverses[i] @ matrices[j]
            key = (gids[i],gids[j],*np.round(relative,self.config['relative_transform_cache_decimals']).flat)
            if key in self.cache:
                reused += 1
            else:
                try:
                    result = exact_overlap(solids[i],transformed(solids[j],relative),self.config)
                except Exception as exc:
                    result = {'status':'UNKNOWN_KERNEL_FAILURE','message':str(exc)}
                self.cache[key] = result
                self.evaluations += 1
            row = dict(region_ids=[cid,other], cad_hinge_adjacent=frozenset((cid,other)) in adjacent, **self.cache[key])
            if row['status']=='OVERLAP':
                row['intersection_world_aabb_mm'] = transformed_bounds(
                    row['intersection_bounds_in_first_panel_frame_mm'],matrices[i])
            rows.append(row)
            counts[row['status']] += 1
        if counts['UNKNOWN_KERNEL_FAILURE']:
            status = 'UNKNOWN_KERNEL_FAILURE'
        elif counts['OVERLAP']:
            status = 'MODEL_OVERLAP_DETECTED'
        else:
            status = 'NO_OVERLAP_AT_THIS_SAMPLE'
        return dict(pair_count=len(self.first),counts=dict(counts),narrow_phase_pairs=rows,
                    cache_reuses=reused,status=status)
