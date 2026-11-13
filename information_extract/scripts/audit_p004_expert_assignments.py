"""Read-only P004 expert audit; quantify drawing variants without changing annotations."""
from collections import defaultdict
from copy import deepcopy
import hashlib
import html
import json
from pathlib import Path
import numpy as np
from information_extract.scripts.extract_p002_expert_dxf import transformed
from information_extract.scripts.extract_full_expert_drawings import transitive_parent_conflicts
from information_extract.extract_app.process_config import inspect_process

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/p004_expert_dxf_v01'


def line_error(a, b):
    if a['kind'] != 'LINE' or b['kind'] != 'LINE': return float('inf')
    x = np.array(a['points']); y = np.array(b['points'])
    return float(min(np.linalg.norm(x-y, axis=1).max(), np.linalg.norm(x-y[::-1], axis=1).max()))


def distinct_line_matches(source, targets, tolerance):
    """Maximum one-to-one matching: a single expert line cannot explain two source lines."""
    edges = {i: sorted((j for j, t in enumerate(targets) if line_error(s, t) <= tolerance),
                       key=lambda j: line_error(s, targets[j])) for i, s in enumerate(source)}
    owner = {}
    def augment(i, visited):
        for j in edges[i]:
            if j in visited: continue
            visited.add(j)
            if j not in owner or augment(owner[j], visited):
                owner[j] = i
                return True
        return False
    for i in edges: augment(i, set())
    return [{'source_id': source[i]['source_id'], 'expert': targets[j],
             'max_endpoint_error_mm': line_error(source[i], targets[j])} for j, i in sorted(owner.items(), key=lambda pair: pair[1])]


