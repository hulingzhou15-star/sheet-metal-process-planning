"""Extract local bend boundaries, audit CAD transitions, build a finite-band review."""
from pathlib import Path
import sys
import json
import math
import hashlib
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / 'information_extract'
sys.path.insert(0, str(ROOT))
from information_extract.three_d.finite_bend import frames, panel_transforms, bend_point, band_area


def read(p):
    return json.loads(p.read_text(encoding='utf-8'))


def write(p, value):
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def build():
    cfg = read(MODULE / 'configs/P001_finite_bend_v01.json')
    out = MODULE / cfg['output']; out.mkdir(parents=True, exist_ok=True)
    drawing = read(MODULE / cfg['source_drawing_pack'] / 'evidence.json')
    if hashlib.sha256(Path(drawing['source']).read_bytes()).hexdigest() != drawing['source_sha256']:
        raise ValueError('Original DXF changed; rebuild and review evidence first')
    old = read(MODULE / cfg['source_material_pack'] / 'viewer_data.json')
    asset = read(MODULE / cfg['source_cad_pack'] / 'asset.json')
    cad_config = read(MODULE / 'configs/three_d_state_v01.json')
    if hashlib.sha256((MODULE / cad_config['step']).read_bytes()).hexdigest() != asset['asset_id']:
        raise ValueError('STEP changed; rebuild source topology first')
    material = read(MODULE / 'configs/P001_confirmed_material_parameters.json')
    t = material['stock_thickness_mm']; tol = cfg['geometry_tolerance_mm']
    example = old['example']; joints = example['joints']
    if [example['joint_to_operation'][j['joint_id']] for j in joints] != cfg['operation_ids']:
        raise ValueError('Unexpected local chain')
    unfold = next(v for v in drawing['views'] if v['id'] == 'unfold')
    if not unfold['registration']['independently_validated']:
        raise ValueError('Unfold frame not independently checked')
    bend_rows = []
    for j, assoc in zip(joints, example['association_evidence']):
        if abs(j['axis_direction'][0]) < 1 - tol:
            raise ValueError('This local prototype only supports X-parallel bends')
        center = np.mean(assoc['recorded_endpoints_xy_mm'], axis=0)
        xmin, xmax = sorted(p[0] for p in assoc['recorded_endpoints_xy_mm'])
        candidates = []
        for e in unfold['local_curves']:
            if e['type'] != 'LINE':
                continue
            a, b = np.array(e['start_cad_xy_mm']), np.array(e['end_cad_xy_mm'])
            if abs(a[1] - b[1]) > tol or abs(min(a[0], b[0]) - xmin) > tol or abs(max(a[0], b[0]) - xmax) > tol:
                continue
            if abs(a[1] - center[1]) <= cfg['boundary_search_mm']:
                candidates.append({'y_mm': float(a[1]), 'source_id': e['source_id']})
        lower = sorted((c for c in candidates if c['y_mm'] < center[1] - tol), key=lambda c: -c['y_mm'])
        upper = sorted((c for c in candidates if c['y_mm'] > center[1] + tol), key=lambda c: c['y_mm'])
        if not lower or not upper:
            raise ValueError('Cannot bracket recorded bend line with actual DXF boundaries')
        lo, hi = lower[0], upper[0]; width = hi['y_mm'] - lo['y_mm']
        ev = j['surface_evidence'][0]; cy = asset['faces'][ev['cylinder_face_id'] - 1]
        # The opposite skin faces share a sharp edge through the cylinder axis.
        regions = {r['region_id']: r for r in example['regions']}
        opposites = [next(f for f in regions[r]['skin_face_ids'] if f != ev['contact_skin_faces'][r])
                     for r in [j['parent_region_id'], j['child_region_id']]]
        shared = set(asset['faces'][opposites[0] - 1]['edge_ids']) & set(asset['faces'][opposites[1] - 1]['edge_ids'])
        axis = np.array(cy['cylinder']['direction']); origin = np.array(cy['cylinder']['point_mm'])
        sharp = []
        for eid in shared:
            edge = asset['edges'][eid - 1]
            if edge['curve_type'] != 'Line':
                continue
            error = max(np.linalg.norm(np.cross(np.array(p) - origin, axis)) for p in edge['points'])
            if error <= tol:
                sharp.append({'edge_id': eid, 'axis_distance_mm': float(error)})
        if not sharp or abs(cy['cylinder']['radius_mm'] - t) > tol:
            raise ValueError('No verified sharp-inner / outer-radius-equals-thickness CAD section')
        target = j['angle_deg']; equivalent_offset = width / abs(math.radians(target))
        if not 0 < equivalent_offset < t:
            raise ValueError('Incompatible developed strip / CAD target geometry')
        bend_rows.append({'operation_id': example['joint_to_operation'][j['joint_id']],
                          'target_angle_deg': target, 'flat_boundary_y_mm': [lo['y_mm'], hi['y_mm']],
                          'boundary_source_ids': [lo['source_id'], hi['source_id']], 'developed_width_mm': width,
                          'x_limits_mm': [xmin, xmax], 'outer_radius_mm': cy['cylinder']['radius_mm'],
                          'inner_radius_in_exported_cad_mm': 0.0, 'inner_sharp_edge_evidence': sharp,
                          'cylinder_face_id': cy['face_id'], 'cylinder_axis_point_mm': cy['cylinder']['point_mm'],
                          'equivalent_offset_from_inner_mm': equivalent_offset,
                          'equivalent_k_for_this_reconstruction': equivalent_offset / t,
                          'neutral_offset_from_top_mm': equivalent_offset if target > 0 else t - equivalent_offset,
                          'parameter_status': 'DERIVED_LOCAL_GEOMETRIC_EQUIVALENT_NOT_IDENTIFIED_MATERIAL_K'})
    middle = bend_rows[1]['flat_boundary_y_mm'][0] - bend_rows[0]['flat_boundary_y_mm'][1]
    root_origin = bend_rows[0]['cylinder_axis_point_mm']
    if abs(root_origin[1] - bend_rows[0]['flat_boundary_y_mm'][0]) > tol:
        raise ValueError('Root bend start not aligned between drawing and CAD')
    model = {'schema_version': 1, 'thickness_mm': t, 'bends': bend_rows,
             'root_start_top_yz_mm': root_origin[1:], 'middle_straight_length_mm': middle,
             'terminal_straight_length_mm': asset['faces'][example['regions'][-1]['front_face_id'] - 1]['bounds_mm'][4] - asset['faces'][example['regions'][-1]['front_face_id'] - 1]['bounds_mm'][1],
             'band_mesh_intervals': cfg['band_mesh_intervals'], 'scope': cfg['interpretation'],
             'source_dxf_sha256': drawing['source_sha256'], 'source_step_sha256': asset['asset_id'],
             'physical_forming_validated': False, 'collision_filter_enabled': False}
    feature = old['feature']; center = feature['formed_surface_center_mm']
    stages = [({'O009': 0, 'O010': 0}, '展开 / 第4序图所示局部', 'stage04'),
              ({'O009': 0, 'O010': 1}, '末端已折 / 第5序图所示局部', 'stage05'),
              ({'O009': 1, 'O010': 1}, '两次折弯已形成 / 第6序图所示局部', 'stage06')]
    snapshots = []
    for fractions, title, view in stages:
        transforms = panel_transforms(model, fractions)
        point = (transforms[-1] @ [*center, 1])[:3]
        snapshots.append({'fractions': fractions, 'title': title, 'drawing_view': view,
                          'feature_center_mm': point.tolist(), 'transforms': [a.tolist() for a in transforms]})
    # Held-out validation: center, rounded tip and intermediate projection were
    # not used to determine strip widths, radii or equivalent offsets.
    predicted_flat = snapshots[0]['feature_center_mm']
    flat_circle = next(e for e in unfold['local_curves'] if e['type'] == 'CIRCLE')
    flat_tip = max(e['start_cad_xy_mm'][1] for e in unfold['local_curves'] if e['type'] == 'LINE')
    final_tip_y = max(asset['faces'][f - 1]['bounds_mm'][4] for f in example['regions'][-1]['skin_face_ids'])
    computed_tip = (panel_transforms(model, stages[0][0])[-1] @ [center[0], final_tip_y, center[2], 1])[:3]
    stage5 = next(v for v in drawing['views'] if v['id'] == 'stage05')
    # full-width lines corresponding to front/back terminal start in the top view
    targets = frames(model, stages[1][0])['panels'][-1]
    start = np.array(targets['start_top_yz_mm']); phi = targets['phi']
    projected_y = [start[0], (start - t * np.array([-math.sin(phi), math.cos(phi)]))[0]]
    observed = [e for e in stage5['local_curves'] if e['type'] == 'LINE' and
                abs(abs(e['start_cad_xy_mm'][0] - e['end_cad_xy_mm'][0]) - (bend_rows[1]['x_limits_mm'][1] - bend_rows[1]['x_limits_mm'][0])) < tol and
                abs(e['start_cad_xy_mm'][1] - e['end_cad_xy_mm'][1]) < tol]
    checks = [{'name': '展开孔中心（未参与参数确定）', 'residual_mm': float(np.linalg.norm(np.array(predicted_flat[:2]) - flat_circle['center_cad_xy_mm']))},
              {'name': '展开末端外边界（未参与参数确定）', 'residual_mm': abs(float(computed_tip[1]) - flat_tip)},
              {'name': '中间板直段：DXF 与 STEP', 'residual_mm': abs(middle - (asset['faces'][example['regions'][1]['front_face_id'] - 1]['bounds_mm'][5] - asset['faces'][example['regions'][1]['front_face_id'] - 1]['bounds_mm'][2]))}]
    for y in projected_y:
        best = min(observed, key=lambda e: abs(e['start_cad_xy_mm'][1] - y))
        checks.append({'name': '第5序图末端板厚边界投影', 'computed_y_mm': float(y), 'drawing_source_id': best['source_id'],
                       'residual_mm': abs(best['start_cad_xy_mm'][1] - y)})
    # Outer bend samples must lie on the exact CAD cylinders in the final state.
    zones = frames(model, stages[-1][0])['zones']
    for i, (b, z) in enumerate(zip(bend_rows, zones)):
        depth = t if b['target_angle_deg'] > 0 else 0.0
        errors = [abs(np.linalg.norm(bend_point(**z, depth=depth, u=u) - np.array(b['cylinder_axis_point_mm'][1:])) - b['outer_radius_mm'])
                  for u in np.linspace(0, 1, 65)]
        checks.append({'name': b['operation_id'] + ' 成品外圆柱截面', 'residual_mm': float(max(errors))})
    for c in checks:
        c['residual_mm'] = float(c['residual_mm'])
        c['passed'] = bool(c['residual_mm'] <= cfg['geometry_validation_tolerance_mm'])
    area_checks = [{'operation_id': b['operation_id'], 'flat_band_area_mm2': band_area(model, i, 0),
                    'formed_band_area_mm2': band_area(model, i, 1),
                    'relative_change': band_area(model, i, 1) / band_area(model, i, 0) - 1}
                   for i, b in enumerate(bend_rows)]
    validation = {'checks': checks, 'max_geometric_residual_mm': max(c['residual_mm'] for c in checks),
                  'old_flat_center_residual_mm': feature['center_residual_mm'],
                  'band_area_consistency': area_checks,
                  'physical_gate': 'FAIL_VOLUME_CONSERVATION_FOR_THIS_CONSTANT_THICKNESS_INTERPOLATION',
                  'limits': ['CAD inner corners are sharp in this export, not verified physical zero inner radius.',
                             'Geometric endpoint matching does not validate forming motion or identify material K.',
                             'Side-view Z registration remains unresolved; intermediate height is a model prediction.',
                             'Final holes reused; punching, flanging, tapping and leveling are not simulated.',
                             'No collision checks or candidate filtering are enabled.']}
    write(out / 'model.json', model); write(out / 'validation.json', validation); write(out / 'snapshots.json', snapshots)
    write(out / 'kernel_reference.json', [
        {'fractions': {'O009': a, 'O010': b}, 'transforms': [m.tolist() for m in panel_transforms(model, {'O009': a, 'O010': b})],
         'zones': frames(model, {'O009': a, 'O010': b})['zones']}
        for a in [0, .23, .5, 1] for b in [0, .23, .5, 1]])
    payload = {'model': model, 'surfaces': old['surfaces'], 'feature': feature, 'snapshots': snapshots,
               'validation': validation, 'drawings': {v['drawing_view']: (MODULE / cfg['source_drawing_pack'] / (v['drawing_view'] + '_local.svg')).read_text(encoding='utf-8') for v in snapshots}}
    write(out / 'viewer_data.json', payload)
    web = MODULE / 'three_d/web'
    page = (web / 'finite_bend_review.html').read_text(encoding='utf-8')
    for key, value in [('DATA', json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c')),
                       ('CORE', (web / 'core.js').read_text(encoding='utf-8')),
                       ('RENDERER', (web / 'motion_renderer.js').read_text(encoding='utf-8')),
                       ('FINITE', (web / 'finite_bend_core.js').read_text(encoding='utf-8')),
                       ('APP', (web / 'finite_bend_review.js').read_text(encoding='utf-8'))]:
        page = page.replace('/*__' + key + '__*/', value)
    (out / 'review.html').write_text(page, encoding='utf-8')
    print(json.dumps({'checks': checks, 'band_area': area_checks, 'snapshot_centers': [s['feature_center_mm'] for s in snapshots]}, ensure_ascii=False))


if __name__ == '__main__':
    build()
