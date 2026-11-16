"""Replay all selected P002 plans using sampled curve sweeps and material states."""
import argparse
from collections import Counter
from itertools import combinations
from pathlib import Path
import shutil
import time

import numpy as np

from information_extract.three_d.artifacts import ROOT, read, sha, verify_sources, write
from information_extract.three_d.local_forming import load_shape, save_shape
from information_extract.three_d.band_material_solids import triangle_solid
from information_extract.three_d.local_collision import volume
from information_extract.three_d.meshing import mesh
from information_extract.three_d.p002_motion_reference import CHECK_CONFIG
from information_extract.three_d.p002_curve_motion import bind_vertices, compile_guides, frame, sweep
from information_extract.three_d.p002_staged_material import material_states
from information_extract.three_d.sampled_mesh_contact import contact, triangle_bounds
from information_extract.scripts.experiment_bend_allocation_3d import protected_hashes

M = ROOT/'information_extract'
OLD = M/'outputs/p002_candidate_motion_v02'
CELLS = M/'outputs/p002_material_cells_v01'
PREP = M/'outputs/p002_generation_v01'
ASSET = M/'outputs/multi_part_step_v01/P002'
EVIDENCE = M/'outputs/p002_stage_evidence_v01/stage_geometry.json'
DEST = M/'outputs/p002_candidate_motion_v05'
STEP = M/'sources/P002/originals/P002.stp'
WEB = M/'three_d/web'
IDENTITY = np.eye(4).tolist()


def setup():
    inputs = read(OLD/'inputs.json')['files']
    verify_sources(inputs)
    verify_sources(read(OLD/'output_manifest.json')['files'])
    paths = [Path(__file__), OLD/'output_manifest.json', OLD/'inputs.json',
             ASSET/'base_registration.json', PREP/'geometry_paths.json']
    paths += [M/'three_d'/name for name in ('p002_curve_motion.py','p002_staged_material.py',
                                            'sampled_mesh_contact.py')]
    paths += [WEB/name for name in ('p002_curve_review.html','p002_curve_review.js')]
    inputs.update({p.relative_to(ROOT).as_posix():sha(p) for p in paths})
    inputs.update(read(OLD/'output_manifest.json')['files'])
    model = read(OLD/'model.json')
    plans = read(OLD/'plans.json')
    asset = read(ASSET/'asset.json')
    solids = {c['cell_id']:load_shape(CELLS/c['brep_file']) for c in model['cells']}
    model['curve_guides'] = compile_guides(asset,read(PREP/'curved_side_evidence.json'),solids['C001'])
    carrier = read(EVIDENCE)['o094_carrier']
    model['o094_stage_reference'].update(carrier_joint=carrier['carrier_joint'],
        carrier_flat_shift_mm=[carrier['measured_x_compensation_mm'],0,0])
    model['motion_method'] = 'STEP_WALL_SECTION_SWEEP_WITH_SHARED_LINEAR_STATION_ANGLE'
    model['material_method'] = 'SCHEDULED_STEP_ENDPOINT_RECONSTRUCTION'
    model['limitations'] = [
        'Curved wall motion is geometric interpolation with constant section thickness and a 0.4 mm flat setback',
        'Bend bands, tools, fixtures and continuous non-bend forming are excluded',
        'Transformed wall meshes use sampled surface crossing, contact and containment probes without Boolean overlap volumes',
    ]
    return model,plans,asset,solids,inputs


