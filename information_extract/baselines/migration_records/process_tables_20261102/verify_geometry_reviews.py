from pathlib import Path
import json,hashlib,math,sys,collections
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from verify_bound_geometry_workbooks import verify_geometry
import openpyxl

out=ROOT/'outputs/geometry_review_20261102'
payload=json.loads((out/'review_payload.json').read_text(encoding='utf-8'));results=[]
for p in payload['parts']:
    file=out/f'钣金加工数据表_{p["part"]}.xlsx';book=openpyxl.load_workbook(file,data_only=False)
    assert book.sheetnames==list(p['tables'])
    count=0
    for sn,prior in p['tables'].items():
        s=book[sn];assert s.freeze_panes==prior['freeze'] and s.sheet_view.showGridLines==prior['grid']
        if sn=='Sheet2':continue
        for addr,old in prior['cells'].items():
            c=s[addr];expected=p['changes'].get(addr,{}).get('after',old['value']) if sn=='Sheet1' else old['value']
            assert c.value==expected,(p['part'],sn,addr,c.value,expected)
            assert (c.font.name,c.font.sz,c.font.b,c.number_format,c.fill.patternType)==(old['font'],old['size'],old['bold'],old['number_format'],old['fill'])
            count+=1
        assert {c.coordinate for row in s for c in row if c.value is not None}==set(prior['cells'])
    header=p['sheet2_headers'];s=book['Sheet2'];assert [c.value for c in s[1]]==header
    assert s.max_row==len(p['sheet2_rows'])+1
    for i,row in enumerate(p['sheet2_rows'],2):
        for j,value in enumerate(row,1):
            actual=s.cell(i,j).value
            if isinstance(value,(float,int)) and not isinstance(value,bool):assert math.isclose(actual,value,rel_tol=1e-12,abs_tol=1e-10)
            else:assert actual==value,(p['part'],i,j)
            count+=1
    # Check every unaffected geometry, including O112's shared parent boundary.
    old_groups=collections.defaultdict(list);new_groups=collections.defaultdict(list)
    old_rows=collections.defaultdict(dict)
    from openpyxl.utils.cell import coordinate_from_string,column_index_from_string
    for addr,cell in p['tables']['Sheet2']['cells'].items():
        col,row=coordinate_from_string(addr)
        if row>1:old_rows[row][column_index_from_string(col)-1]=cell['value']
    for _,values in sorted(old_rows.items()):
        row=[values.get(i) for i in range(len(header))];old_groups[row[1]].append(row)
    for row in p['sheet2_rows']:new_groups[row[1]].append(row)
    for gid,rows in old_groups.items():
        if p['part']=='P002' and gid=='G069':continue
        old=rows;new=new_groups[gid]
        assert len(old)==len(new)
        for a,b in zip(old,new):
            for i,(x,y) in enumerate(zip(a,b)):
                if i==header.index('geometry_status') and ((p['part']=='P002' and gid=='G120') or (p['part']=='P003' and gid=='G112')):continue
                assert x==y,(p['part'],gid,i)
    for clip in p['clips']:
        parent,child=clip['parent'],clip['child'];d=child['derivation']
        assert 0<=d['start_fraction']<d['end_fraction']<=1
        for endpoint,t in [('start',d['start_fraction']),('end',d['end_fraction'])]:
            for axis in ('x','y'):
                expected=parent['start_'+axis]+t*(parent['end_'+axis]-parent['start_'+axis])
                assert abs(child[endpoint+'_'+axis]-expected)<1e-10
        assert math.isclose(clip['length'],.4,abs_tol=1e-9)
    source=json.loads((out/f'{p["part"]}_geometry_sources.json').read_text(encoding='utf-8'))
    for record in source['records']:verify_geometry(record,1e-6)
    assert len(new_groups)==p['operation_count']
    assert all([r[2] for r in rows]==list(range(1,len(rows)+1)) for rows in new_groups.values())
    results.append({'part':p['part'],'passed':True,'verified_cells':count,'sheet2_rows':s.max_row-1,
                    'unchanged_geometry_preserved':True,'shared_parent_boundary_preserved':True,
                    'clip_lengths':[c['length'] for c in p['clips']],
                    'sha256':hashlib.sha256(file.read_bytes()).hexdigest()});book.close()
assert all(hashlib.sha256(Path(f).read_bytes()).hexdigest()==h for f,h in payload['protected_files'].items())
(out/'verification_report.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(results,ensure_ascii=False))
