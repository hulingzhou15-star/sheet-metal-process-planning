"""Generate new P003 candidates with STEP-axis motion feedback."""
import argparse
from collections import Counter
from pathlib import Path
import time
from uuid import uuid4

from information_extract.scripts.check_p003_axis_sensitivity import cylinder_axis_model
from information_extract.scripts.check_p003_candidate_motion import (
    MODULE, PREP, SELECTED, STEP, VariantChecker, complete_material, evaluate_plan,
    load_material, protected_hashes, setup, verify_run,
)
from information_extract.scripts.check_p003_remaining_motion import OUT as PREVIOUS
from information_extract.three_d.artifacts import read, relative, sha, source_manifest, verify_sources, write
from information_extract.three_d.p003_motion import FORMS, REMOVALS, frame, samples
from information_extract.three_d.project_geometry_scoring import ProjectGeometryScorer, geometry_context
from src.candidate.distance import plan_hash
from src.neighborhood_penalty import load_config
from src.neighborhood_penalty.generation import ranking_key, select_candidates

OUT = MODULE/'outputs/p003_motion_regeneration_v01'
COARSE_STEPS = 24
FINAL_STEPS = 96


def write_checkpoint(path, value):
    temporary = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        write(temporary, value)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def prove_material_order(context, bend_ops):
    material = [op for op, row in context.operations.items() if row.operation_type in REMOVALS | FORMS]
    latest_material = max(st.order for op in material for st in context.allowed_stations(op))
    earliest_bend = min(st.order for op in bend_ops for st in context.allowed_stations(op))
    if latest_material >= earliest_bend:
        raise ValueError('Cannot share fold checks when material changes during folding')


def previous_evidence(axis_geometry, bend_ops):
    report_path = PREVIOUS/'pool_interference.json'
    report = read(report_path)
    pool = read(SELECTED/'pool.json')['plans']
    by_id = {row['plan_id']:row for row in pool}
    if (len(by_id) != len(pool) or set(by_id) != {row['plan_id'] for row in report['plans']}
            or report['steps'] != COARSE_STEPS or report['j297_pivots'] != axis_geometry):
        raise ValueError('Previous motion evidence differs from the current screening inputs')
    verify_sources(report['source_files'])
    positives = {}
    for row in report['plans']:
        pid = row['plan_id']
        evidence = row['axis_sensitivity']
        if not evidence['confirmed']:
            continue
        run = read(row['report'])
        assignment = by_id[pid]['assignments']
        if run['assignments'] != assignment:
            raise ValueError(f'Previous motion assignments differ for {pid}')
        witness = next(sample for sample in evidence['samples'] if sample['step_axis_status'] == 'OVERLAP')
        signature = tuple(assignment[op] for op in bend_ops)
        positives.setdefault(signature, dict(status='overlap', previous_plan_id=pid, witness=witness))
    retained = [by_id[row['plan_id']] for row in report['plans'] if not row['axis_sensitivity']['confirmed']]
    return positives, retained, source_manifest([report_path, SELECTED/'pool.json'])


def screen_early_material(model, candidates, material, checker):
    accepted, dropped = [], []
    for plan in candidates:
        for sid, phase, tau in samples(model, plan, COARSE_STEPS):
            state = frame(model, plan, sid, phase, tau)
            if complete_material(state):
                accepted.append(plan)
                break
            key = tuple(state['executed_removal_ids']), tuple(state['executed_forming_ids'])
            variant = material[key]
            counts = checker.check(variant['cells'], state['transforms'])['counts']
            if counts.get('OVERLAP') or counts.get('UNKNOWN_KERNEL_FAILURE') or variant['pending_unmodelled']:
                dropped.append(dict(plan_id=plan['plan_id'], plan_hash=plan['plan_hash'], station_id=sid,
                                    phase=phase, counts=counts, pending=variant['pending_unmodelled']))
                break
        else:
            raise ValueError('Full material was never reached')
    return accepted, dropped


