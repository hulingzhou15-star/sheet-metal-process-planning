"""Read-only DXF parameter inventory; never an inference or assignment input."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import sys
from collections import Counter

MODULE = Path(__file__).resolve().parents[1]

import ezdxf
from ezdxf.lldxf.tagwriter import TagCollector

PARAMETER = re.compile(r'k[\s_-]*factor|neutral|allowance|deduction|unfold|bend\s*table|'
                       r'展开系数|展開係数|折弯系数|折彎係數|中性层|中性層|折弯补偿|折弯扣除|'
                       r'曲げ係数|伸び率|伸び代|曲げ控除', re.I)


def safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {'binary_byte_count': len(value), 'sha256': hashlib.sha256(value).hexdigest()}
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    try:
        return [safe(v) for v in value]
    except TypeError:
        return str(value)


def audit(source):
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    doc = ezdxf.readfile(source)
    owners = {b.block_record_handle: b.name for b in doc.blocks}
    texts, dimensions, metadata, keyword_hits, opaque = [], [], [], [], []
    for entity in doc.entitydb.values():
        if not entity.is_alive:
            continue
        kind = entity.dxftype()
        identity = {'handle': entity.dxf.handle, 'type': kind,
                    'owner': entity.dxf.get('owner'),
                    'block': owners.get(entity.dxf.get('owner'))}
        if kind in ('TEXT', 'MTEXT', 'ATTRIB', 'ATTDEF'):
            texts.append({**identity, 'text': entity.plain_text(),
                          'attributes': safe(entity.dxf.all_existing_dxf_attribs())})
        if kind == 'DIMENSION':
            dimensions.append({**identity, 'attributes': safe(entity.dxf.all_existing_dxf_attribs())})
        tags = TagCollector.dxftags(entity, dxfversion=doc.dxfversion)
        for tag in tags:
            if isinstance(tag.value, str) and PARAMETER.search(tag.value):
                keyword_hits.append({**identity, 'code': tag.code, 'value': tag.value})
        if entity.xdata or entity.appdata or entity.has_extension_dict or kind in ('XRECORD', 'DICTIONARY', 'DICTIONARYVAR', 'VLO-VL'):
            metadata.append({**identity, 'tags': safe(tags)})
        if kind in ('VLO-VL', 'ACAD_PROXY_ENTITY', 'ACAD_PROXY_OBJECT'):
            opaque.append(identity)
    header = {k: safe(doc.header.get(k)) for k in doc.header.varnames()}
    for k, v in header.items():
        if PARAMETER.search(k + ' ' + str(v)):
            keyword_hits.append({'header': k, 'value': v})
    validation = doc.audit()  # checks/fixes in memory only; never saves the DXF
    after = hashlib.sha256(source.read_bytes()).hexdigest()
    assert before == after, 'Source DXF changed during read-only audit'
    return {
        'source': str(source), 'sha256': before, 'source_unchanged': True,
        'usage': 'AUDIT_ONLY_MIXED_PRODUCT_AND_EXPERT_PROCESS_DRAWINGS_NOT_INFERENCE_INPUT',
        'dxf_version': doc.dxfversion, 'encoding': doc.encoding, 'header': header,
        'modelspace_entity_counts': dict(Counter(e.dxftype() for e in doc.modelspace())),
        'objects_counts': dict(Counter(e.dxftype() for e in doc.objects)),
        'layers': [safe(e.dxf.all_existing_dxf_attribs()) for e in doc.layers],
        'linetypes': [safe(e.dxf.all_existing_dxf_attribs()) for e in doc.linetypes],
        'blocks': [{'name': b.name, 'entity_count': len(b)} for b in doc.blocks],
        'block_inserts': [safe(e.dxf.all_existing_dxf_attribs()) for e in doc.modelspace().query('INSERT')],
        'appid_names': [e.dxf.name for e in doc.appids],
        'xdata_app_counts': dict(Counter(a for e in doc.entitydb.values() if e.xdata for a in e.xdata.data)),
        'texts': texts, 'dimensions': dimensions, 'metadata': metadata,
        'unfold_parameter_keyword_hits': keyword_hits,
        'opaque_objects_not_semantically_decoded': opaque,
        'audit_errors': [str(e) for e in validation.errors],
        'audit_in_memory_fix_count': len(validation.fixes),
        'limits': ['A keyword search cannot prove absence of parameters in opaque application data.',
                   'Dimension, arc and circle radii are not automatically bend inner radii.',
                   'Stock rectangle dimensions are not a validated initial blank boundary.',
                   'No assignment, expert sequence, Sheet5 or geometry was updated.'],
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('sha256', 'source_unchanged', 'modelspace_entity_counts',
                     'unfold_parameter_keyword_hits', 'opaque_objects_not_semantically_decoded',
                     'audit_errors', 'audit_in_memory_fix_count')}, ensure_ascii=False))
