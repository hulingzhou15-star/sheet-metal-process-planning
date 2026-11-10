"""Prepare explicitly requested geometry drafts without changing annotations.

Sheet2 keeps its twelve original columns, followed by path/provenance/status
columns. Derived straight-bend spans remain provisional; curved bends and
unresolved contours retain exact source curves instead of fabricated geometry.
"""
from collections import Counter
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT)]
import openpyxl  # read only; the authoring step uses Artifact Tool
from extract_app.drawing import load_drawing
from extract_app.geometry import FIELDS, _bend, _primitive
from extract_app.workflow import FEATURE_BY_KEY, _paths, _circle


HEADERS = ['part_id','geometry_id','seq','segment_type',*FIELDS,
           'operation_id','path_id','geometry_role','geometry_status',
           'source_segment_ids','path_closed']


def digest(data):
    return hashlib.sha256(data).hexdigest()


def raw_paths(selected, tolerance, role):
    return [{'path_id':None, 'closed':None, 'role':role,
             'segments':[_primitive(s, tolerance) for s in selected],
             'source_segment_ids':[s['id'] for s in selected]}]


def derive(op, selected, config):
    feature = FEATURE_BY_KEY[op['feature_type']]
    kind, shape, tol = feature['operation_type'], feature['shape'], config['geometry_tolerance']
    if kind == 'BEND' and shape == 'CURVED':
        return raw_paths(selected,tol,'BEND_SOURCE_CURVES'), 'RULE_PENDING', '保留所选真实曲线；曲线折弯参考线及三维运动模型尚未确定。'
    if kind == 'BEND':
        try:
            lines = _bend(selected,op.get('geometry_options',{}),config,op.get('bend_source_is_centerline',False),preview=True)
            confirmed = op.get('geometry_options',{}).get('confirmed_span') is True
            if op.get('bend_source_is_centerline') and len(selected)==1:
                confirmed = True
            return [{'path_id':'CENTERLINE','closed':False,'role':'BEND_REFERENCE',
                     'segments':lines,'source_segment_ids':[s['id'] for s in selected]}], \
                   'EXTRACTED' if confirmed else 'PROVISIONAL_CENTERLINE', \
                   '' if confirmed else '按所选平行边界计算中线；整体跨度尚未人工确认，不表示三维运动模型已确定。'
        except (ValueError,KeyError,TypeError) as error:
            return raw_paths(selected,tol,'BEND_BOUND_SOURCE'), 'NEEDS_REVIEW', str(error)
    if kind in {'CIRCLE_PUNCH','IRREGULAR_PUNCH','PRE_PUNCH','FLANGING','DRAW_FORM'}:
        try:
            paths = _paths(selected,tol)
            if shape == 'CIRCULAR':
                circles = [_circle(p,tol) for p in paths]
                if kind != 'FLANGING' and len(circles)!=1:
                    raise ValueError('圆形冲孔/预孔绑定了多个独立圆，需核对。')
                if kind == 'FLANGING' and any(abs(c['center_x']-circles[0]['center_x'])>tol or abs(c['center_y']-circles[0]['center_y'])>tol for c in circles):
                    raise ValueError('圆形翻孔所选多圈不同心，需核对。')
                for p,circle in zip(paths,circles):
                    p['segments']=[circle]
            role = 'UNSPECIFIED_FLANGE_CONTOUR' if kind=='FLANGING' else 'UNSPECIFIED_FORM_CONTOUR' if kind=='DRAW_FORM' else 'BOUND_CLOSED_CONTOUR'
            for p in paths:
                p['role']=role
            if shape=='NONCIRCULAR' and any(s['segment_type']=='CIRCLE' for p in paths for s in p['segments']):
                return paths, 'NEEDS_TYPE_REVIEW', '细分类型标为异形，但所选几何包含完整圆；保留实际几何和原类型，需核对绑定或分类。'
            return paths,'EXTRACTED',''
        except (ValueError,KeyError,TypeError) as error:
            return raw_paths(selected,tol,'UNRESOLVED_BOUND_SOURCE'),'NEEDS_REVIEW',str(error)
    return raw_paths(selected,tol,'BOUND_SOURCE'),'EXTRACTED',''


