"""Path-aware seven-class adapter for the adopted geometric allocation policy.

Frozen scorer/model implementations remain unchanged. Curved references are
analytic mid-curves of paired source traces; Z references remain two paths.
"""
from collections import defaultdict
from copy import deepcopy
from dataclasses import replace, asdict
from itertools import combinations
import math

from src.data.schema import Geometry, GeometrySegment
from src.graph.geometry_primitives import Line, Arc, geometry_primitives, primitives_bbox
from src.graph.geometry_distance import primitives_distance
from src.proximity_review.witness import project_point, point_on_arc, closest_witness
from src.proximity_review.analysis import family_for, existing_relations
from scripts.try_scale_aware_proximity import circular_profile
from src.neighborhood.context import digest, validate_config as validate_neighborhood
from src.neighborhood_penalty.config import validate_config
from src.neighborhood_penalty.catalogue import check_catalogue
from src.constraints.v06.soft_priors import precedence_extra_gap
from .artifacts import ROOT, sha
from .allocation_policy import AllocationContext
from .project_allocation_facts import hard_context, KINDS
from .geometric_p1 import GeometryScorer, check_basis, ordering, VERSION


def segment(row,seq):
    fields=GeometrySegment.__dataclass_fields__
    return GeometrySegment(**{**{k:v for k,v in row.items() if k in fields},'seq':seq})


def mid_reference(a,b,tolerance=1e-6,max_width=.8):
    """Return a finite parallel/concentric mid-curve or reject the pairing."""
    if isinstance(a,Line) and isinstance(b,Line):
        reverse=math.dist(a.start,b.end)+math.dist(a.end,b.start)<math.dist(a.start,b.start)+math.dist(a.end,b.end)
        bs,be=(b.end,b.start) if reverse else (b.start,b.end)
        d=tuple(x-y for x,y in zip(bs,a.start));e=tuple(x-y for x,y in zip(be,a.end))
        v=tuple(x-y for x,y in zip(a.end,a.start));length=math.hypot(*v)
        if not length or math.dist(d,e)>tolerance or abs(sum(x*y for x,y in zip(d,v)))/length>tolerance:return None
        width=math.hypot(*d)
        if not tolerance<width<=max_width+tolerance:return None
        return Line(tuple((x+y)/2 for x,y in zip(a.start,bs)),tuple((x+y)/2 for x,y in zip(a.end,be)))
    if isinstance(a,Arc) and isinstance(b,Arc):
        if math.dist(a.center,b.center)>tolerance or not tolerance<abs(a.radius-b.radius)<=max_width+tolerance:return None
        if a.sweep*b.sweep<0:b=Arc(b.center,b.radius,b.start_angle+b.sweep,-b.sweep)
        delta=math.atan2(math.sin(a.start_angle-b.start_angle),math.cos(a.start_angle-b.start_angle))
        if abs(delta)>tolerance or abs(a.sweep-b.sweep)>tolerance:return None
        return Arc(tuple((x+y)/2 for x,y in zip(a.center,b.center)),(a.radius+b.radius)/2,a.start_angle,a.sweep)
    return None


def primitive_segment(p,seq):
    if isinstance(p,Line):return GeometrySegment(seq,'LINE',*p.start,*p.end)
    return GeometrySegment(seq,'ARC',*p.endpoints[0],*p.endpoints[1],*p.center,p.radius,math.degrees(p.sweep))


def curved_references(source_segments,matched_ids):
    primitives={s['id']:geometry_primitives(Geometry('', '',(segment(s,1),)))[0] for s in source_segments}
    if not set(matched_ids)<=primitives.keys() or len(primitives)!=2*len(matched_ids):raise ValueError('Incomplete curved trace pairs')
    other=set(primitives)-set(matched_ids);result=[];proof=[]
    for sid in sorted(matched_ids):
        matches=[(t,mid_reference(primitives[sid],primitives[t])) for t in sorted(other)]
        matches=[(t,p) for t,p in matches if p is not None]
        if len(matches)!=1:raise ValueError('Ambiguous or unmatched curved trace: '+sid)
        t,p=matches[0];other.remove(t);result.append(primitive_segment(p,len(result)+1))
        proof.append(dict(source_segment_ids=[sid,t],method='PARALLEL_OR_CONCENTRIC_MID_CURVE'))
    if other:raise ValueError('Unconsumed curve sources')
    return result,proof


