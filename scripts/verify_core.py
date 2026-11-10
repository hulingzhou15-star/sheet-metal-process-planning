"""Verify protected facts, frozen H1-H7 plans and reference-free graph structure."""
from pathlib import Path
import dataclasses
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def clean(value):
    if dataclasses.is_dataclass(value):
        value = dataclasses.asdict(value)
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()
                if k not in {'source', 'source_path', 'source_sha256', 'metadata_sha256'}}
    if isinstance(value, (tuple, list)):
        return [clean(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(clean(v) for v in value)
    if isinstance(value, Path):
        return value.name
    return value


def digest(value):
    return hashlib.sha256(json.dumps(clean(value), ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def planning_snapshot(export_directory=None):
    from src.inference.input import load_inference_context
    from src.constraints.v06.hard_constraints import HardConstraintEngine
    from src.graph.builders import build_part_graph, build_baselines
    from src.graph.plan_overlay import build_plan_overlay
    context = load_inference_context(ROOT/'data/parts/P001/钣金加工数据表_P001.xlsx')
    plans = json.loads((ROOT/'baselines/P001/plans/all_16_complete_assignments.json').read_text(encoding='utf-8'))['plans']
    engine = HardConstraintEngine(context)
    graph = build_part_graph(context.part_data, k=8)
    if export_directory is not None:
        export_directory.mkdir(parents=True, exist_ok=True)
        (export_directory/'overlays').mkdir(exist_ok=True)
        (export_directory/'static_graph.json').write_text(json.dumps(
            {'schema_version':1,'part_id':'P001','graph':clean(graph)}, ensure_ascii=False,indent=2),encoding='utf-8')
    rows = []
    for plan in plans:
        result = engine.evaluate(plan['assignments'])
        if not result.feasible:
            raise ValueError((plan['plan_id'], result.to_dict()))
        overlay = build_plan_overlay(graph, plan['assignments'])
        if export_directory is not None:
            (export_directory/'overlays'/(plan['plan_id']+'.json')).write_text(json.dumps(
                {'schema_version':1,'plan_id':plan['plan_id'],'overlay':clean(overlay)},
                ensure_ascii=False,indent=2),encoding='utf-8')
        rows.append({'plan_id':plan['plan_id'], 'assignment_sha256':digest(plan['assignments']),
                     'overlay_sha256':digest(overlay),
                     'graphs':[{'level':g.level, 'edge_count':len(g.edges),
                                'edges_sha256':digest(g.edges), 'dynamic_edges_sha256':digest(g.plan_edge_features)}
                               for g in build_baselines(graph, overlay)]})
    return {'operation_count':len(context.part_data.operations), 'plan_count':len(plans),
            'facts_fingerprint':context.facts_fingerprint, 'static_graph_sha256':digest(graph), 'plans':rows}


def verify_protected():
    rows = json.loads((ROOT/'registry/protected_inputs.json').read_text(encoding='utf-8'))['files']
    for row in rows:
        p = ROOT/row['path']
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Protected input changed or missing: '+row['path'])
    return len(rows)


def main():
    count = verify_protected()
    snapshot = planning_snapshot(ROOT/'outputs/planning/P001')
    expected = json.loads((ROOT/'baselines/P001/planning_expected.json').read_text(encoding='utf-8'))
    if snapshot != expected:
        raise ValueError('Frozen facts/graphs/overlays changed')
    out = ROOT/'outputs/verification'; out.mkdir(parents=True, exist_ok=True)
    (out/'planning.json').write_text(json.dumps({'protected_files':count, 'successful':True, **snapshot},ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Protected {count} files; {snapshot["operation_count"]} actions, {snapshot["plan_count"]} legal plans; Graph0/1/2 unchanged',flush=True)


if __name__ == '__main__':
    main()
