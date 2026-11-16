"""Search P003 fold schedules separately from their full-plan completions."""
from fractions import Fraction

from ortools.sat.python import cp_model

from src.candidate.distance import plan_hash
from src.inference.generation import assignment_from_solution, derived_seed, solver_audit, solver_for
from src.neighborhood_penalty.catalogue import evaluate_p5
from src.neighborhood_penalty.generation import compact_score
from src.neighborhood_penalty.model import build_objective_model

from .allocation_policy import AllocationEngine


def _bend_ids(context):
    return tuple(sorted(op for op, row in context.operations.items() if row.operation_type == 'BEND'))


def _station_ranks(context, stations):
    return [context.station_ranks[station] for station in stations]


def _locked_model(context, catalogue, config, primary, scorer=None, channel=None):
    if (
        primary['facts_fingerprint'] != context.facts_fingerprint
        or primary['catalogue_fingerprint'] != catalogue['fingerprint']
        or not primary['generation_allowed']
        or not primary['minimum_proven']
    ):
        raise ValueError('P003 rescreening requires the matching proven primary optimum')
    model, ranks, p5, objective = build_objective_model(context, catalogue, config, scorer, channel)
    target = Fraction(primary['minimum_exact']) * p5.scale
    if target.denominator != 1:
        raise ValueError('Nonintegral P5 lock')
    model.add(p5.expression == int(target))
    return model, ranks, objective


def _can_pin_nonbends(context, catalogue, bend_ids):
    """The existing P003 facts decouple OP10/20 from bend assignment."""
    bends = set(bend_ids)
    if any(bends.intersection(pair['operation_ids']) for pair in catalogue['pairs']):
        return False
    domains = {
        op: _station_ranks(context, (station.station_id for station in context.allowed_stations(op)))
        for op in context.operations
    }
    for relation in context.precedence_relations:
        before, after = relation.predecessor_id, relation.operation_id
        if (before in bends) != (after in bends) and max(domains[before]) >= min(domains[after]):
            return False
    return True


class BendSearch:
    """Enumerate unique complete bend signatures near a verified passing witness."""

    def __init__(self, context, catalogue, config, primary, seed_assignments, initial_excluded=()):
        self.context = context
        self.catalogue = catalogue
        self.config = config
        self.primary_exact = Fraction(primary['minimum_exact'])
        self.engine = AllocationEngine(context)
        self.engine.require_feasible(seed_assignments)
        if Fraction(evaluate_p5(context, catalogue, seed_assignments)['exact']) != self.primary_exact:
            raise ValueError('Bend search seed is outside the proven P5 optimum')
        self.bend_ids = _bend_ids(context)
        self.model, self.ranks, _ = _locked_model(context, catalogue, config, primary)
        self.bend_vars = [self.ranks[op] for op in self.bend_ids]
        if _can_pin_nonbends(context, catalogue, self.bend_ids):
            for op in self.ranks.keys() - set(self.bend_ids):
                self.model.add(self.ranks[op] == context.station_ranks[seed_assignments[op]])
        changes = []
        for op in self.bend_ids:
            flag = self.model.new_bool_var(f'rescreen_changed[{op}]')
            rank = context.station_ranks[seed_assignments[op]]
            self.model.add(self.ranks[op] != rank).only_enforce_if(flag)
            self.model.add(self.ranks[op] == rank).only_enforce_if(flag.negated())
            changes.append(flag)
        self.model.minimize(sum(changes))
        self.excluded = set(map(tuple, initial_excluded))
        if any(len(row) != len(self.bend_vars) for row in self.excluded):
            raise ValueError('Invalid excluded bend signature length')
        if self.excluded:
            self.model.add_forbidden_assignments(
                self.bend_vars, [_station_ranks(context, row) for row in sorted(self.excluded)],
            )
        self.attempt = 0

    def signature(self, assignments):
        return tuple(assignments[op] for op in self.bend_ids)

    def next(self):
        self.attempt += 1
        seed = derived_seed(
            self.context, self.config['generation']['seed'], 'P003 rescreen bend', self.attempt,
        )
        solver = solver_for(self.config, seed)
        status = solver.solve(self.model)
        if status == cp_model.MODEL_INVALID:
            raise ValueError(self.model.validate())
        audit = solver_audit(solver, status)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return {**audit, 'assignments': None}
        assignments = assignment_from_solution(self.context, self.ranks, solver)
        self.engine.require_feasible(assignments)
        if Fraction(evaluate_p5(self.context, self.catalogue, assignments)['exact']) != self.primary_exact:
            raise RuntimeError('Bend witness missed the P5 optimum')
        signature = self.signature(assignments)
        if signature in self.excluded:
            raise RuntimeError('Bend search repeated an excluded signature')
        self.excluded.add(signature)
        self.model.add_forbidden_assignments(self.bend_vars, [_station_ranks(self.context, signature)])
        return {**audit, 'solver_status': audit['status'], 'status': 'FEASIBLE', 'assignments': assignments}