def geometry_context(facts,paths,sides):
    base=hard_context(facts)
    if facts['not_later_relations']:raise ValueError('Generation adapter does not yet support additional non-strict relations')
    if set(paths)!=set(base.operations):raise ValueError('Path operation coverage mismatch')
    sources={s['id']:s for s in facts['source_segments']};raw={o['operation_id']:o for o in facts['operations']}
    curves={r['operation_id']:r for r in sides['rows']};geometries=[];ops=[];audit={}
    for oid,o in sorted(base.operations.items()):
        record=paths[oid];refs=record['paths']
        if oid in curves:
            segs,proof=curved_references([sources[s] for s in raw[oid]['source_segment_ids']],
                [r['source_segment_id'] for r in curves[oid]['matches']])
            audit[oid]=dict(kind='CURVED_MID_REFERENCE',pairs=proof)
        else:
            if o.operation_type=='BEND' and any(p['role']!='BEND_REFERENCE' for p in refs):raise ValueError('Unresolved bend reference '+oid)
            segs=[segment(s,i+1) for i,s in enumerate(s for p in refs for s in p['segments'])]
            audit[oid]=dict(kind='INDEPENDENT_PATH_UNION',path_count=len(refs),roles=[p['role'] for p in refs],
                implicit_connectors=False,component_ids=[p.get('component_operation_id') for p in refs])
        if not segs:raise ValueError('Missing geometry '+oid)
        g=Geometry(facts['part_id'],'G_'+oid,tuple(segs));geometry_primitives(g)
        geometries.append(g);ops.append(replace(o,geometry_id=g.geometry_id))
    data=replace(base.part_data,operations=tuple(ops),geometries=tuple(geometries))
    return AllocationContext(data,(),(),facts['fingerprint']),audit


def point_to_reference(point,primitives):
    choices=[]
    for i,p in enumerate(primitives):
        q=project_point(point,p)['point'] if isinstance(p,Line) else point_on_arc(point,p)
        choices.append((math.dist(point,q),i,q))
    distance,index,q=min(choices)
    return dict(measurement=distance,point_from=point,point_to=q,primitive_index=index,
                primitive_count=len(primitives),implicit_connectors=False)


def recorded_circle_profile(primitives,tolerance=1e-7):
    exact=circular_profile(primitives)
    if exact is not None:return {**exact,'center_residual_mm':0.}
    if not primitives or not all(isinstance(p,Arc) and p.full_circle and p.radius>0 for p in primitives):return None
    center=tuple(math.fsum(p.center[i] for p in primitives)/len(primitives) for i in (0,1))
    residual=max(math.dist(p.center,center) for p in primitives)
    if residual>tolerance:return None
    return dict(center=list(center),diameter=2*max(p.radius for p in primitives),
        recorded_radii=sorted({p.radius for p in primitives}),center_residual_mm=residual,
        numeric_center_tolerance_mm=tolerance)


def measure_paths(context,config,progress=None):
    """Same gates/thresholds as v0.1, extending finite lines to finite path unions."""
    rules=validate_neighborhood(config)['rules'];ids=sorted(context.operations)
    shapes={o:geometry_primitives(context.part_data.geometry_for(context.operations[o])) for o in ids}
    profiles={o:recorded_circle_profile(p) for o,p in shapes.items() if context.operations[o].operation_type in rules['circular_types']}
    if any(v is None for v in profiles.values()):raise ValueError('Unsupported noncircular scale operation')
    direct=defaultdict(list)
    for r in context.precedence_relations:
        direct[r.predecessor_id,r.operation_id].append(dict(before=r.predecessor_id,after=r.operation_id,source=r.relation_source,origin=r.origin_ref))
    rows=[];max_residual=0.
    for i,(a,b) in enumerate(combinations(ids,2),1):
        oa,ob=context.operations[a],context.operations[b];pa,pb=shapes[a],shapes[b]
        distance=primitives_distance(pa,pb);witness=closest_witness(pa,pb)
        residual=abs(witness['distance']-distance);max_residual=max(max_residual,residual)
        if residual>rules['numeric_witness_tolerance']:raise ValueError('Distance/witness mismatch')
        family=family_for(oa.operation_type,ob.operation_type,rules['circular_types']);gates=[]
        if family in ('BEND_CIRCULAR','IRREGULAR_CIRCULAR','CIRCULAR_CIRCULAR'):
            for circle in [o for o in (a,b) if o in profiles and (family!='CIRCULAR_CIRCULAR' or context.operations[o].operation_type=='FLANGING')]:
                other=b if circle==a else a;profile=profiles[circle]
                g=dict(scale_operation=circle,diameter=profile['diameter'],center=profile['center'])
                if family=='BEND_CIRCULAR':g.update(metric='CENTER_TO_FINITE_BEND',**point_to_reference(profile['center'],shapes[other]))
                elif family=='CIRCULAR_CIRCULAR':g.update(metric='CENTER_TO_CENTER',measurement=math.dist(profile['center'],profiles[other]['center']))
                else:g.update(metric='RECORDED_BOUNDARY',measurement=distance)
                gates.append(g)
        rows.append(dict(pair_id=a+'__'+b,a=a,b=b,type_a=oa.operation_type,type_b=ob.operation_type,family=family,
            boundary_distance=distance,boundary_witness=witness,curve_contact=round(distance,9)==0,
            relations=existing_relations(a,b,context.operations,direct),gates=gates))
        if progress and i%2000==0:progress(i)
    return dict(source=dict(facts_fingerprint=context.facts_fingerprint),expert_assignments_used=False,
        operations={o:dict(has_geometry=True,bbox=primitives_bbox(p)) for o,p in shapes.items()},pairs=rows,
        geometry_adapter='finite_path_union.v1',numeric_max_witness_residual=max_residual,circular_profiles=profiles,
        policy='Unchanged finite-distance gates; no new P5 family or threshold',manufacturing_safety_proved=False)


