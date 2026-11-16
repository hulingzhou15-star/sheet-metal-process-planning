"""Compare J297 pivot choices at selected P003 overlap samples using rigid solids."""
import argparse
from copy import deepcopy
from pathlib import Path

import numpy as np

from information_extract.scripts.build_p003_material_basis import MODULE, OUT as PREP, STEP
from information_extract.scripts.check_p003_candidate_motion import setup
from information_extract.three_d.artifacts import ROOT, sha, verify_sources, write
from information_extract.three_d.assets import bounds
from information_extract.three_d.local_collision import transformed, volume
from information_extract.three_d.local_forming import intersect, load_shape
from information_extract.three_d.p003_motion import FORMS, REMOVALS, frame
from information_extract.three_d.panel_collision import aabb_gap, transformed_bounds

OUT = MODULE/'outputs/p003_motion_axis_sensitivity_v01'
PAIRS = [('P104_105','P298_299'),('P409_410','P70_295')]
SAMPLES = [('entry',0.),('motion',.5),('motion',.75),('completed',1.)]
VOLUME_TOLERANCE = 1e-6


def cylinder_axis_model(model, asset):
    joint = next(j for j in model['joints'] if j['joint_id']=='J297')
    pivot = np.asarray(joint['hinge_point_mm'])
    axis = np.asarray(joint['axis_direction'])
    if not np.isclose(np.linalg.norm(axis),1.,atol=1e-9,rtol=0):
        raise ValueError('Expected a unit J297 hinge direction')
    faces = [e['cylinder_face_id'] for e in joint['surface_evidence']]
    projected = []
    radii = []
    for fid in faces:
        face = asset['faces'][fid-1]['cylinder']
        point = np.asarray(face['point_mm'])
        if not np.isclose(abs(np.dot(axis,face['direction'])),1.,atol=1e-9,rtol=0):
            raise ValueError('STEP cylinder direction differs from J297')
        projected.append(point+axis*np.dot(pivot-point,axis))
        radii.append(face['radius_mm'])
    if not np.allclose(projected,projected[0],atol=1e-6,rtol=0):
        raise ValueError('J297 cylinder faces no longer lie on one axis')
    alternate = deepcopy(model)
    node = next(n for n in alternate['nodes'] if n['region_id']==joint['child_cell_id'])
    node['joint']['axis_point_mm'] = projected[0].tolist()
    return alternate,dict(joint_id=joint['joint_id'],step_cylinder_face_ids=faces,
        step_outer_radii_mm=radii,axis_direction=axis.tolist(),step_cylinder_axis_point_mm=projected[0].tolist(),
        midplane_axis_point_mm=pivot.tolist(),offset_mm=(projected[0]-pivot).tolist())


