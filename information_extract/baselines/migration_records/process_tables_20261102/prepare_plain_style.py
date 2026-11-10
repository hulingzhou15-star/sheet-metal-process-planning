"""Read current workbooks before restyling; preserve user edits and metadata."""
from pathlib import Path
import json
import hashlib
import sys
import openpyxl

root = Path(__file__).resolve().parents[2]
out = Path(sys.argv[1]).resolve()
template = root.parent / 'data/raw/钣金加工数据表_P001.xlsx'
ref = openpyxl.load_workbook(template, data_only=False)
style = {}
for sh in ref:
    style[sh.title] = {'headers':[c.value for c in sh[1]],
                       'widths':{k:v.width for k,v in sh.column_dimensions.items()},
                       'font':sh['A1'].font.name,'font_size':sh['A1'].font.sz,
                       'row_height':sh.sheet_format.defaultRowHeight or 18}
ref.close()
original = []
backup = Path(__file__).parent / 'before_plain_style'
backup.mkdir(exist_ok=True)
for part in ('P002','P003'):
    file = out / f'{part}_工艺信息表_待补全.xlsx'
    raw_bytes = file.read_bytes()
    prior = backup / file.name
    if not prior.exists():
        prior.write_bytes(raw_bytes)
    book = openpyxl.load_workbook(file, data_only=False)
    records, formulas = {}, {}
    for name in ('Sheet1','Sheet2','Sheet3','Sheet4','Sheet5'):
        sh = book[name]
        header = [c.value for c in sh[1]]
        data = [[c.value for c in row] for row in sh.iter_rows(min_row=2) if any(c.value is not None for c in row)]
        records[name] = [dict(zip(header,row)) for row in data]
        formulas[name] = []
        for index,row in enumerate([row for row in sh.iter_rows(min_row=2) if any(c.value is not None for c in row)],2):
            formulas[name] += [{'row':index,'field':header[c.column-1],'formula':c.value} for c in row if c.data_type=='f']
    extra_sheet = book['动作补充']
    extra_headers = [c.value for c in extra_sheet[1]]
    extras = {row[1]: dict(zip(extra_headers,row)) for row in extra_sheet.iter_rows(min_row=2, values_only=True) if row[1]}
    added = [('feature_type','细分类型代码'), ('geometry_shape','几何形状'),
             ('bend_angle_deg','折弯角度（°）'), ('protrusion_side','凸起侧'), ('notes','原标注备注')]
    headers = {s:list(v['headers']) for s,v in style.items()}
    headers['Sheet1'] += [field for field,_ in added]
    for row in records['Sheet1']:
        row.setdefault('expert_station', None)
        extra = extras.get(row['operation_id'], {})
        for field,label in added:
            row[field] = extra.get(label)
    companion = {}
    for name in ('填表说明','动作补充','几何来源','待确认'):
        companion[name] = [[c.value for c in row] for row in book[name] if any(c.value is not None for c in row)]
    (out / f'{part}_补充记录.json').write_text(json.dumps(companion,ensure_ascii=False,indent=2),encoding='utf-8')
    book.close()
    original.append({'part':part,'file':str(file),'headers':headers,'records':records,'formulas':formulas,
                     'extras':extras,'original_sha256':hashlib.sha256(raw_bytes).hexdigest()})
(out/'plain_style_payload.json').write_text(json.dumps({'template_sha256':hashlib.sha256(template.read_bytes()).hexdigest(),
                                                     'style':style,'parts':original},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps([{'part':p['part'],'operations':len(p['records']['Sheet1'])} for p in original]))
