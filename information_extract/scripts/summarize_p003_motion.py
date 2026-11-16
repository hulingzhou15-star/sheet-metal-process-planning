"""Group P003 sampled interference by physical region pair and overfold phase."""
from collections import Counter, defaultdict

from information_extract.scripts.check_p003_candidate_motion import OUT
from information_extract.three_d.artifacts import read, relative, sha, verify_sources, write


def analyze():
    summary = read(OUT/'summary.json')
    plans = read(OUT/'plans.json')
    inputs = read(OUT/'inputs.json')
    verify_sources(inputs['files'])
    grouped = defaultdict(lambda:dict(sample_count=0,plan_ids=set(),joint_angle_deg=[float('inf'),-float('inf')]))
    phase_counts = Counter()
    plan_rows = []
    for plan in plans['plans']:
        pid = plan['plan_id']
        report = read(OUT/pid/'collision_report.json')
        if report['assignments']!=plan['assignments']:
            raise ValueError('Report changed the selected assignment')
        peak = None
        counts = Counter()
        for sample in report['runs']:
            angle = sample['joint_angles_deg']['J297']
            first,second = plan['assignments']['O069'],plan['assignments']['O120']
            region = ('O069 station' if sample['station_id']==first else 'O120 station'
                      if sample['station_id']==second else 'between steps' if angle>90. else 'other')
            for p in sample['reference_geometry']['narrow_phase_pairs']:
                if p['status']!='OVERLAP':
                    continue
                v = p['intersection_volume_mm3']
                counts['all'] += 1
                counts['adjacent' if p['cad_hinge_adjacent'] else 'nonadjacent'] += 1
                phase_counts[region] += 1
                pair = tuple(p['region_ids'])
                row = grouped[pair]
                row['sample_count'] += 1
                row['plan_ids'].add(pid)
                span = row['joint_angle_deg']
                span[0],span[1] = min(span[0],angle),max(span[1],angle)
                item = dict(plan_id=pid,station_id=sample['station_id'],phase=sample['phase'],tau=sample['tau'],
                            joint_angle_deg=angle,cells=list(pair),volume_mm3=v)
                if peak is None or v>peak['volume_mm3']:
                    peak = item
                if 'peak' not in row or v>row['peak']['volume_mm3']:
                    row.update(peak=item,cad_hinge_adjacent=p['cad_hinge_adjacent'])
        plan_rows.append(dict(plan_id=pid,counts=dict(counts),peak=peak))
    rows = [dict(cells=list(key),**{**v,'plan_ids':sorted(v['plan_ids'])}) for key,v in sorted(grouped.items())]
    if sum(x['sample_count'] for x in rows)!=summary['counts'].get('OVERLAP',0):
        raise ValueError('Interference summary does not match the sampled reports')
    write(OUT/'overlap_analysis.json',dict(pairs=rows,plans=plan_rows,phase_counts=dict(phase_counts),
        plans_with_positive_overlap=sum(r['counts'].get('all',0)>0 for r in plan_rows),
        plans_with_nonadjacent_overlap=sum(r['counts'].get('nonadjacent',0)>0 for r in plan_rows),
        positive_overlap_pair_states=summary['counts'].get('OVERLAP',0)))
    paths = [*OUT.glob('*.json'),*OUT.glob('NP2_*/*'),*OUT.glob('brep/*.brep'),*OUT.glob('meshes/*.json.gz'),
             *OUT.glob('bend_checkpoints/*.json'),*OUT.glob('refined/*/collision_report.json')]
    write(OUT/'output_manifest.json',dict(files={relative(p):sha(p) for p in sorted(paths)
                                                if p.is_file() and p.name!='output_manifest.json'}))
    print('Overlap groups',[(r['cells'],r['sample_count'],r['peak']['volume_mm3']) for r in rows])


if __name__=='__main__':
    analyze()
