"""Refine P003 fold samples for one pool candidate using either J297 axis."""
import argparse
from collections import Counter
from itertools import combinations
from pathlib import Path

from information_extract.scripts.check_p003_axis_sensitivity import cylinder_axis_model
from information_extract.scripts.check_p003_candidate_motion import (
    PREP, SELECTED, STEP, VariantChecker, compare_refinement, complete_material, setup,
)
from information_extract.scripts.check_p003_remaining_motion import OUT
from information_extract.three_d.artifacts import read, relative, sha, verify_sources, write
from information_extract.three_d.p003_motion import frame, samples


def check_case(plan_id, axis, steps):
    model, _, asset, _, fingerprints = setup()
    pool = SELECTED/'pool.json'
    plan = next((row for row in read(pool)['plans'] if row['plan_id'] == plan_id), None)
    if plan is None or asset['asset_id'] != sha(STEP):
        raise ValueError('Expected a P003 pool plan and its current source STEP')
    if steps < 24 or steps % 24:
        raise ValueError('Refinement must include all 24-subdivision samples')
    axes = None
    if axis == 'step_cylinder':
        model, axes = cylinder_axis_model(model, asset)
    checkpoints = OUT/'bend_checkpoints'/f'{plan_id}.json'
    if axis == 'midplane':
        prior = read(checkpoints)
        fingerprints[relative(checkpoints)] = sha(checkpoints)
    for source in (Path(__file__), pool, PREP/'partition.json'):
        fingerprints[relative(source)] = sha(source)
    checker = VariantChecker(model, PREP)
    cells = {cell['cell_id']:cell['cell_id'] for cell in model['cells']}
    pairs = len(list(combinations(cells, 2)))
    runs, counts, positives = [], Counter(), {}
    for sid, phase, tau in samples(model, plan, steps):
        state = frame(model, plan, sid, phase, tau)
        if not complete_material(state):
            continue
        result = checker.check(cells, state['transforms'])
        if result['pair_count'] != pairs or sum(result['counts'].values()) != pairs:
            raise ValueError('Wrong all-pair denominator')
        counts.update(result['counts'])
        for row in result['narrow_phase_pairs']:
            if row['status'] != 'OVERLAP':
                continue
            key = tuple(row['region_ids'])
            stats = positives.setdefault(key, dict(cells=list(key), stations=set(), pair_states=0,
                cad_hinge_adjacent=row['cad_hinge_adjacent'], peak_volume_mm3=0.))
            stats['stations'].add(sid)
            stats['pair_states'] += 1
            if row['intersection_volume_mm3'] > stats['peak_volume_mm3']:
                stats.update(peak_volume_mm3=row['intersection_volume_mm3'], peak_station=sid, peak_phase=phase,
                             peak_tau=tau, peak_j297_angle_deg=state['joint_angles_deg']['J297'])
        state['reference_geometry'] = result
        runs.append(state)
    comparison = None
    if axis == 'midplane':
        base = dict(runs=[{**r, 'reference_geometry':r['result']} for r in prior])
        comparison = compare_refinement(base, dict(runs=runs))
    summary = dict(plan_id=plan_id, axis=axis, axis_geometry=axes, steps=steps, sample_count=len(runs),
                   pair_states=sum(counts.values()), counts=dict(counts), prior_sample_comparison=comparison,
                   positive_overlap_pairs=[{**v, 'stations':sorted(v['stations'])} for _,v in sorted(positives.items())],
                   kernel_evaluations=checker.evaluations, scope='SAMPLES_WITH_COMPLETE_MATERIAL_RIGID_CELLS')
    fingerprint_file = Path(cylinder_axis_model.__code__.co_filename)
    fingerprints[relative(fingerprint_file)] = sha(fingerprint_file)
    verify_sources(fingerprints)
    write(OUT/'confirmation'/plan_id/f'{axis}.json', dict(summary=summary, sources=fingerprints, runs=runs))
    print(plan_id, axis, steps, len(runs), dict(counts), summary['positive_overlap_pairs'], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan')
    parser.add_argument('--axis', choices=('midplane','step_cylinder'), required=True)
    parser.add_argument('--steps', type=int, default=96)
    args = parser.parse_args()
    check_case(args.plan, args.axis, args.steps)


if __name__ == '__main__':
    main()
