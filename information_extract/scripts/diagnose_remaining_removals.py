"""Quantify why ordinary punch outlines do not yet map to a unique CAD inner wire."""
from pathlib import Path
import json
import sys
import numpy as np
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from information_extract.scripts.check_whole_panel_collision import read, sha
from information_extract.three_d.local_collision import indexed, bounds, volume
from information_extract.three_d.local_motion import poses
from information_extract.three_d.material_removal import source_prism
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_FACE, TopAbs_WIRE, TopAbs_FORWARD
from OCP.TopoDS import TopoDS
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.gp import gp_Vec

MODULE = ROOT / 'information_extract'


def main():
    cfg = read(MODULE / 'configs/P001_whole_panel_collision_v01.json')
    stage = read(MODULE / cfg['stage_data'])
    audit = read(MODULE / 'outputs/staged_removal_v01/P001/mapping_audit.json')
    old = read(MODULE / 'outputs/whole_panel_collision_v01/P001/report.json')
    evidence = {x['region_id']: x for x in old['panel_evidence']}
    plans = next(x for x in stage['plans'] if x['plan_id'] == cfg['plan_id'])
    skeleton = stage['skeleton']
    flat = poses(skeleton, {j['joint_id']: 0 for j in skeleton['joints']})
    regions = {r['region_id']: r for r in skeleton['regions']}
    flat_z = {p['region_id']: p.get('flat_z', 0) for p in stage['panels']}
    operations = {o['operation_id']: o for o in stage['operations']}

    step = MODULE / read(MODULE / cfg['cad_config'])['step']
    reader = STEPControl_Reader()
    if reader.ReadFile(str(step)) != IFSelect_RetDone or not reader.TransferRoots():
        raise ValueError('STEP read failed')
    faces = indexed(reader.OneShape(), TopAbs_FACE)

    # Keep CAD wire solids in the same panel-local prism construction as the
    # accepted audit. They are evidence only; no unassigned wire is cut.
    wires = []
    for row in audit['inner_wires']:
        face = TopoDS.Face_s(faces.FindKey(row['front_face_id']))
        wire_map = indexed(face, TopAbs_WIRE)
        wire = TopoDS.Wire_s(wire_map.FindKey(row['wire_index']))
        made = BRepBuilderAPI_MakeFace(TopoDS.Wire_s(wire.Oriented(TopAbs_FORWARD)), True)
        if not made.IsDone():
            continue
        solid = BRepPrimAPI_MakePrism(made.Face(), gp_Vec(*evidence[row['region_id']]['extrusion_vector_mm'])).Shape()
        wires.append({**row, 'solid': solid, 'volume_mm3': volume(solid),
                      'bounds_mm': bounds(solid)})

    rows = []
    for base in audit['operations']:
        if base['status'] not in {'NO_EXACT_INNER_WIRE_MATCH', 'UNRESOLVED_PANEL'}:
            continue
        op = operations[base['operation_id']]
        row = {k: base.get(k) for k in ['operation_id', 'operation_type', 'geometry_id', 'region_id', 'status']}
        row['station_id'] = plans['assignments'][op['operation_id']]
        row['candidates'] = []
        if not op.get('region_id'):
            row.update(classification='PANEL_ASSOCIATION_UNRESOLVED', reason='No unique supported panel for complete operation geometry')
            rows.append(row)
            continue
        rid = op['region_id']
        try:
            cutter = source_prism(op, stage['display_outline'], flat_z[rid],
                                  np.linalg.inv(flat[rid]), evidence[rid]['extrusion_vector_mm'])
        except Exception as exc:
            row.update(classification='OPERATION_GEOMETRY_REBUILD_FAILED', reason=str(exc))
            rows.append(row)
            continue
        cv = volume(cutter)
        cb = np.asarray(bounds(cutter))
        for w in wires:
            if w['region_id'] != rid:
                continue
            wb = np.asarray(w['bounds_mm'])
            gap = np.maximum(0, np.maximum(cb[:3] - wb[3:], wb[:3] - cb[3:]))
            bbox_gap = float(np.linalg.norm(gap))
            if bbox_gap > 1e-6:
                continue
            common = BRepAlgoAPI_Common(cutter, w['solid'])
            if not common.IsDone():
                continue
            common_volume = volume(common.Shape())
            row['candidates'].append({
                'wire_id': w['wire_id'],
                'wire_volume_mm3': w['volume_mm3'],
                'operation_volume_mm3': cv,
                'common_volume_mm3': common_volume,
                'operation_fraction_in_wire': common_volume / max(cv, 1e-12),
                'wire_fraction_covered': common_volume / max(w['volume_mm3'], 1e-12),
                'symmetric_difference_mm3': cv + w['volume_mm3'] - 2 * common_volume,
                'bbox_gap_mm': bbox_gap,
            })
        row['candidates'].sort(key=lambda x: (-x['operation_fraction_in_wire'], x['symmetric_difference_mm3'], x['wire_id']))
        if not row['candidates']:
            row.update(classification='NO_CAD_INNER_WIRE_INTERSECTION', reason='No same-panel CAD inner wire shares a positive-volume region')
        else:
            best = row['candidates'][0]
            row['best_wire_id'] = best['wire_id']
            if best['operation_fraction_in_wire'] >= .999999 and best['wire_fraction_covered'] >= .999999 and best['symmetric_difference_mm3'] <= 1e-5:
                row.update(classification='EXACT_MATCH_REQUIRES_AUDIT_RECONCILIATION', reason='Numerically exact candidate was not accepted by the existing uniqueness audit')
            elif best['operation_fraction_in_wire'] >= .999 and best['wire_fraction_covered'] < .999:
                row.update(classification='CONTAINED_IN_LARGER_CAD_REGION', reason='Punch geometry is inside a larger CAD inner region; cutting it would alter an unconfirmed region')
            elif best['operation_fraction_in_wire'] > .05:
                row.update(classification='PARTIAL_GEOMETRIC_MATCH', reason='Only part of the operation footprint overlaps a CAD inner region')
            else:
                row.update(classification='OFFSET_OR_BOUNDARY_MISMATCH', reason='Same-panel bounding boxes touch but volumetric overlap is too small')
        rows.append(row)

    sources = {str(MODULE / cfg['stage_data']): sha(MODULE / cfg['stage_data']),
               str(MODULE / 'outputs/staged_removal_v01/P001/mapping_audit.json'): sha(MODULE / 'outputs/staged_removal_v01/P001/mapping_audit.json'),
               str(step): sha(step)}
    result = {'scope': 'remaining ordinary punch operations only', 'source_sha256': sources,
              'rows': rows, 'classification_counts': {},
              'interpretation': 'Classification is diagnostic evidence. Only the prior 12 exact unique matches are used for cutting.'}
    from collections import Counter
    result['classification_counts'] = dict(Counter(x['classification'] for x in rows))
    out = MODULE / 'outputs/staged_removal_v01/P001'
    (out / 'remaining_mapping_diagnostic.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 剩余普通冲孔对应关系诊断', '',
             '本报告仅量化剩余普通冲孔与同板面 CAD 内环的几何关系，不将接近或部分重叠升级为去料事件。', '',
             '|分类|数量|', '|---|---:|']
    lines.extend(f'|{k}|{v}|' for k, v in result['classification_counts'].items())
    lines += ['', '## 动作级结果', '', '|动作|类型|板面|工序|分类|最佳内环|操作体积 mm³|操作在内环占比|内环覆盖占比|', '|---|---|---|---|---|---|---:|---:|---:|']
    for x in rows:
        b = x['candidates'][0] if x.get('candidates') else {}
        lines.append(f"|{x['operation_id']}|{x['operation_type']}|{x.get('region_id') or '未决'}|{x['station_id']}|{x['classification']}|{x.get('best_wire_id','')}|{b.get('operation_volume_mm3',0):.6g}|{b.get('operation_fraction_in_wire',0):.6g}|{b.get('wire_fraction_covered',0):.6g}|")
    lines += ['', '## 使用边界', '', '- 仍只保留原先 12 个 EXACT_UNIQUE_GEOMETRIC_SUBSET 作为真实切孔。', '- `CONTAINED_IN_LARGER_CAD_REGION` 不能直接切除：CAD 内环可能包含更大最终特征或未分解区域。', '- `PARTIAL_GEOMETRIC_MATCH`、`OFFSET_OR_BOUNDARY_MISMATCH` 需要核对轮廓方向、弯曲补偿或 CAD 分区，不能凭阈值强配。', '- `PANEL_ASSOCIATION_UNRESOLVED` 需要新的可审计材料连接证据；不能把动作投到最近板面。', '- 该诊断不修改 H7、候选、评分或 reference。']
    (out / 'REMAINING_MAPPING_DIAGNOSTIC.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(result['classification_counts'], ensure_ascii=False))


if __name__ == '__main__':
    main()
