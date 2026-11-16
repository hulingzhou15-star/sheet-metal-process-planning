"""Generate P001 plans under the active no-trim scope and screen their staged motion."""
import argparse
from collections import Counter
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
import time

from ortools.sat.python import cp_model

from information_extract.scripts.check_generated_allocation import SOURCE
from information_extract.scripts.experiment_bend_allocation_3d import protected_hashes
from information_extract.scripts.regenerate_p003_candidates import write_checkpoint
from information_extract.scripts.run_geometry_allocation import prepare, verify_optimizer
from information_extract.three_d.allocation_material_states import rebuild, compare_materials
from information_extract.three_d.allocation_policy import AllocationContext, AllocationEngine, load_context
from information_extract.three_d.artifacts import ROOT, read, relative, source_manifest, verify_sources, write
from information_extract.three_d.geometric_p1 import compile_basis
from information_extract.three_d.p001_rescreen_motion import StagedMotionChecker
from information_extract.three_d.step_detail_endpoints import extract_details
from src.candidate.distance import plan_hash
from src.inference.generation import assignment_from_solution,derived_seed,solver_for
from src.neighborhood.context import digest
from src.neighborhood_penalty import load_config
from src.neighborhood_penalty.generation import compact_score,establish_primary,generate_candidates,ranking_key,select_candidates
from src.neighborhood_penalty.model import build_objective_model

MODULE = ROOT/'information_extract'
OUT = MODULE/'outputs/p001_motion_regeneration_v01'
WORKBOOK = ROOT/'data/parts/P001/钣金加工数据表_P001.xlsx'
COARSE, FINE = 24, 96


def active_context():
    original = load_context(WORKBOOK)
    omitted = {oid for oid,op in original.operations.items() if op.operation_type=='TRIM'}
    if any(r.predecessor_id in omitted and r.operation_id not in omitted for r in original.precedence_relations):
        raise ValueError('An active P001 operation requires excluded trim')
    data = original.part_data
    groups = []
    for row in data.process_groups:
        ids = tuple(oid for oid in row.operation_ids if oid not in omitted)
        if ids:
            groups.append(replace(row,operation_ids=ids))
    active = replace(data,operations=tuple(op for op in data.operations if op.operation_id not in omitted),
        stations=tuple(replace(station,allowed_operation_types=station.allowed_operation_types-{'TRIM'})
                       for station in data.stations),process_groups=tuple(groups))
    scoped_id = digest(dict(source_facts=original.facts_fingerprint, excluded_operations=sorted(omitted),
                            scope='ACTIVE_NO_TRIM'))
    context = AllocationContext(active,tuple(a for a in original.fixed_assignments if a.operation_id not in omitted),
                                (),scoped_id)
    return context,sorted(omitted)


def source_inputs(viewer):
    from information_extract.three_d import allocation_material_states, p001_rescreen_motion, p004_collision
    sources = dict(viewer['model']['source_sha256'])
    verify_sources(sources)
    files = [Path(__file__),Path(allocation_material_states.__file__),Path(p001_rescreen_motion.__file__),
             Path(p004_collision.__file__), WORKBOOK,SOURCE/'viewer_data.json',SOURCE/'flanging_step_inventory.json',
             ROOT/'configs/neighborhood_penalty_v02.json',ROOT/'configs/neighborhood_v01.json',
             MODULE/'outputs/stage_review_v01/P001/stage_data.json',MODULE/'projects/P001/project.json',
             MODULE/'configs/app.json',MODULE/'configs/three_d_state_v01.json',MODULE/'sources/P001/Plotview 1.dxf',
             MODULE/'sources/P001/formed.step',MODULE/'three_d/step_detail_endpoints.py',
             MODULE/'three_d/band_material.py',MODULE/'scripts/run_geometry_allocation.py',
             MODULE/'scripts/check_local_forming_collision.py']
    sources.update(source_manifest([*files,*SOURCE.glob('brep/*.brep')]))
    return sources


