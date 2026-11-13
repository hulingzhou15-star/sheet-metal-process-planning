"""Resolve P004 drawing variants using preserved user geometry and measured evidence."""
import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from information_extract.extract_app.drawing import load_drawing
from information_extract.extract_app.model import ProjectStore, validate_project
from information_extract.scripts.extract_p002_expert_dxf import key, source_primitive, transformed

ROOT = Path(__file__).resolve().parents[1]
DECISION = 'P004_DRAWING_VARIANTS_RESOLVED_20261112'
SAW = ['O002', *[f'O{i:03d}' for i in range(8, 13)], *[f'O{i:03d}' for i in range(55, 62)]]


def measure(evidence, audit, drawing):
    flat = {s['source_id']: s for s in evidence['flat_primitives']}
    row = next(r for r in audit['variant_evidence'] if r['operation_id'] == 'O013')
    pairs = row['distinct_matches']
    sy = sorted({round(p[1], 9) for m in pairs for p in flat[m['source_id']]['points']})
    ey = sorted({round(p[1], 9) for m in pairs for p in m['expert']['points']})
    if len(pairs) != 8 or len(sy) != 2 or len(ey) != 2:
        raise ValueError('O013 four-span collinearity changed.')
    spans = sorted({tuple(sorted(p[0] for p in flat[m['source_id']]['points'])) for m in pairs})
    if len(spans) != 4:
        raise ValueError('O013 should have four spans.')
    xerror = max(max(abs(a-b) for a,b in zip(sorted(p[0] for p in flat[m['source_id']]['points']),
                                          sorted(p[0] for p in m['expert']['points']))) for m in pairs)
    if xerror > 1e-6 or row['maximum_endpoint_error_mm'] > .05:
        raise ValueError('O013 correspondence changed.')
    o13 = dict(four_spans_share_axis=True, spans_x_mm=spans, flat_boundary_y_mm=sy,
               expert_boundary_y_mm=ey, flat_width_mm=sy[1]-sy[0], expert_width_mm=ey[1]-ey[0],
               centerline_shift_y_mm=sum(ey)/2-sum(sy)/2, max_x_error_mm=xerror,
               explanation='四段在各自图中严格共线；叠合后整体中线平移及双线间距有差，不是四段相互错位。保留录入几何。')
    segs = {s['id']: s for s in drawing['segments']}
    arc = segs['S_37f94174a26e5ef505631861']
    edge = segs['S_02703939a321475af3f207d9']
    upper = segs['S_5e7ae0cd7fc21e152ce0de7f']
    lower = segs['S_a5d85870c27a577401d31e4f']
    if arc['segment_type'] != 'ARC' or abs(arc['radius']-5) > 1e-6:
        raise ValueError('O077 material arc changed.')
    end = [upper['end_x'], upper['end_y']]
    radial_error = abs(math.hypot(end[0]-arc['center_x'],end[1]-arc['center_y'])-arc['radius'])
    if radial_error > 1e-6:
        raise ValueError('O077 upper stroke no longer terminates on arc.')
    frame = next(f for f in evidence['frames'] if f['station_id'] == 'OP30')
    m,t = np.array(frame['matrix']),np.array(frame['translation'])
    normalized = [transformed(g,m.T,-m.T@t) for g in frame['targets']]
    matched = [g for g in normalized if key(g) == key(source_primitive(arc))]
    if len(matched) != 1 or matched[0]['color'] != '#808080':
        raise ValueError('O077 shared material boundary not verified in expert drawing.')
    o77row = next(r for r in evidence['summary']['operations'] if r['operation_id']=='O077')
    active = [e for e in o77row['evidence'] if e['highlighted_source_count']]
    if [(e['station_id'],e['highlighted_source_count']) for e in active] != [('OP30',4)]:
        raise ValueError('O077 marking strokes changed.')
    angle = math.atan2(end[1]-arc['center_y'],end[0]-arc['center_x'])
    o77 = dict(material_arc_source_id=arc['id'], material_edge_source_id=edge['id'], radius_mm=arc['radius'],
               boundary_arc_start_xy=[arc['start_x'],arc['start_y']], boundary_arc_end_xy=end,
               boundary_arc_sweep_deg=math.degrees(angle), boundary_arc_length_mm=arc['radius']*angle,
               straight_join_length_mm=arc['start_y']-lower['end_y'], radial_error_mm=radial_error,
               expert_material_arc=matched[0], marking_highlight_station='OP30',
               opposite_end_cap_source_id='S_5c343487585dbe5b0e9f6eca',
               explanation='用户箭头在刻印右端：沿外轮廓R5小弧约0.297175mm收边，弧前约0.003mm直线连接；专家图同一材料边界为灰色，不要求整条外轮廓随刻印高亮。原0.3mm未匹配封口线在另一端。此记录是局部边界语义，不将整段90度外轮廓绑定为刻印。')
    return {'O013':o13,'O077':o77}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    out=ROOT/'outputs/p004_variant_confirmation_20261112'
    evidence=json.loads((ROOT/'outputs/p004_expert_dxf_v01/evidence.json').read_text(encoding='utf-8'))
    audit=json.loads((ROOT/'outputs/p004_expert_dxf_v01/detailed_audit.json').read_text(encoding='utf-8'))
    for rel,sha in evidence['summary']['input_hashes'].items():
        if hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()!=sha:raise ValueError('Stale input: '+rel)
    cfg=json.loads((ROOT/'configs/P004.json').read_text(encoding='utf-8'))
    drawing=load_drawing(ROOT/cfg['source_file'],cfg);store=ProjectStore(ROOT,drawing,'P004');previous=store.load()
    if previous['revision']!=11:raise ValueError('Expected revision 11; do not overwrite later edits.')
    measurements=measure(evidence,audit,drawing)
    p=deepcopy(previous);ops={o['operation_id']:o for o in p['operations']}
    assignments={oid:'OP60' for oid in SAW+['O013']};assignments['O077']='OP30'
    for oid,station in assignments.items():
        if ops[oid].get('expert_station'):raise ValueError('Already assigned: '+oid)
        if audit['candidate_assignments'][oid]!=station:raise ValueError('Candidate changed: '+oid)
        ops[oid]['expert_station']=station
    explanations={oid:'用户指定锯齿折弯以录入几何为准；保留两条折弯线。专家OP60唯一红色对应线足以定位动作，不要求两条全高亮。' for oid in SAW}
    explanations.update({oid:measurements[oid]['explanation'] for oid in measurements})
    p.setdefault('user_decisions',[]).append(dict(id=DECISION,
        quote='锯齿部分的折弯以我的为准；O013四个部分是否在一整条直线上；O077边界是这一段小弧。',
        expert_assignments=assignments,explanations=explanations,measurements=measurements,
        basis='用户澄清边界语义后，结合逐段坐标/颜色核验解决对应；不是用户逐一指定全部工序。',
        preserved_source_ids={oid:ops[oid]['source_segment_ids'] for oid in assignments},
        evidence_input_hashes=evidence['summary']['input_hashes']))
    report=validate_project(p,drawing,annotation_only=True)
    if report['errors'] or report['process_config']['reference_issues']:raise ValueError(json.dumps(report,ensure_ascii=False))
    out.mkdir(parents=True,exist_ok=True)
    for name,obj in [('before_project',previous),('measurements',measurements),('validation',report),('prior_audit',audit)]:
        (out/(name+'.json')).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.apply:p=store.save(p,previous['revision'])['project']
    print(json.dumps(dict(revision=p['revision'],applied=args.apply,assigned=sum(bool(o.get('expert_station')) for o in p['operations']),measurements=measurements),ensure_ascii=False))


if __name__=='__main__':main()
