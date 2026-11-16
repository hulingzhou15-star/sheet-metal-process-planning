"""Seven-class planning facts from annotations; no expert or Sheet5 adapter.

Geometry stays in a path-aware sidecar. The context here is for hard feasibility;
it must not be passed as a geometry-complete input to the old P5 scorer.
"""
from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict

from src.data.schema import Operation, Station, ProcessGroup
from src.inference.input import InferenceFacts
from src.neighborhood.context import digest
from .allocation_policy import AllocationContext, AllocationEngine
from ..extract_app.process_config import CAPABILITY_TYPES, PUNCH_TYPES, expanded_capabilities, tokens
from ..extract_app.allocation_constraints import add_not_later_constraints, check_not_later_constraints

OP_FIELDS=('operation_id','name','operation_type','surface_side','required_predecessor_ids',
    'cad_parent_operation_id','process_group_id','feature_type','geometry_shape','bend_angle_deg',
    'source_segment_ids','primary_source_segment_ids','shared_source_segment_ids',
    'composite_operation_ids','composite_policy','superseded_by_operation_id','not_later_than_operation_ids')
SEG_FIELDS=('id','segment_type','start_x','start_y','end_x','end_y','center_x','center_y','radius','sweep_angle')
KINDS=(set(CAPABILITY_TYPES)-{'PUNCH'})|set(PUNCH_TYPES)


def extract_facts(project,drawing):
    if project.get('planning_scope')!='seven_classes_no_trim_v1':
        raise ValueError('Explicit seven-class/no-trim scope required')
    raw=[{k:deepcopy(o[k]) for k in OP_FIELDS if k in o} for o in project['operations']]
    ids=[o['operation_id'] for o in raw]
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate operation id')
    all_ops={o['operation_id']:o for o in raw}
    active=[o for o in raw if not o.get('superseded_by_operation_id') and o['operation_type']!='TRIM']
    active_ids={o['operation_id'] for o in active}
    for o in raw:
        parent=o.get('superseded_by_operation_id')
        if parent and (parent not in active_ids or o['operation_id'] not in all_ops[parent].get('composite_operation_ids',[])):
            raise ValueError('Invalid composite membership')
    index={s['id']:s for s in drawing['segments']};used=set()
    relations=[]
    for o in active:
        if o['operation_type'] not in KINDS:raise ValueError('Unknown operation type')
        refs=set(tokens(o.get('required_predecessor_ids')))|set(tokens(o.get('not_later_than_operation_ids')))
        if o.get('cad_parent_operation_id'):refs.add(o['cad_parent_operation_id'])
        if not refs<=active_ids or o['operation_id'] in refs:raise ValueError('Active relation references excluded/missing/self action')
        bindings=set(o.get('source_segment_ids',[]))
        if not bindings or not bindings<=index.keys():raise ValueError('Missing source geometry: '+o['operation_id'])
        used.update(bindings)
        members=o.get('composite_operation_ids',[])
        if members:
            if len(members)!=len(set(members)) or any(m not in all_ops or all_ops[m].get('superseded_by_operation_id')!=o['operation_id'] for m in members):
                raise ValueError('Invalid composite action')
            expected={s for m in members for s in all_ops[m]['source_segment_ids']}
            if o.get('composite_policy')=='INTEGRATED_DRAW_FORM':
                if o['operation_type']!='DRAW_FORM' or any(all_ops[m]['operation_type'] not in PUNCH_TYPES for m in members):raise ValueError('Invalid integrated draw holes')
                expected.update(o.get('primary_source_segment_ids',[]))
            elif o.get('feature_type')!='BEND_Z' or len(members)!=2:raise ValueError('Unsupported composite semantics')
            if bindings!=expected:raise ValueError('Composite geometry coverage differs')
        relations.extend(dict(operation_id=o['operation_id'],not_later_than=x) for x in tokens(o.get('not_later_than_operation_ids')))
    stations=[]
    for s in project['station_rows']:
        allowed=expanded_capabilities(s['allowed_operation_types'],omit_trim=True)
        allowed=[] if allowed==['NONE'] else allowed
        if not set(allowed)<=KINDS:raise ValueError('Unknown station capability')
        stations.append(dict(station_id=s['station_id'],order=s['order'],allowed_operation_types=sorted(allowed)))
    body=dict(schema_version='project_allocation_facts.v1',part_id=project['part_id'],planning_scope=project['planning_scope'],
        operations=sorted(active,key=lambda x:x['operation_id']),stations=sorted(stations,key=lambda x:x['order']),
        source_segments=[{k:deepcopy(index[s][k]) for k in SEG_FIELDS if k in index[s]} for s in sorted(used)],
        excluded_operations=[dict(operation_id=o['operation_id'],reason='SUPERSEDED' if o.get('superseded_by_operation_id') else 'TRIM',
            superseded_by_operation_id=o.get('superseded_by_operation_id')) for o in raw if o['operation_id'] not in active_ids],
        not_later_relations=sorted(relations,key=lambda r:(r['operation_id'],r['not_later_than'])),expert_used=False,sheet5_used=False,
        geometry_status='SOURCE_BINDINGS_PRESERVED_NOT_YET_MATERIAL_DOMAINS')
    return {**body,'fingerprint':digest(body)}


def hard_context(facts):
    if facts['fingerprint']!=digest({k:v for k,v in facts.items() if k!='fingerprint'}):raise ValueError('Fact fingerprint mismatch')
    part=facts['part_id'];groups=defaultdict(list);ops=[]
    for o in facts['operations']:
        ops.append(Operation(part,o['operation_id'],o['operation_type'],o.get('name'),o.get('surface_side') or 'NOT_APPLICABLE',
            tuple(tokens(o.get('required_predecessor_ids'))),o.get('cad_parent_operation_id') or None,o.get('process_group_id') or None))
        if o.get('process_group_id'):groups[o['process_group_id']].append(o['operation_id'])
    stations=tuple(Station(part,s['station_id'],s['order'],frozenset(s['allowed_operation_types'])) for s in facts['stations'])
    data=InferenceFacts(tuple(ops),(),stations,tuple(ProcessGroup(part,g,tuple(ids)) for g,ids in sorted(groups.items())))
    return AllocationContext(data,(),(),facts['fingerprint'])


def solve_hard(facts):
    from ortools.sat.python import cp_model
    from src.constraints.v06.feasibility import build_feasibility_model
    context=hard_context(facts);model,ranks=build_feasibility_model(context)
    add_not_later_constraints(model,ranks,facts['not_later_relations'])
    solver=cp_model.CpSolver();solver.parameters.num_search_workers=1;solver.parameters.max_time_in_seconds=30
    solver.parameters.random_seed=20261113;status=solver.solve(model)
    result=dict(status=solver.status_name(status),feasible=False,kind='HARD_WITNESS_ONLY_NOT_RANKED_CANDIDATE',expert_used=False)
    if status in (cp_model.OPTIMAL,cp_model.FEASIBLE):
        a={o:context.ordered_stations[solver.value(rank)].station_id for o,rank in ranks.items()}
        hard=AllocationEngine(context).evaluate(a).to_dict()
        extra=check_not_later_constraints(a,{s.station_id:s.order for s in context.ordered_stations},facts['not_later_relations'])
        if not hard['feasible'] or not extra['feasible']:raise ValueError('Solver witness failed independent validation')
        result.update(feasible=True,assignments=a,hard=hard,additional_relations=extra)
    return context,result
