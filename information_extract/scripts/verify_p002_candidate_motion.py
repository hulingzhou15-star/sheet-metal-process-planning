"""Separate pose coverage, material consistency and actual process completion."""
import argparse
from collections import Counter
from pathlib import Path

from information_extract.three_d.artifacts import ROOT,read,write,sha,verify_sources

DEFAULT=ROOT/'information_extract/outputs/p002_candidate_motion_v01'


def analyze(folder):
    verify_sources(read(folder/'inputs.json')['files']);verify_sources(read(folder/'output_manifest.json')['files'])
    model=read(folder/'model.json');plans=read(folder/'plans.json')['plans'];orders={s['station_id']:s['order'] for s in model['stations']}
    domains={d['operation_id']:set(d['cell_ids']) for d in read(ROOT/'information_extract/outputs/p002_material_cells_v01/cells.json')['domains']}
    bends=set(domains);others={o['operation_id'] for o in model['operations']}-bends
    total=Counter();rows=[];all_known=set();all_unknown=set();sources={}
    for p in plans:
        path=folder/p['plan_id']/'collision_report.json';sources[path.relative_to(ROOT).as_posix()]=sha(path)
        r=read(path);counts=Counter();known=set();unknown=set()
        for s in r['runs']:
            valid=not s['unexecuted_material_event_ids'];n=s['reference_geometry']['pair_count']
            counts['completed_material_consistent_samples']+=int(valid)
            counts['known_pose_and_completed_material_pair_states' if valid else 'known_pose_but_material_reference_only_pair_states']+=n
            counts['unknown_motion_pair_states']+=len(s['unknown_motion_pairs'])
            if valid:
                counts['completed_material_consistent_overlap_pair_states']+=s['reference_geometry']['counts'].get('OVERLAP',0)
                counts['completed_material_consistent_kernel_unknown_pair_states']+=s['reference_geometry']['counts'].get('UNKNOWN_KERNEL_FAILURE',0)
            if s['phase']=='motion':
                for oid in set(s['current_operation_ids'])&bends:
                    (unknown if domains[oid]&set(s['unknown_cells']) else known).add(oid)
        if known&unknown or known|unknown!=bends:raise ValueError('Inconsistent motion coverage')
        expected=171*r['sample_count']
        if sum(counts[k] for k in ('known_pose_and_completed_material_pair_states','known_pose_but_material_reference_only_pair_states','unknown_motion_pair_states'))!=expected:raise ValueError('Pair denominator lost')
        before=max(orders[p['assignments'][o]] for o in others)<min(orders[p['assignments'][o]] for o in bends)
        rows.append(dict(plan_id=p['plan_id'],all_nonbend_actions_before_first_bend=before,
            supported_reference_motion_operation_ids=sorted(known),unknown_motion_operation_ids=sorted(unknown),counts=dict(counts)))
        total.update(counts);all_known.update(known);all_unknown.update(unknown)
    result=dict(part_id='P002',plans=rows,totals=dict(total),
        supported_reference_motion_operation_ids=sorted(all_known),unknown_motion_operation_ids=sorted(all_unknown),
        nonbend_before_bend_plan_ids=[r['plan_id'] for r in rows if r['all_nonbend_actions_before_first_bend']],
        completed_material_consistency_rule='Only after all 115 non-bend actions are complete can common completed material represent the current rigid-region material; finite bands still omitted',
        pose_scope='Existing rigid midplane-hinge approximation, not finite-width calibrated motion',
        full_process_status='UNKNOWN_INCOMPLETE_MODEL',manufacturing_safety_proved=False,
        source_sha256=sources,analysis_implementation_sha256=sha(Path(__file__)))
    write(folder/'coverage_analysis.json',result);return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,default=DEFAULT);args=p.parse_args()
    result=analyze(args.input.resolve());print(result['totals']);print('UNKNOWN',result['unknown_motion_operation_ids'])


if __name__=='__main__':main()
