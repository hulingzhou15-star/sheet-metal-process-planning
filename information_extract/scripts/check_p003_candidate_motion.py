"""Replay staged P003 material through the 16 selected plans and check rigid-cell pairs."""
import argparse
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
import gzip
import json
import math
import shutil
import time

import numpy as np

from information_extract.scripts.build_p003_material_basis import MODULE, OUT as PREP, ASSETPATH, STEP
from information_extract.scripts.experiment_bend_allocation_3d import protected_hashes
from information_extract.scripts.generate_p003_allocation import OUT as SELECTED
from information_extract.three_d.allocation_policy import AllocationEngine
from information_extract.three_d.artifacts import ROOT, read, sha, verify_sources, write
from information_extract.three_d.local_forming import load_shape
from information_extract.three_d.meshing import mesh
from information_extract.three_d.p002_motion_reference import CHECK_CONFIG
from information_extract.three_d.p003_motion import build_model, frame, samples
from information_extract.three_d.panel_collision import PanelChecker
from information_extract.three_d.project_geometry_scoring import geometry_context

OUT = MODULE/'outputs/p003_candidate_motion_v01'


class VariantChecker:
    def __init__(self, model, out):
        self.out = out
        self.adjacent = [(j['parent_cell_id'],j['child_cell_id']) for j in model['joints']]
        self.shapes = {}
        self.checkers = {}

    def check(self, cells, transforms):
        key = tuple(sorted(cells.items()))
        if key not in self.checkers:
            for _,gid in key:
                if gid not in self.shapes:
                    self.shapes[gid] = load_shape(self.out/'brep'/f'{gid}.brep')
            self.checkers[key] = PanelChecker({cid:self.shapes[gid] for cid,gid in key},self.adjacent,CHECK_CONFIG)
        poses = {cid:np.asarray(m) for cid,m in transforms.items()}
        return self.checkers[key].check(poses)

    @property
    def evaluations(self):
        return sum(chk.kernel_evaluations for chk in self.checkers.values())


def setup():
    fingerprints = dict(read(SELECTED/'inputs.json')['files'])
    verify_sources(fingerprints)
    facts = read(PREP/'facts.json')
    paths = read(PREP/'geometry_paths.json')
    context, _ = geometry_context(facts,paths,{'rows':[]})
    plans = read(SELECTED/'plans.json')
    if len(plans['plans'])!=16 or len(set(plans['ranking']))!=16:
        raise ValueError('Exactly 16 selected P003 plans required')
    engine = AllocationEngine(context)
    for plan in plans['plans']:
        engine.require_feasible(plan['assignments'])
    model = build_model(facts,read(PREP/'skeleton.json'),read(PREP/'material_basis.json'),read(PREP/'partition.json'))
    asset = read(ASSETPATH)
    for path in (Path(__file__),SELECTED/'plans.json',MODULE/'three_d/p003_motion.py'):
        fingerprints[path.relative_to(ROOT).as_posix()] = sha(path)
    return model,plans,asset,paths,fingerprints


