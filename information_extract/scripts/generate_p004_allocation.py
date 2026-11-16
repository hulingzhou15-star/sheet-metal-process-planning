"""Prepare the P004 scoring inputs, then generate and select a reference-free pool."""
import argparse
from collections import Counter
from pathlib import Path

from information_extract.extract_app.drawing import load_drawing
from information_extract.extract_app.workflow import compile_operation
from information_extract.scripts.experiment_bend_allocation_3d import protected_hashes
from information_extract.three_d.allocation_policy import AllocationEngine
from information_extract.three_d.artifacts import ROOT, read, source_manifest, verify_sources, write
from information_extract.three_d.project_allocation_facts import extract_facts, solve_hard
from information_extract.three_d.project_geometry_scoring import ProjectGeometryScorer, geometry_context, measure_paths
from src.neighborhood import build_neighborhood, load_config as load_neighborhood
from src.neighborhood_penalty import build_catalogue, load_config
from src.neighborhood_penalty.generation import establish_primary, generate_candidates, ranking_key, select_candidates

MODULE = ROOT/'information_extract'
PREP = MODULE/'outputs/p004_generation_v01'
OUT = MODULE/'outputs/geometry_allocation_v1_generated/P004'


def prepare():
    project_path = MODULE/'projects/P004/project.json'
    drawing_config_path = MODULE/'configs/P004.json'
    drawing_config = read(drawing_config_path)
    project = read(project_path)
    dxf = MODULE/drawing_config['source_file']
    sources = source_manifest([
        project_path, drawing_config_path, dxf,
        *(MODULE/drawing_config[key] for key in ('confirmed_geometry_supplement',
            'confirmed_geometry_correction','confirmed_geometry_revision')),
        ROOT/'configs/neighborhood_penalty_v02.json',
        Path(__file__),
    ])
    drawing = load_drawing(dxf, drawing_config)
    if drawing['source']['sha256'] != project['source_sha256']:
        raise ValueError('P004 project and compiled drawing refer to different sources')
    facts = extract_facts(project, drawing)
    hard, witness = solve_hard(facts)
    if not witness['feasible'] or facts['not_later_relations']:
        raise ValueError('Expected feasible P004 facts without additional relation types')
    segments = {row['id']:row for row in drawing['segments']}
    paths = {}
    for action in facts['operations']:
        compiled = compile_operation(action, [segments[s] for s in action['source_segment_ids']], drawing_config)
        paths[action['operation_id']] = dict(feature_type=action['feature_type'], paths=compiled['paths'])
    context, audit = geometry_context(facts, paths, {'rows':[]})
    config = load_config()
    neighborhood_path = ROOT/config['neighborhood_config']
    sources.update(source_manifest([neighborhood_path]))
    neighborhood_config = load_neighborhood(neighborhood_path)
    evidence = measure_paths(context, neighborhood_config, progress=lambda n:print('GEOMETRY', n, flush=True))
    neighborhood = build_neighborhood(context, neighborhood_config, evidence=evidence)
    catalogue = build_catalogue(context, neighborhood, config)
    if catalogue['missing_geometry_operations']:
        raise ValueError('Incomplete P004 scoring geometry')
    primary = establish_primary(context, catalogue, config)
    if not primary['generation_allowed']:
        raise ValueError('P004 P5 optimum is unresolved')
    verify_sources(sources)
    for name, payload in dict(facts=facts, geometry_paths=paths, path_audit=audit, hard_witness=witness,
                              geometry_evidence=evidence, neighborhood=neighborhood.to_dict(),
                              catalogue=catalogue, primary=primary,
                              scoring_inputs=dict(files=sources, source_revision=project['revision'],
                                  hard_constraint_counts=dict(Counter(r.constraint_type for r in hard.precedence_relations)),
                                  facts_fingerprint=context.facts_fingerprint)).items():
        write(OUT/f'{name}.json',payload)
    print('P004 revision',project['revision'],len(facts['operations']),'actions; P5',primary['minimum_exact'],flush=True)


def load_scoring():
    scoring_inputs = read(OUT/'scoring_inputs.json')
    basis_inputs = read(PREP/'inputs.json')
    verify_sources(scoring_inputs['files'])
    verify_sources(basis_inputs['files'])
    facts = read(OUT/'facts.json')
    paths = read(OUT/'geometry_paths.json')
    if facts!=read(PREP/'facts.json') or paths!=read(PREP/'geometry_paths.json'):
        raise ValueError('Scoring and STEP geometry use different annotated paths')
    context, _ = geometry_context(facts, paths, {'rows':[]})
    config = load_config()
    catalogue = read(OUT/'catalogue.json')
    primary = read(OUT/'primary.json')
    basis = read(PREP/'material_basis.json')
    return context, catalogue, config, primary, ProjectGeometryScorer(context,catalogue,config,basis)


def generate():
    protection = protected_hashes()
    context, catalogue, config, primary, scorer = load_scoring()
    pool, generation = generate_candidates(context,catalogue,config,primary,scorer,
        progress=lambda p:print('CANDIDATES',p['pool'],p['channel'],p['status'],flush=True))
    if len(pool)!=config['generation']['pool_target'] or len({p['plan_hash'] for p in pool})!=len(pool):
        raise ValueError('Incomplete or repeated P004 generation pool')
    engine = AllocationEngine(context)
    for plan in pool:
        engine.require_feasible(plan['assignments'])
    selected, selection = select_candidates(context,pool,config)
    sources = {**read(OUT/'scoring_inputs.json')['files'],**read(PREP/'inputs.json')['files']}
    sources.update(source_manifest([OUT/'catalogue.json',OUT/'primary.json',PREP/'material_basis.json']))
    verify_sources(sources)
    if protected_hashes()!=protection:
        raise ValueError('Protected input changed during candidate generation')
    write(OUT/'inputs.json',dict(files=sources))
    write(OUT/'pool.json',dict(part_id='P004',plans=pool))
    write(OUT/'plans.json',dict(part_id='P004',plans=selected,
        ranking=[p['plan_id'] for p in sorted(selected,key=ranking_key)],selection=selection))
    write(OUT/'generation.json',generation)
    print('P004',len(pool),'distinct candidates;',len(selected),'selected',flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only',action='store_true')
    args = parser.parse_args()
    if args.prepare_only or not (OUT/'scoring_inputs.json').exists():
        prepare()
    if not args.prepare_only:
        generate()
