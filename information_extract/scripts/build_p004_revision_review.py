"""Reproduce the current P004 drawing change review from versioned source rules."""
import json
from pathlib import Path
from information_extract.extract_app.drawing import load_drawing
from information_extract.scripts.convert_dxf_to_html import path_data

M = Path(__file__).resolve().parents[1]


def main():
    config=json.loads((M/'configs/P004.json').read_text(encoding='utf-8'))
    previous=dict(config); previous.pop('confirmed_geometry_revision')
    before=load_drawing(M/config['source_file'],previous)
    after=load_drawing(M/config['source_file'],config)
    manifest=json.loads((M/config['confirmed_geometry_revision']).read_text(encoding='utf-8'))
    added={s['id'] for s in after['source_revision']['added_segments']}
    removed=set(manifest['remove_source_ids'])
    def svg(d,box,ids,color):
        x0,y0,x1,y1=box
        curves=[]
        for s in d['segments']:
            b=s['bbox']
            if b[2]<x0 or b[0]>x1 or b[3]<y0 or b[1]>y1:continue
            stroke=color if s['id'] in ids else '#7a8b9e'
            dash=' stroke-dasharray="4 3"' if s['linetype'].upper() in ('DASHED','HIDDEN') else ''
            curves.append(f'<path d="{path_data(s)}" stroke="{stroke}"{dash}/>')
        return f'<svg viewBox="{x0} {-y1} {x1-x0} {y1-y0}"><g transform="scale(1 -1)">{"".join(curves)}</g></svg>'
    views=[('整图',before['bounds']),('左侧多余翻孔与新增孔',[3490,3950,3522,4000]),
           ('右侧多余翻孔',[3608,3950,3636,3972]),('右侧新增孔',[3662,3950,3688,4000]),
           ('左侧补入折弯双线',[3345,3875,3380,3900]),('右侧补入折弯双线',[3704,3927,3731,3946]),
           ('底部折弯线全部保留',[3360,3808,3775,3848])]
    cards=[]
    for label,box in views:
        cards.append(f'<h2>{label}</h2><main><section><h3>修正前（红色为替换/移除）</h3>{svg(before,box,removed,"#c55050")}</section><section><h3>修正后（蓝色为补入）</h3>{svg(after,box,added,"#197db7")}</section></main>')
    out=M/'outputs/p004_revision_20261111';out.mkdir(parents=True,exist_ok=True)
    page='''<!doctype html><meta charset="utf-8"><title>P004 新图修正对照</title><style>body{font:16px system-ui;margin:24px;color:#243b4f;background:#f1f5f8}main{display:grid;grid-template-columns:1fr 1fr;gap:16px}section{background:white;padding:12px}svg{width:100%;height:370px}path{fill:none;stroke-width:1;vector-effect:non-scaling-stroke}h3{font-size:15px}</style><h1>P004 · 新版图纸修正</h1><p>已有折弯线保留；新增两组共四条折弯线。翻孔同心预孔保留。42 个已存动作绑定不变。</p><a href="http://127.0.0.1:8769/">打开特征录入页</a>'''+''.join(cards)
    (out/'review.html').write_text(page,encoding='utf-8')
    print(out/'review.html')


if __name__=='__main__': main()