def refill(context,catalogue,config,primary,scorer,attempted,amount):
    model,ranks,p5,objective = build_objective_model(context,catalogue,config,scorer,'secondary')
    value = Fraction(primary['minimum_exact'])*p5.scale
    if value.denominator!=1:
        raise ValueError('P001 P5 lock has a nonintegral value')
    model.add(p5.expression==int(value))
    model.minimize(objective.expression)
    ids = sorted(ranks)
    variables = [ranks[o] for o in ids]

    def row(plan):
        return [context.station_ranks[plan['assignments'][o]] for o in ids]

    model.add_forbidden_assignments(variables,[row(plan) for plan in attempted])
    proposals = []
    for i in range(amount):
        seed = derived_seed(context,config['generation']['seed'],'P001 staged replenishment',len(attempted)+i)
        solver = solver_for(config,seed)
        status = solver.solve(model)
        if status==cp_model.MODEL_INVALID:
            raise ValueError(model.validate())
        if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE):
            return proposals,solver.status_name(status)
        assignment = assignment_from_solution(context,ranks,solver)
        proposals.append(dict(assignments=assignment,plan_hash=plan_hash(assignment),
                              score=compact_score(scorer.evaluate(assignment))))
        model.add_forbidden_assignments(variables,[row(proposals[-1])])
    return proposals,'BATCH_FILLED'


def summarize(report):
    return {key:report[key] for key in ('plan_id','status','witness','motion_subdivisions',
                                       'sample_count','pair_states','counts','kernel_calls')}


