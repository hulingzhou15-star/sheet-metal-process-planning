"""Build all selected P002 models and run supported reference-motion diagnostics.

Always report incomplete process coverage: curves and staged material have no
existing P002 implementation. Never promote reference geometry to a real stage.
"""
import argparse
from collections import Counter
from pathlib import Path
import shutil
import time
import numpy as np

from information_extract.three_d.artifacts import ROOT,read,write,sha,verify_sources
from information_extract.three_d.project_geometry_scoring import geometry_context
from information_extract.three_d.allocation_policy import AllocationEngine
from information_extract.three_d.p002_motion_reference import compile_model,frame,ReferenceChecker,run_plan,CHECK_CONFIG
from information_extract.three_d.local_forming import load_shape
from information_extract.three_d.meshing import mesh
from information_extract.scripts.experiment_bend_allocation_3d import protected_hashes
from src.neighborhood.context import digest

MODULE=ROOT/'information_extract';PREP=MODULE/'outputs/p002_generation_v01'
SOURCE=MODULE/'outputs/geometry_allocation_v1_generated/P002';CELLS=MODULE/'outputs/p002_material_cells_v01'
DEST=MODULE/'outputs/p002_candidate_motion_v01'


def build_scene(model,asset,out):
    geometries={}
    for c in model['cells']:
        src=CELLS/c['brep_file'];dst=out/c['brep_file'];dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
        geometries[c['cell_id']]=mesh(load_shape(src),CHECK_CONFIG)
    geometries['COMPLETED_STEP']=dict(triangles=np.asarray(asset['mesh']['positions']).reshape(-1,3,3).tolist())
    write(out/'scene.json',dict(geometries=geometries,model=model))


def publish(out,model,plans,reports):
    rows=[];total=Counter()
    for r in reports:
        s=r['summary'];counts=s['reference_counts']
        row=dict(plan_id=r['plan_id'],sample_count=r['sample_count'],
            checked_reference_pair_states=s['reference_pair_states'],unknown_motion_pair_states=s['unknown_motion_pair_states'],
            reference_overlap_pair_states=counts.get('OVERLAP',0),kernel_unknown_pair_states=counts.get('UNKNOWN_KERNEL_FAILURE',0),
            full_process_status='UNKNOWN_INCOMPLETE_MODEL')
        rows.append(row);total.update({k:v for k,v in row.items() if isinstance(v,int)})
    payload=dict(part_id='P002',plans=rows,completed=len(rows),requested=len(plans),totals=dict(total),
        known_rigid_joint_count=len(model['joints']),unknown_curved_actions=model['curved_operation_ids'],
        stage_material_model_implemented=False,full_process_collision_checked=False,manufacturing_safety_proved=False,
        interpretation='Counts concern completed STEP material under supported rigid motions only; unknown is not collision-free')
    write(out/'summary.json',payload)
    return payload


