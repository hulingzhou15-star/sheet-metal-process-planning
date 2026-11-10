from pathlib import Path
from collections import Counter
import hashlib,json,math,sys
from copy import deepcopy
ROOT=Path(__file__).resolve().parents[2]
sys.dont_write_bytecode=True
sys.path[:0]=[str(ROOT),str(ROOT/'.runtime/deps')]
import openpyxl
from extract_app.drawing import load_drawing
from extract_app.workflow import _paths
from extract_app.geometry import FIELDS
from extract_app.source_subsegments import confirmed_line_subsegment

out=ROOT/'outputs/geometry_review_20261102';out.mkdir(parents=True,exist_ok=True)
reviews=json.loads((ROOT/'configs/confirmed_geometry_reviews_20261102.json').read_text(encoding='utf-8'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
protected=set(ROOT.glob('projects/**/*.json'))|set(ROOT.glob('*.dxf'))|set((ROOT.parent/'src').rglob('*.py'))|set((ROOT.parent/'data/raw').glob('*.xlsx'))|{ROOT.parent/'PROJECT_SPEC.md'}
protected_hashes={str(p):sha(p) for p in protected}
payload=[]
for part in ('P002','P003'):
    desktop=Path('C:/Users/hulin/Desktop/图纸文件夹')/part/f'钣金加工数据表_{part}.xlsx'
    workspace=ROOT/'outputs/process_tables_20261102'/desktop.name
    content=desktop.read_bytes()
    project_path=ROOT/'projects'/part/'project.json';project=json.loads(project_path.read_text(encoding='utf-8'))
    config=json.loads((ROOT/'configs'/f'{part}.json').read_text(encoding='utf-8'))
    drawing=load_drawing(ROOT/config['source_file'],config)
    assert drawing['source']['sha256']==reviews[part]['source_sha256']==project['source_sha256']
    sources={s['id']:s for s in drawing['segments']};ops={o['operation_id']:o for o in project['operations']}
    book=openpyxl.load_workbook(desktop,data_only=False)
    before={s.title:{'cells':{c.coordinate:{'value':c.value,'font':c.font.name,'size':c.font.sz,'bold':c.font.b,'number_format':c.number_format,'fill':c.fill.patternType} for row in s for c in row if c.value is not None},'freeze':s.freeze_panes,'grid':s.sheet_view.showGridLines} for s in book}
    headers=[c.value for c in book['Sheet2'][1]]
    all_rows=[[c.value for c in row] for row in book['Sheet2'].iter_rows(min_row=2) if any(c.value is not None for c in row)]
    dicts=[dict(zip(headers,row)) for row in all_rows]
    h1={c.value:c.column for c in book['Sheet1'][1]}
    locations={book['Sheet1'].cell(row,h1['operation_id']).value:row for row in range(2,book['Sheet1'].max_row+1)}
    changes={};source_data=json.loads((ROOT/'outputs/geometry_fill_20261102'/f'{part}_geometry_sources.json').read_text(encoding='utf-8'))
    records={r['operation_id']:r for r in source_data['records']}
    clips=[]
    def update(oid,key,value):
        cell=book['Sheet1'].cell(locations[oid],h1[key])
        changes[cell.coordinate]={'before':cell.value,'after':value}
    if part=='P002':
        r=reviews[part]['O069'];op=ops['O069'];assert op['uid']==r['uid']
        selected=[sources[s] for s in op['source_segment_ids']]
        for spec in r['clips']:
            parent=sources[spec['parent_source_segment_id']]
            assert parent['id'] in ops[r['parent_owner_operation_id']]['source_segment_ids']
            child=confirmed_line_subsegment(parent,spec['start_fraction'],spec['end_fraction'],config['geometry_tolerance'])
            clips.append({'parent':parent,'child':child,'parent_owner_operation_id':r['parent_owner_operation_id'],
                          'length':math.dist((child['start_x'],child['start_y']),(child['end_x'],child['end_y']))})
            selected.append(child)
        paths=_paths(selected,config['geometry_tolerance'])
        assert len(paths)==1 and paths[0]['closed'] and len(paths[0]['segments'])==16
        gid=book['Sheet1'].cell(locations['O069'],h1['geometry_id']).value
        status='EXTRACTED_WITH_SUBSEGMENTS'
        note='用户确认工具未局部切分；已从 O112 的原始边界精确截取两段各 0.4 mm，补齐此轮廓。原边界与编辑器既有绑定保持原样，局部截取来源另有记录。'
        normalized=[];seq=0
        for p in paths:
            p['role']='BOUND_CLOSED_CONTOUR'
            for segment,sid in zip(p['segments'],p['source_segment_ids']):
                seq+=1
                normalized.append({'part_id':part,'geometry_id':gid,'seq':seq,**{k:segment.get(k) for k in ('segment_type',*FIELDS)},
                                   'operation_id':'O069','path_id':p['path_id'],'geometry_role':p['role'],
                                   'geometry_status':status,'source_segment_ids':sid,'path_closed':True})
        result=[];inserted=False
        for row in dicts:
            if row['geometry_id']==gid:
                if not inserted:result+=normalized;inserted=True
            else:result.append(row)
        assert inserted;dicts=result
        update('O069','geometry_status',status);update('O069','geometry_note',note)
        records['O069'].update(status=status,note=note,paths=paths,source_segments=selected,source_subsegments=clips)
        correction=reviews[part]['O120'];op=ops['O120'];assert op['uid']==correction['uid']
        assert op['feature_type']==correction['expected_feature_type'] and op['geometry_shape']=='CIRCULAR'
        update('O120','feature_type',op['feature_type']);update('O120','geometry_shape',op['geometry_shape'])
        update('O120','geometry_status','EXTRACTED')
        note='已同步用户在录入页面保存的修正：圆形冲孔，二维几何为圆。'
        update('O120','geometry_note',note)
        for row in dicts:
            if row['operation_id']=='O120':row['geometry_status']='EXTRACTED'
        records['O120'].update(feature_type=op['feature_type'],status='EXTRACTED',note=note)
    else:
        r=reviews[part]['O112'];op=ops['O112'];assert op['uid']==r['uid']
        assert r['source_segment_id'] in op['source_segment_ids'] and sources[r['source_segment_id']]['segment_type']=='CIRCLE'
        status='CONFIRMED_VIEW_DIFFERENCE';update('O112','geometry_status',status);update('O112','geometry_note',r['note'])
        for row in dicts:
            if row['operation_id']=='O112':row['geometry_status']=status
        records['O112'].update(status=status,note=r['note'],confirmed_view_difference={'formed_shape':r['formed_shape'],'flat_drawing_shape':r['flat_drawing_shape'],'source':'USER_20261102'})
    source_data['project_revision']=project['revision'];source_data['reviewed_on']='2026-11-02'
    (out/f'{part}_geometry_sources.json').write_text(json.dumps(source_data,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'before').mkdir(exist_ok=True);(out/'before'/f'{part}_{hashlib.sha256(content).hexdigest()[:12]}.xlsx').write_bytes(content)
    payload.append({'part':part,'desktop':str(desktop),'workspace':str(workspace),'desktop_sha256':hashlib.sha256(content).hexdigest(),
                    'workspace_sha256':sha(workspace),'project_sha256':sha(project_path),'tables':before,
                    'changes':changes,'sheet2_headers':headers,'sheet2_rows':[[r.get(k) for k in headers] for r in dicts],
                    'clips':clips,'operation_count':book['Sheet1'].max_row-1})
    assert content==desktop.read_bytes(),'读取期间工作表发生变化。'
    book.close()
assert all(sha(Path(p))==h for p,h in protected_hashes.items())
(out/'review_payload.json').write_text(json.dumps({'parts':payload,'protected_files':protected_hashes},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps([{'part':p['part'],'cells_to_update':len(p['changes']),'geometry_rows':len(p['sheet2_rows']),'clip_lengths':[c['length'] for c in p['clips']]} for p in payload],ensure_ascii=False))
