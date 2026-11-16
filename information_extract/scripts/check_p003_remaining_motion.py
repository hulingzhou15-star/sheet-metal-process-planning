"""Apply the existing P003 staged-motion checker to the 48 untested pool plans."""
from collections import Counter
from pathlib import Path
import shutil
import time

from information_extract.scripts.check_p003_candidate_motion import (
    MODULE, OUT as SELECTED_MOTION, SELECTED, STEP, VariantChecker, compare_refinement,
    evaluate_plan, load_material, prepare_bend_checks, protected_hashes, setup, verify_run,
)
from information_extract.three_d.allocation_policy import AllocationEngine
from information_extract.three_d.artifacts import read, relative, sha, verify_sources, write
from information_extract.three_d.p003_motion import FORMS, REMOVALS
from information_extract.three_d.project_geometry_scoring import geometry_context
from information_extract.scripts.build_p003_material_basis import OUT as PREP


OUT = MODULE/'outputs/p003_candidate_motion_remaining_v01'


def remaining_plans(model, selected, paths):
    pool = read(SELECTED/'pool.json')
    by_id = {p['plan_id']:p for p in pool['plans']}
    selected_ids = set(selected['ranking'])
    if (pool['part_id'] != 'P003' or len(pool['plans']) != 64 or len(by_id) != 64
            or len({p['plan_hash'] for p in by_id.values()}) != 64
            or {p['plan_id'] for p in selected['plans']} != selected_ids
            or not selected_ids <= by_id.keys()):
        raise ValueError('The selected candidates do not form the verified subset of the 64-plan P003 pool')
    for plan in selected['plans']:
        if by_id[plan['plan_id']]['assignments'] != plan['assignments']:
            raise ValueError(f"Selected and pool assignments disagree for {plan['plan_id']}")
    candidates = [p for p in pool['plans'] if p['plan_id'] not in selected_ids]
    facts = read(PREP/'facts.json')
    context, _ = geometry_context(facts, paths, {'rows': []})
    engine = AllocationEngine(context)
    order = {s['station_id']: s['order'] for s in model['stations']}
    material = [o['operation_id'] for o in model['operations'] if o['operation_type'] in REMOVALS | FORMS]
    bends = [o['operation_id'] for o in model['operations'] if o['operation_type'] == 'BEND']
    for plan in candidates:
        assignment = plan['assignments']
        engine.require_feasible(assignment)
        if max(order[assignment[o]] for o in material) >= min(order[assignment[o]] for o in bends):
            raise ValueError(f"Material events extend into bend motion for {plan['plan_id']}")
    return candidates


def main():
    started = time.monotonic()
    protected = protected_hashes()
    model, selected, asset, paths, fingerprints = setup()
    previous = read(SELECTED_MOTION/'inputs.json')
    verify_sources(previous['files'])
    verify_sources(read(SELECTED_MOTION/'output_manifest.json')['files'])
    if model != read(SELECTED_MOTION/'model.json') or previous['steps'] != 24 or asset['asset_id'] != sha(STEP):
        raise ValueError('The motion model, sampling protocol or STEP input differs from the initial run')
    steps = previous['steps']
    plans = remaining_plans(model, selected, paths)
    for source in (Path(__file__), SELECTED/'pool.json', SELECTED_MOTION/'inputs.json',
                   SELECTED_MOTION/'output_manifest.json'):
        fingerprints[relative(source)] = sha(source)
    write(OUT/'model.json', model)
    write(OUT/'plans.json', dict(part_id='P003', plans=plans, prior_checked_ids=sorted(selected['ranking'])))
    print('P003 remaining', len(plans), 'plans;', steps, 'subdivisions', flush=True)

    bend_checks = prepare_bend_checks(model, plans, fingerprints, OUT, steps)
    material = load_material(model, plans, asset, paths, fingerprints, OUT)
    checker = VariantChecker(model, OUT)
    reports, totals = [], Counter()
    for plan in plans:
        pid = plan['plan_id']
        checkpoints = read(OUT/'bend_checkpoints'/f'{pid}.json')
        completed = {(r['station_id'], r['phase'], r['tau']): r['result'] for r in checkpoints}
        report = evaluate_plan(model, plan, material, checker, steps, completed)
        write(OUT/pid/'collision_report.json', report)
        shutil.copyfile(STEP, OUT/pid/'completed.step')
        reports.append(report)
        counts = report['summary']['counts']
        totals.update(counts)
        print('PLAN', pid, report['sample_count'], counts, flush=True)
    verify_run(model, reports, material)

    worst = max(reports, key=lambda r: (r['summary']['counts'].get('UNKNOWN_KERNEL_FAILURE', 0),
                                       r['summary']['counts'].get('OVERLAP', 0)))
    worst_id = worst['plan_id']
    cold = VariantChecker(model, OUT)
    refined = evaluate_plan(model, next(p for p in plans if p['plan_id'] == worst_id), material, cold, 2*steps)
    refinement = compare_refinement(worst, refined)
    write(OUT/'refined'/worst_id/'collision_report.json', refined)
    verify_sources(fingerprints)
    verify_sources(read(SELECTED_MOTION/'output_manifest.json')['files'])
    if protected_hashes() != protected:
        raise ValueError('Protected working sources changed')
    if any(sha(OUT/p['plan_id']/'completed.step') != asset['asset_id'] for p in plans):
        raise ValueError('A completed STEP copy differs from the source')
    write(OUT/'inputs.json', dict(files=fingerprints, steps=steps))
    write(OUT/'summary.json', dict(part_id='P003', plan_count=len(reports), steps=steps,
          sample_count=sum(r['sample_count'] for r in reports), pair_states=sum(totals.values()),
          counts=dict(totals), material_variant_count=len(material), all_completed_steps_byte_equal=True,
          final_material_checkpoint=bend_checks, kernel_evaluations=bend_checks['kernel_evaluations']+checker.evaluations,
          refinement=dict(plan_id=worst_id, steps=2*steps, sample_count=refined['sample_count'], **refinement),
          source_count=len(fingerprints), elapsed_seconds=round(time.monotonic()-started, 2),
          plans=[{k:r[k] for k in ('plan_id', 'sample_count', 'summary')} for r in reports]))
    print('COMPLETE', dict(totals), 'refinement', refinement, flush=True)


if __name__ == '__main__':
    main()
