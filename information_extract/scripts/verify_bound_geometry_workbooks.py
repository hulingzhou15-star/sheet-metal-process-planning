"""Independent saved-cell, source-coverage and geometric identity checks."""
from collections import Counter,defaultdict
import argparse
import hashlib
import json
import math
from pathlib import Path
import openpyxl


def close(a,b,tol):
    if a is None or b is None:
        return a is None and b is None
    return abs(a-b)<=tol


def point(row,which):
    return (row[which+'_x'],row[which+'_y'])


def verify_geometry(record, tolerance):
    sources={s['id']:s for s in record['source_segments']}
    referenced=[]
    numeric=('start_x','start_y','end_x','end_y','center_x','center_y','radius','sweep_angle')
    for path in record['paths']:
        pieces=path['segments'];ids=path['source_segment_ids'];referenced.extend(ids)
        if path['role']=='BEND_REFERENCE':
            assert len(pieces)==1
            line=pieces[0];a,b=point(line,'start'),point(line,'end')
            length=math.dist(a,b);assert length>tolerance
            u=((b[0]-a[0])/length,(b[1]-a[1])/length);normal=(-u[1],u[0])
            dot=lambda p,v:p[0]*v[0]+p[1]*v[1]
            endpoints=[point(sources[sid],end) for sid in ids for end in ('start','end')]
            along=[dot(p,u) for p in endpoints];across=[dot(p,normal) for p in endpoints]
            assert close(dot(a,u),min(along),tolerance) and close(dot(b,u),max(along),tolerance)
            assert close(dot(a,normal),(min(across)+max(across))/2,tolerance)
            assert close(dot(b,normal),dot(a,normal),tolerance)
        elif len(pieces)==1 and len(ids)>1 and pieces[0]['segment_type']=='CIRCLE':
            circle=pieces[0]
            assert all(sources[sid]['segment_type']=='ARC' for sid in ids)
            for sid in ids:
                assert all(close(circle[k],sources[sid][k],tolerance) for k in ('center_x','center_y','radius'))
            assert close(sum(abs(sources[sid]['sweep_angle']) for sid in ids),360,tolerance)
        else:
            assert len(pieces)==len(ids)
            for row,sid in zip(pieces,ids):
                source=sources[sid];kind=row['segment_type']
                assert kind==source['segment_type']
                if kind=='CIRCLE':
                    assert all(close(row[k],source[k],tolerance) for k in ('center_x','center_y','radius'))
                    continue
                forward=all(close(row.get(k),source.get(k),tolerance) for k in numeric)
                reversed_source={**source,'start_x':source.get('end_x'),'start_y':source.get('end_y'),
                                 'end_x':source.get('start_x'),'end_y':source.get('start_y')}
                if kind=='ARC':reversed_source['sweep_angle']=-source['sweep_angle']
                backward=all(close(row.get(k),reversed_source.get(k),tolerance) for k in numeric)
                assert forward or backward,(record['operation_id'],sid,'source identity')
                if kind=='ARC':
                    cx,cy=point(row,'center');radius=row['radius']
                    theta=math.atan2(row['start_y']-cy,row['start_x']-cx)+math.radians(row['sweep_angle'])
                    assert math.dist((cx+radius*math.cos(theta),cy+radius*math.sin(theta)),point(row,'end'))<=tolerance
        if path['closed'] is True and not (len(pieces)==1 and pieces[0]['segment_type']=='CIRCLE'):
            assert all(math.dist(point(row,'end'),point(pieces[(i+1)%len(pieces)],'start'))<=tolerance for i,row in enumerate(pieces))
    assert Counter(referenced)==Counter(sources.keys())


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',required=True,type=Path);args=parser.parse_args()
    out=args.output_dir.resolve();root=Path(__file__).resolve().parents[1]
    payload=json.loads((out/'geometry_payload.json').read_text(encoding='utf-8'))
    result=[]
    for p in payload:
        file=out/f'钣金加工数据表_{p["part"]}.xlsx'
        book=openpyxl.load_workbook(file,data_only=False)
        changes={f'J{u["excel_row"]}':u['geometry_id'] for u in p['updates']}
        cells=0
        assert book.sheetnames==list(p['tables'])
        for sn,prior in p['tables'].items():
            sheet=book[sn]
            assert sheet.sheet_view.showGridLines==(True if sn=='Sheet2' else prior['grid'])
            assert sheet.freeze_panes==('A2' if sn=='Sheet2' else prior['freeze'])
            for addr,old in prior['cells'].items():
                cell=sheet[addr];expected=changes.get(addr,old['value']) if sn=='Sheet1' else old['value']
                assert cell.value==expected,(p['part'],sn,addr,cell.value,expected)
                if sn!='Sheet2':
                    assert (cell.font.name,cell.font.sz,cell.font.b)==(old['font'],old['size'],old['bold'])
                    assert (cell.number_format,cell.fill.patternType)==(old['number_format'],old['fill'])
                cells+=1
            if sn not in ('Sheet1','Sheet2'):
                assert {c.coordinate for row in sheet for c in row if c.value is not None}==set(prior['cells'])
        s2=book['Sheet2'];header=p['sheet2_headers']
        assert [c.value for c in s2[1]]==header
        assert s2.max_row==len(p['sheet2_rows'])+1
        for r,row in enumerate(p['sheet2_rows'],2):
            for c,value in enumerate(row,1):
                actual=s2.cell(r,c).value
                if isinstance(value,(int,float)) and not isinstance(value,bool):
                    assert isinstance(actual,(int,float)) and math.isclose(actual,value,rel_tol=1e-12,abs_tol=1e-10)
                else:assert actual==value,(p['part'],r,c,actual,value)
                cells+=1
        grouped=defaultdict(list)
        for row in p['sheet2_rows']:
            record=dict(zip(header,row));grouped[record['geometry_id']].append(record)
        assert set(grouped)=={u['geometry_id'] for u in p['updates']}
        for u in p['updates']:
            rows=grouped[u['geometry_id']]
            assert [r['seq'] for r in rows]==list(range(1,len(rows)+1))
            assert all(r['operation_id']==u['operation_id'] and r['geometry_status']==u['geometry_status'] for r in rows)
            assert book['Sheet1'].cell(u['excel_row'],16).value==u['geometry_status']
            assert book['Sheet1'].cell(u['excel_row'],17).value==u['geometry_note']
        source_data=json.loads((out/f'{p["part"]}_geometry_sources.json').read_text(encoding='utf-8'))
        for record in source_data['records']:verify_geometry(record,1e-6)
        assert sum(len(record['source_segments']) for record in source_data['records'])==p['raw_source_count']
        project=root/'projects'/p['part']/'project.json'
        assert hashlib.sha256(project.read_bytes()).hexdigest()==p['project_sha256']
        dxf=root/p['source']['file_name']
        assert hashlib.sha256(dxf.read_bytes()).hexdigest()==p['source']['sha256']
        result.append({'part':p['part'],'passed':True,'verified_cells':cells,'geometry_count':len(grouped),
                       'sheet2_rows':len(p['sheet2_rows']),'source_binding_count':p['raw_source_count'],
                       'status_counts':p['status_counts'],'all_original_facts_preserved':True,
                       'source_geometry_equivalence':True,'station_execution':'SIMULTANEOUS',
                       'workbook_sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
        book.close()
    (out/'verification_report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
