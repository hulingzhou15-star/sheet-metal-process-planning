"""Explicit per-project constraints layered onto the unchanged H1-H7 model.

These facts contain no expert assignment. An exported workbook alone does not
encode these relations: consumers must also read allocation_relations.json.
"""


def add_not_later_constraints(model, ranks, relations):
    for row in relations:
        a,b=row['operation_id'],row['not_later_than']
        if a not in ranks or b not in ranks or a==b:
            raise ValueError(f'Invalid not-later relation: {a} <= {b}')
        model.add(ranks[a] <= ranks[b])


def check_not_later_constraints(assignments, station_orders, relations):
    violations=[]
    for row in relations:
        a,b=row['operation_id'],row['not_later_than']
        sa,sb=assignments.get(a),assignments.get(b)
        if sa not in station_orders or sb not in station_orders or station_orders[sa]>station_orders[sb]:
            violations.append(dict(row,operation_station=sa,target_station=sb))
    return {'feasible':not violations,'violations':violations,'relation_count':len(relations)}