def compare_refinement(coarse,fine):
    index={(r['station_id'],r['phase'],r['tau']):r for r in fine['runs']};maximum=0.
    for a in coarse['runs']:
        b=index[(a['station_id'],a['phase'],a['tau'])]
        if a['unknown_cells']!=b['unknown_cells'] or a['reference_geometry']['counts']!=b['reference_geometry']['counts']:
            raise ValueError('Cold refinement changes common sample status')
        ai={tuple(p['region_ids']):p for p in a['reference_geometry']['narrow_phase_pairs']}
        bi={tuple(p['region_ids']):p for p in b['reference_geometry']['narrow_phase_pairs']}
        if ai.keys()!=bi.keys():raise ValueError('Cold refinement pair coverage differs')
        for k in ai:
            for field in ('distance_mm','intersection_volume_mm3'):
                error=abs(ai[k].get(field,0)-bi[k].get(field,0));maximum=max(maximum,error)
                if error>1e-6:raise ValueError('Cold refinement geometry differs')
    return dict(common_samples=len(coarse['runs']),maximum_numeric_difference=maximum,
        refined_sample_count=len(fine['runs']),refined_summary=fine['summary'])


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=DEST)
    p.add_argument('--steps',type=int,default=24);args=p.parse_args();out=args.output.resolve()
    if not out.is_relative_to(MODULE/'outputs') or out in (MODULE/'outputs',SOURCE,CELLS,PREP):raise ValueError('Dedicated new output required')
    started=time.monotonic();protected=protected_hashes();generation=read(SOURCE/'inputs.json');verify_sources(generation['files'])
    cells=read(CELLS/'cells.json');verify_sources(cells['inputs'])
    payload=read(SOURCE/'plans.json');plans=payload['plans']
    if len(plans)!=16 or len({p['plan_id'] for p in plans})!=16:raise ValueError('Expected selected 16 plans')
    facts=read(PREP/'facts.json');context,_=geometry_context(facts,read(PREP/'geometry_paths.json'),read(PREP/'curved_side_evidence.json'))
    for plan in plans:AllocationEngine(context).require_feasible(plan['assignments'])
    model=compile_model(cells,read(PREP/'straight_branch_proposals.json'),read(PREP/'carried_region_proposals.json'),facts)
    asset_path=MODULE/'outputs/multi_part_step_v01/P002/asset.json';asset=read(asset_path)
    step=MODULE/'sources/P002/originals/P002.stp'
    if sha(step)!=cells['step_sha256'] or sha(step)!=asset['asset_id']:raise ValueError('STEP mismatch')
    files={ROOT/x for x in generation['files']}|{ROOT/x for x in cells['inputs']}
    files.update([Path(__file__),SOURCE/'plans.json',SOURCE/'inputs.json',CELLS/'cells.json',asset_path,ROOT/'registry/protected_inputs.json'])
    files.update((MODULE/'three_d').glob('*.py'))
    web=MODULE/'three_d/web'
    files.update(web/x for x in ('core.js','finite_material_renderer.js','p002_motion_review.js','p002_motion_review.html'))
    hashes={f.relative_to(ROOT).as_posix():sha(f) for f in sorted(files)};signature=digest(dict(files=hashes,steps=args.steps))
    if (out/'inputs.json').exists() and read(out/'inputs.json')['signature']!=signature:raise ValueError('Output belongs to different inputs; use --output with a new directory')
    out.mkdir(parents=True,exist_ok=True);write(out/'inputs.json',dict(signature=signature,files=hashes,steps=args.steps))
    write(out/'model.json',model);write(out/'plans.json',payload)
    build_scene(model,asset,out)
    for name in ('core.js','finite_material_renderer.js','p002_motion_review.js'):shutil.copyfile(web/name,out/name)
    shutil.copyfile(web/'p002_motion_review.html',out/'index.html')
    print('MODEL',len(model['cells']),'cells',len(model['joints']),'rigid joints',flush=True)
    checker=ReferenceChecker(model,out);reports=[];publish(out,model,plans,reports)
    for plan in plans:
        folder=out/plan['plan_id'];folder.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(step,folder/'completed.step')
        report=run_plan(model,plan,checker,args.steps,progress=lambda *v:print(plan['plan_id'],*v,flush=True))
        write(folder/'collision_report.json',report)
        states=[{k:v for k,v in s.items() if k not in ('reference_geometry','unknown_motion_pairs')} for s in report['runs']]
        write(folder/'states.json',states)
        write(folder/'model.json',dict(part_id='P002',plan_id=plan['plan_id'],assignments=plan['assignments'],
            geometry_root='..',shared_model='../model.json',state_file='states.json',completed_step='completed.step',
            completed_step_sha256=sha(step),state_geometry_scope='COMMON_COMPLETED_STEP_REFERENCE_ONLY',
            full_process_model=False,manufacturing_safety_proved=False))
        final=states[-1]
        if final['unknown_cells'] or set(final['transforms'])!={c['cell_id'] for c in model['cells']} or any(not np.allclose(m,np.eye(4),atol=1e-10,rtol=0) for m in final['transforms'].values()):raise ValueError('Final STEP pose mismatch')
        if set(final['executed_operation_ids'])!=set(plan['assignments']):raise ValueError('Event coverage incomplete')
        reports.append(report);summary=publish(out,model,plans,reports)
    # Independent cold-cache, double-density repeat of the most overlapping plan.
    worst=max(reports,key=lambda r:r['summary']['reference_counts'].get('OVERLAP',0))
    selected=next(p for p in plans if p['plan_id']==worst['plan_id'])
    print('COLD REFINEMENT',selected['plan_id'],flush=True)
    cold=ReferenceChecker(model,out);fine=run_plan(model,selected,cold,args.steps*2)
    write(out/'refined'/selected['plan_id']/'collision_report.json',fine)
    refinement=compare_refinement(worst,fine)
    verify_sources(hashes)
    if protected_hashes()!=protected:raise ValueError('Protected inputs changed')
    for row in protected['preexisting_mismatches']:
        if row['path']=='information_extract/projects/P002/project.json':row['reason']='User-authorized revision16 consumed at actual hash; registry expected hash unchanged'
    write(out/'verification.json',dict(source_files_unchanged=len(hashes),protection=protected,all_16_hard_feasible=True,
        assignments_unchanged=True,all_final_cell_poses_equal_step=True,all_final_event_lists_complete=True,
        each_completed_step_byte_equal=all(sha(out/p['plan_id']/'completed.step')==sha(step) for p in plans),
        kernel_calls=checker.kernel_calls,refinement=dict(plan_id=selected['plan_id'],**refinement),
        elapsed_seconds=round(time.monotonic()-started,2),full_process_collision_checked=False,manufacturing_safety_proved=False))
    generated=[*out.glob('*.json'),*out.glob('*.js'),out/'index.html',*out.glob('brep/*.brep'),*out.glob('NP2_*/*'),*out.glob('refined/*/*.json')]
    write(out/'output_manifest.json',dict(files={f.relative_to(ROOT).as_posix():sha(f) for f in generated if f.is_file()}))
    print('COMPLETE',summary['totals'],flush=True)


if __name__=='__main__':main()
