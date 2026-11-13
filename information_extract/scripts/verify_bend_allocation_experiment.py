"""Independently refine the predeclared maximum-P1 variant, with a fresh cache."""
from pathlib import Path
import time
import numpy as np

from information_extract.scripts.experiment_bend_allocation_3d import (
    DEST, ROOT, OUT, read, sha, digest, atomic_write, map_material_states, protected_hashes,
)
from information_extract.scripts.check_local_forming_collision import run
from information_extract.scripts.check_all_local_forming_collisions import summarize
from information_extract.three_d.band_material import state_transforms, station_fractions


def main():
    inputs = read(DEST / 'inputs.json')
    if any(sha(ROOT / p) != h for p, h in inputs['files'].items()):
        raise ValueError('Experiment source inputs changed')
    inventory = read(DEST / 'inventory.json')
    plans = inventory['plans']
    # Deterministic selection by OLD metric, used only for this extra sensitivity
    # check. Main experiment includes every legal one-bend allocation.
    plan = sorted(plans, key=lambda p: (-p['area_audit']['inversion_count'], p['plan_id']))[0]
    viewer = read(OUT / 'viewer_data.json')
    base = next(p for p in viewer['plans'] if p['plan_id'] == inventory['base_plan_id'])
    viewer = {**viewer, 'plans': plans, 'material_states': map_material_states(viewer, base, plans)}
    model = viewer['model']
    orders = {s['station_id']: s['order'] for s in viewer['stations']}
    last = max(orders, key=orders.get)
    def transforms(p, sid, t):
        return state_transforms(model, station_fractions(model, p['assignments'], orders, sid, t))
    final_base = transforms(plans[0], last, 1.)
    pose_checks = []
    for p in plans[1:]:
        change = p['changes'][0]
        sid = min((change['before'], change['after']), key=orders.get)
        a, b = transforms(plans[0], sid, .5), transforms(p, sid, .5)
        altered = [rid for rid in a if not np.allclose(a[rid], b[rid], atol=1e-8, rtol=0)]
        final = transforms(p, last, 1.)
        same_final = all(np.allclose(final_base[rid], final[rid], atol=1e-8, rtol=0) for rid in final)
        if not altered or not same_final:
            raise ValueError('Expected different intermediate poses and identical completed geometry')
        pose_checks.append(dict(plan_id=p['plan_id'], intermediate_changed_regions=altered,
                                final_pose_identical=same_final))
    signature = digest(dict(base_experiment=inputs['signature'], steps=48, plan=plan['plan_id'],
                            script_sha256=sha(Path(__file__))))
    started = time.monotonic()
    report = run(plan['plan_id'], viewer, DEST / (plan['plan_id'] + '_refined48'),
                 shared={}, signature=signature, steps=48)
    original = read(DEST / plan['plan_id'] / 'collision_report.json')
    key = lambda r: (r['station_id'], r['phase'], r['tau'])
    index = {key(r): r for r in report['runs']}
    fields = ('material_id', 'executed_forming_ids', 'pair_count', 'counts', 'issues')
    identical = all(all(r[f] == index[key(r)][f] for f in fields) for r in original['runs'])
    if not identical:
        raise ValueError('Fresh-cache results at original samples differ')
    if any(sha(ROOT / p) != h for p, h in inputs['files'].items()) or protected_hashes() != inputs['protected_snapshot']:
        raise ValueError('Inputs changed during verification')
    result = dict(selection='Maximum old P1 inversion count, tie by plan id', plan_id=plan['plan_id'],
        changes=plan['changes'], motion_subdivisions=48, original_samples_identical=identical,
        original_sample_count=original['sample_count'], extra_sample_count=report['sample_count']-original['sample_count'],
        cache_reused=False, elapsed_seconds=round(time.monotonic()-started, 2),
        pose_checks=pose_checks, summary=summarize(report), signature=signature,
        script_sha256=sha(Path(__file__)), base_experiment_signature=inputs['signature'])
    atomic_write(DEST / 'sensitivity.json', result)
    print({k: v for k, v in result.items() if k != 'pose_checks'}, flush=True)


if __name__ == '__main__':
    main()
