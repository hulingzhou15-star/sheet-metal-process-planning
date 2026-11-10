"""Exact, explicit portions of an existing LINE; no extrapolation or gap filling.

Used by reviewed workbook geometry only. This does not change the editor's
active source list, original source identity or any operation binding.
"""
from copy import deepcopy
import hashlib
import json
import math

from .geometry import _primitive, segment_bbox


def confirmed_line_subsegment(parent, start_fraction, end_fraction, tolerance):
    if parent.get('segment_type') != 'LINE' or not isinstance(parent.get('id'),str) or not parent['id']:
        raise ValueError('局部截取需要带明确来源 ID 的原始 LINE。')
    values=(start_fraction,end_fraction,tolerance)
    if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in values):
        raise ValueError('截取参数及容差必须为有限数值。')
    if tolerance<=0 or not 0<=start_fraction<end_fraction<=1:
        raise ValueError('截取区间必须满足 0 <= start < end <= 1，禁止外推。')
    _primitive(parent,tolerance)
    child=deepcopy(parent)
    for axis in ('x','y'):
        origin=parent['start_'+axis];delta=parent['end_'+axis]-origin
        child['start_'+axis]=origin+start_fraction*delta
        child['end_'+axis]=origin+end_fraction*delta
    _primitive(child,tolerance)
    key=json.dumps([parent['id'],start_fraction,end_fraction],separators=(',',':'))
    child['id']='SUB_'+hashlib.sha256(key.encode()).hexdigest()[:24]
    child['bbox']=segment_bbox(child)
    child['derivation']={'method':'EXACT_LINE_SUBSEGMENT','parent_source_segment_id':parent['id'],
                         'start_fraction':start_fraction,'end_fraction':end_fraction}
    return child
