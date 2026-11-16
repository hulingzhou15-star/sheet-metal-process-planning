"""Verify the delivered stage schedule, sweep coverage and replay references."""
import argparse
from collections import Counter
from pathlib import Path

import numpy as np

from information_extract.three_d.artifacts import ROOT, read, sha, verify_sources, write
from information_extract.three_d.p002_curve_motion import frame
from information_extract.three_d.p002_staged_material import FORMS, REMOVALS
from information_extract.scripts.check_p002_curve_motion import DEST, OLD, STEP


def verify(folder):
    sources = read(folder/'inputs.json')['files']
    verify_sources(sources)
    output = read(folder/'verification.json')
    model = read(folder/'model.json')
    plan_data = read(folder/'plans.json')
    if plan_data != read(OLD/'plans.json'):
        raise ValueError('Candidate assignments, ranking or scores differ')
    staging = read(folder/'material_states.json')
    audit = read(folder/'material_audit.json')
    if audit['unapplied_operation_ids']:
        raise ValueError('Incomplete scheduled material')
    records = {(r['plan_id'],r['station_id'],r['phase']):r for r in staging['records']}
    variants = {r['material_id']:r for r in staging['variants']}
    if (folder/'replay_scene.json').exists():
        scene = read(folder/'replay_scene.json')
        geometries = {p.name.removesuffix('.json.gz') for p in (folder/'meshes').glob('*.json.gz')}
    else:
        scene = read(folder/'scene.json')
        geometries = scene['geometries']
    if scene['model'] != model or len(records)!=320:
        raise ValueError('Model or material record coverage differs')
    for variant in variants.values():
        for gid in variant['cells'].values():
            if not (folder/'brep'/f'{gid}.brep').is_file():
                raise ValueError('Missing material BRep')
    stations = {s['station_id']:s['order'] for s in model['stations']}
    kinds = {o['operation_id']:o['operation_type'] for o in model['operations']}
    totals = Counter()
    sample_count = 0
    issues = {}
    curve_action_samples = Counter()
    for p in plan_data['plans']:
        pid = p['plan_id']
        a = p['assignments']
        report = read(folder/pid/'collision_report.json')
        if a != report['assignments'] or sha(folder/pid/'completed.step') != sha(STEP):
            raise ValueError('Plan or final STEP changed')
        observed = Counter()
        for r in report['runs']:
            sid,phase = r['station_id'],r['phase']
            material_phase = 'completed' if phase=='completed' else 'entry'
            st = records[pid,sid,material_phase]
            processed = {o for o,s in a.items() if stations[s] < stations[sid] or (s==sid and phase=='completed')}
            if r['material_id'] != st['material_id'] or set(r['executed_operation_ids']) != processed:
                raise ValueError('Material state does not match candidate progress')
            for typ, key in ((REMOVALS,'executed_removal_ids'),(FORMS,'executed_forming_ids')):
                expected = {o for o in processed if kinds[o] in typ}
                if set(st[key]) != expected or set(r[key]) != expected:
                    raise ValueError('Material event boundary differs')
            expected_pose = frame(model,p,sid,phase,r['tau'])
            variant = variants[st['material_id']]
            for cid,gid in variant['cells'].items():
                progress = expected_pose['curve_fractions'].get(cid)
                expected_gid = gid if progress is None else f'{gid}_sweep_{progress:.9f}'
                if r['geometry_ids'][cid] != expected_gid or expected_gid not in geometries:
                    raise ValueError('Missing or wrong pose mesh')
                pose = expected_pose['transforms'][cid] if progress is None else np.eye(4)
                if not np.allclose(r['transforms'][cid],pose,atol=1e-10,rtol=0):
                    raise ValueError('Pose does not match candidate progress')
                if progress is not None and 0<progress<1:
                    curve_action_samples[model['curve_guides'][cid]['operation_id']] += 1
            g = r['reference_geometry']
            if r['unknown_motion_pairs'] or r['unapplied_material_event_ids'] or g['pair_count'] != 171 or sum(g['counts'].values()) != 171:
                raise ValueError('Pair denominator or applicability mismatch')
            observed.update(g['counts'])
            for row in g['narrow_phase_pairs']:
                if row['status'] in {'MESH_CROSSING','MESH_CONTAINMENT','OVERLAP','UNKNOWN_KERNEL_FAILURE'}:
                    key = (row['status'],*row['region_ids'])
                    issue = issues.setdefault(key,dict(samples=0,plans=set(),first=[pid,sid,phase,r['tau']]))
                    issue['samples'] += 1
                    issue['plans'].add(pid)
        if dict(observed) != report['summary']['counts']:
            raise ValueError('Plan count disagrees with samples')
        totals.update(observed)
        sample_count += len(report['runs'])
    rows = [dict(status=status,cells=[a,b],samples=r['samples'],plans=sorted(r['plans']),first=r['first'])
            for (status,a,b),r in sorted(issues.items(),key=lambda item:-item[1]['samples'])]
    result = dict(**output, sample_count=sample_count, pair_state_counts=dict(totals),
        material_variants=len(variants), material_operations=len(audit['operations']),
        curve_wall_samples=dict(curve_action_samples), issue_pairs=rows,
        geometry_count=len(geometries),
        mode='Discrete geometric diagnostic, including surface contact for non-rigid sweeps')
    write(folder/'replay_verification.json',result)
    output_files = [*folder.glob('*.json'),*folder.glob('*.html'),*folder.glob('*.js'),
        *folder.glob('NP2_*/*'),*folder.glob('brep/*.brep'),*folder.glob('curve_validation/*.json'),
        *folder.glob('meshes/*.json.gz')]
    write(folder/'output_manifest.json',dict(files={f.relative_to(ROOT).as_posix():sha(f)
                                                  for f in output_files if f.name!='output_manifest.json'}))
    return result


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=DEST)
    result = verify(parser.parse_args().input.resolve())
    print({k:v for k,v in result.items() if k!='issue_pairs'})
    print('Flagged cell pairs:',len(result['issue_pairs']))
