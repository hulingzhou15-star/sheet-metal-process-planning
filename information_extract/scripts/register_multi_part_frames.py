"""Diagnostic base-frame proposals from saved circular punches; no moving domains."""
import json
from pathlib import Path
from information_extract.extract_app.drawing import load_drawing
from information_extract.three_d.association import register
from information_extract.three_d.facts import sha256

ROOT=Path(__file__).resolve().parents[2]
MODULE=ROOT/'information_extract'


def main():
    cfg=json.loads((MODULE/'configs/multi_part_step_v01.json').read_text(encoding='utf-8'))
    for part,source in cfg['parts'].items():
        folder=MODULE/f'outputs/multi_part_step_v01/{part}'
        asset=json.loads((folder/'asset.json').read_text(encoding='utf-8'))
        if asset['asset_id']!=source['sha256'] or sha256(MODULE/source['step'])!=source['sha256']:
            raise ValueError(f'{part}: stale asset/source')
        project_path=MODULE/f'projects/{part}/project.json'
        project=json.loads(project_path.read_text(encoding='utf-8'))
        drawing_cfg=json.loads((MODULE/f'configs/{part}.json').read_text(encoding='utf-8'))
        drawing=load_drawing(MODULE/drawing_cfg['source_file'],drawing_cfg)
        if drawing['source']['sha256']!=project['source_sha256']:raise ValueError('Stale annotation')
        segments={s['id']:s for s in drawing['segments']}
        # Only explicitly annotated circular punches qualify. In particular,
        # P003 O112 is NOT an anchor: its saved type and discrepancy stay intact.
        operations=[{'operation_id':o['operation_id'],'operation_type':'CIRCLE_PUNCH',
                     'segments':[segments[s] for s in o['source_segment_ids']]}
                    for o in project['operations'] if o.get('feature_type')=='CIRCLE_PUNCH']
        result=register({'operations':operations},asset,{'registration':cfg['registration']})
        result.update(part_id=part,annotation_sha256=sha256(project_path),
                      dxf_sha256=drawing['source']['sha256'],step_sha256=asset['asset_id'],
                      eligible_circular_punch_count=len(operations),
                      policy=cfg['registration'],scope_limit='Largest horizontal face and XY quarter-turn/reflection search only; no general unfolding, no operation mapping confirmation.')
        (folder/'base_registration.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(part,result['status'],'anchors',result.get('anchor_count',0),'runner_up',result.get('runner_up_anchor_count',0),flush=True)


if __name__=='__main__':main()
