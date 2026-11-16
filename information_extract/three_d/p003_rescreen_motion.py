"""Screen the completed material along a projected P003 fold schedule."""
from collections import Counter

import numpy as np

from information_extract.scripts.check_p003_axis_sensitivity import cylinder_axis_model
from information_extract.scripts.build_p003_material_basis import OUT as PREP
from information_extract.three_d.local_forming import load_shape
from information_extract.three_d.p002_motion_reference import CHECK_CONFIG
from information_extract.three_d.p003_motion import checked_assignments, motion
from information_extract.three_d.panel_collision import PanelChecker


class MotionScreen:
    """Hold the common STEP material and reuse pair checks across candidates."""

    def __init__(self, model, asset, cells=None):
        self.model, self.axes = cylinder_axis_model(model, asset)
        cell_ids = {row['cell_id'] for row in model['cells']}
        if cells is None:
            cells = {cid: load_shape(PREP/'brep'/f'{cid}.brep') for cid in sorted(cell_ids)}
        if cells.keys() != cell_ids:
            raise ValueError('The motion screen requires the complete STEP partition')
        self.bends = tuple(sorted(
            step['operation_id'] for joint in model['joints'] for step in joint['steps']
        ))
        self.nonbends = tuple(
            op['operation_id'] for op in model['operations'] if op['operation_type'] != 'BEND'
        )
        adjacent = [(joint['parent_cell_id'], joint['child_cell_id']) for joint in model['joints']]
        self.checker = PanelChecker(cells, adjacent, CHECK_CONFIG)
        self.pair_count = len(cell_ids) * (len(cell_ids) - 1) // 2
        self.sample_cache = {}

    def _sample(self, progress):
        if progress not in self.sample_cache:
            state = motion(self.model, dict(zip(self.bends, progress)))
            result = self.checker.check({
                cid: np.asarray(matrix) for cid, matrix in state['transforms'].items()
            })
            if (result['pair_count'] != self.pair_count
                    or sum(result['counts'].values()) != self.pair_count):
                raise ValueError('Incomplete material pair screen')
            overlap = max(
                (row for row in result['narrow_phase_pairs'] if row['status'] == 'OVERLAP'),
                key=lambda row: row['intersection_volume_mm3'], default=None,
            )
            unknown = next(
                (row for row in result['narrow_phase_pairs'] if row['status'] == 'UNKNOWN_KERNEL_FAILURE'),
                None,
            )
            self.sample_cache[progress] = result['counts'], overlap, unknown, state['joint_angles_deg']
        return self.sample_cache[progress]

    def check(self, assignments, subdivisions=24):
        if type(subdivisions) is not int or subdivisions < 2:
            raise ValueError('Two or more motion subdivisions required')
        assignment, order = checked_assignments(self.model, dict(assignments=assignments))
        folds = sorted({assignment[op] for op in self.bends}, key=order.get)
        if max(order[assignment[op]] for op in self.nonbends) >= order[folds[0]]:
            raise ValueError('The completed STEP partition is unavailable at the first fold')
        counts = Counter()
        first_unknown = None
        sample_count = 0
        for station in folds:
            preceding = [order[assignment[op]] < order[station] for op in self.bends]
            active = [assignment[op] == station for op in self.bends]
            for index in range(subdivisions + 1):
                tau = index / subdivisions
                phase = 'entry' if index == 0 else 'completed' if index == subdivisions else 'motion'
                progress = tuple(
                    1. if done else tau if moving else 0.
                    for done, moving in zip(preceding, active)
                )
                pair_counts, overlap, unknown, angles = self._sample(progress)
                counts.update(pair_counts)
                sample_count += 1
                position = dict(station_id=station, phase=phase, tau=tau, joint_angles_deg=angles)
                if overlap:
                    return dict(
                        status='collision', witness={**position, **overlap}, counts=dict(counts),
                        sample_count=sample_count, pair_states=sample_count*self.pair_count,
                        subdivisions=subdivisions,
                    )
                if unknown and first_unknown is None:
                    first_unknown = {**position, **unknown}
        return dict(
            status='unknown' if first_unknown else 'passed', witness=first_unknown,
            counts=dict(counts), sample_count=sample_count, pair_states=sample_count*self.pair_count,
            subdivisions=subdivisions,
        )