def bend_sequence(stations, station_ranks):
    ordered = sorted(set(stations), key=station_ranks.get)
    groups = {station:index for index, station in enumerate(ordered)}
    return tuple(groups[station] for station in stations)


def choose_plans(context, pool, config):
    # Empty bend stations do not distinguish otherwise identical synchronized paths.
    representatives = {}
    for row in sorted(pool, key=ranking_key):
        representatives.setdefault(bend_sequence(row['bend_stations'], context.station_ranks), row)
    selected, trace = select_candidates(context, list(representatives.values()), config)
    supplements = []
    if len(selected) < config['generation']['selected_target']:
        supplement, _ = select_candidates(context, pool, config)
        selected_hashes = {row['plan_hash'] for row in selected}
        supplements = [row for row in supplement if row['plan_hash'] not in selected_hashes]
        supplements = supplements[:config['generation']['selected_target']-len(selected)]
        selected.extend(supplements)
    return selected, dict(bend_representatives=trace, supplements=[row['plan_id'] for row in supplements])


def search_paths(context, catalogue, config, primary, screen, prior, retained, log_path, sources,
                 path_target, path_budget):
    from information_extract.three_d.p003_rescreen_search import BendSearch

    # A driver or solver edit does not invalidate sampled geometry. Checker, assets and grids do.
    driver = relative(Path(__file__))
    motion_sources = {path:hash_ for path, hash_ in sources.items() if path != driver}
    history = read(log_path) if log_path.exists() else dict(sources=motion_sources, paths=[])
    if {path:hash_ for path, hash_ in history['sources'].items() if path != driver} != motion_sources:
        raise ValueError('Saved motion results have different input fingerprints')
    rows = {tuple(row['bend_stations']):row for row in history['paths']}
    for row in rows.values():
        fine = row['refined']
        if (row['coarse']['subdivisions'] != COARSE_STEPS
                or (fine is not None and fine['subdivisions'] != FINAL_STEPS)
                or (row['status'] == 'passed' and fine is None)):
            raise ValueError('Saved motion results have incompatible sample grids')
    retained_keys = [tuple(row['assignments'][op] for op in screen.bends) for row in retained]
    search = BendSearch(context, catalogue, config, primary, retained[0]['assignments'],
                        [*prior, *rows, *retained_keys])

    def record(assignment):
        signature = search.signature(assignment)
        coarse = screen.check(assignment, subdivisions=COARSE_STEPS)
        refined = screen.check(assignment, subdivisions=FINAL_STEPS) if coarse['status'] == 'passed' else None
        status = (refined or coarse)['status']
        rows[signature] = dict(bend_stations=list(signature), status=status, coarse=coarse, refined=refined)
        write_checkpoint(log_path, dict(sources=motion_sources, paths=list(rows.values())))
        return status

    for plan in retained:
        assignment = plan['assignments']
        if search.signature(assignment) not in rows and record(assignment) != 'passed':
            raise ValueError(f"Retained plan {plan['plan_id']} does not reproduce the dense STEP-axis pass")
    passes = {bend_sequence(key, context.station_ranks) for key,row in rows.items() if row['status'] == 'passed'}
    status = 'path_budget_reached'
    solver_status = None
    while len(passes) < path_target and len(rows) < path_budget:
        trial = search.next()
        solver_status = trial['status']
        if not trial['assignments']:
            status = 'projection_exhausted' if solver_status == 'INFEASIBLE' else 'solver_incomplete'
            break
        outcome = record(trial['assignments'])
        if outcome == 'passed':
            passes.add(bend_sequence(search.signature(trial['assignments']), context.station_ranks))
        if outcome == 'passed' or len(rows) % 25 == 0:
            print('PATHS', len(rows), 'checked;', len(passes), 'distinct motion passes;', outcome, flush=True)
    if len(passes) >= path_target:
        status = 'passing_path_target_reached'
    return rows, status, solver_status


