"""Display all selected complete plans without changing generation or selection."""
import argparse
import csv
from html import escape
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.inference.input import load_inference_context
from src.constraints.v06 import HardConstraintEngine
from src.candidate.distance import plan_hash
from src.graph.geometry_primitives import geometry_primitives,primitives_bbox
from src.visualization.geometry_adapter import primitive_shape
from src.visualization.svg_renderer import shape_markup,outline_markup
from src.visualization.outline_adapter import load_display_outline
from src.visualization.config import load_visualization_config
from scripts.run_neighborhood_v01 import write_json


def build(source,out):
    meta=json.loads((source/'run_metadata.json').read_text(encoding='utf-8'))
    context=load_inference_context(Path(meta['source_workbook']))
    if context.facts_fingerprint!=meta['facts_fingerprint']:raise ValueError('Frozen plans belong to different part facts')
    paths=[source/'selected_candidates.jsonl',source/'selection_report.json']
    selected=[json.loads(s) for s in paths[0].read_text(encoding='utf-8').splitlines() if s.strip()]
    selection=json.loads(paths[1].read_text(encoding='utf-8'));visual=load_visualization_config()
    if [r['plan_hash'] for r in selected]!=[r['plan_hash'] for r in selection['steps']]:raise ValueError('Selection order mismatch')
    reasons={r['plan_hash']:r['reason'] for r in selection['steps']}
    for r in selected:
        if not re.fullmatch(r'[A-Za-z0-9_-]+',r['plan_id']):raise ValueError('Unsafe plan filename')
        if plan_hash(r['assignments'])!=r['plan_hash'] or not HardConstraintEngine(context).evaluate(r['assignments']).feasible:raise ValueError('Invalid frozen plan')
    operations={};geometries={};hit={};boxes=[]
    for op in context.operations.values():
        geom=context.part_data.geometry_for(op)
        primitives=geometry_primitives(geom) if geom is not None else ()
        bbox=list(primitives_bbox(primitives)) if primitives else None
        operations[op.operation_id]={'operation_id':op.operation_id,'operation_type':op.operation_type,'has_geometry':bool(primitives),'bbox':bbox}
        if primitives:
            hit[op.operation_id]=[primitive_shape(p) for p in primitives]
            geometries[op.operation_id]=''.join(shape_markup(s) for s in hit[op.operation_id])
            x0,y0,x1,y1=bbox;boxes.append((x0,-y1,x1,-y0))
    part_id=next(iter(context.operations.values())).part_id
    outline,outline_audit=load_display_outline(visual,ROOT,part_id)
    if outline:boxes.append(outline['native_svg_bbox'])
    if not boxes:raise ValueError('No recoverable display geometry')
    x0=min(b[0] for b in boxes);y0=min(b[1] for b in boxes);x1=max(b[2] for b in boxes);y1=max(b[3] for b in boxes)
    pad=max(x1-x0,y1-y0)*.025
    full_view=[-y1-pad,x0-pad,y1-y0+2*pad,x1-x0+2*pad]
    stations=[{'station_id':s.station_id,'order':s.order,'color':visual['colors']['station_palette'][i%len(visual['colors']['station_palette'])]} for i,s in enumerate(context.ordered_stations)]
    colors={s['station_id']:s['color'] for s in stations}
    plans=[{'plan_id':r['plan_id'],'plan_hash':r['plan_hash'],'assignments':r['assignments'],'selection_reason':reasons[r['plan_hash']],
            'station_loads':{s['station_id']:sum(v==s['station_id'] for v in r['assignments'].values()) for s in stations}} for r in selected]
    payload={'schema_version':'selected_plan_gallery.v0.1','part_id':part_id,'plans':plans,'operations':operations,'stations':stations,
             'geometries':geometries,'hit_shapes':hit,'outline_markup':outline_markup({'display_outline':outline},visual),'full_view_box':full_view,
             'missing_geometry_ids':sorted(o for o,r in operations.items() if not r['has_geometry']),
             'display_rotation_degrees':270,'hit_radius_px':6,'minimum_focus_extent':24}
    encoded=json.dumps(payload,ensure_ascii=False,allow_nan=False).replace('<','\\u003c').replace('&','\\u0026')
    template=(ROOT/'src/visualization/selected_plan_gallery_v02.html').read_text(encoding='utf-8')
    (out/'selected_plans.html').write_text(template.replace('__PAYLOAD__',encoded),encoding='utf-8')
    write_json(out,'all_16_complete_assignments.json',{'part_id':part_id,'plans':plans,'operation_count':len(operations),'expert_used':False})
    with (out/'all_16_assignment_matrix.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['operation_id','operation_type','has_geometry',*[p['plan_id'] for p in plans]])
        for o in sorted(operations):writer.writerow([o,operations[o]['operation_type'],operations[o]['has_geometry'],*[p['assignments'][o] for p in plans]])
    svg_dir=out/'svg';svg_dir.mkdir(exist_ok=True)
    for p in plans:
        markup=[f'<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="800" viewBox="{" ".join(map(str,full_view))}"><title>{escape(p["plan_id"])} · 完整分配 · 270°</title>',
                '<style>.operation path,.operation line,.operation circle{fill:none;vector-effect:non-scaling-stroke;stroke-width:1.7}</style>',
                '<g transform="matrix(0 1 -1 0 0 0)">',payload['outline_markup']]
        for o,shape in geometries.items():markup.append(f'<g class="operation" data-operation="{escape(o)}" data-station="{escape(p["assignments"][o])}" stroke="{colors[p["assignments"][o]]}">{shape}</g>')
        markup.append('</g></svg>');(svg_dir/(p['plan_id']+'.svg')).write_text(''.join(markup),encoding='utf-8')
    write_json(out,'build_metadata.json',{'schema_version':payload['schema_version'],'facts_fingerprint':context.facts_fingerprint,
               'plans':len(plans),'operations_per_plan':len(operations),'geometry_operations_per_plan':len(geometries),'missing_geometry_ids':payload['missing_geometry_ids'],
               'source_files_sha256':{str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},'outline_audit':outline_audit,
               'plan_assignments_changed':False,'selection_order_changed':False,'expert_used':False})
    print(json.dumps({'plans':len(plans),'operations_per_plan':len(operations),'drawable_per_plan':len(geometries),'html':str(out/'selected_plans.html')},ensure_ascii=False))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=ROOT/'outputs/neighborhood_penalty_v02')
    p.add_argument('--output',type=Path,default=ROOT/'outputs/selected_plans_v02');args=p.parse_args()
    out=args.output.resolve()
    if not out.is_relative_to(ROOT/'outputs') or out==ROOT/'outputs' or out==args.source.resolve():raise ValueError('Separate output directory required')
    out.mkdir(exist_ok=True,parents=True)
    if (out/'build_metadata.json').exists():
        if json.loads((out/'build_metadata.json').read_text(encoding='utf-8'))['schema_version']!='selected_plan_gallery.v0.1':raise ValueError('Foreign output owner')
    elif any(f.name!='baseline_manifest.json' for f in out.iterdir()):raise ValueError('Unrelated output files')
    build(args.source,out)


if __name__=='__main__':main()
