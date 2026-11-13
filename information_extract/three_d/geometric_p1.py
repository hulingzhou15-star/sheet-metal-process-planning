"""Adopted P1: material-tree carried volume replaces Sheet5 affected area.

User adoption: 2026-11-13. Preference/ratio/weights retained; not a safety label.
Compiled geometry basis is independent of assignments and expert/Sheet5 values.
"""
from copy import deepcopy
from fractions import Fraction
from itertools import combinations
import math

from src.neighborhood.context import digest
from src.neighborhood_penalty.scoring import Scorer as BaselineScorer
from .allocation_policy import AllocationEngine, AllocationContext
from .allocation_metrics import MetricProbe

VERSION = 'geometry_p1.v1'


def geometry_tree(model):
    ids = [r['region_id'] for r in model['regions']]
    if len(set(ids)) != len(ids) or model['root_region_id'] not in ids:
        raise ValueError('Invalid material region identities')
    children = {r: [] for r in ids}
    parent, joints, operations = {}, set(), set()
    for b in model['bends']:
        a, z = b['parent_region_id'], b['child_region_id']
        if a not in children or z not in children or z in parent or z == model['root_region_id']:
            raise ValueError('Material graph must have one parent per non-root region')
        if b['joint_id'] in joints or b['operation_id'] in operations:
            raise ValueError('Multiple geometry joints per operation need explicit multi-step support')
        parent[z] = a; children[a].append(z)
        joints.add(b['joint_id']); operations.add(b['operation_id'])
    def subtree(start):
        seen, pending = set(), [start]
        while pending:
            r = pending.pop()
            if r in seen:
                raise ValueError('Cyclic material graph')
            seen.add(r); pending.extend(children[r])
        return seen
    if subtree(model['root_region_id']) != set(ids):
        raise ValueError('Disconnected material graph: cannot silently omit material')
    return subtree


def compile_basis(context, viewer, geometry_root):
    if not isinstance(context, AllocationContext):
        raise TypeError('Geometry P1 requires the no-H6 fact context')
    expected = {o.operation_id for o in context.operations.values() if o.operation_type == 'BEND'}
    if {b['operation_id'] for b in viewer['model']['bends']} != expected:
        raise ValueError('Geometric bend coverage differs from allocation facts')
    subtree = geometry_tree(viewer['model'])
    cuts = {o for m in viewer['variants'] for o in m['executed_removal_ids']}
    forms = {o for m in viewer['variants'] for o in m['executed_forming_ids']}
    finals = [m for m in viewer['variants'] if set(m['executed_removal_ids']) == cuts and set(m['executed_forming_ids']) == forms]
    if len(finals) != 1:
        raise ValueError('Unique common completed material required')
    final = finals[0]
    probe = MetricProbe(viewer, geometry_root)
    region_volumes = {r['region_id']: 0. for r in viewer['model']['regions']}
    component_ids = set()
    for c in final['components']:
        if c['kind'] != 'panel':
            continue
        if c['component_id'] in component_ids or c['region_id'] not in region_volumes:
            raise ValueError('Duplicate or unattached material component')
        component_ids.add(c['component_id'])
        probe.geometry(c['geometry_id'])
        volume = probe.mass[c['geometry_id']][0]
        if not math.isfinite(volume) or volume <= 0:
            raise ValueError('Invalid component volume')
        region_volumes[c['region_id']] += volume
    domains = []
    for b in viewer['model']['bends']:
        regions = subtree(b['child_region_id'])
        volume = math.fsum(region_volumes[r] for r in sorted(regions))
        if volume <= 0:
            raise ValueError('Empty carried material domain')
        domains.append(dict(operation_id=b['operation_id'], joint_id=b['joint_id'],
                            region_ids=sorted(regions), carried_volume_mm3=volume))
    body = dict(schema_version=VERSION, facts_fingerprint=context.facts_fingerprint,
        part_id=viewer['model']['part_id'], material_id=final['material_id'],
        domain_definition='Geometric child material subtree; NOT H7 operation descendants',
        reference_state='COMMON_COMPLETED_RIGID_MATERIAL_WITH_STEP_LOCAL_PATCHES',
        domains=sorted(domains, key=lambda d: d['operation_id']), region_volumes_mm3=region_volumes,
        metric='carried_volume_mm3', expert_used=False, sheet5_used=False,
        limitations=['Finite bend bands omitted', 'Not a force/time/collision/safety metric',
                     'Multi-step and curved-bend domains need explicit supported geometry'])
    return {**body, 'fingerprint': digest(body)}


