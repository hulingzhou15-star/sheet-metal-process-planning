"""Fact-only fixed denominator; no candidate-dependent pruning."""
from collections import Counter
from copy import deepcopy
from fractions import Fraction

from src.inference.input import InferenceContext
from src.neighborhood.context import digest
from .config import validate_config

TYPE_FAMILIES = {frozenset(['FLANGING','IRREGULAR_PUNCH']): 'FLANGING_IRREGULAR',
                 frozenset(['BEND','FLANGING']): 'BEND_FLANGING',
                 frozenset(['BEND','IRREGULAR_PUNCH']): 'BEND_IRREGULAR'}


def build_catalogue(context, neighborhood, config):
    config = validate_config(config)
    if not isinstance(context, InferenceContext) or neighborhood.snapshot['facts_fingerprint'] != context.facts_fingerprint:
        raise ValueError('Expert-free matching facts required')
    included, excluded, seen, relevant = [], [], set(), Counter()
    for row in neighborhood.snapshot['relations']:
        key = row['pair_id']
        if key in seen: raise ValueError('Duplicate neighborhood pair')
        seen.add(key)
        a,b = row['operation_ids']
        if key != '__'.join(sorted((a,b))) or a == b: raise ValueError('Noncanonical pair')
        family = TYPE_FAMILIES.get(frozenset(context.operations[o].operation_type for o in (a,b)))
        arcs = row['arcs']
        if family == 'FLANGING_IRREGULAR':
            arcs = [r for r in arcs if context.operations[r['anchor']].operation_type == 'FLANGING']
        triggers = [r for r in arcs if r['tier'] == 'PRIORITY']
        reason = None
        if row['kind'] != 'EXTERNAL_NEIGHBOR': reason = 'internal_process_relation'
        elif family is None: reason = 'unscored_operation_combination'
        elif not triggers: reason = 'no_applicable_priority_arc'
        else:
            relevant[family] += 1
            if b in context.precedence_reachability[a] or a in context.precedence_reachability[b]: reason = 'hard_precedence_path'
            elif not ({s.station_id for s in context.allowed_stations(a)} & {s.station_id for s in context.allowed_stations(b)}): reason = 'disjoint_station_domains'
        record = {'pair_id': key, 'operation_ids': [a,b], 'family': family,
                  'boundary_distance': row['boundary_distance'], 'trigger_arcs': deepcopy(triggers),
                  'gates': deepcopy(row['gates']), 'existing_relations': deepcopy(row['relations'])}
        if reason:
            record['reason'] = reason
            if reason == 'hard_precedence_path':
                before,after = (a,b) if b in context.precedence_reachability[a] else (b,a)
                record['hard_path'] = context.precedence_path(before,after)
            excluded.append(record)
        else: included.append(record)
    counts = Counter(r['family'] for r in included)
    missing = sorted(o for o,r in neighborhood.snapshot['operations'].items() if not r['has_geometry'])
    body = {'schema_version': config['schema_version'], 'policy': config['policy'],
            'facts_fingerprint': context.facts_fingerprint, 'neighborhood_fingerprint': neighborhood.fingerprint,
            'pairs': sorted(included,key=lambda r:r['pair_id']), 'excluded': sorted(excluded,key=lambda r:r['pair_id']),
            'type_counts': dict(sorted(counts.items())), 'priority_type_counts_before_hard_exclusion': dict(relevant),
            'missing_geometry_operations': missing, 'coverage_status': 'coverage_incomplete' if missing else 'covered_for_declared_types',
            'unsupported_or_observation_only_types': sorted({o.operation_type for o in context.operations.values()}-{'FLANGING','BEND','IRREGULAR_PUNCH'}),
            'expert_used': False, 'interpretation': 'Experimental same-station preference, not collision truth or training labels'}
    return {**body, 'fingerprint': digest(body)}


def check_catalogue(context, catalogue):
    if not isinstance(context, InferenceContext) or catalogue['facts_fingerprint'] != context.facts_fingerprint:
        raise ValueError('Catalogue fact mismatch')
    if digest({k:v for k,v in catalogue.items() if k != 'fingerprint'}) != catalogue['fingerprint']:
        raise ValueError('Catalogue fingerprint mismatch')


def evaluate_p5(context, catalogue, assignments):
    check_catalogue(context,catalogue)
    by_type = {}
    for family,n in catalogue['type_counts'].items():
        rows = []
        for r in catalogue['pairs']:
            if r['family'] != family: continue
            a,b = r['operation_ids']
            rows.append({'pair_id':r['pair_id'], 'station_ids':[assignments[a],assignments[b]], 'same_station':assignments[a]==assignments[b]})
        count = sum(r['same_station'] for r in rows)
        by_type[family] = {'pair_count':n,'same_station_count':count,'value':count/n,'pairs':rows}
    value = sum((Fraction(r['same_station_count'],r['pair_count']) for r in by_type.values()),Fraction())/len(by_type) if by_type else Fraction()
    return {'value':float(value),'exact':str(value),'types':by_type,
            'same_station_count':sum(r['same_station_count'] for r in by_type.values()),
            'applicability':'applicable' if by_type else 'not_applicable','coverage_status':catalogue['coverage_status'],
            'missing_geometry_operations':catalogue['missing_geometry_operations']}