def selected_report(model, axis_model, plan, material, checker):
    completed = {}
    axis_poses = {}
    cells = {cell['cell_id']:cell['cell_id'] for cell in model['cells']}
    for sid, phase, tau in samples(axis_model, plan, FINAL_STEPS):
        state = frame(axis_model, plan, sid, phase, tau)
        if complete_material(state):
            key = sid, phase, tau
            axis_poses[key] = state['transforms']
            completed[key] = checker.check(cells, state['transforms'])
    report = evaluate_plan(model, plan, material, checker, FINAL_STEPS, completed_checks=completed)
    for state in report['runs']:
        pose = axis_poses.get((state['station_id'], state['phase'], state['tau']))
        if pose is not None:
            state['transforms'] = pose
    report['motion_reference'] = dict(partial_material='REGISTERED_DXF_FLAT_PLACEMENT',
                                      completed_material='J297_STEP_CYLINDER_AXIS')
    verify_run(model, [report], material)
    return report


def build_and_verify_pool(context, catalogue, config, primary, scorer, allowed, retained,
                          model, axis_model, asset, paths, sources):
    from information_extract.three_d.p003_rescreen_search import build_passing_pool

    accepted, rejected, trials, material_batches = [], [], [], {}
    excluded = []
    generated_ids = {}
    seeds = retained
    while True:
        pool, audit = build_passing_pool(context, catalogue, config, primary, scorer, allowed,
                                         seed_plans=seeds, excluded_assignments=excluded)
        trials.append(audit)
        fresh = [row for row in pool if row['plan_hash'] not in material_batches]
        if not fresh:
            break
        for row in fresh:
            key = row['plan_hash']
            if key != plan_hash(row['assignments']):
                raise ValueError('Candidate hash and assignments differ')
            if key not in generated_ids:
                generated_ids[key] = row['plan_id'] if key in {p['plan_hash'] for p in retained} else (
                    f'P003_R{len(generated_ids)+1:03d}')
            row['plan_id'] = generated_ids[key]
            row['bend_stations'] = [row['assignments'][op] for op in sorted(
                o['operation_id'] for o in model['operations'] if o['operation_type'] == 'BEND')]
        stage_dir = OUT/'staged'/f'batch_{len(trials):02d}'
        print('STAGED MATERIAL', len(fresh), 'candidate plans', flush=True)
        material = load_material(model, fresh, asset, paths, sources, stage_dir)
        checker = VariantChecker(axis_model, stage_dir)
        passed, failures = screen_early_material(model, fresh, material, checker)
        for row in fresh:
            material_batches[row['plan_hash']] = (material, checker, stage_dir)
        accepted.extend(passed)
        rejected.extend(failures)
        rejected_hashes = {row['plan_hash'] for row in failures}
        excluded.extend(row['assignments'] for row in fresh if row['plan_hash'] in rejected_hashes)
        if not failures or len(accepted) >= config['generation']['pool_target']:
            break
        seeds = accepted

    selected, selection = choose_plans(context, accepted, config)
    for row in selected:
        material, checker, stage_dir = material_batches[row['plan_hash']]
        report = selected_report(model, axis_model, row, material, checker)
        counts = report['summary']['counts']
        if counts.get('OVERLAP') or counts.get('UNKNOWN_KERNEL_FAILURE') or report['summary']['samples_with_unapplied_material_events']:
            raise ValueError(f"Complete staged motion did not reproduce passage for {row['plan_id']}")
        report_path = OUT/'selected'/row['plan_id']/'collision_report.json'
        write(report_path, report)
        row['motion_verification'] = dict(sample_count=report['sample_count'], pair_states=sum(counts.values()),
                                          report=relative(report_path), material_directory=relative(stage_dir))
    return accepted, selected, dict(generation=trials, selection=selection, rejected_early_material=rejected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--passing-path-target', type=int, default=16)
    parser.add_argument('--max-paths', type=int, default=2048)
    args = parser.parse_args()
    if not 1 <= args.passing_path_target <= args.max_paths:
        parser.error('Expected 1 <= passing path target <= max paths')
    from information_extract.three_d import p003_rescreen_motion, p003_rescreen_search

    started = time.monotonic()
    protection = protected_hashes()
    model, _, asset, paths, sources = setup()
    if asset['asset_id'] != sha(STEP):
        raise ValueError('STEP axis evidence is stale')
    facts = read(PREP/'facts.json')
    context, _ = geometry_context(facts, paths, {'rows': []})
    config = load_config()
    catalogue = read(SELECTED/'catalogue.json')
    primary = read(SELECTED/'primary.json')
    scorer = ProjectGeometryScorer(context, catalogue, config, read(PREP/'material_basis.json'))
    bend_ops = sorted(o['operation_id'] for o in model['operations'] if o['operation_type'] == 'BEND')
    prove_material_order(context, bend_ops)
    axis_model, axes = cylinder_axis_model(model, asset)
    prior, retained, evidence_sources = previous_evidence(axes, bend_ops)
    sources.update(evidence_sources)
    sources.update(source_manifest([Path(__file__), SELECTED/'catalogue.json', SELECTED/'primary.json',
        Path(p003_rescreen_motion.__file__), Path(p003_rescreen_search.__file__),
        MODULE/'scripts/check_p003_axis_sensitivity.py']))
    verify_sources(sources)
    screen = p003_rescreen_motion.MotionScreen(model, asset)
    print('INITIAL', len(prior), 'confirmed forbidden bend assignments;', len(retained), 'prior candidates', flush=True)
    write(OUT/'previous_collisions.json', dict(bend_operations=bend_ops,
        collisions=[dict(bend_stations=list(key), **value) for key,value in sorted(prior.items())]))
    rows, termination, solver_status = search_paths(context, catalogue, config, primary, screen, prior,
        retained, OUT/'path_checks.json', dict(sources), args.passing_path_target, args.max_paths)
    allowed = [key for key,row in rows.items() if row['status'] == 'passed']
    print('FOLD SEARCH COMPLETE', termination, len(allowed), 'passing bend assignments', flush=True)
    pool, selected, audit = build_and_verify_pool(context, catalogue, config, primary, scorer, allowed,
        retained, model, axis_model, asset, paths, sources)
    verify_sources(sources)
    if protected_hashes() != protection:
        raise ValueError('Protected working inputs changed')
    write(OUT/'pool.json', dict(part_id='P003', plans=pool))
    write(OUT/'plans.json', dict(part_id='P003', plans=selected,
                               ranking=[row['plan_id'] for row in sorted(selected, key=ranking_key)]))
    summary = dict(part_id='P003', axes=axes, coarse_subdivisions=COARSE_STEPS, refined_subdivisions=FINAL_STEPS,
        termination=termination, terminal_solver_status=solver_status, excluded_previous_paths=len(prior),
        checked_path_counts=dict(Counter(row['status'] for row in rows.values())),
        processed_paths=len(rows), passing_bend_assignments=len(allowed), pool_size=len(pool), selected_count=len(selected),
        passing_motion_sequences=len({bend_sequence(key, context.station_ranks) for key in allowed}),
        selected_bend_assignments=len({tuple(row['bend_stations']) for row in selected}),
        selected_motion_sequences=len({bend_sequence(row['bend_stations'], context.station_ranks) for row in selected}),
        selected_sample_count=sum(row['motion_verification']['sample_count'] for row in selected),
        selected_pair_states=sum(row['motion_verification']['pair_states'] for row in selected),
        elapsed_seconds=round(time.monotonic()-started, 2), sources=sources, **audit)
    write(OUT/'summary.json', summary)
    print('COMPLETE', len(pool), 'pool plans;', len(selected), 'selected;', summary['selected_bend_assignments'],
          'selected bend assignments', flush=True)


if __name__ == '__main__':
    main()
