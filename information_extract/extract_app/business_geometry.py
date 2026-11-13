"""Preserve approved workbook geometry separately from raw DXF bindings."""
from copy import deepcopy
import math


def active_business_geometry(op):
    data = op.get('business_geometry')
    return bool(isinstance(data, dict) and data.get('segments')
                and not op.get('source_segment_ids') and not op.get('no_geometry')
                and data.get('operation_type') == op.get('operation_type')
                and data.get('feature_type') == op.get('feature_type'))


def business_record(op):
    if not active_business_geometry(op):
        return None
    data = op['business_geometry']
    segments = deepcopy(data['segments'])
    for row in segments:
        kind = row['segment_type']
        required = ['center_x', 'center_y', 'radius'] if kind == 'CIRCLE' else ['start_x', 'start_y', 'end_x', 'end_y']
        if kind == 'ARC':
            required += ['center_x', 'center_y', 'radius', 'sweep_angle']
        if kind not in {'LINE', 'ARC', 'CIRCLE', 'BEND_LINE', 'BRIDGE'}:
            raise ValueError('业务表几何类型不支持：' + str(kind))
        if any(type(row.get(k)) not in (int, float) or not math.isfinite(row[k]) for k in required):
            raise ValueError('业务表几何含缺失或非有限数值。')
    region = deepcopy(op.get('region')) if op.get('operation_type') == 'BEND' else None
    if region:
        region['bend_operation_id'] = op['operation_id']
    return {'operation_id': op['operation_id'], 'feature_type': op['feature_type'],
            'source_segment_ids': [], 'status': 'READY', 'legacy_supported': True,
            'legacy_geometry': segments, 'region': region, 'issues': [],
            'geometry_origin': 'BUSINESS_WORKBOOK', 'provenance': deepcopy(data['source']),
            'paths': [{'path_id': data['geometry_id'], 'role': 'IMPORTED_BUSINESS_GEOMETRY',
                       'closed': None, 'segments': segments, 'source_segment_ids': []}]}
