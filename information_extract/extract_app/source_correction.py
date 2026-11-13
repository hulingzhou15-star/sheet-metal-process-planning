"""Explicit user-approved source replacement; original DXFs remain immutable."""
import hashlib
import json
from .source_paths import companion_root
from .source_supplement import _contained, _translated, _residual


def apply_confirmed_correction(path, config, base_sha, segments):
    relative = config.get('confirmed_geometry_correction')
    if not relative:
        return segments, None, []
    from .drawing import load_drawing
    root = companion_root(path)
    manifest_path = _contained(root, relative)
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    if manifest['schema_version'] != 1 or manifest['baseline_sha256'] != base_sha:
        raise ValueError('修正清单基准指纹不匹配')
    reference_path = _contained(root, manifest['reference_file'])
    if hashlib.sha256(reference_path.read_bytes()).hexdigest() != manifest['reference_sha256']:
        raise ValueError('修正参考图指纹不匹配')
    rc = {k: config[k] for k in ('geometry_tolerance', 'parallel_tolerance', 'coordinate_mode', 'material_block')}
    rc['confirmed_source_units'] = config['confirmed_source_units']
    reference = load_drawing(reference_path, rc)
    if config['coordinate_mode'] != 'world_xy' or manifest['registration']['type'] != 'TRANSLATION':
        raise ValueError('修正只允许已核验的整图平移')
    translated = {s['id']: _translated(s, manifest['registration']['translation_xy']) for s in reference['segments']}
    index = {s['id']: s for s in segments}
    pairs = manifest['registration']['verified_pairs']
    if len(pairs) < 3 or any(_residual(index[p['baseline_id']], translated[p['reference_id']]) > config['geometry_tolerance'] for p in pairs):
        raise ValueError('修正整图配准失配')
    removals = manifest['remove_source_ids']
    additions = manifest['add_reference_ids']
    if len(set(removals)) != len(removals) or not set(removals) <= index.keys() or len(set(additions)) != len(additions):
        raise ValueError('修正来源清单重复或缺失')
    project = json.loads(_contained(root, manifest['project_file']).read_text(encoding='utf-8'))
    if project['part_id'] != config['part_id'] or project['source_sha256'] != base_sha:
        raise ValueError('修正项目与来源不一致')
    if any(set(o['source_segment_ids']) & set(removals) for o in project['operations']):
        raise ValueError('待移除来源已绑定动作，必须先明确迁移，禁止静默丢失')
    kept = [s for s in segments if s['id'] not in removals]
    added = []
    for sid in additions:
        row = translated[sid].copy()
        allowed = manifest.get('allowed_segment_types', ['LINE', 'ARC'])
        if not set(allowed) <= {'LINE', 'ARC', 'CIRCLE'} or row['segment_type'] not in allowed or any(_residual(row, s) <= config['geometry_tolerance'] for s in kept + added):
            raise ValueError('修正轮廓类型无效或与已有来源重合')
        identity = [base_sha, manifest['reference_sha256'], sid, manifest['registration']['translation_xy']]
        row['id'] = 'S_' + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
        row['provenance'] = {'source_file': manifest['reference_file'], 'source_sha256': manifest['reference_sha256'],
                             'original_source_id': sid, 'translation_xy': manifest['registration']['translation_xy'], 'reason': 'USER_CONFIRMED_DRAWING_CORRECTION'}
        added.append(row)
    if len({s['id'] for s in kept + added}) != len(kept + added):
        raise ValueError('修正后来源 ID 冲突')
    audit = {'manifest': relative, 'removed_segments': [index[s] for s in removals], 'added_segments': added,
             'decision': manifest['decision'], 'groups': manifest['groups']}
    dependencies = [{'file_name': relative, 'sha256': hashlib.sha256(raw).hexdigest()},
                    {'file_name': manifest['reference_file'], 'sha256': manifest['reference_sha256']}]
    return kept + added, audit, dependencies
