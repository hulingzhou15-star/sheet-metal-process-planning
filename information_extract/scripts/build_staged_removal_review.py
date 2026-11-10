"""Offline view of the very same B-rep meshes used for removal diagnostics."""
from pathlib import Path
import json
MODULE=Path(__file__).resolve().parents[1]


def main():
    out=MODULE/'outputs/staged_removal_v01/P001';web=MODULE/'three_d/web'
    html=(web/'staged_removal_review.html').read_text(encoding='utf-8')
    for name,value in [('DATA',(out/'viewer_data.json').read_text(encoding='utf-8').replace('<','\\u003c')),
                       ('CORE',(web/'core.js').read_text(encoding='utf-8')),
                       ('MOTION',(web/'motion_core.js').read_text(encoding='utf-8')),
                       ('RENDERER',(web/'motion_renderer.js').read_text(encoding='utf-8'))]:
        html=html.replace('/*__'+name+'__*/',value)
    (out/'review.html').write_text(html,encoding='utf-8')


if __name__=='__main__':main()
