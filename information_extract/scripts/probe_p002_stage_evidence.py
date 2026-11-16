"""Read-only DXF stage evidence; never supplies allocations to the planner."""
import json
from collections import Counter
from pathlib import Path
import ezdxf
import numpy as np
from information_extract.three_d.artifacts import ROOT, read, write, sha

MODULE=ROOT/'information_extract'
OUT=MODULE/'outputs/p002_stage_evidence_v01'
TARGETS={'O069':8,'O088':9,'O094':6,'O096':9,'O104':7}


def annotations(doc):
    rows=[]
    def walk(e,path):
        kind=e.dxftype()
        if kind=='INSERT':
            for i,c in enumerate(e.virtual_entities()):walk(c,path+[f'virtual:{i}'])
        elif kind in ('TEXT','MTEXT','DIMENSION','LEADER'):
            row=dict(kind=kind,source_path=path,layer=e.dxf.layer)
            if kind in ('TEXT','MTEXT'):
                row.update(text=e.dxf.text if kind=='TEXT' else e.plain_text(),position=list(e.dxf.insert),rotation=e.dxf.get('rotation',0))
            elif kind=='LEADER':row['vertices']=[list(v) for v in e.vertices]
            else:
                row['dimension_type']=e.dxf.dimtype & 7
                row['text_override']=e.dxf.get('text','')
                row['points']={k:list(e.dxf.get(k)) for k in ('defpoint','defpoint2','defpoint3','defpoint4','defpoint5','text_midpoint') if e.dxf.hasattr(k)}
                try:row['measurement']=float(e.get_measurement())
                except (TypeError,ValueError,AttributeError):row['measurement']=None
            rows.append(row)
    for e in doc.modelspace():walk(e,[e.dxf.handle])
    return rows


def main():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    source=MODULE/'sources/P002/originals/P002.dxf'
    doc=ezdxf.readfile(source);rows=annotations(doc)
    OUT.mkdir(parents=True,exist_ok=True)
    write(OUT/'annotations.json',dict(source_sha256=sha(source),rows=rows,counts=dict(Counter(r['kind'] for r in rows))))
    ev=read(MODULE/'outputs/p002_expert_dxf_v01/evidence.json')
    if ev['source_sha256']!=sha(source):raise ValueError('Stale expert evidence')
    fig=plt.figure(figsize=(14,12));ax=fig.add_axes([.02,.02,.96,.94])
    Frontend(RenderContext(doc),MatplotlibBackend(ax)).draw_layout(doc.modelspace(),finalize=True)
    for index in (5,7,8,9,10,11):
        f=ev['frames'][index-1];x,y=f['xy']
        ax.set_xlim(x-470,x-40);ax.set_ylim(y-230,y+145)
        ax.set_aspect('equal',adjustable='box');fig.suptitle(f'P002 source view {index:02d}')
        fig.savefig(OUT/f'view_{index:02d}.png',dpi=150,facecolor=fig.get_facecolor())
        print('rendered',index,flush=True)
    plt.close(fig)
    print('annotations',dict(Counter(r['kind'] for r in rows)),flush=True)


if __name__=='__main__':main()