def overlap(shapes, boxes, matrices, pair):
    a,b = pair
    gap = aabb_gap(transformed_bounds(boxes[a],matrices[a]),transformed_bounds(boxes[b],matrices[b]))
    if gap>1e-6:
        return dict(cells=pair,intersection_volume_mm3=0.,aabb_gap_mm=gap,status='AABB_SEPARATED')
    common = intersect(transformed(shapes[a],matrices[a]),transformed(shapes[b],matrices[b]))
    result_volume = volume(common)
    return dict(cells=pair,intersection_volume_mm3=result_volume,aabb_gap_mm=gap,
        status='OVERLAP' if result_volume>VOLUME_TOLERANCE else 'NO_POSITIVE_VOLUME',
        intersection_world_aabb_mm=bounds(common) if result_volume>VOLUME_TOLERANCE else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',default='NP2_001')
    parser.add_argument('--station')
    parser.add_argument('--phase',choices=('entry','motion','completed'))
    parser.add_argument('--tau',type=float)
    parser.add_argument('--pair',nargs=2,metavar=('FIRST_CELL','SECOND_CELL'))
    args = parser.parse_args()
    model,plans,asset,_,fingerprints = setup()
    plan = next((p for p in plans['plans'] if p['plan_id']==args.plan),None)
    if plan is None:
        raise ValueError('Choose one of the selected P003 plans')
    if asset['asset_id']!=sha(STEP):
        raise ValueError('Stale STEP cylinder evidence')
    alternate,axes = cylinder_axis_model(model,asset)
    has_sample = (args.station is not None,args.phase is not None,args.tau is not None)
    if any(has_sample) and not all(has_sample):
        parser.error('--station, --phase and --tau must be specified together')
    pairs = [args.pair] if args.pair else PAIRS
    cell_ids = {c for pair in pairs for c in pair}
    if not cell_ids<={c['cell_id'] for c in model['cells']} or any(a==b for a,b in pairs):
        parser.error('Choose two distinct P003 cell IDs')
    if all(has_sample):
        samples = [(None,args.station,args.phase,args.tau)]
    else:
        samples = [(oid,plan['assignments'][oid],phase,tau)
                   for oid in ('O069','O120') for phase,tau in SAMPLES]
    shapes = {cid:load_shape(PREP/'brep'/f'{cid}.brep') for cid in sorted(cell_ids)}
    boxes = {rid:bounds(shape) for rid,shape in shapes.items()}
    expected = {o['operation_id'] for o in model['operations'] if o['operation_type'] in FORMS|REMOVALS}
    rows = []
    for oid,sid,phase,tau in samples:
        sample = frame(model,plan,sid,phase,tau)
        check = frame(alternate,plan,sid,phase,tau)
        if not expected<=set(sample['executed_operation_ids']):
            raise ValueError('The common completed material reference is too early for this sample')
        if sample['joint_angles_deg']!=check['joint_angles_deg']:
            raise ValueError('Pivot perturbation changed a joint angle')
        rows.append(dict(operation_id=oid,station_id=sid,phase=phase,tau=tau,
            shared_j297_angle_deg=sample['joint_angles_deg']['J297'],joint_angles_deg=sample['joint_angles_deg'],
            active_operation_ids=sample['current_operation_ids'],
            midplane_axis=[overlap(shapes,boxes,sample['transforms'],p) for p in pairs],
            step_cylinder_axis=[overlap(shapes,boxes,check['transforms'],p) for p in pairs]))
    last_sid = max(model['stations'],key=lambda s:s['order'])['station_id']
    for version in (model,alternate):
        final = frame(version,plan,last_sid,'completed',1.)['transforms']
        if any(not np.allclose(m,np.eye(4),atol=1e-10,rtol=0) for m in final.values()):
            raise ValueError('Perturbation changed the completed STEP reference')
    self_path = Path(__file__).resolve()
    fingerprints[self_path.relative_to(ROOT).as_posix()] = sha(self_path)
    verify_sources(fingerprints)
    result = dict(part_id='P003',plan_id=plan['plan_id'],axes=axes,samples=rows,
        volume_tolerance_mm3=VOLUME_TOLERANCE,source_count=len(fingerprints),
        scope='Selected rigid-region pairs and samples with completed STEP material',
        interpretation='Overlaps sensitive to the J297 pivot should not be interpreted as robust. '
            'The cylinder-axis variant checks sensitivity only and does not specify a physical radius evolution.',
        bend_bands_checked=False,manufacturing_safety_proved=False)
    if all(has_sample):
        pair_tag = '_'.join(args.pair) if args.pair else 'default_pairs'
        name = f'{args.station}_{args.phase}_{args.tau:g}_{pair_tag}.json'
    else:
        name = 'sensitivity.json' if not args.pair else '_'.join(args.pair)+'.json'
    write(OUT/plan['plan_id']/name,result)
    for r in rows:
        print(r['operation_id'] or r['station_id'],r['phase'],r['tau'],r['shared_j297_angle_deg'],
              [(round(a['intersection_volume_mm3'],6),round(b['intersection_volume_mm3'],6))
               for a,b in zip(r['midplane_axis'],r['step_cylinder_axis'])])


if __name__=='__main__':
    main()
