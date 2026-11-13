"""Read-only reconciliation of P002-P004 annotation, drawings and workbooks."""
from collections import Counter
from copy import deepcopy
import hashlib
import html
import json
from pathlib import Path

from openpyxl import load_workbook
from information_extract.extract_app.drawing import load_drawing
from information_extract.extract_app.geometry import segment_bbox
from information_extract.extract_app.pairing import synchronize_pairing

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT/'information_extract'
OUT = MODULE/'outputs/multi_part_readiness_v01'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sheet_rows(sheet):
    rows=iter(sheet.values)
    header=next(rows)
    return [dict(zip(header,row)) for row in rows if any(v is not None for v in row)]


def audit(part, decisions):
    project_path=MODULE/f'projects/{part}/project.json'
    config_path=MODULE/f'configs/{part}.json'
    project=read(project_path);config=read(config_path)
    source=MODULE/config['source_file'];drawing=load_drawing(source,config)
    model_policy_path=MODULE/'configs/model_geometry_decisions_20261111.json'
    model_policies=read(model_policy_path).get(part,{})
    assert drawing['source']['sha256']==project['source_sha256'],part
    index={s['id']:s for s in drawing['segments']}
    pairing=synchronize_pairing(deepcopy(project))
    workbook_files=list((ROOT/f'data/parts/{part}').glob('*.xlsx'))
    if len(workbook_files)>1:raise ValueError(f'{part}: ambiguous workbook')
    sheets={};workbook_path=None
    if workbook_files:
        workbook_path=workbook_files[0]
        book=load_workbook(workbook_path,read_only=True,data_only=True)
        sheets={s.title:sheet_rows(s) for s in book}
        book.close()
    facts={r['operation_id']:r for r in sheets.get('Sheet1',[])}
    geometries={}
    for r in sheets.get('Sheet2',[]):geometries.setdefault(r.get('operation_id'),[]).append(r)
    operations=[]
    for op in project['operations']:
        oid=op['operation_id'];fact=facts.get(oid,{})
        status=pairing['statuses'].get(op['uid'])
        missing=[sid for sid in op.get('source_segment_ids',[]) if sid not in index]
        notes=[]
        if op['operation_type']=='BEND' and not op.get('region'):notes.append('折弯影响区未填写')
        if op.get('feature_type')=='BEND_CURVED':notes.append('曲线折弯参考路径及运动/影响区规则待定')
        if op['operation_type']=='DRAW_FORM':notes.append('拉包轮廓工艺角色及下游适配待确认')
        if status and status['status'] not in ('PAIRED','EXPLICIT_PAIR'):notes.append(status['message'])
        if missing:notes.append('绑定来源在当前图纸中缺失')
        if not op.get('source_segment_ids') and not op.get('no_geometry'):notes.append('未绑定二维来源')
        decision=decisions.get(part,{}).get(oid)
        model_decision=model_policies.get(oid)
        if model_decision:
            assert model_decision['source_sha256']==drawing['source']['sha256']
            assert model_decision['source_segment_id'] in op['source_segment_ids']
            notes.append(model_decision['note'])
        if decision:
            if decision['decision']=='KEEP_USER_CONFIRMED_VIEW_DIFFERENCE':notes.append(decision['note'])
            elif decision['decision']=='EXTRACT_EXISTING_SOURCE_SUBSEGMENTS':notes.append('工作表已修复局部子段；编辑器仍保留原绑定，不能覆盖回旧状态')
        if fact.get('geometry_status')=='PROVISIONAL_CENTERLINE':notes.append('工作表中线跨度尚待确认')
        if not workbook_path:notes.append('仓库尚无此零件业务工作簿')
        operations.append({'operation_id':oid,'name':op.get('name'),'feature_type':op.get('feature_type'),
            'annotation_operation_type':op['operation_type'],'workbook_operation_type':fact.get('operation_type'),
            'source_segment_ids':op.get('source_segment_ids',[]),'missing_source_ids':missing,
            'pairing':status,'region':op.get('region'),'workbook_geometry_status':fact.get('geometry_status'),
            'workbook_geometry_note':fact.get('geometry_note'),'workbook_geometry_rows':len(geometries.get(oid,[])),
            'confirmed_decision':decision,'model_geometry_decision':model_decision,'notes':notes,'three_d_mapping_status':'NOT_VERIFIED_IN_THIS_AUDIT'})
    files={project_path,config_path,source,MODULE/'configs/confirmed_geometry_reviews_20261102.json'}
    files.add(model_policy_path)
    files.update(p for p in (MODULE/f'sources/{part}').rglob('*') if p.is_file())
    files.update(workbook_files)
    for key in ('confirmed_source_cleanup','confirmed_geometry_supplement','confirmed_geometry_correction','confirmed_geometry_revision'):
        if config.get(key):files.add(MODULE/config[key])
    hashes={p.relative_to(ROOT).as_posix():sha(p) for p in sorted(files)}
    paired=[o for o in operations if o['pairing'] and o['pairing']['status'] in ('PAIRED','EXPLICIT_PAIR')]
    pending=[o['operation_id'] for o in operations if o['pairing'] and o not in paired]
    bends=[o for o in project['operations'] if o['operation_type']=='BEND']
    step_files=[p.relative_to(ROOT).as_posix() for p in (MODULE/f'sources/{part}').rglob('*') if p.suffix.lower() in ('.step','.stp')]
    return {'part_id':part,'revision':project['revision'],'inventory_confirmed':project.get('inventory_confirmed',False),
        'operation_count':len(operations),'type_counts':dict(Counter(o['feature_type'] for o in operations)),
        'source_hash_matches':True,'drawing_source_count':len(index),'drawing_issues':drawing['issues'],
        'missing_binding_count':sum(len(o['missing_source_ids']) for o in operations),
        'bend_count':len(bends),'filled_annotation_regions':sum(bool(o.get('region')) for o in bends),
        'paired_groups_from_explicit_records':len({o['pairing']['group_id'] for o in paired}),
        'pending_pair_operations':pending,'pairing_issues':pairing['issues'],
        'step_files':step_files,'three_d_mapping_status':'NOT_VERIFIED_IN_THIS_AUDIT',
        'workbook':workbook_path.relative_to(ROOT).as_posix() if workbook_path else None,
        'workbook_station_count':len(sheets.get('Sheet3',[])),
        'workbook_geometry_status_counts':dict(Counter(r.get('geometry_status') for r in facts.values())),
        'workbook_region_id_only_rows':sum(bool(r.get('bend_operation_id')) and all(v is None for k,v in r.items() if k!='bend_operation_id') for r in sheets.get('Sheet5',[])),
        'operations':operations,'source_sha256':hashes,
        'drawing':{'segments':[dict(s,bbox=segment_bbox(s)) for s in drawing['segments']],'bounds':drawing['bounds']}}


def main():
    decisions=read(MODULE/'configs/confirmed_geometry_reviews_20261102.json')
    parts=[audit(part,decisions) for part in ('P002','P003','P004')]
    OUT.mkdir(parents=True,exist_ok=True)
    payload={'date':'2026-11-11','scope':'Read-only saved facts; no inferred CAD matches or new manufacturing rules','parts':parts}
    # Verify the audit has not changed any original input.
    for part in parts:
        for path,digest in part['source_sha256'].items():assert sha(ROOT/path)==digest,path
    (OUT/'audit.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    template=(MODULE/'web/multi_part_readiness.html').read_text(encoding='utf-8')
    (OUT/'review.html').write_text(template.replace('__AUDIT_DATA__',json.dumps(payload,ensure_ascii=False).replace('<','\\u003c')),encoding='utf-8')
    for part in parts:
        print(json.dumps({k:v for k,v in part.items() if k not in ('operations','drawing','source_sha256')},ensure_ascii=False))


if __name__=='__main__':main()
