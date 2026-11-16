"""Verify the P003 pool reports and cross-check nonadjacent overlap on the STEP J297 axis."""
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from information_extract.scripts.check_p003_axis_sensitivity import cylinder_axis_model, overlap
from information_extract.scripts.check_p003_candidate_motion import (
    CHECK_CONFIG, MODULE, OUT, PREP, SELECTED, STEP, complete_material, setup,
)
from information_extract.three_d.artifacts import read, relative, sha, source_manifest, verify_sources, write
from information_extract.three_d.assets import bounds
from information_extract.three_d.local_forming import load_shape
from information_extract.three_d.p003_motion import frame, samples


REMAINING = MODULE/'outputs/p003_candidate_motion_remaining_v01'
STEPS = 24


def matches_plans(model, plan, report):
    if report['plan_id'] != plan['plan_id'] or report['assignments'] != plan['assignments']:
        raise ValueError(f'Motion report differs from pool assignment for {plan["plan_id"]}')
    actual = [(run['station_id'], run['phase'], run['tau']) for run in report['runs']]
    if actual != list(samples(model, plan, STEPS)) or len(actual) != report['sample_count']:
        raise ValueError(f'Incomplete {STEPS}-subdivision sampling for {plan["plan_id"]}')


def sample_evidence(run, pair):
    return dict(station_id=run['station_id'], phase=run['phase'], tau=run['tau'],
                cells=pair['region_ids'], volume_mm3=pair['intersection_volume_mm3'],
                joint_angle_deg=run['joint_angles_deg']['J297'])


def summarize_report(model, report):
    pair_count = len(model['cells'])*(len(model['cells'])-1)//2
    threshold = CHECK_CONFIG['volume_tolerance_mm3']
    cadence = {s['station_id']:s['order'] for s in model['stations']}
    adjacent = {frozenset((j['parent_cell_id'], j['child_cell_id'])) for j in model['joints']}

    def stations(rows):
        return sorted({r['station_id'] for r, _ in rows}, key=cadence.get)

    groups = {'adjacent': [], 'nonadjacent': []}
    counts = Counter()
    for run in report['runs']:
        result = run['reference_geometry']
        if result['pair_count'] != pair_count or sum(result['counts'].values()) != pair_count:
            raise ValueError(f'Incomplete region pair count for {report["plan_id"]}')
        counts.update(result['counts'])
        for pair in result['narrow_phase_pairs']:
            if pair['status'] != 'OVERLAP':
                continue
            if not pair['intersection_volume_mm3'] > threshold:
                raise ValueError('Reported overlap is below its positive-volume threshold')
            if pair['cad_hinge_adjacent'] != (frozenset(pair['region_ids']) in adjacent):
                raise ValueError('Reported adjacency differs from the material hinge graph')
            key = 'adjacent' if pair['cad_hinge_adjacent'] else 'nonadjacent'
            groups[key].append((run, pair))
    if dict(counts) != report['summary']['counts'] or sum(map(len, groups.values())) != counts['OVERLAP']:
        raise ValueError(f'Inconsistent totals for {report["plan_id"]}')
    by_geometry = {}
    pair_rows = []
    for name, rows in groups.items():
        rows.sort(key=lambda r:r[1]['intersection_volume_mm3'], reverse=True)
        by_pair = defaultdict(list)
        for run, pair in rows:
            by_pair[tuple(pair['region_ids'])].append((run, pair))
        pair_rows.extend(dict(cells=list(cells), cad_hinge_adjacent=name == 'adjacent',
                              pair_states=len(records), stations=stations(records),
                              peak=sample_evidence(*records[0])) for cells, records in sorted(by_pair.items()))
        by_geometry[name] = dict(pair_states=len(rows), peak=sample_evidence(*rows[0]) if rows else None,
                                 stations=stations(rows))
    final = report['runs'][-1]['reference_geometry']['counts']
    return dict(counts=dict(counts), **by_geometry, pairs=pair_rows,
                final_overlap_pair_states=final.get('OVERLAP', 0),
                final_unknown_pair_states=final.get('UNKNOWN_KERNEL_FAILURE', 0)), groups['nonadjacent']


def confirm_alternate_axis(plan, rows, alternate, shapes, boxes, cache):
    attempts = []
    untested_material = 0
    for run, pair in rows:
        if not complete_material(run) or any(cid != gid for cid, gid in run['geometry_ids'].items()):
            untested_material += 1
            continue
        evidence = sample_evidence(run, pair)
        cells = tuple(evidence['cells'])
        state = frame(alternate, plan, evidence['station_id'], evidence['phase'], evidence['tau'])
        if state['joint_angles_deg'] != run['joint_angles_deg']:
            raise ValueError(f'STEP pivot changed the joint angles for {plan["plan_id"]}')
        # This tuple uniquely determines P003's rigid transforms in the alternate model.
        key = cells, tuple(state['joint_angles_deg'].items())
        if key not in cache:
            cache[key] = overlap(shapes, boxes, state['transforms'], cells)
            if not np.isfinite(cache[key]['intersection_volume_mm3']):
                raise ValueError(f'Nonfinite STEP-axis volume for {plan["plan_id"]}')
        result = cache[key]
        attempts.append(dict(**evidence, step_axis_volume_mm3=result['intersection_volume_mm3'],
                             step_axis_status=result['status']))
        if result['status'] == 'OVERLAP':
            return dict(confirmed=True, samples=attempts)
    return dict(confirmed=False, samples=attempts, samples_without_complete_material=untested_material)