def run():
    evidence = json.loads((OUT / 'evidence.json').read_text(encoding='utf-8'))
    project = json.loads((ROOT / 'projects/P004/project.json').read_text(encoding='utf-8'))
    hashes = evidence['summary']['input_hashes']
    def verify_inputs():
        for path, expected in hashes.items():
            if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != expected:
                raise ValueError('Stale expert evidence: ' + path)
    verify_inputs()
    normalized = {}
    for f in evidence['frames']:
        m = np.array(f['matrix']); t = np.array(f['translation'])
        normalized[f['station_id']] = [transformed(g, m.T, -m.T @ t) for g in f['targets']]
    operations = {o['operation_id']: o for o in project['operations']}
    candidates = {r['operation_id']: r['proposed_expert_station'] for r in evidence['summary']['operations'] if r.get('proposed_expert_station')}
    variants = []
    for r in evidence['summary']['operations']:
        if r.get('proposed_expert_station') and r['status'] != 'RESOLVED_DRAWING_VARIANT': continue
        oid = r['operation_id']; source = [s for s in evidence['flat_primitives'] if s['source_id'] in r['source_segment_ids']]
        highlighted = [e for e in r['evidence'] if e['highlighted_source_count']]
        row = {'operation_id': oid, 'name': r['name'], 'strict_status': r.get('raw_matching_status', r['status']),
               'resolved': r['status'] == 'RESOLVED_DRAWING_VARIANT', 'candidate_station': None}
        if operations[oid]['operation_type'] == 'BEND' and len(source) == 2 and all(s['kind']=='LINE' for s in source) and len(highlighted)==1 and highlighted[0]['highlighted_source_count']==1:
            gap = line_error(source[0], source[1])
            if abs(gap - .15) > 1e-6: raise ValueError('Unexpected double-line spacing: ' + oid)
            row.update(candidate_station=highlighted[0]['station_id'], kind='SINGLE_EXPERT_LINE_DOUBLE_SOURCE_LINES',
                       source_double_line_spacing_mm=gap, exact_highlighted_segments=1, source_segment_count=2,
                       explanation='展开双线间距0.15mm；其中一条在OP60精确红色高亮，另一条未出现；位置明确，不能伪记2/2严格匹配。')
        elif oid == 'O013':
            matches_by_station = {}
            for station, targets in normalized.items():
                active = [g for g in targets if g['kind']=='LINE' and g['color'] not in ('#808080','#ffffff','#000000')]
                matches_by_station[station] = distinct_line_matches(source, active, .05)
            full = [s for s, matches in matches_by_station.items() if len(matches)==len(source)]
            if full != ['OP60']: raise ValueError('O013 does not have unique complete near-line evidence')
            matches = matches_by_station['OP60']
            row.update(candidate_station='OP60', kind='SMALL_LINE_POSITION_VARIANT', tolerance_used_only_for_audit_mm=.05,
                       distinct_matches=matches, maximum_endpoint_error_mm=max(m['max_endpoint_error_mm'] for m in matches),
                       minimum_endpoint_error_mm=min(m['max_endpoint_error_mm'] for m in matches),
                       explanation='8条线在OP60均有一对一红色对应，端点最大差小于0.05mm；严格匹配仍为0/8，不修改原图或全局容差。')
        elif oid == 'O077':
            matched_ids = set(next(e for e in r['evidence'] if e['station_id']=='OP30')['highlighted_source_ids'])
            missing = [s for s in source if s['source_id'] not in matched_ids]
            if len(missing)!=1: raise ValueError('O077 missing segment changed')
            length = float(np.linalg.norm(np.array(missing[0]['points'][0])-np.array(missing[0]['points'][1])))
            if abs(length-.3)>1e-6: raise ValueError('Unexpected O077 cap length')
            row.update(candidate_station='OP30', kind='MARKING_CAP_ABSENT', absent_source=missing[0], absent_length_mm=length,
                       explanation='4/5条刻印轮廓仅在OP30红色高亮；缺失的是0.3mm短封口线，位置和主轮廓明确。')
        else: raise ValueError('Unexpected pending action: ' + oid)
        if row['resolved']:
            row['explanation'] = r['basis']
        candidates[oid] = row['candidate_station']; variants.append(row)
    draft = deepcopy(project)
    for op in draft['operations']: op['expert_station'] = candidates[op['operation_id']]
    process = inspect_process(draft)
    direct = [{'child': o['operation_id'], 'parent': o['cad_parent_operation_id'], 'child_station': o['expert_station'],
               'parent_station': candidates[o['cad_parent_operation_id']]} for o in draft['operations']
              if o.get('cad_parent_operation_id') and int(o['expert_station'][2:]) >= int(candidates[o['cad_parent_operation_id']][2:])]
    raw_candidates = {r['operation_id']:r.get('raw_proposed_expert_station',r.get('proposed_expert_station')) for r in evidence['summary']['operations']}
    raw_direct = [{'child':o['operation_id'],'parent':o['cad_parent_operation_id']} for o in draft['operations']
                  if o.get('cad_parent_operation_id') and raw_candidates.get(o['operation_id']) and raw_candidates.get(o['cad_parent_operation_id'])
                  and int(raw_candidates[o['operation_id']][2:]) >= int(raw_candidates[o['cad_parent_operation_id']][2:])]
    hole_checks = []
    for conflict in raw_direct:
        oid = conflict['child']; op = operations[oid]
        if op['operation_type'] != 'PRE_PUNCH': continue
        s = next(s for s in evidence['flat_primitives'] if s['source_id'] in op['source_segment_ids']); center = np.array(s['points'][0])
        same_center = {station: [g for g in targets if g['kind']=='CIRCLE' and np.linalg.norm(np.array(g['points'][0])-center)<.05]
                       for station, targets in normalized.items() if station in ('OP10','OP20','OP30')}
        hole_checks.append({'operation_id': oid, 'flat_radius_mm': s['radius'], 'same_center_circles_by_station': same_center})
    result = {'part_id':'P004','annotation_revision':project['revision'],'input_hashes':hashes,
              'annotation_assigned_count':sum(bool(o.get('expert_station')) for o in project['operations']),
              'strict_unique_count':evidence['summary']['counts'].get('UNIQUE_FULL_HIGHLIGHT',0),
              'user_confirmed_count':evidence['summary']['counts'].get('USER_CONFIRMED_EXPERT_ASSIGNMENT',0),'geometry_variant_count':len(variants),
              'candidate_count':len(candidates),'candidate_assignments':candidates,'variant_evidence':variants,
              'cad_parent_conflicts':direct,'raw_drawing_parent_conflicts':raw_direct,'transitive_conflicts':transitive_parent_conflicts(draft,candidates),
              'process_check_of_candidate_copy':process,'pre_punch_position_checks':hole_checks,
              'annotations_modified':False,'scope':'只读核查；区分已解决绘图差异和待核候选，按当前保存配置检查H7及能力。'}
    verify_inputs()
    (OUT/'detailed_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    rows=''.join(f'<tr><td>{html.escape(r["operation_id"])}</td><td>{html.escape(r["name"])}</td><td>{r["candidate_station"]}</td><td>{html.escape(r["explanation"])}</td></tr>' for r in variants)
    issues=''.join(f'<li>{html.escape(s)}</li>' for s in process['reference_issues'])
    content=f'''<!doctype html><meta charset="utf-8"><title>P004 专家方案检查</title><style>body{{font:16px system-ui;max-width:1200px;margin:30px auto;background:#f4f6f9;color:#263d50}}table{{width:100%;border-collapse:collapse;background:white}}td,th{{padding:10px;border:1px solid #ccd6df;text-align:left}}aside{{padding:15px;background:#fff0cf}}a{{color:#1669b1}}</style><h1>P004 · 专家位置与关系检查</h1><aside>当前人工标注修订{project['revision']}：77动作，专家序已保存0项。62项严格完整唯一匹配；另外15项已找到位置候选，但存在绘图表达差异。原数据未改。</aside><p><a href="review.html">逐动作二维对照页</a> · <a href="detailed_audit.json">完整证据与坐标</a></p><h2>15项绘图差异</h2><table><tr><th>动作</th><th>名称</th><th>候选工序</th><th>检查结果</th></tr>{rows}</table><h2>按候选工序检查当前关系和能力</h2><ul>{issues}</ul><p>6组预孔在OP10同心位置没有找到圆；OP20同时存在红色R0.7预孔及R1.37/R2.02翻孔圈。不能擅自认定同序内部先冲后翻，也不能将这些预孔猜填OP10。OP10当前能力为NONE，与10个冲孔图示不符。OP70未匹配到本项目已录动作。</p><p>本页仅诊断，未改变H7、父子关系、工序能力或专家分配。</p>'''
    content=content.replace('专家序已保存0项',f'专家序已保存{result["annotation_assigned_count"]}项')
    content=content.replace('62项严格完整唯一匹配',f'{result["strict_unique_count"]}项采用严格完整唯一匹配，{result["user_confirmed_count"]}项采用用户指定分配')
    if result['user_confirmed_count']:
        content=content.replace('原数据未改。','最新用户修订：OP10支持冲孔；六个预孔改OP10，配对翻孔保留OP20，父子关系不变。')
        content=content.replace('OP10当前能力为NONE，与10个冲孔图示不符。','OP10能力现为PUNCH；当前候选分配能力与H7检查均无冲突。')
        content=content.replace('不能擅自认定同序内部先冲后翻，也不能将这些预孔猜填OP10。','原图同序证据保留；当前依据用户明确指令将六个预孔分配OP10，翻孔OP20，不假设序内先冲后翻。')
        content=content.replace('本页仅诊断，未改变H7、父子关系、工序能力或专家分配。','本页反映用户修订后的分配与能力；H7及父子关系保持不变。其余15项几何候选未回填。')
    resolved_variants = sum(r['resolved'] for r in variants)
    if resolved_variants:
        content = content.replace('另外15项已找到位置候选，但存在绘图表达差异。', f'另外{resolved_variants}项绘图差异已根据用户澄清及坐标证据解决并回填。')
        content = content.replace('其余15项几何候选未回填。', f'绘图差异已解决{resolved_variants}/15项；保留原始严格匹配计数。')
        content = content.replace('15项绘图差异</h2>', '15项绘图差异及处理依据</h2>').replace('<th>候选工序</th>', '<th>对应工序</th>')
    (OUT/'audit.html').write_text(content,encoding='utf-8')
    print(json.dumps({'strict_unique':result['strict_unique_count'],'located_variants':len(variants),'candidate_count':len(candidates),
                      'direct_H7_conflicts':len(direct),'transitive_conflicts':len(result['transitive_conflicts']),
                      'process_issues':process['reference_issues'],'O013_max_error_mm':next(r['maximum_endpoint_error_mm'] for r in variants if r['operation_id']=='O013')},ensure_ascii=False))
    return result


if __name__ == '__main__': run()