class Replay:
    def __init__(self, model, geometries, material):
        self.model = model
        self.geometry = geometries
        self.material = material
        self.meshes = {}
        self.cache = {}

    def triangles(self, gid):
        if gid not in self.meshes:
            self.meshes[gid] = np.array(self.geometry[gid]['triangles'])
        return self.meshes[gid]

    def geometry_id(self, cell, gid, progress):
        if progress is None:
            return gid
        key = f'{gid}_sweep_{progress:.9f}'
        if key not in self.geometry:
            triangles = self.triangles(gid)
            ref = self.model['curve_guides'][cell]
            # Shared input vertices get identical transforms, including both sides of a seam.
            coords, inverse = np.unique(triangles.reshape(-1,3).round(10),axis=0,return_inverse=True)
            moved = sweep(coords,bind_vertices(coords,ref['guides']),progress,ref['z_sign'])
            self.geometry[key] = dict(triangles=moved[inverse].reshape(triangles.shape).round(7).tolist())
        return key

    def check(self, plan, old):
        state = frame(self.model,plan,old['station_id'],old['phase'],old['tau'])
        sig = (tuple(state['executed_removal_ids']),tuple(state['executed_forming_ids']))
        mat = self.material[sig]
        transforms = state['transforms']
        original_pairs = {tuple(p['region_ids']):p for p in old['reference_geometry']['narrow_phase_pairs']}
        ids = {}
        for cid,gid in mat['cells'].items():
            progress = state['curve_fractions'].get(cid)
            ids[cid] = self.geometry_id(cid,gid,progress)
            if progress is not None:
                transforms[cid] = IDENTITY
        if len(ids) != 19 or ids.keys() != transforms.keys():
            raise ValueError('Incomplete nineteen-cell replay')
        exact = {cid for cid in ids if ids[cid] == cid and old['transforms'].get(cid) == transforms[cid]}
        counts = Counter()
        narrow = []
        world = {}
        for a,b in combinations(sorted(ids),2):
            if {a,b} <= exact:
                row = original_pairs.get((a,b))
                status = 'AABB_SEPARATED' if row is None else row['status']
                if row is not None and status in {'OVERLAP','UNKNOWN_KERNEL_FAILURE'}:
                    narrow.append(row)
            else:
                ma,mb = np.asarray(transforms[a]),np.asarray(transforms[b])
                relative = np.linalg.inv(ma)@mb
                cache_key = (ids[a],ids[b],*relative.round(10).flat)
                if cache_key not in self.cache:
                    for cid,pose in ((a,ma),(b,mb)):
                        if cid not in world:
                            tri = self.triangles(ids[cid])
                            vertices = tri@pose[:3,:3].T+pose[:3,3]
                            world[cid] = vertices,triangle_bounds(vertices)
                    ta,ba = world[a]
                    tb,bb = world[b]
                    self.cache[cache_key] = contact(ta,tb,ba,bb)
                status = self.cache[cache_key]
                if status in {'MESH_CROSSING','MESH_CONTAINMENT','MESH_CONTACT_OR_EDGE_CROSSING'}:
                    narrow.append(dict(region_ids=[a,b],status=status))
            counts[status] += 1
        state.update(geometry_ids=ids, material_id=mat['material_id'],
                     unapplied_material_event_ids=mat['pending_unmodelled'],
                     executed_material_events_applied_to_geometry=not mat['pending_unmodelled'],
                     material_scope='SCHEDULED_STEP_ENDPOINT_RECONSTRUCTION',
                     reference_geometry=dict(pair_count=sum(counts.values()),counts=dict(counts),narrow_phase_pairs=narrow),
                     unknown_motion_pairs=[], manufacturing_safety_proved=False)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=DEST)
    parser.add_argument('--model-only',action='store_true')
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(M/'outputs') or out in {OLD,M/'outputs'}:
        raise ValueError('Use a new output folder')
    started = time.monotonic()
    protected = protected_hashes()
    model,plans,asset,solids,inputs = setup()
    if (out/'inputs.json').exists() and read(out/'inputs.json')['files'] != inputs:
        raise ValueError('Existing output uses different sources or code')
    write(out/'inputs.json',dict(files=inputs))
    write(out/'model.json',model)
    write(out/'plans.json',plans)
    geometries = {cid:mesh(s,CHECK_CONFIG) for cid,s in solids.items()}
    for cid,ref in model['curve_guides'].items():
        tri = np.array(geometries[cid]['triangles'])
        vertices = tri.reshape(-1,3)
        bound = bind_vertices(vertices,ref['guides'])
        report = []
        for p in (0,.25,.5,.75,1):
            moved = sweep(vertices,bound,p,ref['z_sign']).reshape(tri.shape)
            area = np.linalg.norm(np.cross(moved[:,1]-moved[:,0],moved[:,2]-moved[:,0]),axis=1)
            if not np.isfinite(moved).all() or np.max(area) <= 0:
                raise ValueError('Invalid curved sweep')
            solid_volume = volume(triangle_solid(moved))
            report.append(dict(progress=p,triangles=len(tri),degenerate_triangles=int(sum(area<1e-10)),
                               closed_faceted_solid_volume_mm3=solid_volume,
                               bounds_mm=[*moved.reshape(-1,3).min(0),*moved.reshape(-1,3).max(0)]))
        write(out/'curve_validation'/f'{cid}.json',dict(operation_id=ref['operation_id'],samples=report,
            max_guide_distance_mm=float(bound[2].max()), completed_vertices_equal=bool(np.array_equal(moved,tri))))
    print('Four curved sweep models compiled',flush=True)
    if args.model_only:
        return
    material = material_states(model,plans['plans'],asset,solids,read(PREP/'geometry_paths.json'),
                               read(ASSET/'base_registration.json'),out,geometries)
    for cid,solid in solids.items():
        save_shape(out/'brep'/f'{cid}.brep',solid)
    geometries['COMPLETED_STEP'] = dict(triangles=np.asarray(asset['mesh']['positions']).reshape(-1,3,3).tolist())
    simulator = Replay(model,geometries,material)
    summaries = []
    for plan in plans['plans']:
        pid = plan['plan_id']
        original = read(OLD/pid/'collision_report.json')
        if original['assignments'] != plan['assignments']:
            raise ValueError('Assignment changed')
        rows = [simulator.check(plan,old) for old in original['runs']]
        totals = Counter()
        for s in rows:
            totals.update(s['reference_geometry']['counts'])
        if any(rows[-1]['geometry_ids'][k] != k or not np.allclose(v, IDENTITY, atol=1e-10, rtol=0)
               for k,v in rows[-1]['transforms'].items()):
            raise ValueError('Final pose/material drift')
        folder = out/pid
        write(folder/'collision_report.json',dict(plan_id=pid,assignments=plan['assignments'],
            runs=rows, sample_count=len(rows), summary=dict(counts=dict(totals)), manufacturing_safety_proved=False))
        write(folder/'model.json',dict(plan_id=pid,assignments=plan['assignments'], model='../model.json',
              states='collision_report.json',geometry='../scene.json',completed_step='completed.step'))
        shutil.copyfile(STEP,folder/'completed.step')
        summaries.append(dict(plan_id=pid,sample_count=len(rows),counts=dict(totals),
                              unknown_motion_pair_states=0,
                              samples_with_unapplied_material_events=sum(bool(s['unapplied_material_event_ids']) for s in rows)))
        print(pid,summaries[-1],flush=True)
    write(out/'scene.json',dict(model=model,geometries=geometries))
    write(out/'summary.json',dict(plans=summaries,elapsed_seconds=round(time.monotonic()-started,2),
        material_audit='material_audit.json',pair_method='OCCT retained for unchanged rigid pairs, otherwise thick-mesh probes',
        full_process_collision_checked=False,manufacturing_safety_proved=False))
    for name in ('core.js','finite_material_renderer.js','p002_curve_review.js'):
        shutil.copyfile(WEB/name,out/name)
    shutil.copyfile(WEB/'p002_curve_review.html',out/'index.html')
    verify_sources(inputs)
    if protected_hashes() != protected:
        raise ValueError('Protected working files changed')
    write(out/'verification.json',dict(source_count=len(inputs),protected_matches=protected['matches'],
        protected_preexisting_mismatches=len(protected['preexisting_mismatches']),plan_count=len(summaries),
        material_record_count=20*len(summaries),
        final_steps_byte_equal=all(sha(out/p['plan_id']/'completed.step')==sha(STEP) for p in plans['plans'])))
    print('Done',len(summaries),'plans',flush=True)


if __name__ == '__main__':
    main()
