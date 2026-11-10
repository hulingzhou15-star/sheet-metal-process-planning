"""Prepare and commit the authorized P001 Geometry-only edit.

openpyxl is used for reads/verification only. Cell values are authored by the
artifact-tool companion builder. The commit copies those verified values into
the original OOXML rows, preserving every other workbook ZIP member verbatim.
"""
from __future__ import annotations
import argparse
import copy
import csv
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import zipfile
import xml.etree.ElementTree as ET
import openpyxl

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/geometry_validation/after_update'
SUPPORT = OUT / '_support'
PLAN = SUPPORT / 'edit_plan.json'
TARGETS = {f'G{i:03}' for i in range(201, 212)}
FIELDS = ['part_id', 'geometry_id', 'seq', 'segment_type', 'start_x', 'start_y',
          'end_x', 'end_y', 'center_x', 'center_y', 'radius', 'sweep_angle']


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def json_write(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def vec(row, prefix):
    return row[prefix+'_x'], row[prefix+'_y']


def diff(a, b):
    return a[0]-b[0], a[1]-b[1]


def length(a):
    return math.hypot(*a)


def signed_arc(row, before, after):
    a = diff(vec(row, 'start'), vec(row, 'center'))
    b = diff(vec(row, 'end'), vec(row, 'center'))
    angle = math.degrees(math.atan2(a[0]*b[1]-a[1]*b[0], a[0]*b[0]+a[1]*b[1])) % 360
    votes = []
    for neighbor, radial in [(before, a), (after, b)]:
        if neighbor['segment_type'] != 'LINE':
            continue
        direction = diff(vec(neighbor, 'end'), vec(neighbor, 'start'))
        votes.append((-radial[1]*direction[0]+radial[0]*direction[1])/(length(radial)*length(direction)))
    assert votes and abs(sum(votes)) > .5, (row, votes)
    return angle if sum(votes) > 0 else angle-360


def prepare():
    SUPPORT.mkdir(parents=True, exist_ok=True)
    source = next((ROOT / 'data/parts/P001').glob('*P001.xlsx'))
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')
    backup_dir = ROOT / 'data/backups/P001'
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f'{source.stem}_before_geometry_update_{stamp}.xlsx'
    shutil.copy2(source, backup)
    assert sha(source) == sha(backup)
    wb = openpyxl.load_workbook(backup, read_only=True, data_only=False)
    ops = list(wb['Sheet1'].values)
    irregular = {r[-1] for r in ops[1:] if r[3] == 'IRREGULAR_PUNCH'}
    rows = list(wb['Sheet2'].values)
    assert list(rows[0]) == FIELDS
    chains = {gid: [] for gid in irregular}
    for number, values in enumerate(rows[1:], 2):
        if values[1] in irregular:
            row = dict(zip(FIELDS, values))
            row['_source_row'] = number
            chains[values[1]].append(row)
    wb.close()
    blocks, signs, merges, details = [], [], [], []
    for gid, original in sorted(chains.items()):
        if gid in TARGETS:
            assert [r['seq'] for r in original] == list(range(1, len(original)+1))
            kept = []
            i = 0
            while i < len(original):
                row = copy.deepcopy(original[i])
                # Replace LINE + (R0.5 ARC pair + LINE)+ by one collinear LINE.
                if row['segment_type'] == 'LINE':
                    source_line_seqs, removed_arc_seqs = [row['seq']], []
                    while i+3 < len(original) and all(original[j]['segment_type'] == 'ARC' and original[j]['radius'] == .5 for j in [i+1, i+2]) and original[i+3]['segment_type'] == 'LINE':
                        following = original[i+3]
                        direction = diff(vec(row, 'end'), vec(row, 'start'))
                        next_direction = diff(vec(following, 'end'), vec(following, 'start'))
                        offset = diff(vec(following, 'start'), vec(row, 'start'))
                        assert abs(direction[0]*next_direction[1]-direction[1]*next_direction[0]) < 1e-8
                        assert abs(direction[0]*offset[1]-direction[1]*offset[0]) < 1e-8
                        assert direction[0]*next_direction[0]+direction[1]*next_direction[1] > 0
                        removed_arc_seqs.extend([original[i+1]['seq'], original[i+2]['seq']])
                        source_line_seqs.append(following['seq'])
                        row['end_x'], row['end_y'] = following['end_x'], following['end_y']
                        i += 3
                    if removed_arc_seqs:
                        merges.append({'geometry_id': gid, 'old_line_seqs': source_line_seqs,
                                       'removed_R05_arc_seqs': removed_arc_seqs,
                                       'merged_start': vec(row, 'start'), 'merged_end': vec(row, 'end')})
                assert not (row['segment_type'] == 'ARC' and row['radius'] == .5)
                row['_old_seq'] = row['seq']
                kept.append(row)
                i += 1
            for seq, row in enumerate(kept, 1):
                row['seq'] = seq
                if row['segment_type'] == 'ARC':
                    computed = signed_arc(row, kept[(seq-2) % len(kept)], kept[seq % len(kept)])
                    assert abs(abs(computed)-90) < 1e-8
                    row['sweep_angle'] = round(computed, 12)
                assert length(diff(vec(row, 'end'), vec(kept[seq % len(kept)], 'start'))) < 1e-9
            deleted_arcs = sum(r['segment_type'] == 'ARC' and r['radius'] == .5 for r in original)
            removed_lines = sum(len(m['old_line_seqs'])-1 for m in merges if m['geometry_id'] == gid)
            details.append({'geometry_id': gid, 'old_segments': len(original), 'new_segments': len(kept),
                            'removed_R05_arcs': deleted_arcs, 'line_merges': removed_lines,
                            'net_removed_rows': len(original)-len(kept),
                            'changed_sweeps_remaining': sum(r['segment_type']=='ARC' and r['sweep_angle'] != original[r['_old_seq']-1]['sweep_angle'] for r in kept),
                            'old_to_new_seq': {str(r['_old_seq']): r['seq'] for r in kept}})
            blocks.append({'geometry_id': gid, 'first_row': original[0]['_source_row'],
                           'last_row': original[-1]['_source_row'], 'rows': kept,
                           'values': [[r[f] for f in FIELDS] for r in kept]})
        else:
            count = 0
            for i, row in enumerate(original):
                if row['segment_type'] == 'ARC':
                    computed = signed_arc(row, original[(i-1) % len(original)], original[(i+1) % len(original)])
                    new_value = math.copysign(abs(row['sweep_angle']), computed)
                    if new_value != row['sweep_angle']:
                        signs.append({'geometry_id': gid, 'seq': row['seq'], 'row': row['_source_row'],
                                      'old_value': row['sweep_angle'], 'new_value': new_value})
                        count += 1
            if count:
                details.append({'geometry_id': gid, 'old_segments': len(original), 'new_segments': len(original),
                                'removed_R05_arcs': 0, 'line_merges': 0, 'net_removed_rows': 0,
                                'changed_sweeps_remaining': count})
    assert len(blocks) == 11
    assert sum(d['removed_R05_arcs'] for d in details) == 48
    assert sum(d['line_merges'] for d in details) == 24
    assert sum(d['net_removed_rows'] for d in details) == 72
    assert sum(d['changed_sweeps_remaining'] for d in details) == 49
    plan = {'source': str(source), 'backup': str(backup), 'source_sha256': sha(source),
            'protected_files': {str(p): sha(p) for p in [ROOT/'PROJECT_SPEC.md', ROOT/'information_extract/sources/P001/legacy_outline.html']},
            'sheet': 'Sheet2', 'blocks': blocks, 'sign_changes': signs, 'merges': merges, 'details': details,
            'artifact_stage': str(SUPPORT/'artifact_stage.xlsx'), 'candidate': str(SUPPORT/'validated_candidate.xlsx')}
    json_write(PLAN, plan)
    print(json.dumps({'backup': str(backup), 'backup_hash': sha(backup), 'modified_geometry_ids': [d['geometry_id'] for d in details],
                      'removed_arcs': 48, 'line_merges': 24, 'removed_rows': 72, 'sweep_changes': 49}, ensure_ascii=True, indent=2))


def commit():
    plan = json.loads(PLAN.read_text(encoding='utf-8'))
    source, backup = Path(plan['source']), Path(plan['backup'])
    assert sha(source) == sha(backup) == plan['source_sha256'], 'Source changed after backup; abort.'
    authored = openpyxl.load_workbook(plan['artifact_stage'], read_only=True, data_only=False)
    sheet = authored['Sheet2']
    for block in plan['blocks']:
        actual = list(sheet.iter_rows(min_row=block['first_row'], max_row=block['first_row']+len(block['values'])-1, max_col=12, values_only=True))
        assert [list(r) for r in actual] == block['values'], block['geometry_id']
    for change in plan['sign_changes']:
        assert sheet.cell(change['row'], 12).value == change['new_value']
    authored.close()
    with zipfile.ZipFile(backup) as zin:
        original_xml = zin.read('xl/worksheets/sheet2.xml').decode('utf-8')
        row_pattern = re.compile(r'<row\b[^>]*\br="(\d+)"[^>]*>.*?</row>', re.S)
        matches = list(row_pattern.finditer(original_xml))
        rows_xml = {int(m.group(1)): m.group(0) for m in matches}
        sign_lookup = {c['row']: c for c in plan['sign_changes']}
        block_lookup = {b['first_row']: b for b in plan['blocks']}

        def set_number(xml, column, row_number, value):
            pattern = re.compile(r'(<c\b[^>]*\br="'+column+str(row_number)+r'"[^>]*>)(.*?)(</c>)', re.S)
            found = pattern.search(xml)
            assert found, (column, row_number)
            body = found.group(2)
            assert '<f' not in body
            new_v = '<v>'+format(float(value), '.15g')+'</v>'
            assert '<v>' in body
            new_body = re.sub(r'<v>.*?</v>', new_v, body, count=1)
            return xml[:found.start(2)]+new_body+xml[found.end(2):]

        result_rows = []
        old_num = 1
        while old_num <= len(matches):
            if old_num in block_lookup:
                block = block_lookup[old_num]
                for offset, row in enumerate(block['rows']):
                    source_row = row['_source_row']
                    xml = rows_xml[source_row]
                    # Values are taken from the artifact-tool output after independent equality verification.
                    values = block['values'][offset]
                    xml = set_number(xml, 'C', source_row, values[2])
                    if row['segment_type'] == 'LINE':
                        xml = set_number(xml, 'G', source_row, values[6])
                        xml = set_number(xml, 'H', source_row, values[7])
                    else:
                        xml = set_number(xml, 'L', source_row, values[11])
                    result_rows.append((source_row, xml))
                old_num = block['last_row']+1
            else:
                xml = rows_xml[old_num]
                if old_num in sign_lookup:
                    xml = set_number(xml, 'L', old_num, sign_lookup[old_num]['new_value'])
                result_rows.append((old_num, xml))
                old_num += 1
        rewritten = []
        row_mapping = {}
        for new_num, (source_row, xml) in enumerate(result_rows, 1):
            row_mapping[source_row] = new_num
            if new_num != source_row:
                assert '<f' not in xml, 'Do not relocate formulas without translating references.'
            xml = re.sub(r'(<row\b[^>]*\br=")\d+("[^>]*>)', lambda m: m.group(1)+str(new_num)+m.group(2), xml, count=1)
            xml = re.sub(r'(<c\b[^>]*\br="[A-Z]+)\d+(")', lambda m: m.group(1)+str(new_num)+m.group(2), xml)
            rewritten.append(xml)
        replacement = ''.join(rewritten)
        new_xml = original_xml[:matches[0].start()]+replacement+original_xml[matches[-1].end():]
        new_xml = re.sub(r'(<dimension\s+ref="A1:L)\d+("\s*/>)', lambda m: m.group(1)+str(len(rewritten))+m.group(2), new_xml, count=1)
        ET.fromstring(new_xml)
        candidate = Path(plan['candidate'])
        with zipfile.ZipFile(candidate, 'w') as zout:
            zout.comment = zin.comment
            for member in zin.infolist():
                zout.writestr(member, new_xml.encode('utf-8') if member.filename == 'xl/worksheets/sheet2.xml' else zin.read(member.filename))
    # Verify workbook contents, formulas and formatting before replacing the raw file.
    before = openpyxl.load_workbook(backup, data_only=False)
    after = openpyxl.load_workbook(candidate, data_only=False)
    assert before.sheetnames == after.sheetnames
    for sheet_name in ['Sheet1', 'Sheet3']:
        assert list(before[sheet_name].values) == list(after[sheet_name].values)
    expected = []
    original_rows = list(before['Sheet2'].values)
    n = 1
    while n <= len(original_rows):
        if n in block_lookup:
            block = block_lookup[n]
            expected.extend(tuple(r) for r in block['values'])
            n = block['last_row']+1
        else:
            values = list(original_rows[n-1])
            if n in sign_lookup:
                values[11] = sign_lookup[n]['new_value']
            expected.append(tuple(values))
            n += 1
    assert list(after['Sheet2'].values) == expected
    for source_row, destination_row in row_mapping.items():
        for old_cell, new_cell in zip(before['Sheet2'][source_row], after['Sheet2'][destination_row]):
            assert old_cell._style == new_cell._style, (source_row, destination_row, old_cell.column)
    assert after['Sheet2'].max_row == 690
    before.close()
    after.close()
    with zipfile.ZipFile(backup) as z0, zipfile.ZipFile(candidate) as z1:
        assert z0.namelist() == z1.namelist()
        changed_members = [name for name in z0.namelist() if z0.read(name) != z1.read(name)]
        assert changed_members == ['xl/worksheets/sheet2.xml']
    for p, expected_hash in plan['protected_files'].items():
        assert sha(p) == expected_hash
    assert sha(source) == plan['source_sha256']
    # User explicitly authorized this source update; backup and staging checks are complete.
    os.replace(candidate, source)
    plan['source_sha256_after'] = sha(source)
    plan['changed_zip_members'] = changed_members
    plan['integrity_checks'] = {'operation_sheet_unchanged': True, 'station_sheet_unchanged': True,
                               'other_geometry_values_unchanged': True, 'existing_formulas_preserved': True,
                               'surviving_cell_styles_preserved': True, 'project_spec_unchanged': True,
                               'reference_html_unchanged': True, 'only_sheet2_zip_member_changed': True}
    json_write(OUT/'change_log.json', {k: v for k, v in plan.items() if k not in ['blocks']})
    with (OUT/'changed_geometries.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        fields = ['geometry_id', 'old_segments', 'new_segments', 'removed_R05_arcs', 'line_merges', 'net_removed_rows', 'changed_sweeps_remaining']
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(plan['details'])
    print(json.dumps({'source_updated': str(source), 'backup': str(backup), 'sha256_before': plan['source_sha256'],
                      'sha256_after': plan['source_sha256_after'], 'integrity': plan['integrity_checks']}, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'commit'])
    args = parser.parse_args()
    prepare() if args.mode == 'prepare' else commit()
