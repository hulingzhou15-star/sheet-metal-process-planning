"""Summarize expert DXF evidence without overwriting manual facts or reference."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import numpy as np
from information_extract.scripts.extract_p002_expert_dxf import MODULE,OUT,transformed
from information_extract.extract_app.process_config import planning_operations, inspect_process


def audit_identified_radius_variant(data, decision):
    """Check the user-pointed CAD entity, without relaxing global matching."""
    op=next(o for o in data['operations'] if o['operation_id']==decision['operation_ids'][0])
    f=next(f for f in data['frames'] if f['station_id']==decision['station_id'] and not f['ancillary'])
    if not f['alignment_supported']:
        raise ValueError('Identified hole frame is not registered')
    source=[s for s in data['flat_primitives'] if s['source_id'] in op['source_segment_ids']]
    target=[s for s in f['targets'] if s['source_path'][0]==decision['expert_entity_handle']]
    if Counter(s['kind'] for s in source)!=Counter({'LINE':4,'ARC':4}) or Counter(s['kind'] for s in target)!=Counter({'LINE':4,'ARC':4}):
        raise ValueError('Identified rounded rectangle topology changed')
    aligned=[transformed(s,np.array(f['matrix']),np.array(f['translation'])) for s in source]
    def bounds(shapes):
        pts=np.array([p for s in shapes for p in s['points']])
        return np.array([pts.min(axis=0),pts.max(axis=0)])
    a,b=bounds(aligned),bounds(target)
    error=float(np.max(np.abs(a-b)))
    if error>1e-4 or any(s['color'].lower()!='#00ff00' for s in target):
        raise ValueError('Identified hole position, size or color changed')
    return {'operation_id':op['operation_id'],'station_id':f['station_id'],
            'frame_id':f['frame_id'],'expert_entity_handle':decision['expert_entity_handle'],
            'flat_size_mm':(a[1]-a[0]).tolist(),'expert_size_mm':(b[1]-b[0]).tolist(),
            'bbox_max_difference_mm':error,
            'flat_radii_mm':[s['radius'] for s in source if s['kind']=='ARC'],
            'expert_radii_mm':[s['radius'] for s in target if s['kind']=='ARC'],
            'expert_primitives':target,'basis':'User identified location; same registered position and size, different corner radii.'}


def apply_user_decisions(rows, operations, decisions):
    """Keep geometric evidence intact; explicit user semantics resolve assignment."""
    by_id={op['operation_id']:op for op in operations}
    by_row={row['operation_id']:row for row in rows}
    for decision in decisions['decisions']:
        for oid in decision['operation_ids']:
            op,row=by_id[oid],by_row[oid]
            if op['operation_type']!=decision['operation_type']:
                raise ValueError(f'User decision type changed: {oid}')
            station=decision['station_id']
            method=decision.get('resolution_method','FULL_HIGHLIGHT_SINGLE_STAGE')
            if method=='USER_IDENTIFIED_RADIUS_VARIANT':
                supported=row.get('geometry_variant_audit',{}).get('station_id')==station
            elif method=='USER_INTEGRATED_DRAW_HOLES':
                primary=set(op.get('primary_source_segment_ids',[]))
                supported=(op.get('composite_policy')=='INTEGRATED_DRAW_FORM'
                           and set(op.get('composite_operation_ids',[]))==set(decision['component_operation_ids'])
                           and bool(primary) and any(e['station_id']==station and primary<=set(e['highlighted_source_ids']) for e in row['evidence']))
            elif method=='USER_CONFIRMED_COMPOSITE_SINGLE_STAGE':
                supported=any(e['station_id']==station and e['highlighted_source_count']>0 for e in row['evidence'])
            elif method=='FULL_HIGHLIGHT_SINGLE_STAGE':
                supported=any(e['station_id']==station and e['highlighted_source_count']==row['source_count'] for e in row['evidence'])
            else:
                raise ValueError('Unknown decision method: '+method)
            if not supported:
                raise ValueError(f'Confirmed station lacks full geometry evidence: {oid}')
            row['geometric_status']=row['status']
            row['proposed_expert_station']=station
            row['user_decision_id']=decision['id']
            row['basis']=decision['quote']
            if row['status']!='UNIQUE_FULL_HIGHLIGHT':
                row['status']='USER_CONFIRMED_GEOMETRY_VARIANT' if method=='USER_IDENTIFIED_RADIUS_VARIANT' else 'USER_CONFIRMED_SINGLE_STAGE'


def main():
    data=json.loads((OUT/'evidence.json').read_text(encoding='utf-8'))
    for path,expected in [
        (MODULE.parent/data['source'],data['source_sha256']),
        (MODULE/'sources/P002/P002_1.dxf',data['flat_source_sha256']),
        (MODULE/'projects/P002/project.json',data['annotation_sha256']),
    ]:
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ValueError(f'Stale expert evidence: {path}')
    frames=data['frames']
    for i,f in enumerate(frames):
        f['frame_id']=f'view_{i+1:02d}'
        f['ancillary']=bool(re.search(r'/10\s*后',f['text']))
        f['station_id']='OP'+str(int(re.search(r'(\d+)/10',f['text']).group(1))*10)
        f['alignment_supported']=f.get('matched_count',0)>=20 and f.get('matched_count',0)-f.get('runner_up_count',0)>=10
    rows=[]
    active_ids={o['operation_id'] for o in planning_operations({'operations':data['operations']})}
    neutral={'#808080','#ffffff','#000000'}
    for op in data['operations']:
        evidence=[]
        for f in frames:
            if f['ancillary'] or not f['alignment_supported']:continue
            matched={sid:gs for sid in op['source_segment_ids'] if (gs:=f['matches'].get(sid))}
            active={sid:[g for g in gs if g['color'].lower() not in neutral] for sid,gs in matched.items()}
            active={sid:gs for sid,gs in active.items() if gs}
            evidence.append({'frame_id':f['frame_id'],'station_id':f['station_id'],
                             'matched_source_count':len(matched),'highlighted_source_count':len(active),
                             'highlighted_source_ids':list(active),
                             'colors':dict(Counter(g['color'] for gs in active.values() for g in gs))})
        active=[e for e in evidence if e['highlighted_source_count']]
        proposed=None
        if not active:status='NO_HIGHLIGHT_MATCH'
        elif len(active)>1:status='MULTIPLE_STATION_HIGHLIGHTS'
        elif active[0]['highlighted_source_count']!=len(op['source_segment_ids']):status='PARTIAL_HIGHLIGHT'
        else:status='UNIQUE_FULL_HIGHLIGHT';proposed=active[0]['station_id']
        rows.append({'operation_id':op['operation_id'],'name':op['name'],'feature_type':op['feature_type'],
                     'allocation_included':op['operation_id'] in active_ids,
                     'superseded_by_operation_id':op.get('superseded_by_operation_id'),
                     'composite_policy':op.get('composite_policy'),
                     'composite_operation_ids':op.get('composite_operation_ids',[]),
                     'source_count':len(op['source_segment_ids']),'source_segment_ids':op['source_segment_ids'],
                     'status':status,'proposed_expert_station':proposed,'evidence':evidence,
                     'basis':'User: differently colored entities denote current-stage work; matching restricted to saved flat feature bindings.'})
    decision_path=MODULE/'configs/P002_expert_decisions_20261111.json'
    decisions=json.loads(decision_path.read_text(encoding='utf-8'))
    if any(decisions[k]!=data[k] for k in ('source_sha256','flat_source_sha256')):
        raise ValueError('User decisions belong to different source drawings')
    for decision in decisions['decisions']:
        if decision.get('resolution_method')=='USER_IDENTIFIED_RADIUS_VARIANT':
            audit=audit_identified_radius_variant(data,decision)
            next(r for r in rows if r['operation_id']==audit['operation_id'])['geometry_variant_audit']=audit
    apply_user_decisions(rows,data['operations'],decisions)
    assigned={r['operation_id']:r['proposed_expert_station'] for r in rows if r['allocation_included'] and r['proposed_expert_station']}
    conflicts=[]
    for op in data['operations']:
        child,parent=op['operation_id'],op.get('cad_parent_operation_id')
        if child in assigned and parent in assigned and int(assigned[child][2:])>=int(assigned[parent][2:]):
            conflicts.append({'child':child,'parent':parent,'child_station':assigned[child],
                              'parent_station':assigned[parent],'status':'SOURCE_ANNOTATION_SEMANTICS_REVIEW_REQUIRED',
                              'note':'Reported after extraction; did not change H7, assignments or manual CAD links.'})
    non_strict=[]
    for op in data['operations']:
        for target in op.get('not_later_than_operation_ids',[]):
            if op['operation_id'] in assigned and target in assigned and int(assigned[op['operation_id']][2:])>int(assigned[target][2:]):
                non_strict.append({'operation_id':op['operation_id'],'not_later_than':target,
                                   'operation_station':assigned[op['operation_id']],'target_station':assigned[target],
                                   'relation':'<=','same_station_allowed':True})
    saved_project=json.loads((MODULE/'projects/P002/project.json').read_text(encoding='utf-8'))
    result={'part_id':'P002','status':'EXPERT_DRAWING_EXTRACTION_DRAFT','source_sha256':data['source_sha256'],
            'flat_source_sha256':data['flat_source_sha256'],'annotation_sha256':data['annotation_sha256'],
            'counts':dict(Counter(r['status'] for r in rows if r['allocation_included'])),
            'station_counts':dict(sorted(Counter(assigned.values()).items(),key=lambda x:int(x[0][2:]))),
            'operations':rows,'cad_parent_order_diagnostics':conflicts,
            'not_later_order_diagnostics':non_strict,
            'relation_capacity_issues':inspect_process(saved_project)['relation_capacity_issues'],
            'record_count':len(rows),'allocation_count':len(active_ids),
            'excluded_ancillary_views':[f['text'] for f in frames if f['ancillary']],
            'unmatched_expert_entities_ignored':True,'workbook_modified':False,
            'reference_or_inference_modified':False,'yellow_semantics':'DRAW_FORM_SINGLE_STAGE_CONFIRMED_OTHER_COLOR_SEMANTICS_NOT_GENERALIZED',
            'user_decisions':decisions,'annotation_user_decisions':saved_project.get('user_decisions',[]),
            'resolved_count':len(assigned),'unresolved_count':len(active_ids)-len(assigned),
            'notes':['User explicitly confirms P002 draw-form as a single action at OP10 only; raw multi-stage colors are retained.',
                     'O106/O107 are integrated into O105 by the latest user decision, excluded from separate allocation; their raw OP40 evidence is preserved.',
                     'O112 kept as one composite action at OP40 by user confirmation; O117 user-identified OP20 entity has matching position/size but differing corner radii.',
                     'Unique geometric matches are evidence proposals, not a claim of full expert-plan validation.']}
    (OUT/'assignments_draft.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    # A viewer copy uses the flat drawing frame. Raw evidence retains original
    # expert coordinates, source paths, transforms and DXF colors.
    display=[]
    for f in frames:
        if not f['alignment_supported']:continue
        m=np.array(f['matrix']);t=np.array(f['translation'])
        display.append({k:v for k,v in f.items() if k not in ('matches','targets')}|
                       {'targets':[transformed(g,m.T,-m.T@t) for g in f['targets']]})
    payload={'summary':result,'frames':display,'flat_primitives':data['flat_primitives']}
    template=(MODULE/'web/p002_expert_review.html').read_text(encoding='utf-8')
    (OUT/'review.html').write_text(template.replace('__DATA__',json.dumps(payload,ensure_ascii=False).replace('<','\\u003c')),encoding='utf-8')
    print(result['counts']);print(result['station_counts']);print('CAD parent order diagnostics',conflicts)


if __name__=='__main__':main()