def check_basis(context, basis):
    if not isinstance(context, AllocationContext) or basis['schema_version'] != VERSION:
        raise ValueError('Wrong geometry policy/context')
    if basis['fingerprint'] != digest({k:v for k,v in basis.items() if k != 'fingerprint'}):
        raise ValueError('Geometry basis fingerprint mismatch')
    if context.facts_fingerprint != basis['facts_fingerprint']:
        raise ValueError('Geometry basis belongs to different allocation facts')
    ids = [d['operation_id'] for d in basis['domains']]
    expected = {o.operation_id for o in context.operations.values() if o.operation_type == 'BEND'}
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise ValueError('Missing/duplicate bend material domains')
    if any(not math.isfinite(d['carried_volume_mm3']) or d['carried_volume_mm3'] <= 0 for d in basis['domains']):
        raise ValueError('Positive finite material volume required')


def ordering(context, assignments, basis, ratio=1.15):
    check_basis(context, basis)
    if not math.isfinite(ratio) or ratio <= 1:
        raise ValueError('Significance ratio must exceed one')
    values = {d['operation_id']: d['carried_volume_mm3'] for d in basis['domains']}
    comparable, excluded, insignificant = [], [], []
    for a, b in combinations(sorted(values), 2):
        if b in context.precedence_reachability[a] or a in context.precedence_reachability[b]:
            excluded.append([a, b]); continue
        large, small = (a,b) if values[a] >= values[b] else (b,a)
        if values[large]/values[small] <= ratio:
            insignificant.append([a,b]); continue
        sa, sb = assignments[large], assignments[small]
        comparable.append(dict(larger_operation_id=large, smaller_operation_id=small,
            larger_value=values[large], smaller_value=values[small], station_ids=[sa,sb],
            inversion=context.stations[sa].order < context.stations[sb].order, same_station=sa==sb))
    count = sum(r['inversion'] for r in comparable)
    return dict(metric='geometric_carried_material_volume', units='mm3', policy_version=VERSION,
        basis_fingerprint=basis['fingerprint'], significance_ratio=ratio, bend_count=len(values),
        comparable_pair_count=len(comparable), comparable_pairs=comparable,
        inversion_count=count, inversion_rate=count/len(comparable) if comparable else 0.,
        applicability='applicable' if comparable else 'no_comparable_pairs',
        excluded_pair_count=len(excluded), hard_excluded_pairs=excluded,
        insignificant_pairs=insignificant, status='ACTIVE_USER_ADOPTED', manufacturing_quality_label=False)


class GeometryScorer(BaselineScorer):
    """v0.2-compatible P5/P2/fragmentation with adopted geometric P1.

The inherited evaluator computes an empty legacy P1 on the no-Sheet5 context;
its slot is then replaced exactly, never combined with a hidden legacy penalty.
The optimizer receives this same geometric template through area_template.
"""
    def __init__(self, context, catalogue, config, basis):
        check_basis(context, basis)
        self.basis = deepcopy(basis)
        super().__init__(context, catalogue, config)
        seed = {o:context.allowed_stations(o)[0].station_id for o in context.operations}
        self.area_template = ordering(context, seed, self.basis, self.config['area_significance_ratio'])

    def evaluate(self, plan):
        a = AllocationEngine(self.context).require_feasible(plan)
        result = super().evaluate(a)
        if result['area_audit']['bend_count'] != 0 or Fraction(result['components']['area']['exact']):
            raise ValueError('Legacy area data leaked into geometry scoring')
        p1 = ordering(self.context, a, self.basis, self.config['area_significance_ratio'])
        value = Fraction(p1['inversion_count'], p1['comparable_pair_count']) if p1['comparable_pair_count'] else Fraction()
        weight = self.config['secondary_weights']['area']
        result['components']['area'] = dict(value=float(value), exact=str(value), weight=weight,
            weighted_contribution=float(value*Fraction(str(weight))), metric='geometric_carried_material_volume')
        secondary = sum((Fraction(c['exact'])*Fraction(str(c['weight'])) for c in result['components'].values()), Fraction())
        result.update(secondary=float(secondary), secondary_exact=str(secondary), area_audit=p1,
            scoring_version=VERSION, basis_fingerprint=self.basis['fingerprint'],
            legacy_slot_mapping={'area':'P1 geometric carried material volume'},
            active_hard_constraints=['H1','H2','H3','H4','H5','H7'], disabled_hard_constraints=['H6'])
        result['fragmentation']['bounds_source'] = 'H1-H5/H7, recomputed on no-H6 context'
        return result
