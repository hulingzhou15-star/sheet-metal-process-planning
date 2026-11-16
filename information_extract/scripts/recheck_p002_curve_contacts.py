"""Check representative flagged poses using faceted or original BRep solids."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path

import numpy as np

from information_extract.scripts.check_p002_curve_motion import DEST
from information_extract.three_d.artifacts import read,write
from information_extract.three_d.band_material_solids import triangle_solid
from information_extract.three_d.local_collision import transformed
from information_extract.three_d.local_forming import load_shape
from information_extract.three_d.panel_collision import narrow_pair
from information_extract.three_d.p002_motion_reference import CHECK_CONFIG


def recheck(folder,limit=None):
    plans = read(folder/'plans.json')['plans']
    candidates = {}
    for plan in plans:
        pid = plan['plan_id']
        for state in read(folder/pid/'collision_report.json')['runs']:
            for pair in state['reference_geometry']['narrow_phase_pairs']:
                if pair['status'] not in {'MESH_CROSSING','MESH_CONTAINMENT','OVERLAP'}:
                    continue
                a,b = pair['region_ids']
                gids = state['geometry_ids']
                ma,mb = [np.asarray(state['transforms'][cid]) for cid in (a,b)]
                key = (gids[a],gids[b],*np.round(np.linalg.inv(ma)@mb,10).flat)
                if key not in candidates:
                    candidates[key] = dict(status=pair['status'],cells=[a,b],sample_count=0,
                        first=[pid,state['station_id'],state['phase'],state['tau']],poses=[ma,mb])
                candidates[key]['sample_count'] += 1
    shape_cache = {}
    results = []
    for key,issue in list(candidates.items())[:limit]:
        shapes = []
        methods = []
        for gid, pose in zip(key[:2],issue['poses']):
            path = folder/'brep'/f'{gid}.brep'
            method = 'ORIGINAL_MATERIAL_BREP' if path.is_file() else 'FACETED_SWEPT_SOLID'
            methods.append(method)
            if gid not in shape_cache:
                if path.is_file():
                    shape_cache[gid] = load_shape(path)
                else:
                    with gzip.open(folder/'meshes'/f'{gid}.json.gz','rt',encoding='utf8') as stream:
                        shape_cache[gid] = triangle_solid(json.load(stream)['triangles'])
            shapes.append(transformed(shape_cache[gid],pose))
        try:
            result = narrow_pair(*shapes,CHECK_CONFIG)
        except Exception as exc:
            result = dict(status='UNKNOWN_KERNEL_FAILURE',message=str(exc))
        results.append(dict(issue={k:v for k,v in issue.items() if k!='poses'},
                            geometry_ids=key[:2],construction=methods,result=result))
        print(issue['cells'],issue['first'],result,flush=True)
    counts = Counter(r['result']['status'] for r in results)
    write(folder/'representative_contacts.json',dict(results=results,counts=dict(counts),
        checked_pair_states=sum(r['issue']['sample_count'] for r in results),
        unique_flagged_geometries_and_relative_poses=len(candidates),
        scope='One OCCT comparison for each distinct flagged material/pose, using faceted geometry where swept'))


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=DEST)
    parser.add_argument('--limit',type=int)
    a = parser.parse_args()
    recheck(a.input.resolve(),a.limit)
