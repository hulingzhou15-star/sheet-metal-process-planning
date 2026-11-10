"""Offline human review of 42 unresolved punch mappings; no production writes."""
from pathlib import Path
import hashlib
import json
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
MODULE = ROOT / 'information_extract'
from information_extract.scripts.check_whole_panel_collision import read, sha
from information_extract.three_d.local_collision import indexed
from information_extract.three_d.local_motion import poses
from information_extract.three_d.state import transform_point
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_FACE, TopAbs_WIRE, TopAbs_REVERSED
from OCP.TopoDS import TopoDS
from OCP.BRepTools import BRepTools_WireExplorer
from OCP.BRepAdaptor import BRepAdaptor_Curve


def sample_wire(wire):
    """Follow the actual oriented CAD wire, preserving closure and curved edges."""
    explorer = BRepTools_WireExplorer(wire)
    points = []
    while explorer.More():
        edge = explorer.Current()
        curve = BRepAdaptor_Curve(edge)
        count = 2 if 'Line' in str(curve.GetType()) else 97
        params = np.linspace(curve.FirstParameter(), curve.LastParameter(), count)
        if edge.Orientation() == TopAbs_REVERSED:
            params = params[::-1]
        for t in params[:-1]:
            p = curve.Value(float(t))
            points.append([p.X(), p.Y(), p.Z()])
        explorer.Next()
    if not points:
        raise ValueError('Empty CAD wire')
    return points + [points[0]]