def compile_cell_basis(context,cells):
    for p,h in cells['inputs'].items():
        if sha(ROOT/p)!=h:raise ValueError('Material source or BREP changed: '+p)
    if not cells['all_partition_faces_owned_by_solids'] or cells['cell_sum_relative_error']>1e-5:raise ValueError('Inconsistent material partition')
    domains=deepcopy(cells['domains']);volumes={c['cell_id']:c['volume_mm3'] for c in cells['cells']}
    sets={d['operation_id']:set(d['cell_ids']) for d in domains}
    if len(volumes)!=len(cells['cells']) or len(sets)!=len(domains):raise ValueError('Duplicate material identity')
    if any(not s or not s<=volumes.keys() for s in sets.values()):raise ValueError('Missing domain cell')
    for a,b in combinations(sets,2):
        x,y=sets[a],sets[b]
        if x==y or (x&y and not(x<y or y<x)):raise ValueError('Material domains do not form a unique nested tree')
    parents={o:min((x for x in sets if sets[o]<sets[x]),key=lambda x:len(sets[x]),default='ROOT') for o in sets}
    all_sets={**sets,'ROOT':set(volumes)};tree=[]
    for o,s in all_sets.items():
        children=sorted(x for x,p in parents.items() if p==o)
        owned=s-set().union(*(sets[x] for x in children))
        if not owned:raise ValueError('Material tree node has no own body')
        tree.append(dict(node_id=o,parent_id=parents.get(o),children=children,owned_cell_ids=sorted(owned)))
    for d in domains:
        v=math.fsum(volumes[c] for c in d['cell_ids'])
        if not math.isfinite(v) or v<=0 or abs(v-d['carried_volume_mm3'])>1e-8:raise ValueError('Domain volume mismatch')
    body=dict(schema_version=VERSION,part_id='P002',facts_fingerprint=context.facts_fingerprint,
        domains=domains,material_tree=tree,region_volumes_mm3=volumes,material_id=cells['step_sha256'],
        domain_definition='Geometric carried STEP cell subtree, independent of H7; curved walls grouped, Z constituent cells unioned once',
        reference_state='COMMON_COMPLETED_STEP_WITH_FINITE_BEND_BANDS_REMOVED',metric='carried_volume_mm3',
        cell_partition_fingerprint=digest(cells),correspondence=cells['correspondence'],expert_used=False,sheet5_used=False,
        limitations=['No unfolding/trajectory is inferred from grouped curve/Z domains','Finite bend bands omitted','Not a manufacturing safety proof'])
    basis={**body,'fingerprint':digest(body)};check_basis(context,basis);return basis


class ProjectGeometryScorer(GeometryScorer):
    """Explicit DRAW_FORM group extension; reuse exact evaluate/bounds/CP code."""
    def __init__(self,context,catalogue,config,basis):
        check_basis(context,basis);check_catalogue(context,catalogue)
        self.context,self.catalogue,self.config=context,catalogue,validate_config(config)
        if not {o.operation_type for o in context.operations.values()}<=KINDS:raise ValueError('Unknown seven-class action')
        self.config['fragmentation_types']=[t for t in self.config['fragmentation_types'] if t!='TRIM']+['DRAW_FORM']
        for o in context.operations.values():
            if o.operation_type=='BEND' and o.surface_side not in self.config['bend_directions']:raise ValueError('Unresolved bend side')
        self.basis=deepcopy(basis);self._bounds=None
        seed={o:context.allowed_stations(o)[0].station_id for o in context.operations}
        self.area_template=ordering(context,seed,basis,self.config['area_significance_ratio'])
        self.gap_template=precedence_extra_gap(context,self._topological_template())

    def evaluate(self,plan):
        result=super().evaluate(plan)
        result['project_adapter']='seven_classes_finite_paths.v1'
        result['fragmentation']['group_extension']='DRAW_FORM as its own type; TRIM excluded'
        return result
