"""Independent allocation policy: H1-H5/H7 active; H6 and Sheet5 retired.

Reuse validated fact parsing on an isolated in-memory XLSX package. The original
Sheet5 XML is never read: the legacy parser receives an empty header-only table.
No expert field is requested by that parser. Frozen src implementations stay intact.
"""
from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib

from src.inference.input import InferenceContext, load_inference_context
from src.constraints.context import METADATA_HEADERS
from src.constraints.v06.hard_constraints import HardConstraintEngine as FrozenEngine, RuleConstraintResult

VERSION = 'geometry_allocation_v1_no_H6'
ACTIVE = ('H1', 'H2', 'H3', 'H4', 'H5', 'H7')
NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def isolated_package(content):
    """Keep only input Sheets1-4; inject schema-only Sheet5 for parser compatibility."""
    destination = BytesIO()
    with ZipFile(BytesIO(content)) as src, ZipFile(destination, 'w', ZIP_DEFLATED) as dst:
        book = ET.fromstring(src.read('xl/workbook.xml'))
        rels = ET.fromstring(src.read('xl/_rels/workbook.xml.rels'))
        types = ET.fromstring(src.read('[Content_Types].xml'))
        targets = {r.attrib['Id']: r.attrib['Target'] for r in rels}
        sheets = book.find(f'{{{NS}}}sheets')
        permitted, ids = set(), set()
        for sheet in list(sheets):
            rid = sheet.attrib[f'{{{REL}}}id']
            if sheet.attrib['name'] not in ('Sheet1', 'Sheet2', 'Sheet3', 'Sheet4'):
                sheets.remove(sheet)
            else:
                target = targets[rid]
                permitted.add(target.lstrip('/') if target.startswith('/') else 'xl/' + target)
                ids.add(rid)
        for r in list(rels):
            if r.attrib['Type'].endswith('/worksheet') and r.attrib['Id'] not in ids:
                rels.remove(r)
        rid = 'allocationSchemaOnly'
        if any(r.attrib['Id'] == rid for r in rels):
            raise ValueError('Reserved relationship id collision')
        target = 'xl/worksheets/allocation_schema_only.xml'
        ET.SubElement(sheets, f'{{{NS}}}sheet', name='Sheet5', sheetId='9999', **{f'{{{REL}}}id': rid})
        ET.SubElement(rels, '{http://schemas.openxmlformats.org/package/2006/relationships}Relationship',
                      Id=rid, Type=REL+'/worksheet', Target='/'+target)
        for t in list(types):
            if t.attrib.get('PartName', '').startswith('/xl/worksheets/'):
                if t.attrib['PartName'].lstrip('/') not in permitted:
                    types.remove(t)
        ET.SubElement(types, '{http://schemas.openxmlformats.org/package/2006/content-types}Override',
                      PartName='/'+target,
                      ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml')
        sheet = ET.Element(f'{{{NS}}}worksheet')
        row = ET.SubElement(ET.SubElement(sheet, f'{{{NS}}}sheetData'), f'{{{NS}}}row', r='1')
        for i, field in enumerate(METADATA_HEADERS['Sheet5']):
            cell = ET.SubElement(row, f'{{{NS}}}c', r=f'{chr(65+i)}1', t='inlineStr')
            ET.SubElement(ET.SubElement(cell, f'{{{NS}}}is'), f'{{{NS}}}t').text = field
        replacements = {'xl/workbook.xml': ET.tostring(book),
                        'xl/_rels/workbook.xml.rels': ET.tostring(rels),
                        '[Content_Types].xml': ET.tostring(types)}
        for entry in src.infolist():
            if entry.filename.startswith('xl/worksheets/') and entry.filename not in permitted:
                continue
            # Do not eagerly evaluate src.read for a replaced member.
            value = replacements[entry.filename] if entry.filename in replacements else src.read(entry.filename)
            dst.writestr(entry, value)
        dst.writestr(target, ET.tostring(sheet))
    return destination.getvalue()


@dataclass(frozen=True)
class AllocationContext(InferenceContext):
    def __post_init__(self):
        if self.bend_influences:
            raise ValueError('Sheet5/H6 inputs are disabled in this allocation policy')
        super().__post_init__()
        if any(r.constraint_type == 'H6' for r in self.precedence_relations):
            raise ValueError('H6 relation leaked into allocation policy')


def load_context(path):
    path = Path(path).resolve()
    content = path.read_bytes()
    with TemporaryDirectory(prefix='sheet_allocation_') as tmp:
        isolated = Path(tmp) / 'facts.xlsx'
        isolated.write_bytes(isolated_package(content))
        parsed = load_inference_context(isolated)
    digest = hashlib.sha256(content).hexdigest()
    facts = replace(parsed.part_data, source_path=path, source_sha256=digest)
    return AllocationContext(facts, parsed.fixed_assignments, (), digest)


class AllocationResult(RuleConstraintResult):
    def to_dict(self):
        result = super().to_dict()
        result['rule_version'] = VERSION
        result['active_constraints'] = list(ACTIVE)
        result['disabled_constraints'] = ['H6']
        result['violation_breakdown'].pop('H6', None)
        return result


class AllocationEngine(FrozenEngine):
    def __init__(self, context):
        if not isinstance(context, AllocationContext):
            raise TypeError('New entry requires AllocationContext, not a legacy Sheet5 context')
        super().__init__(context)

    def evaluate(self, plan):
        result = super().evaluate(plan)
        return AllocationResult(result.feasible, result.violations)


def enumerate_single_bend_moves(context, base, bend_ids):
    engine = AllocationEngine(context)
    engine.require_feasible(base)
    if not set(bend_ids) <= context.operations.keys():
        raise ValueError('Unknown bend operation')
    plans = [dict(plan_id='N6_BASE', assignments=dict(base), changes=[])]
    rejected = []
    for oid in sorted(bend_ids):
        if context.operations[oid].operation_type != 'BEND':
            raise ValueError('Single-bend enumeration requires BEND actions')
        for s in context.ordered_stations:
            if s.station_id == base[oid]:
                continue
            a = {**base, oid: s.station_id}
            hard = engine.evaluate(a).to_dict()
            change = dict(operation_id=oid, before=base[oid], after=s.station_id)
            if hard['feasible']:
                plans.append(dict(plan_id=f'N6_{len(plans):03d}', assignments=a, changes=[change]))
            else:
                rejected.append(dict(change=change, hard=hard))
    for p in plans:
        p['hard'] = engine.evaluate(p['assignments']).to_dict()
    return plans, rejected