def shortlist(context,pool,config):
    bends = sorted(oid for oid,op in context.operations.items() if op.operation_type=='BEND')
    reps = {}
    for row in sorted(pool,key=ranking_key):
        key = tuple(row['assignments'][oid] for oid in bends)
        reps.setdefault(key,row)
    chosen,trace = select_candidates(context,list(reps.values()),config)
    limit = config['generation']['selected_target']
    if len(chosen)<limit:
        buffer,_ = select_candidates(context,pool,config)
        seen = {p['plan_hash'] for p in chosen}
        chosen.extend(p for p in buffer if p['plan_hash'] not in seen)
    return chosen[:limit],trace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-plans',type=int,default=128)
    parser.add_argument('--generate-only',action='store_true')
    args = parser.parse_args()
    if args.max_plans<64:
        parser.error('The search budget must include at least the initial 64 plans')
    started = time.monotonic()
    protected = protected_hashes()
    viewer = read(SOURCE/'viewer_data.json')
    sources = source_inputs(viewer)
    context,omitted = active_context()
    config = load_config()
    basis = compile_basis(context,viewer,SOURCE)
    scorer,catalogue = prepare(context,basis,config)
    primary = establish_primary(context,catalogue,config)
    if not primary['generation_allowed']:
        raise ValueError('P001 P5 minimum remains unresolved')
    initial,trace = generate_candidates(context,catalogue,config,primary,scorer,
        progress=lambda row: print('P001 GENERATE',row['pool'],row['status'],flush=True) if row['pool']%8==0 else None)
    if len(initial)!=config['generation']['pool_target']:
        raise ValueError('Initial P001 candidate generation did not reach the configured pool size')
    write(OUT/'initial_pool.json',dict(part_id='P001',active_operations=len(context.operations),excluded_trim=omitted,
        plans=initial,generation=trace,primary=primary))
    write(OUT/'material_basis.json',basis)
    write(OUT/'catalogue.json',catalogue)
    write(OUT/'inputs.json',dict(files=sources,coarse_subdivisions=COARSE,refined_subdivisions=FINE))
    if args.generate_only:
        verify_sources(sources)
        return

    batches = []
    checked = []
    passing = []
    coarse = {}
    dense = {}
    material_source = {}
    engine = AllocationEngine(context)
    proposals = initial
    termination = 'pool_and_selection_filled'
    while True:
        if proposals:
            batch_path = OUT/'staged'/f'batch_{len(batches)+1:02d}'
            for i,p in enumerate(proposals,len(checked)+1):
                p['plan_id'] = f'P001_R{i:03d}'
                engine.require_feasible(p['assignments'])
            details,detail_audit = extract_details(viewer,SOURCE,batch_path)
            staged = rebuild(viewer,proposals,SOURCE,batch_path,details)
            comparisons = compare_materials(viewer,SOURCE,staged,batch_path,details)
            modeled = ({b['operation_id'] for b in staged['model']['bends']}|set(staged['step_comparison'])
                       |{entry['operation_id'] for entry in staged['removal_coverage']})
            if modeled!=context.operations.keys():
                raise ValueError(f'P001 staged action coverage differs: {sorted(modeled ^ context.operations.keys())}')
            write(batch_path/'viewer_data.json',staged)
            write(batch_path/'material_audit.json',dict(covered_operations=sorted(modeled),step_details=detail_audit,
                shared_reference_comparisons=comparisons,material_variants=len(staged['variants'])))
            checker = StagedMotionChecker(staged,batch_path)
            batches.append(batch_path)
            for p in proposals:
                material_source[p['plan_id']] = batch_path,checker
                checked.append(p)
                result = checker.evaluate(p,COARSE,stop_on_overlap=True)
                coarse[p['plan_id']] = summarize(result)
                if result['status']=='passed':
                    passing.append(p)
                write_checkpoint(OUT/'coarse_checks.json',dict(plans=list(coarse.values())))
                if len(checked)%8==0 or result['status']!='passed':
                    print('P001 SCREEN',len(checked),len(passing),'passed;',result['status'],flush=True)

        selected,selection = shortlist(context,passing,config)
        for p in selected:
            pid = p['plan_id']
            if pid in dense:
                continue
            directory,checker = material_source[pid]
            result = checker.evaluate(p,FINE,stop_on_overlap=True)
            dense[pid] = summarize(result)
            write_checkpoint(OUT/'dense_checks.json',dict(plans=list(dense.values())))
            if result['status']=='passed':
                report = OUT/'selected'/pid/'collision_report.json'
                write(report,result)
                p['motion_verification'] = dict(report=relative(report),material_directory=relative(directory),
                    sample_count=result['sample_count'],pair_states=result['pair_states'])
            else:
                passing.remove(p)
            print('P001 DENSE',pid,result['status'],result['sample_count'],flush=True)
        selected,selection = shortlist(context,passing,config)
        if any(p['plan_id'] not in dense for p in selected):
            proposals = []
            continue
        if len(passing)>=config['generation']['pool_target'] and len(selected)>=config['generation']['selected_target']:
            break
        if len(checked)>=args.max_plans:
            termination = 'search_budget_reached'
            break
        missing = config['generation']['pool_target']-len(passing)
        proposals,search_status = refill(context,catalogue,config,primary,scorer,checked,
                                        min(max(1,missing),args.max_plans-len(checked)))
        if not proposals:
            termination = 'projection_exhausted' if search_status=='INFEASIBLE' else 'solver_incomplete'
            break

    if selected:
        verify_optimizer(context,catalogue,config,scorer,selected[0]['assignments'])
    verify_sources(sources)
    if protected_hashes()!=protected:
        raise ValueError('Protected P001 inputs changed')
    write(OUT/'pool.json',dict(part_id='P001',plans=passing))
    write(OUT/'plans.json',dict(part_id='P001',plans=selected,ranking=[p['plan_id'] for p in sorted(selected,key=ranking_key)]))
    summary = dict(part_id='P001', termination=termination,pool_size=len(passing),selected_count=len(selected),
        active_operation_count=len(context.operations),excluded_trim=omitted,coarse_subdivisions=COARSE,
        refined_subdivisions=FINE,coarse_outcomes=dict(Counter(row['status'] for row in coarse.values())),
        dense_outcomes=dict(Counter(row['status'] for row in dense.values())),selection=selection,
        selected_sample_count=sum(p['motion_verification']['sample_count'] for p in selected),
        selected_pair_states=sum(p['motion_verification']['pair_states'] for p in selected),
        manufacturing_safety_proved=False,elapsed_seconds=round(time.monotonic()-started,2),sources=sources)
    write(OUT/'summary.json',summary)
    print('P001 COMPLETE',len(passing),'pool;',len(selected),'selected',termination,flush=True)


if __name__=='__main__':
    main()
