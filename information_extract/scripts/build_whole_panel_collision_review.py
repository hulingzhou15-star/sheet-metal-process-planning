"""Package audited geometry and results in a standalone offline diagnostic page."""
from pathlib import Path
import sys,json
ROOT=Path(__file__).resolve().parents[2];MODULE=ROOT/'information_extract'
sys.path.insert(0,str(ROOT))
from information_extract.scripts.check_whole_panel_collision import read,sha


def main():
    cfg=read(MODULE/'configs/P001_whole_panel_collision_v01.json');out=MODULE/cfg['output']
    report=read(out/'report.json');data=read(out/'viewer_data.json')
    for p,h in report['source_sha256'].items():
        if sha(Path(p))!=h:raise ValueError('Source changed: '+p)
    sensitivity=read(out/'artifact_sensitivity.json')
    if sensitivity['source_report_sha256']!=sha(out/'report.json'):raise ValueError('Sensitivity report stale')
    data['artifact_sensitivity']=sensitivity
    stage=read(MODULE/cfg['stage_data'])
    boundaries={p['region_id']:p['boundary'] for p in stage['panels']}
    for p in data['panels']:p['display_boundary']=boundaries[p['region_id']]
    web=MODULE/'three_d/web';html=(web/'whole_panel_collision_review.html').read_text(encoding='utf-8')
    for k,v in [('DATA',json.dumps(data,ensure_ascii=False,allow_nan=False).replace('<','\\u003c')),
                ('CORE',(web/'core.js').read_text(encoding='utf-8')),
                ('MOTION',(web/'motion_core.js').read_text(encoding='utf-8')),
                ('RENDERER',(web/'motion_renderer.js').read_text(encoding='utf-8')),
                ('APP',(web/'whole_panel_collision_review.js').read_text(encoding='utf-8'))]:
        html=html.replace('/*__'+k+'__*/',v)
    (out/'review.html').write_text(html,encoding='utf-8')
    print(out/'review.html')


if __name__=='__main__':main()