def analyze():
    model, selected, asset, _, base_sources = setup()
    if asset['asset_id'] != sha(STEP):
        raise ValueError('Stale STEP cylinder evidence')
    alternate, axes = cylinder_axis_model(model, asset)
    # The original and alternate pivots must recover the same final STEP reference.
    last = max(model['stations'], key=lambda s:s['order'])['station_id']
    pool_path = SELECTED/'pool.json'
    pool = read(pool_path)['plans']
    if len(pool) != 64 or len({p['plan_id'] for p in pool}) != 64 or len({p['plan_hash'] for p in pool}) != 64:
        raise ValueError('P003 must contain 64 distinct generated candidates')
    if len({frozenset(p['assignments'].items()) for p in pool}) != 64:
        raise ValueError('Duplicate assignment in the P003 input pool')
    original = {p['plan_id']:p['assignments'] for p in selected['plans']}
    expanded = {p['plan_id']:p['assignments'] for p in read(REMAINING/'plans.json')['plans']}
    if len(original) != 16 or len(expanded) != 48 or original.keys() & expanded.keys():
        raise ValueError('Expected disjoint sets of original and remaining plans')
    if {p['plan_id']:p['assignments'] for p in pool} != original | expanded:
        raise ValueError('Original and remaining assignments do not reconstruct the pool')
    files = [pool_path, Path(__file__), MODULE/'scripts/check_p003_axis_sensitivity.py']
    for folder in (OUT, REMAINING):
        manifest = folder/'inputs.json'
        data = read(manifest)
        if data['steps'] != STEPS or read(folder/'model.json') != model:
            raise ValueError(f'Motion model or subdivision mismatch in {relative(folder)}')
        verify_sources(data['files'])
        files.extend([manifest, folder/'model.json', folder/'plans.json'])
    shapes = {cid:load_shape(PREP/'brep'/f'{cid}.brep') for cid in (r['cell_id'] for r in model['cells'])}
    boxes = {cid:bounds(shape) for cid,shape in shapes.items()}

    plans = []
    cache = {}
    total = Counter()
    by_pair = {}
    for plan in pool:
        pid = plan['plan_id']
        source = (OUT if pid in original else REMAINING)/pid/'collision_report.json'
        files.append(source)
        report = read(source)
        matches_plans(model, plan, report)
        if any(not np.allclose(m, np.eye(4), atol=1e-10, rtol=0)
               for m in frame(alternate, plan, last, 'completed', 1.)['transforms'].values()):
            raise ValueError(f'STEP pivot changed final poses for {pid}')
        data, overlaps = summarize_report(model, report)
        confirmation = confirm_alternate_axis(plan, overlaps, alternate, shapes, boxes, cache)
        plans.append(dict(plan_id=pid, sample_count=report['sample_count'], report=relative(source),
                          set='selected' if pid in original else 'remaining', **data,
                          axis_sensitivity=confirmation))
        total.update(data['counts'])
        for row in data['pairs']:
            key = tuple(row['cells'])
            agg = by_pair.setdefault(key, dict(cells=row['cells'], cad_hinge_adjacent=row['cad_hinge_adjacent'],
                                               pair_states=0, plan_ids=[], peak=dict(plan_id=pid, **row['peak'])))
            agg['pair_states'] += row['pair_states']
            agg['plan_ids'].append(pid)
            if row['peak']['volume_mm3'] > agg['peak']['volume_mm3']:
                agg['peak'] = dict(plan_id=pid, **row['peak'])
    sources = base_sources | source_manifest(files)
    verify_sources(sources)
    result = dict(part_id='P003', plan_count=len(plans), selected_count=len(original), remaining_count=len(expanded),
                  steps=STEPS, sample_count=sum(p['sample_count'] for p in plans), pair_states=sum(total.values()),
                  counts=dict(total), nonadjacent_pair_states=sum(p['nonadjacent']['pair_states'] for p in plans),
                  adjacent_pair_states=sum(p['adjacent']['pair_states'] for p in plans),
                  plans_with_nonadjacent_overlap=sum(bool(p['nonadjacent']['pair_states']) for p in plans),
                  plans_with_adjacent_overlap=sum(bool(p['adjacent']['pair_states']) for p in plans),
                  plans_with_unknowns=[p['plan_id'] for p in plans if p['counts'].get('UNKNOWN_KERNEL_FAILURE')],
                  plans_with_final_overlap=[p['plan_id'] for p in plans if p['final_overlap_pair_states']],
                  plans_confirmed_on_step_axis=sum(p['axis_sensitivity']['confirmed'] for p in plans),
                  step_axis_intersections_evaluated=len(cache), j297_pivots=axes, plans=plans,
                  pairs=[by_pair[key] for key in sorted(by_pair)],
                  scope='Sampled rigid material pairs and per-plan nonadjacent witness with alternate J297 pivot',
                  manufacturing_safety_proved=False, source_files=sources)
    write(REMAINING/'pool_interference.json', result)
    print('POOL', len(plans), 'plans', result['sample_count'], 'samples', result['pair_states'], 'pair-states',
          result['plans_with_nonadjacent_overlap'], 'with nonadjacent overlap',
          result['plans_confirmed_on_step_axis'], 'with STEP-axis confirmation', flush=True)
    print('COUNTS', dict(total), 'adjacent', result['adjacent_pair_states'],
          'nonadjacent', result['nonadjacent_pair_states'], 'STEP-axis intersection poses', len(cache), flush=True)
    for row in result['pairs']:
        print('PAIR', '/'.join(row['cells']), len(row['plan_ids']), 'plans', row['pair_states'], 'pair-states',
              'peak', row['peak']['plan_id'], row['peak']['station_id'], row['peak']['tau'],
              round(row['peak']['volume_mm3'], 6), flush=True)


if __name__ == '__main__':
    analyze()
