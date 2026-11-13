"""Geometric/color evidence from the expert drawing, isolated from inference inputs."""
from collections import Counter, defaultdict
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import ezdxf
from ezdxf.addons.drawing.properties import RenderContext
import numpy as np
from information_extract.extract_app.drawing import load_drawing

MODULE=Path(__file__).resolve().parents[1]
OUT=MODULE/'outputs/p002_expert_dxf_v01'


def primitive(kind, points, radius=None):
    return {'kind':kind,'points':points,'radius':radius}


def source_primitive(s):
    kind=s['segment_type']
    if kind=='CIRCLE':return primitive(kind,[[s['center_x'],s['center_y']]],s['radius'])
    points=[[s['start_x'],s['start_y']],[s['end_x'],s['end_y']]]
    if kind=='ARC':
        a=math.atan2(s['start_y']-s['center_y'],s['start_x']-s['center_x'])+math.radians(s['sweep_angle'])/2
        points.append([s['center_x']+s['radius']*math.cos(a),s['center_y']+s['radius']*math.sin(a)])
    return primitive(kind,points,s.get('radius') if kind=='ARC' else None)


def key(s):
    pt=lambda p:tuple(round(float(v),4) for v in p)
    p=s['points'];kind=s['kind']
    if kind=='CIRCLE':return (kind,pt(p[0]),round(s['radius'],4))
    ends=tuple(sorted([pt(p[0]),pt(p[1])]))
    return (kind,ends,pt(p[2])) if kind=='ARC' else (kind,ends)


def transformed(s,m,t):
    return dict(s,points=(np.asarray(s['points'])@m.T+t).tolist())


def extract(path):
    doc=ezdxf.readfile(path);ctx=RenderContext(doc);geometry=[];labels=[];unsupported=Counter()
    def walk(entity,trail):
        kind=entity.dxftype()
        if kind=='INSERT':
            ctx.push_state(ctx.resolve_all(entity))
            try:
                for i,e in enumerate(entity.virtual_entities()):walk(e,trail+[f'virtual:{i}'])
            finally:ctx.pop_state()
            return
        color=ctx.resolve_color(entity)
        if kind in ('TEXT','MTEXT'):
            text=entity.dxf.text if kind=='TEXT' else entity.plain_text()
            if '工序名称' in text:labels.append({'text':text,'xy':list(entity.dxf.insert)[:2],'source_path':trail})
            return
        if kind in ('LWPOLYLINE','POLYLINE'):
            try:
                for i,e in enumerate(entity.virtual_entities()):walk(e,trail+[f'poly:{i}'])
            except (TypeError,ValueError,NotImplementedError):unsupported[kind]+=1
            return
        if kind=='LINE':row=primitive(kind,[list(entity.dxf.start)[:2],list(entity.dxf.end)[:2]])
        elif kind=='CIRCLE':row=primitive(kind,[list(entity.ocs().to_wcs(entity.dxf.center))[:2]],entity.dxf.radius)
        elif kind=='ARC':
            start,end=entity.dxf.start_angle,entity.dxf.end_angle
            mid=start+((end-start)%360)/2
            center=entity.dxf.center;r=entity.dxf.radius
            p=entity.ocs().to_wcs((center.x+r*math.cos(math.radians(mid)),center.y+r*math.sin(math.radians(mid)),center.z))
            row=primitive(kind,[list(entity.start_point)[:2],list(entity.end_point)[:2],list(p)[:2]],r)
        else:unsupported[kind]+=1;return
        row.update(color=color,aci=entity.dxf.get('color',256),layer=entity.dxf.layer,source_path=trail)
        geometry.append(row)
    for e in doc.modelspace():walk(e,[e.dxf.handle])
    return geometry,labels,dict(unsupported)


def align(source,targets):
    circles=[s for s in source if s['kind']=='CIRCLE'];tc={key(s):s for s in targets if s['kind']=='CIRCLE'}
    index=defaultdict(list)
    for i,s in enumerate(targets):index[key(s)].append(i)
    ranked=[]
    for swap,sx,sy in itertools.product((False,True),(-1,1),(-1,1)):
        m=np.array([[0,sx],[sy,0]] if swap else [[sx,0],[0,sy]])
        votes=defaultdict(list)
        for s in circles:
            for t in tc.values():
                if abs(s['radius']-t['radius'])<1e-5:
                    shift=np.asarray(t['points'][0])-m@np.asarray(s['points'][0])
                    votes[tuple(np.round(shift,2))].append(shift)
        for _,shifts in sorted(votes.items(),key=lambda x:-len(x[1]))[:3]:
            t=np.mean(shifts,axis=0)
            matches=[index.get(key(transformed(s,m,t)),[]) for s in source]
            ranked.append({'matrix':m.tolist(),'translation':t.tolist(),'matched_count':sum(bool(x) for x in matches),'matches':matches})
    ranked.sort(key=lambda r:-r['matched_count'])
    return ranked


def main():
    path=MODULE/'sources/P002/originals/P002.dxf'
    source_digest=hashlib.sha256(path.read_bytes()).hexdigest()
    annotation_digest=hashlib.sha256((MODULE/'projects/P002/project.json').read_bytes()).hexdigest()
    cfg=json.loads((MODULE/'configs/P002.json').read_text(encoding='utf-8'))
    drawing=load_drawing(MODULE/cfg['source_file'],cfg)
    project=json.loads((MODULE/'projects/P002/project.json').read_text(encoding='utf-8'))
    assert drawing['source']['sha256']==project['source_sha256']
    source=[dict(source_primitive(s),source_id=s['id']) for s in drawing['segments']]
    geometry,labels,unsupported=extract(path)
    frames=[]
    for label in sorted(labels,key=lambda l:l['xy'][0]):
        x,y=label['xy']
        # Title-relative broad search window; accepted alignment is decided by
        # complete LINE/ARC/CIRCLE geometry, never title proximity alone.
        targets=[g for g in geometry if x-750<g['points'][0][0]<x+50 and y-230<g['points'][0][1]<y+230]
        ranked=align(source,targets)
        if not ranked:frames.append(dict(label,status='UNRESOLVED'));continue
        best=ranked[0];matches={s['source_id']:[targets[i] for i in ids] for s,ids in zip(source,best['matches'])}
        row=dict(label,status='PROPOSED',matrix=best['matrix'],translation=best['translation'],
                 matched_count=best['matched_count'],runner_up_count=ranked[1]['matched_count'] if len(ranked)>1 else 0,
                 match_colors=dict(Counter(g['color'] for values in matches.values() for g in values)),matches=matches,targets=targets)
        frames.append(row)
        print(label['text'],'matched',row['matched_count'],'runner',row['runner_up_count'],'matrix',row['matrix'],'colors',row['match_colors'],flush=True)
    OUT.mkdir(parents=True,exist_ok=True)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==source_digest
    assert hashlib.sha256((MODULE/'projects/P002/project.json').read_bytes()).hexdigest()==annotation_digest
    result={'part_id':'P002','source':path.relative_to(MODULE.parent).as_posix(),'source_sha256':source_digest,
            'flat_source_sha256':drawing['source']['sha256'],'annotation_revision':project['revision'],
            'annotation_sha256':annotation_digest,
            'matching_coordinate_quantization_mm':0.0001,
            'unsupported_entity_counts':unsupported,'frames':frames,'operations':project['operations'],'flat_primitives':source,
            'scope':'Only saved P002_1 feature bindings; expert evidence only, not inference inputs.'}
    (OUT/'evidence.json').write_text(json.dumps(result,ensure_ascii=False,separators=(',',':')),encoding='utf-8')


if __name__=='__main__':main()