def load_material(model, plans, asset, paths, fingerprints, out):
    from information_extract.three_d import p003_staged_material as staged
    src = Path(staged.__file__)
    fingerprints[src.relative_to(ROOT).as_posix()] = sha(src)
    solids = {}
    for cell in model['cells']:
        cid = cell['cell_id']
        path = PREP/'brep'/f'{cid}.brep'
        solids[cid] = load_shape(path)
        (out/'brep').mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,out/'brep'/path.name)
    geometries = {cid:mesh(s,CHECK_CONFIG) for cid,s in solids.items()}
    material = staged.material_states(model,plans,asset,solids,paths,read(PREP/'registration.json'),out,geometries)
    geometries['COMPLETED_STEP'] = dict(triangles=np.asarray(asset['mesh']['positions']).reshape(-1,3,3).tolist())
    mesh_dir = out/'meshes'
    mesh_dir.mkdir(exist_ok=True)
    for gid,g in geometries.items():
        with gzip.open(mesh_dir/f'{gid}.json.gz','wt',encoding='utf8',compresslevel=3) as stream:
            json.dump(g,stream,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    return material


def complete_material(state):
    return len(state['executed_removal_ids'])==59 and len(state['executed_forming_ids'])==51


def prepare_bend_checks(model, plans, fingerprints, out, steps):
    """All bend stations follow OP20. Evaluate their final-material shapes early."""
    dest = out/'bend_checkpoints'
    expected = dict(files=fingerprints,steps=steps)
    if (dest/'inputs.json').exists() and read(dest/'inputs.json')==expected:
        if all((dest/f'{p["plan_id"]}.json').is_file() for p in plans):
            return read(dest/'summary.json')
    checker = VariantChecker(model,PREP)
    cells = {c['cell_id']:c['cell_id'] for c in model['cells']}
    samples_count = 0
    for p in plans:
        rows = []
        for sid,phase,tau in samples(model,p,steps):
            state = frame(model,p,sid,phase,tau)
            if complete_material(state):
                rows.append(dict(station_id=sid,phase=phase,tau=tau,result=checker.check(cells,state['transforms'])))
        write(dest/f'{p["plan_id"]}.json',rows)
        samples_count += len(rows)
        total = sum(r['result']['counts'].get('OVERLAP',0) for r in rows)
        print('BEND',p['plan_id'],len(rows),'samples',total,'overlap pair-states',flush=True)
    summary = dict(sample_count=samples_count,kernel_evaluations=checker.evaluations)
    write(dest/'summary.json',summary)
    write(dest/'inputs.json',expected)
    return summary


def evaluate_plan(model, plan, material, checker, steps, completed_checks=None):
    runs, counts = [],Counter()
    issue_stats = defaultdict(lambda:dict(samples=0,max_volume_mm3=0.))
    for sid,phase,tau in samples(model,plan,steps):
        state = frame(model,plan,sid,phase,tau)
        signature = tuple(state['executed_removal_ids']),tuple(state['executed_forming_ids'])
        variant = material[signature]
        cells = variant['cells']
        if completed_checks is not None and complete_material(state):
            if any(cid!=gid for cid,gid in cells.items()):
                raise ValueError('Completed material differs from the checked STEP partition')
            result = completed_checks[sid,phase,tau]
        else:
            result = checker.check(cells,state['transforms'])
        pending = variant['pending_unmodelled']
        counts.update(result['counts'])
        for issue in result['narrow_phase_pairs']:
            if issue['status'] in {'OVERLAP','UNKNOWN_KERNEL_FAILURE'}:
                key = (sid,issue['status'],*issue['region_ids'])
                info = issue_stats[key]
                info['samples'] += 1
                volume = issue.get('intersection_volume_mm3',0.)
                if info['samples']==1 or volume>info['max_volume_mm3']:
                    info.update(max_volume_mm3=volume,phase=phase,tau=tau,
                        joint_angles_deg=state['joint_angles_deg'],
                        cad_hinge_adjacent=issue['cad_hinge_adjacent'],
                        intersection_world_aabb_mm=issue.get('intersection_world_aabb_mm'))
        state.update(material_id=variant['material_id'],geometry_ids=cells,
                     unapplied_material_event_ids=pending,reference_geometry=result)
        runs.append(state)
    if any(cid!=gid or not np.allclose(runs[-1]['transforms'][cid],np.eye(4),atol=1e-10,rtol=0)
           for cid,gid in runs[-1]['geometry_ids'].items()):
        raise ValueError('Final material or pose differs from STEP rigid cell reference')
    issues = [dict(station_id=sid,status=status,cells=[a,b],**row)
              for (sid,status,a,b),row in sorted(issue_stats.items())]
    return dict(plan_id=plan['plan_id'],assignments=plan['assignments'],sample_count=len(runs),runs=runs,
                summary=dict(counts=dict(counts),issues=issues,
                    samples_with_unapplied_material_events=sum(bool(s['unapplied_material_event_ids']) for s in runs)))


def verify_run(model, reports, material):
    expected_pairs = len(list(combinations(model['cells'],2)))
    orders = {r['station_id']:r['order'] for r in model['stations']}
    for report in reports:
        a = report['assignments']
        for s in report['runs']:
            current = orders[s['station_id']]
            expected = {o for o,st in a.items() if orders[st]<current or
                        (orders[st]==current and s['phase']=='completed')}
            if expected!=set(s['executed_operation_ids']):
                raise ValueError('Wrong event boundary')
            paired = s['reference_geometry']
            if paired['pair_count']!=expected_pairs or sum(paired['counts'].values())!=expected_pairs:
                raise ValueError('Rigid pair denominator changed')
            for j in model['joints']:
                expected_angle = sum(x['increment_deg']*(1. if orders[a[x['operation_id']]]<current else
                    s['tau'] if a[x['operation_id']]==s['station_id'] else 0.) for x in j['steps'])
                if not math.isclose(expected_angle,s['joint_angles_deg'][j['joint_id']],abs_tol=1e-9):
                    raise ValueError('Cumulative angle verification failed')
            key = tuple(s['executed_removal_ids']),tuple(s['executed_forming_ids'])
            if material[key]['cells']!=s['geometry_ids']:
                raise ValueError('Wrong checked material geometry')


def compare_refinement(base, refined):
    def index(r):
        return r['station_id'],r['phase'],r['tau']
    rows = {index(r):r for r in refined['runs']}
    max_volume_delta = 0.
    for a in base['runs']:
        b = rows[index(a)]
        counts = a['reference_geometry']['counts'],b['reference_geometry']['counts']
        pa = {tuple(r['region_ids']):r for r in a['reference_geometry']['narrow_phase_pairs']}
        pb = {tuple(r['region_ids']):r for r in b['reference_geometry']['narrow_phase_pairs']}
        if counts[0]!=counts[1] or pa.keys()!=pb.keys():
            raise ValueError(f'Cold refinement does not reproduce {index(a)}')
        for pair,r in pa.items():
            difference = abs(r.get('intersection_volume_mm3',0.)-pb[pair].get('intersection_volume_mm3',0.))
            max_volume_delta = max(max_volume_delta,difference)
    if max_volume_delta>1e-6:
        raise ValueError('Volume mismatch on shared samples')
    return dict(common_sample_count=len(base['runs']),max_overlap_volume_delta_mm3=max_volume_delta)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUT)
    parser.add_argument('--steps',type=int,default=24)
    parser.add_argument('--model-only',action='store_true')
    parser.add_argument('--bend-only',action='store_true',help='Check states with all OP10/20 material events completed')
    args = parser.parse_args()
    started = time.monotonic()
    out = args.output.resolve()
    if not out.is_relative_to(MODULE/'outputs') or out in {MODULE/'outputs',PREP,SELECTED}:
        raise ValueError('Use an independent output directory')
    protected = protected_hashes()
    model,payload,asset,paths,fingerprints = setup()
    write(out/'model.json',model)
    write(out/'plans.json',payload)
    if args.model_only:
        return
    plans = payload['plans']
    bend_checks = prepare_bend_checks(model,plans,fingerprints,out,args.steps)
    if args.bend_only:
        return
    material = load_material(model,plans,asset,paths,fingerprints,out)
    print('MATERIAL',len(material),'states',flush=True)
    checker = VariantChecker(model,out)
    reports = []
    for p in plans:
        rows = read(out/'bend_checkpoints'/f'{p["plan_id"]}.json')
        completed = {(r['station_id'],r['phase'],r['tau']):r['result'] for r in rows}
        result = evaluate_plan(model,p,material,checker,args.steps,completed)
        folder = out/p['plan_id']
        write(folder/'collision_report.json',result)
        shutil.copyfile(STEP,folder/'completed.step')
        reports.append(result)
        print('PLAN',p['plan_id'],result['sample_count'],result['summary']['counts'],flush=True)
    verify_run(model,reports,material)
    worst = max(reports,key=lambda r:(r['summary']['counts'].get('UNKNOWN_KERNEL_FAILURE',0),r['summary']['counts'].get('OVERLAP',0)))
    p = next(p for p in plans if p['plan_id']==worst['plan_id'])
    cold = VariantChecker(model,out)
    refined = evaluate_plan(model,p,material,cold,2*args.steps)
    write(out/'refined'/p['plan_id']/'collision_report.json',refined)
    refinement = compare_refinement(worst,refined)
    totals = Counter()
    for r in reports:
        totals.update(r['summary']['counts'])
    verify_sources(fingerprints)
    if protected_hashes()!=protected:
        raise ValueError('Protected working source changed')
    if any(sha(out/p['plan_id']/'completed.step')!=sha(STEP) for p in plans):
        raise ValueError('A completed STEP copy differs')
    write(out/'inputs.json',dict(files=fingerprints,steps=args.steps))
    write(out/'summary.json',dict(part_id='P003',plan_count=len(reports),material_variant_count=len(material),
        sample_count=sum(r['sample_count'] for r in reports),pair_states=sum(totals.values()),counts=dict(totals),
        elapsed_seconds=round(time.monotonic()-started,2),kernel_evaluations=checker.evaluations+bend_checks['kernel_evaluations'],
        final_material_checkpoint=bend_checks,
        plans=[{k:r[k] for k in ('plan_id','sample_count','summary')} for r in reports],
        refinement=dict(plan_id=p['plan_id'],steps=2*args.steps,sample_count=refined['sample_count'],
                        counts=refined['summary']['counts'],kernel_evaluations=cold.evaluations,**refinement),
        source_count=len(fingerprints),protected_matches=protected['matches'],
        protected_preexisting_mismatches=len(protected['preexisting_mismatches']),
        all_completed_steps_byte_equal=True,manufacturing_safety_proved=False))
    print('COMPLETE',dict(totals),'refinement',refinement,flush=True)


if __name__=='__main__':
    main()