def prepare(part, out):
    desktop = ROOT.parent/'data/parts'/part/f'钣金加工数据表_{part}.xlsx'
    current = ROOT/'outputs/process_tables_20261102'/desktop.name
    excel_bytes=desktop.read_bytes()
    project_path=ROOT/'projects'/part/'project.json'
    project_bytes=project_path.read_bytes()
    project=json.loads(project_bytes)
    config=json.loads((ROOT/'configs'/f'{part}.json').read_text(encoding='utf-8'))
    drawing=load_drawing(ROOT/config['source_file'],config)
    if drawing['source']['sha256']!=project['source_sha256']:
        raise ValueError(f'{part}: DXF 与已保存标注不匹配。')
    book=openpyxl.load_workbook(desktop,data_only=False)
    saved={}
    for sh in book:
        saved[sh.title]={'cells':{c.coordinate:{'value':c.value,'type':c.data_type,
                                             'font':c.font.name,'size':c.font.sz,'bold':c.font.b,
                                             'fill':c.fill.patternType,'number_format':c.number_format}
                                 for row in sh for c in row if c.value is not None},
                        'widths':{k:v.width for k,v in sh.column_dimensions.items()},
                        'freeze':sh.freeze_panes,'grid':sh.sheet_view.showGridLines,
                        'max_row':sh.max_row,'max_column':sh.max_column}
    if any(c.value is not None for row in book['Sheet2'].iter_rows(min_row=2) for c in row):
        raise ValueError(f'{part}: Sheet2 已有内容，未覆盖。')
    header=[c.value for c in book['Sheet1'][1]]
    operations=[(i,dict(zip(header,[c.value for c in row]))) for i,row in enumerate(book['Sheet1'].iter_rows(min_row=2),2) if any(c.value is not None for c in row)]
    identifiers=[op['operation_id'] for _,op in operations]
    if len(identifiers)!=len(set(identifiers)):
        raise ValueError(f'{part}: Excel 动作编号重复。')
    by_id={o['operation_id']:o for o in project['operations']}
    if set(identifiers)!=set(by_id):
        raise ValueError(f'{part}: Excel 与标注的动作编号集合不同，需先明确来源绑定。')
    src={s['id']:s for s in drawing['segments']}
    rows,updates,geometry_records=[],[],[]
    used=set()
    for excel_row,record in operations:
        oid=record['operation_id'];op=deepcopy(by_id[oid])
        if record.get('geometry_id'):
            raise ValueError(f'{part}/{oid}: 已有 geometry_id，未重绑。')
        feature_type=record.get('feature_type')
        if feature_type not in FEATURE_BY_KEY:
            raise ValueError(f'{part}/{oid}: 未知细分类型。')
        feature=FEATURE_BY_KEY[feature_type]
        effective='PUNCH' if feature['operation_type'] in {'CIRCLE_PUNCH','IRREGULAR_PUNCH'} else feature['operation_type']
        if record.get('operation_type')!=effective or record.get('geometry_shape')!=feature['shape']:
            raise ValueError(f'{part}/{oid}: 动作大类、细分或几何形状不一致，未猜测几何规则。')
        op.update(feature_type=feature_type,operation_type=feature['operation_type'],geometry_shape=feature['shape'])
        selected=[src[s] for s in op.get('source_segment_ids',[])]
        if not selected:
            raise ValueError(f'{part}/{oid}: 当前没有已绑定图元，未发明坐标。')
        match=re.fullmatch(r'O(\d+)',oid)
        gid='G'+match[1] if match else f'G{len(updates)+1:03d}'
        if gid in used:
            raise ValueError('几何编号发生冲突。')
        used.add(gid)
        paths,status,note=derive(op,selected,config)
        referenced=[];seq=0
        for path in paths:
            path_sources=path['source_segment_ids']
            if len(path['segments'])==len(path_sources):
                row_sources=[[sid] for sid in path_sources]
            elif len(path['segments'])==1:
                row_sources=[path_sources]
            else:
                raise ValueError('几何与来源无法逐项追溯。')
            for segment,ids in zip(path['segments'],row_sources):
                _primitive(segment,config['geometry_tolerance'])
                seq+=1
                values={'part_id':part,'geometry_id':gid,'seq':seq,**segment,
                        'operation_id':oid,'path_id':path['path_id'],'geometry_role':path['role'],
                        'geometry_status':status,'source_segment_ids':','.join(ids),'path_closed':path['closed']}
                values['seq']=seq
                rows.append([values.get(k) for k in HEADERS]);referenced.extend(ids)
        if Counter(referenced)!=Counter(op['source_segment_ids']):
            raise ValueError(f'{part}/{oid}: 来源覆盖不完整或重复，未导出。')
        updates.append({'excel_row':excel_row,'operation_id':oid,'geometry_id':gid,'geometry_status':status,'geometry_note':note or None})
        geometry_records.append({'operation_id':oid,'geometry_id':gid,'feature_type':feature_type,
                                 'status':status,'note':note,'paths':paths,'source_segments':selected})
    book.close()
    if desktop.read_bytes()!=excel_bytes or project_path.read_bytes()!=project_bytes:
        raise ValueError('读取期间输入发生变化，请重新生成。')
    backup=out/'before'
    backup.mkdir(parents=True,exist_ok=True)
    (backup/f'{part}_{digest(excel_bytes)[:12]}.xlsx').write_bytes(excel_bytes)
    result={'part':part,'desktop':str(desktop),'workspace':str(current),
            'desktop_sha256':digest(excel_bytes),'workspace_sha256':digest(current.read_bytes()) if current.exists() else None,
            'project_sha256':digest(project_bytes),'project_revision':project['revision'],
            'source':drawing['source'],'tables':saved,'sheet1_headers':header,
            'sheet2_headers':HEADERS,'sheet2_rows':rows,'updates':updates,
            'status_counts':dict(Counter(u['geometry_status'] for u in updates)),
            'raw_source_count':sum(len(o['source_segment_ids']) for o in project['operations']),
            'station_execution':'SIMULTANEOUS'}
    (out/f'{part}_geometry_sources.json').write_text(json.dumps({'part':part,'source':drawing['source'],
        'project_revision':project['revision'],'records':geometry_records},ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path,required=True);args=parser.parse_args()
    out=args.output_dir.resolve()
    if not out.is_relative_to(ROOT/'outputs'):
        raise ValueError('输出应位于模块 outputs 下。')
    out.mkdir(parents=True,exist_ok=True)
    result=[prepare(part,out) for part in ('P002','P003')]
    (out/'geometry_payload.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps([{k:p[k] for k in ('part','status_counts','raw_source_count')}|{'geometry_count':len(p['updates']),'sheet2_rows':len(p['sheet2_rows'])} for p in result],ensure_ascii=False))


if __name__=='__main__':
    main()
