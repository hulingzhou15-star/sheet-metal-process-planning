from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path

from src.data.schema import OperationType
from src.neighborhood.context import positive

DEFAULT_CONFIG = Path(__file__).resolve().parents[2]/"configs/neighborhood_penalty_v02.json"
FAMILIES = {"FLANGING_IRREGULAR", "BEND_FLANGING", "BEND_IRREGULAR"}


def validate_config(config):
    c = deepcopy(config)
    if c['schema_version'] != 'neighborhood_penalty.v0.2':
        raise ValueError('Unknown penalty version')
    w = c['secondary_weights']
    if set(w) != {'area', 'extra_gap', 'group_fragmentation'} or any(not positive(v) for v in w.values()) or sum(Fraction(str(v)) for v in w.values()) != 1:
        raise ValueError('Positive secondary weights must sum exactly to 1')
    p = c['policy']
    if set(p['families']) != FAMILIES or len(p['families']) != len(FAMILIES) or p['tier'] != 'PRIORITY' or p['aggregation'] != 'equal_nonempty_type_means' or p['ordering'] != 'lexicographic':
        raise ValueError('Unconfirmed neighborhood policy')
    types = c['fragmentation_types']
    if not types or len(types) != len(set(types)) or not set(types) <= {t.value for t in OperationType} or set(c['bend_directions']) != {'SMOOTH', 'ROUGH'}:
        raise ValueError('Unsupported fragmentation grouping')
    if not positive(c['area_significance_ratio']) or c['area_significance_ratio'] <= 1:
        raise ValueError('Invalid significance ratio')
    g = c['generation']
    for key in ('pool_target', 'selected_target', 'max_attempts', 'channel_quota', 'score_selection_count', 'random_cost_abs_max'):
        if type(g[key]) is not int or g[key] < 1: raise ValueError('Invalid generation '+key)
    if type(g['seed']) is not int or g['seed'] < 0 or g['selected_target'] > g['pool_target'] or g['score_selection_count'] > g['selected_target']:
        raise ValueError('Invalid generation sizes')
    for settings in (c['solver'], c['bounds_solver']):
        if settings['num_search_workers'] != 1 or not all(positive(settings[k]) for k in ('max_time_seconds', 'max_deterministic_time')):
            raise ValueError('Positive budgets and single worker required')
    if type(c['integer_objective_limit']) is not int or not 0 < c['integer_objective_limit'] < 2**63:
        raise ValueError('Invalid integer objective limit')
    if not all(positive(c['view'][k]) for k in ('minimum_extent', 'padding_fraction', 'hit_radius_px')):
        raise ValueError('Invalid display dimensions')
    return c


def load_config(path=DEFAULT_CONFIG):
    return validate_config(json.loads(Path(path).read_text(encoding='utf-8')))
