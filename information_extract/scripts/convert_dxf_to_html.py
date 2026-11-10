"""Build an offline drawing HTML plus an explicit link to the live editor."""
from pathlib import Path
import argparse
from collections import Counter
import html
import json
import math
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT)]
from extract_app.drawing import load_drawing
from extract_app.server import contained


def ink(color):
    return {1: '#b83337', 2: '#846500', 3: '#287343', 4: '#087e93', 5: '#326ca8'}.get(color, '#566477')


def path_data(s):
    kind = s['segment_type']
    if kind == 'CIRCLE':
        x, y, r = s['center_x'], s['center_y'], s['radius']
        return f'M {x+r} {y} A {r} {r} 0 1 1 {x-r} {y} A {r} {r} 0 1 1 {x+r} {y}'
    a = f"M {s['start_x']} {s['start_y']}"
    if kind == 'ARC':
        return f"{a} A {s['radius']} {s['radius']} 0 {int(abs(s['sweep_angle'])>180)} {int(s['sweep_angle']>=0)} {s['end_x']} {s['end_y']}"
    return f"{a} L {s['end_x']} {s['end_y']}"


def build(config_path):
    config = json.loads(contained(ROOT, config_path).read_text('utf-8'))
    drawing = load_drawing(contained(ROOT, config['source_file']), config)
    x0, y0, x1, y1 = drawing['bounds']
    pad = max(x1-x0, y1-y0)*.035
    box = [x0-pad, -y1-pad, x1-x0+2*pad, y1-y0+2*pad]
    curves = []
    for s in drawing['segments']:
        if not s['is_visible']:
            continue
        title = f"{s['segment_type']} · {s['id']} · 图层 {s['layer']}"
        dash = ' stroke-dasharray="6 3"' if re.search(r'DASH|HIDDEN|CENTER', s.get('linetype') or '', re.I) else ''
        curves.append(f'<path data-id="{s["id"]}" d="{path_data(s)}" stroke="{ink(s["color"])}"{dash}><title>{html.escape(title)}</title></path>')
    labels = []
    for a in drawing['annotations']:
        if not a['is_visible']:
            continue
        transform = f'translate({a["x"]} {-a["y"]}) rotate({-a["rotation"]}) scale({a["width_factor"]} 1)'
        baseline = f' dominant-baseline="{html.escape(a["baseline"], quote=True)}"' if a.get('baseline') else ''
        labels.append(f'<text transform="{transform}" font-size="{a["height"]}" fill="{ink(a["color"])}"{baseline}>{html.escape(a["text"])}</text>')
    editor_url = f'http://127.0.0.1:{config["port"]}/'
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__PART__ · DXF 图纸</title><style>body{margin:0;background:#fafcfe;color:#26354a;font:14px "Microsoft YaHei",sans-serif}header{padding:14px 22px;background:white;border-bottom:1px solid #dce3ec;display:flex;align-items:center;gap:18px;flex-wrap:wrap}h1{font-size:18px;margin:0}a,button{color:#326ca8;background:white;border:1px solid #cad7e5;padding:7px 12px;border-radius:4px;text-decoration:none;cursor:pointer}.note{font-size:12px;color:#68788b}svg{display:block;width:100%;height:calc(100vh - 110px);touch-action:none}path{fill:none;stroke-width:1.2;vector-effect:non-scaling-stroke}path:hover{stroke:#d27b26;stroke-width:2.5}text{font-family:"Microsoft YaHei",sans-serif;pointer-events:none}footer{font-size:11px;padding:7px 22px;color:#68788b}</style>
<header><h1>__PART__ · 二维图纸</h1><a href="__EDITOR__" target="_blank">打开动作录入页</a><button id="fit">适合窗口</button><label><input id="labels-toggle" type="checkbox" checked>工艺文字</label><span class="note">此文件可离线查看；录入需先运行 information_extract/start___PART__.ps1。滚轮缩放，拖动平移。</span></header>
<svg id="sheet" viewBox="__BOX__" xmlns="http://www.w3.org/2000/svg"><g transform="scale(1 -1)">__CURVES__</g><g id="labels">__LABELS__</g></svg>
<footer>来源：__SOURCE__ · __COUNT__ 个几何片段，__TEXTCOUNT__ 条文字。文字仅显示，不计入加工动作。SHA256：__SHA__</footer>
<script>const svg=document.getElementById('sheet'),initial=__BOXJSON__;let box=[...initial],drag=null;function draw(){svg.setAttribute('viewBox',box.join(' '));}function point(e){const p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;return p.matrixTransform(svg.getScreenCTM().inverse());}svg.addEventListener('wheel',e=>{e.preventDefault();const p=point(e),f=e.deltaY>0?1.15:1/1.15;box=[p.x+(box[0]-p.x)*f,p.y+(box[1]-p.y)*f,box[2]*f,box[3]*f];draw();},{passive:false});svg.addEventListener('pointerdown',e=>{drag=point(e);svg.setPointerCapture(e.pointerId);});svg.addEventListener('pointermove',e=>{if(!drag)return;const p=point(e);box[0]+=drag.x-p.x;box[1]+=drag.y-p.y;draw();});svg.addEventListener('pointerup',()=>drag=null);svg.addEventListener('pointercancel',()=>drag=null);document.getElementById('fit').onclick=()=>{box=[...initial];draw();};document.getElementById('labels-toggle').onchange=e=>document.getElementById('labels').style.display=e.target.checked?'':'none';</script></html>'''
    replacements = {'__PART__': html.escape(config['part_id']), '__EDITOR__': editor_url,
                    '__BOX__': ' '.join(map(str, box)), '__BOXJSON__': json.dumps(box),
                    '__CURVES__': ''.join(curves), '__LABELS__': ''.join(labels),
                    '__SOURCE__': html.escape(drawing['source']['file_name']),
                    '__COUNT__': str(len(drawing['segments'])), '__TEXTCOUNT__': str(len(drawing['annotations'])),
                    '__SHA__': drawing['source']['sha256']}
    for key, value in replacements.items():
        page = page.replace(key, value)
    destination = contained(ROOT, f"outputs/{config['part_id']}")
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{config['part_id']}_drawing.html"
    target.write_text(page, 'utf-8')
    report = {'html': str(target), 'editor_url': editor_url, 'source': drawing['source'],
              'segment_count': len(drawing['segments']),
              'segment_types': dict(Counter(s['segment_type'] for s in drawing['segments'])),
              'display_annotation_count': len(drawing['annotations']), 'issues': drawing['issues'],
              'raw_stats': drawing['raw_stats'],
              'removed_sources': drawing.get('removed_sources', []),
              'source_cleanup': drawing.get('source_cleanup'),
              'source_supplement': drawing.get('source_supplement'),
              'background_source_ids': drawing.get('background_source_ids', []),
              'removed_text_count': len(drawing.get('removed_annotations', [])),
              'removed_annotation_issues': drawing.get('removed_annotation_issues', []),
              'annotation_note': 'TEXT is display-only, not manufacturing geometry or an operation.'}
    (destination / 'conversion_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', 'utf-8')
    print(json.dumps({'html': str(target), 'editor_url': editor_url, 'segments': len(drawing['segments']),
                      'text': len(drawing['annotations']), 'issues': len(drawing['issues'])}, ensure_ascii=False))
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='DXF → 可独立打开的 HTML 图纸')
    parser.add_argument('--config', required=True)
    build(parser.parse_args().config)