def main():
    source = MODULE / 'outputs/staged_removal_v01/P001'
    out = MODULE / 'outputs/remaining_mapping_review_v01/P001'
    cfg_path = MODULE / 'configs/P001_whole_panel_collision_v01.json'
    cfg = read(cfg_path)
    diagnostic = read(source / 'remaining_mapping_diagnostic.json')
    for path, expected in diagnostic['source_sha256'].items():
        if sha(Path(path)) != expected:
            raise ValueError('Stale mapping diagnosis: ' + path)
    stage_path, asset_path = [MODULE / cfg[k] for k in ['stage_data', 'asset']]
    stage, asset = read(stage_path), read(asset_path)
    step = MODULE / read(MODULE / cfg['cad_config'])['step']
    if sha(step) != stage['asset_id'] or asset['asset_id'] != stage['asset_id']:
        raise ValueError('Geometry provenance mismatch')
    if len(diagnostic['rows']) != 42:
        raise ValueError('Review scope changed')
    candidates = MODULE / cfg['candidates']
    if sha(candidates) != stage['plans_sha256']:
        raise ValueError('Candidate provenance mismatch')
    protected = {str(p.resolve()): sha(p) for p in
                 [stage_path, asset_path, step, candidates, source / 'mapping_audit.json',
                  source / 'remaining_mapping_diagnostic.json', MODULE / cfg['workbook']]}
    skeleton = stage['skeleton']
    flat = poses(skeleton, {j['joint_id']: 0 for j in skeleton['joints']})
    reader = STEPControl_Reader()
    if reader.ReadFile(str(step)) != IFSelect_RetDone or not reader.TransferRoots():
        raise ValueError('STEP failed')
    faces = indexed(reader.OneShape(), TopAbs_FACE)
    if faces.Extent() != asset['face_count']:
        raise ValueError('Topology mismatch')
    wires = {}
    for row in read(source / 'mapping_audit.json')['inner_wires']:
        face = TopoDS.Face_s(faces.FindKey(row['front_face_id']))
        wire = TopoDS.Wire_s(indexed(face, TopAbs_WIRE).FindKey(row['wire_index']))
        path = sample_wire(wire)
        wires[row['wire_id']] = {
            **{k: row[k] for k in ['wire_id', 'region_id', 'front_face_id', 'wire_index']},
            'formed_path': path,
            'flat_path': [transform_point(flat[row['region_id']], p)[:2] for p in path],
        }
    operations = {o['operation_id']: o for o in stage['operations']}
    rows = []
    for diagnostic_row in diagnostic['rows']:
        op = operations[diagnostic_row['operation_id']]
        rid = op['region_id']
        region = next((r for r in skeleton['regions'] if r['region_id'] == rid), None)
        row = {**diagnostic_row,
               **{k: op[k] for k in ['source_feature_ref', 'flat_paths', 'formed_paths', 'region_candidates']}}
        row['front_face_id'] = region['front_face_id'] if region else None
        row['skin_face_ids'] = region['skin_face_ids'] if region else []
        row['review_question'] = (
            '这个二维孔在成品上对应哪里？请旋转成品并点选相关面，说明是否跨越折弯区。' if not rid else
            '橙色大开口里，哪些区域来自冲掉材料，哪些可能来自保留材料折起？请说明对应部位。'
            if diagnostic_row['classification'] == 'CONTAINED_IN_LARGER_CAD_REGION' else
            '蓝色动作轮廓与橙色 CAD 孔边是否对应同一个孔？若对应，请说明位置、形状或展开补偿的疑点。')
        rows.append(row)
    source_fingerprint = hashlib.sha256(json.dumps(protected, sort_keys=True).encode()).hexdigest()
    payload = {
        'schema_version': 1, 'part_id': 'P001', 'asset_id': asset['asset_id'],
        'topology_fingerprint': asset['topology_fingerprint'],
        'facts_fingerprint': stage['facts_fingerprint'], 'review_source_fingerprint': source_fingerprint,
        'source_sha256': protected, 'representatives': ['O166', 'O201', 'O196'],
        'operations': rows, 'wires': wires,
        'panels': [{'region_id': p['region_id'], 'flat_boundary': [v[:2] for v in p['flat_boundary']]} for p in stage['panels']],
        'bends': [{'operation_id': o['operation_id'], 'flat_paths': o['flat_paths']} for o in stage['operations'] if o['operation_type'] == 'BEND'],
        'faces': [{k: f[k] for k in ['face_id', 'surface_type', 'bounds_mm', 'centroid_mm', 'edge_ids']}
                  | {'normal': f.get('plane', {}).get('normal')} for f in asset['faces']],
        'edges': [{k: e[k] for k in ['edge_id', 'face_ids', 'points']} for e in asset['edges']],
        'mesh': asset['mesh'], 'bounds_mm': asset['bounds_mm'],
        'classification_counts': diagnostic['classification_counts'],
        'review_only': True, 'same_station_removal_trajectory_modeled': False,
    }
    out.mkdir(parents=True, exist_ok=True)
    web = MODULE / 'three_d/web'
    serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    (out / 'review_data.json').write_text(serialized, encoding='utf-8')
    html = (web / 'remaining_mapping_review.html').read_text(encoding='utf-8')
    for marker, value in [
        ('DATA', serialized.replace('<', '\\u003c')),
        ('CORE', (web / 'core.js').read_text(encoding='utf-8')),
        ('REVIEW_CORE', (web / 'mapping_review_core.js').read_text(encoding='utf-8')),
        ('RENDERER', (web / 'mapping_review_renderer.js').read_text(encoding='utf-8')),
        ('APP', (web / 'remaining_mapping_review.js').read_text(encoding='utf-8')),
    ]:
        html = html.replace('/*__' + marker + '__*/', value)
    (out / 'review.html').write_text(html, encoding='utf-8')
    for path, expected in protected.items():
        if sha(Path(path)) != expected:
            raise ValueError('Protected source modified: ' + path)
    (out / 'BUILD.json').write_text(json.dumps({
        'operation_count': len(rows), 'cad_face_count': faces.Extent(),
        'cad_triangle_count': len(asset['mesh']['triangle_face_ids']),
        'source_sha256': protected, 'protected_inputs_unchanged': True,
        'review_source_fingerprint': source_fingerprint,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'README.md').write_text('''# 剩余 42 个普通冲孔 · 人工审核

打开 [review.html](review.html)，可离线使用。建议依次审核 O166、O201、O196。

左侧蓝线是原二维动作；橙线是 CAD 内环经既有零宽逆折叠后的位置，灰线仅作板面定位。
右侧为真实成品 STEP 网格；蓝线是当前板面映射建议，橙线是选定 CAD 内环。
三维叠线始终可见，可能穿过遮挡面；它们不是新增切孔或真实工序状态。
板面未决动作只显示二维来源及真实成品，不自动生成三维动作轮廓。

1. 选择动作，查看局部叠图；可切到整件、旋转、缩放，或选择沿板面法向观察。
2. 点选成品相关面（可多选），填写“对应判断”和材料去留说明；页面自动保存本机草稿。
3. 点击“导出审核记录”，把下载的 JSON 文件交给助手。更换浏览器前也应导出备份。
4. 可导入同版记录恢复；不同零件、CAD、事实或诊断版本会被拒绝。

审核记录仅提供对应关系证据，不会自动修改实体、工序、候选、约束或评分。
程序分类是待核对差异，并未核清全部对应关系。当前真实去料集合仍为原 12 孔。

复现：`.venv-m3/Scripts/python.exe -B -X utf8 information_extract/scripts/build_remaining_mapping_review.py`
''', encoding='utf-8')
    print(out / 'review.html')


if __name__ == '__main__':
    main()
