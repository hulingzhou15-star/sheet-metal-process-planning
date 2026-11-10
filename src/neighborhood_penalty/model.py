"""CP-SAT expressions match independent scores with exact integer scaling."""
from fractions import Fraction
from functools import reduce
from math import lcm

from src.constraints.v06.feasibility import build_feasibility_model
from .catalogue import check_catalogue
from .scoring import occupancy


class RationalExpression:
    def __init__(self,terms=(),constant=0):
        self.terms=list(terms);self.constant=Fraction(constant)

    def scaled(self,weight):
        weight=Fraction(weight)
        return RationalExpression([(v,c*weight) for v,c in self.terms],self.constant*weight)

    def plus(self,other):
        return RationalExpression([*self.terms,*other.terms],self.constant+other.constant)

    def compile(self,limit,rank_bound):
        scale=reduce(lcm,[c.denominator for _,c in self.terms]+[self.constant.denominator],1)
        terms=[(v,int(c*scale)) for v,c in self.terms]
        constant=int(self.constant*scale)
        magnitude=abs(constant)+sum(abs(c)*rank_bound for _,c in terms)
        if scale>limit or magnitude>limit:
            raise ValueError('Exact rational objective exceeds configured integer safety limit')
        return IntegerExpression(terms,constant,scale,magnitude)


class IntegerExpression:
    def __init__(self,terms,constant,scale,magnitude):
        self.terms,self.constant,self.scale,self.magnitude=terms,constant,scale,magnitude
        self.expression=sum((c*v for v,c in terms),constant)

    def integer_value(self,solver):
        return self.constant+sum(c*solver.value(v) for v,c in self.terms)

    def audit(self):
        return {'scale':self.scale,'constant':self.constant,'conservative_abs_bound':self.magnitude,'arithmetic':'exact_rational_integer_scaled'}


def p5_expression(model,ranks,catalogue):
    terms=[];types=catalogue['type_counts']
    for row in catalogue['pairs']:
        a,b=row['operation_ids'];same=model.new_bool_var('P5:'+row['pair_id'])
        model.add(ranks[a]==ranks[b]).only_enforce_if(same)
        model.add(ranks[a]!=ranks[b]).only_enforce_if(same.Not())
        terms.append((same,Fraction(1,len(types)*types[row['family']])))
    return RationalExpression(terms)


def area_expression(model,ranks,template):
    count=template['comparable_pair_count'];terms=[]
    for row in template['comparable_pairs']:
        a,b=row['larger_operation_id'],row['smaller_operation_id']
        flag=model.new_bool_var('area:'+a+':'+b)
        model.add(ranks[a]<ranks[b]).only_enforce_if(flag)
        model.add(ranks[a]>=ranks[b]).only_enforce_if(flag.Not())
        terms.append((flag,Fraction(1,count)))
    return RationalExpression(terms)


def gap_expression(ranks,template):
    terms=[];constant=Fraction();n=template['relation_count']
    for row in template['relations']:
        span=row['g_max']-row['g_min']
        if not span:continue
        a,b=row['operation_ids'];weight=Fraction(1,n*span)
        terms.extend([(ranks[b],weight),(ranks[a],-weight)])
        constant-=row['g_min']*weight
    return RationalExpression(terms,constant)


def fragmentation_expression(model,ranks,context,bounds):
    participating=[r for r in bounds if r['participating']]
    types={r['operation_type'] for r in participating};terms=[];constant=Fraction()
    for row in participating:
        siblings=sum(r['operation_type']==row['operation_type'] for r in participating)
        weight=Fraction(1,len(types)*siblings*(row['K_max']-row['K_min']))
        used=occupancy(model,ranks,context,row['operation_ids'],row['group_id'])
        terms.extend((flag,weight) for flag in used);constant-=weight*row['K_min']
    return RationalExpression(terms,constant)


def build_objective_model(context,catalogue,config,scorer=None,channel=None):
    check_catalogue(context,catalogue)
    model,ranks=build_feasibility_model(context)
    compile_=lambda e:e.compile(config['integer_objective_limit'],max(1,len(context.ordered_stations)-1))
    p5=compile_(p5_expression(model,ranks,catalogue))
    other=None
    if channel=='secondary':
        expressions={'area':area_expression(model,ranks,scorer.area_template),
                     'extra_gap':gap_expression(ranks,scorer.gap_template),
                     'group_fragmentation':fragmentation_expression(model,ranks,context,scorer.bounds())}
        combined=RationalExpression()
        for name,e in expressions.items():combined=combined.plus(e.scaled(Fraction(str(config['secondary_weights'][name]))))
        other=compile_(combined)
    elif channel=='group_fragmentation':other=compile_(fragmentation_expression(model,ranks,context,scorer.bounds()))
    elif channel=='extra_gap':other=compile_(gap_expression(ranks,scorer.gap_template))
    return model,ranks,p5,other
