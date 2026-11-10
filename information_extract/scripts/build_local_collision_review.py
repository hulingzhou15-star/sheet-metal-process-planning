"""Offline inspection of the exact panel envelopes used by the diagnostic."""
from pathlib import Path
import sys
import json
import hashlib

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT/'information_extract'
sys.path.insert(0, str(ROOT))
from information_extract.scripts.check_local_bend_collision import read, write
from information_extract.three_d.local_collision import panel_solids, indexed
from information_extract.three_d.assets import xyz
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopoDS import TopoDS
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRep import BRep_Tool
from OCP.TopLoc import TopLoc_Location


from information_extract.three_d.meshing import mesh

def main():
    cfg = read(MODULE/'configs/P001_local_collision_v01.json'); out = MODULE/cfg['output']
    report = read(out/'report.json')
    for path, expected in report['source_sha256'].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise ValueError('Audit source changed: '+path)
    model = read(MODULE/cfg['model']); old = read(MODULE/cfg['material']); asset = read(MODULE/cfg['asset'])
    cadcfg = read(MODULE/cfg['cad_config']); reader = STEPControl_Reader()
    if reader.ReadFile(str(MODULE/cadcfg['step'])) != IFSelect_RetDone or not reader.TransferRoots():
        raise ValueError('STEP read failed')
    panels, _ = panel_solids(indexed(reader.OneShape(), TopAbs_FACE), old['example']['regions'], asset,
                            model['thickness_mm'], cfg['contact_tolerance_mm'])
    data = {'model': model, 'panels': [mesh(s, cfg) for s in panels],
            'runs': report['runs'][-3:], 'refinement': report['refinement'], 'config': cfg}
    write(out/'viewer_data.json', data)
    web = MODULE/'three_d/web'
    html = (web/'local_collision_review.html').read_text(encoding='utf-8')
    for key, value in [('DATA', json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')),
                       ('CORE', (web/'core.js').read_text(encoding='utf-8')),
                       ('RENDERER', (web/'motion_renderer.js').read_text(encoding='utf-8')),
                       ('FINITE', (web/'finite_bend_core.js').read_text(encoding='utf-8')),
                       ('APP', (web/'local_collision_review.js').read_text(encoding='utf-8'))]:
        html = html.replace('/*__'+key+'__*/', value)
    (out/'review.html').write_text(html, encoding='utf-8')
    print(out/'review.html')


if __name__ == '__main__':
    main()