def build_passing_pool(
    context, catalogue, config, primary, scorer, allowed, seed_plans=(), excluded_assignments=(),
):
    """Complete screened fold schedules under the original exact scoring expressions."""
    options = config['generation']
    target = options['pool_target']
    expected = Fraction(primary['minimum_exact'])
    bend_ids = _bend_ids(context)
    allowed = set(map(tuple, allowed))
    if any(len(signature) != len(bend_ids) for signature in allowed):
        raise ValueError('Invalid passing bend signature length')
    engine = AllocationEngine(context)

    def signature(assignments):
        return tuple(assignments[op] for op in bend_ids)

    pool, attempts = [], []
    forbidden = {plan_hash(a): a for a in excluded_assignments}

    def append(plan_id, assignments, audit, score):
        engine.require_feasible(assignments)
        key = plan_hash(assignments)
        if signature(assignments) not in allowed or key in forbidden:
            raise RuntimeError('Full-plan completion disagrees with the motion filter')
        if Fraction(score['p5']['exact']) != expected:
            raise RuntimeError('Full-plan completion missed the P5 optimum')
        pool.append(dict(
            plan_id=plan_id, plan_hash=key, assignments=assignments, score=compact_score(score),
            generation=dict(audit), hard_feasible=True, preference_label=None,
        ))
        forbidden[key] = assignments

    for plan in seed_plans:
        assignments = plan['assignments']
        if len(pool) < target and signature(assignments) in allowed and plan_hash(assignments) not in forbidden:
            append(plan['plan_id'], assignments, {'channel': 'verified_seed'}, scorer.evaluate(assignments))

    channels = ('secondary', 'group_fragmentation', 'extra_gap')
    models = {}
    for channel in channels if allowed else ():
        model, ranks, objective = _locked_model(context, catalogue, config, primary, scorer, channel)
        model.add_allowed_assignments(
            [ranks[op] for op in bend_ids], [_station_ranks(context, row) for row in sorted(allowed)],
        )
        model.minimize(objective.expression)
        models[channel] = model, ranks, objective

    uncovered = sorted(allowed - {signature(plan['assignments']) for plan in pool})
    excluded_paths = 0
    state = 'no_passing_bend_signature' if not allowed else 'partial_budget_exhausted'
    while models and len(pool) < target and len(attempts) < options['max_attempts']:
        attempt = len(attempts)
        required = uncovered.pop(0) if uncovered else None
        channel = 'secondary' if required else channels[attempt % len(channels)]
        base, ranks, objective = models[channel]
        model = base.clone()
        if required is not None:
            model.add_allowed_assignments([ranks[op] for op in bend_ids], [_station_ranks(context, required)])
        if forbidden:
            ordered = sorted(ranks)
            tuples = [_station_ranks(context, (a[op] for op in ordered)) for a in forbidden.values()]
            model.add_forbidden_assignments([ranks[op] for op in ordered], tuples)
        solver = solver_for(config, derived_seed(context, options['seed'], 'P003 rescreen completion', attempt))
        status = solver.solve(model)
        if status == cp_model.MODEL_INVALID:
            raise ValueError(model.validate())
        audit = {
            'channel': channel, 'required_bend_signature': required, **solver_audit(solver, status),
        }
        attempts.append(audit)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            if required is not None:
                excluded_paths += status == cp_model.INFEASIBLE
                continue
            if status == cp_model.INFEASIBLE:
                state = 'passing_completions_exhausted'
                break
            continue
        assignments = assignment_from_solution(context, ranks, solver)
        score = scorer.evaluate(assignments)
        score_exact = score['secondary_exact'] if channel == 'secondary' else score['components'][channel]['exact']
        if Fraction(objective.integer_value(solver), objective.scale) != Fraction(score_exact):
            raise RuntimeError('Full-plan CP-SAT objective disagrees with the independent score')
        audit['independent_objective_exact'] = score_exact
        append(f'NP3_{len(pool)+1:03d}', assignments, audit, score)

    if len(pool) == target:
        state = 'target_reached'
    return pool, dict(
        state=state, requested_pool=target, actual_pool=len(pool), attempts=attempts,
        allowed_bend_signature_count=len(allowed),
        bend_signatures_in_pool=len({signature(plan['assignments']) for plan in pool}),
        infeasible_required_paths=excluded_paths,
    )
