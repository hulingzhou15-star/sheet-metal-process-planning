from types import SimpleNamespace

from information_extract.scripts.regenerate_p003_candidates import search_paths
from information_extract.three_d import p003_rescreen_search


def test_search_counts_distinct_motion_paths_across_resume(tmp_path, monkeypatch):
    bend_ids = ('A', 'B', 'C')
    retained = ('OP30', 'OP40', 'OP40')
    same_motion = ('OP40', 'OP50', 'OP50')
    new_motion = ('OP30', 'OP30', 'OP50')
    checked = []

    class Search:
        def __init__(self, _context, _catalogue, _config, _primary, _seed, excluded):
            self.remaining = [key for key in (retained, same_motion, new_motion) if key not in excluded]

        def signature(self, assignments):
            return tuple(assignments[op] for op in bend_ids)

        def next(self):
            if not self.remaining:
                return dict(status='INFEASIBLE', assignments=None)
            return dict(status='FEASIBLE', assignments=dict(zip(bend_ids, self.remaining.pop(0))))

    def check(assignments, subdivisions):
        key = tuple(assignments[op] for op in bend_ids)
        checked.append((key, subdivisions))
        return dict(status='passed', subdivisions=subdivisions)

    monkeypatch.setattr(p003_rescreen_search, 'BendSearch', Search)
    context = SimpleNamespace(station_ranks={'OP30': 0, 'OP40': 1, 'OP50': 2})
    screen = SimpleNamespace(bends=bend_ids, check=check)
    seed = [dict(plan_id='NP2_036', assignments=dict(zip(bend_ids, retained)))]
    args = (context, {}, {}, {}, screen, {}, seed, tmp_path/'path_checks.json', {}, 2, 10)
    rows, status, _ = search_paths(*args)
    assert status == 'passing_path_target_reached'
    assert set(rows) == {retained, same_motion, new_motion}
    assert checked == [(key, steps) for key in (retained, same_motion, new_motion) for steps in (24, 96)]

    checked.clear()
    resumed, status, _ = search_paths(*args)
    assert status == 'passing_path_target_reached'
    assert resumed == rows
    assert not checked
