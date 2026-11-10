"""Independent exact-rational scores and hard-feasible occupancy bounds."""
from collections import defaultdict
from copy import deepcopy
from fractions import Fraction

from ortools.sat.python import cp_model
from src.constraints.v06 import HardConstraintEngine
from src.constraints.v06.feasibility import build_feasibility_model
from src.constraints.v06.soft_priors import area_ordering, precedence_extra_gap
from src.inference.generation import solver_for, solver_audit, derived_seed
from .config import validate_config
from .catalogue import check_catalogue, evaluate_p5


class BoundError(ValueError): pass


def occupancy(model,ranks,context,ids,name):
    flags=[]
    for station in context.ordered_stations:
        at=[]
        for op in ids:
            if station not in context.allowed_stations(op): continue
            flag=model.new_bool_var(f'{name}:at:{op}:{station.station_id}')
            model.add(ranks[op]==context.station_ranks[station.station_id]).only_enforce_if(flag)
            model.add(ranks[op]!=context.station_ranks[station.station_id]).only_enforce_if(flag.Not())
            at.append(flag)
        if at:
            use=model.new_bool_var(f'{name}:use:{station.station_id}')
            model.add_max_equality(use,at);flags.append(use)
    return flags


class Scorer:
    def __init__(self,context,catalogue,config):
        check_catalogue(context,catalogue)
        self.context,self.catalogue,self.config=context,catalogue,validate_config(config)
        unknown={o.operation_type for o in context.operations.values()}-set(self.config['fragmentation_types'])
        # Disabled known types are intentional; truly unknown schema types are errors.
        from src.data.schema import OperationType
        if unknown-{t.value for t in OperationType}: raise BoundError('Unknown operation types')
        for o in context.operations.values():
            if o.operation_type=='BEND' and o.surface_side not in self.config['bend_directions']:
                raise BoundError('Missing or unsupported BEND direction: '+o.operation_id)
        self._bounds=None
        # Scoring templates are facts only: choose any allowed assignment; raw values
        # are ignored here and all constants/denominators derive from facts.
        seed_plan={o:context.allowed_stations(o)[0].station_id for o in context.operations}
        self.area_template=area_ordering(context,seed_plan,self.config['area_significance_ratio'])
        self.gap_template=precedence_extra_gap(context,self._topological_template())

    def _topological_template(self):
        # P2 template only needs a feasible pair gap; avoid inventing a full plan.
        # The existing audit validates actual gaps, so use a real hard witness.
        model,ranks=build_feasibility_model(self.context)
        solver=solver_for({'solver':self.config['bounds_solver']},derived_seed(self.context,self.config['generation']['seed'],'score_template'))
        status=solver.solve(model)
        if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE):
            raise BoundError('Hard witness not established: '+solver.status_name(status))
        from src.inference.generation import assignment_from_solution
        return assignment_from_solution(self.context,ranks,solver)

    def bounds(self):
        if self._bounds is not None:return deepcopy(self._bounds)
        rows=[]
        for kind in self.config['fragmentation_types']:
            sides=self.config['bend_directions'] if kind=='BEND' else [None]
            for side in sides:
                ids=sorted(o.operation_id for o in self.context.operations.values() if o.operation_type==kind and (side is None or o.surface_side==side))
                row={'group_id':kind+(':'+side if side else ''),'operation_type':kind,'direction':side,'operation_ids':ids,'operation_count':len(ids),'proofs':{}}
                for name,maximize in [('K_min',False),('K_max',True)]:
                    if not ids:row[name]=0;row['proofs'][name]={'status':'ABSENT'};continue
                    model,ranks=build_feasibility_model(self.context)
                    used=occupancy(model,ranks,self.context,ids,row['group_id'])
                    (model.maximize if maximize else model.minimize)(sum(used))
                    solver=solver_for({'solver':self.config['bounds_solver']},derived_seed(self.context,self.config['generation']['seed'],row['group_id']+name))
                    status=solver.solve(model)
                    row['proofs'][name]=solver_audit(solver,status)
                    if status != cp_model.OPTIMAL:raise BoundError(f'Exact {row["group_id"]} {name} not proven: {solver.status_name(status)}')
                    row[name]=sum(solver.value(v) for v in used)
                row['participating']=bool(ids) and row['K_min']<row['K_max']
                rows.append(row)
        self._bounds=rows
        return deepcopy(rows)

    def evaluate(self,plan):
        assignments=HardConstraintEngine(self.context).require_feasible(plan)
        p5=evaluate_p5(self.context,self.catalogue,assignments)
        area=area_ordering(self.context,assignments,self.config['area_significance_ratio'])
        gap=precedence_extra_gap(self.context,assignments)
        a=Fraction(area['inversion_count'],area['comparable_pair_count']) if area['comparable_pair_count'] else Fraction()
        g=sum((Fraction(r['g_actual']-r['g_min'],r['g_max']-r['g_min']) if r['g_max']>r['g_min'] else Fraction() for r in gap['relations']),Fraction())/gap['relation_count'] if gap['relation_count'] else Fraction()
        groups=self.bounds();by_type=defaultdict(list)
        for row in groups:
            used=sorted({assignments[o] for o in row['operation_ids']},key=self.context.station_ranks.get)
            k=len(used)
            if not row['K_min']<=k<=row['K_max']:raise BoundError('Occupancy outside exact hard bounds')
            value=Fraction(k-row['K_min'],row['K_max']-row['K_min']) if row['participating'] else Fraction()
            row.update(K_actual=k,actual_used_stations=used,value=float(value),exact=str(value))
            if row['participating']:by_type[row['operation_type']].append(value)
        means={kind:sum(values,Fraction())/len(values) for kind,values in by_type.items()}
        f=sum(means.values(),Fraction())/len(means) if means else Fraction()
        components={name:{'value':float(value),'exact':str(value),'weight':self.config['secondary_weights'][name],
                          'weighted_contribution':float(value*Fraction(str(self.config['secondary_weights'][name])))} for name,value in [('area',a),('extra_gap',g),('group_fragmentation',f)]}
        secondary=sum((Fraction(c['exact'])*Fraction(str(c['weight'])) for c in components.values()),Fraction())
        if any(not 0<=v<=1 for v in [a,g,f,secondary,Fraction(p5['exact'])]):raise ValueError('Score outside [0,1]')
        return {'p5':p5,'secondary':float(secondary),'secondary_exact':str(secondary),'components':components,
                'fragmentation':{'groups':groups,'type_means':{k:float(v) for k,v in means.items()},'participating_type_count':len(means),'bounds_source':'H1-H7 only'},
                'area_audit':area,'gap_audit':gap,'purpose':'candidate_generation_heuristic_only','preference_label':None}
