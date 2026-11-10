"""Independent read-only checks of saved XLSX cells and source immutability."""
from pathlib import Path
import hashlib
import json
import math
import sys
import openpyxl
from PIL import Image, ImageOps, ImageDraw

out = Path(sys.argv[1]).resolve()
payload = json.loads((out / 'workbook_payload.json').read_text(encoding='utf-8'))
manifest = json.loads((out / 'source_manifest.json').read_text(encoding='utf-8'))
checks = []


def equal(actual, expected):
    if expected == '':
        expected = None
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-10)
    return actual == expected


def matrix(sheet, expected, start=1):
    count = 0
    for r, row in enumerate(expected, start):
        for c, value in enumerate(row, 1):
            actual = sheet.cell(r, c).value
            assert equal(actual, value), (sheet.title, r, c, actual, value)
            count += 1
    return count


for p in payload:
    file = out / f'{p["part_id"]}_工艺信息表_待补全.xlsx'
    book = openpyxl.load_workbook(file, data_only=False)
    cached = openpyxl.load_workbook(file, data_only=True)
    assert book.sheetnames == ['填表说明', 'Sheet1', 'Sheet2', 'Sheet3', 'Sheet4', 'Sheet5', '动作补充', '几何来源', '待确认']
    cells = 0
    for sheet, header in p['headers'].items():
        cells += matrix(book[sheet], [header, *p['records'][sheet]])
        assert book[sheet].freeze_panes == 'A2'
        assert book[sheet].sheet_view.showGridLines is False
        assert book[sheet].tables
    for name, key in [('动作补充', 'extra'), ('几何来源', 'raw'), ('待确认', 'issues')]:
        cells += matrix(book[name], p[key], 2)
    n = len(p['extra'])
    assert cached['填表说明']['B8'].value == n
    assert cached['填表说明']['B9'].value == len(p['records']['Sheet5'])
    assert cached['填表说明']['B11'].value == 0
    assert cached['填表说明']['C11'].value == '待填写'
    assert cached['填表说明']['B14'].value == '待补全'
    assert len(p['records']['Sheet2']) == 0  # no current confirmed geometry batch
    for name in ('Sheet2', 'Sheet3', 'Sheet4'):
        assert not any(c.value is not None for row in book[name].iter_rows(min_row=2) for c in row)
    assert all(r[0] == p['part_id'] for r in p['extra'] + p['raw'] + p['issues'])
    assert all(r[6] == 90 for r in p['extra'] if r[3].startswith('BEND_'))
    for sheet in book:
        for row in sheet:
            for cell in row:
                assert cell.data_type != 'e', (sheet.title, cell.coordinate, cell.value)
                if cell.data_type == 'f':
                    assert sheet.title == '填表说明'
    assert book['Sheet3']['A2'].value is None
    assert len(book['Sheet3'].data_validations.dataValidation) > 0
    assert len(book['Sheet5'].data_validations.dataValidation) == 2
    assert 'expert_station' not in p['headers']['Sheet1']
    checks.append({'part_id': p['part_id'], 'verified_cells': cells, 'operation_rows': n,
                   'angle_rows': len(p['records']['Sheet5']), 'source_bindings': len(p['raw']),
                   'source_values_preserved': True, 'unknowns_not_fabricated': True,
                   'part_isolation': True, 'formula_cached_values': True,
                   'filter_freeze_validation_preserved': True, 'file_sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
    book.close()
    cached.close()
    names = ['填表说明', 'Sheet1', 'Sheet2', 'Sheet3', 'Sheet4', 'Sheet5', '动作补充', '几何来源', '待确认']
    tiles = []
    for name in names:
        img = Image.open(out / f'{p["part_id"]}_{name}_preview.png').convert('RGB')
        img.thumbnail((740, 600))
        tile = Image.new('RGB', (760, 640), 'white')
        ImageDraw.Draw(tile).text((10, 8), f'{p["part_id"]} {names.index(name)+1}', fill='black')
        tile.paste(img, (10, 30))
        tiles.append(tile)
    grid = Image.new('RGB', (2280, 1920), '#D5DFEA')
    for i, img in enumerate(tiles):
        grid.paste(img, ((i % 3)*760, (i//3)*640))
    grid.save(out / f'{p["part_id"]}_visual_check.png')

changed = [name for name, digest in manifest['protected_files'].items()
           if not Path(name).is_file() or hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest]
assert not changed, changed
result = {'passed': True, 'protected_file_count': len(manifest['protected_files']),
          'protected_files_unchanged': True, 'workbooks': checks,
          'scope': 'Snapshot table preparation; no formal export or H1-H7 solve is claimed.'}
(out / 'verification_report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
