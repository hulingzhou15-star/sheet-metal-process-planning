"""Active no-H6 entry + observational P1 replacement audit, separate from baseline."""
import argparse
from collections import Counter
import html
import json
from pathlib import Path
import time

from information_extract.three_d.allocation_policy import load_context, enumerate_single_bend_moves, VERSION
from information_extract.three_d.allocation_metrics import MetricProbe
from information_extract.scripts.experiment_bend_allocation_3d import (
    ROOT, MODULE, OUT, WORKBOOK, read, sha, digest, protected_hashes, map_material_states, atomic_write,
)

DEST = MODULE / 'outputs/p1_replacement_v01/P001'


def publish(out, plans, results, audit, elapsed):
    by_id = {r['plan_id']: r for r in results}
    rows = []
    for p in plans:
        m = by_id.get(p['plan_id'])
        row = dict(plan_id=p['plan_id'], changes=p['changes'], old_p1_inversions=audit['old_p1'][p['plan_id']], complete=m is not None)
        if m:
            row.update({k: m[k] for k in ('minimum_observed_clearance_mm', 'max_close_pairs',
                'peak_moving_volume_fraction', 'volume_weighted_centroid_travel_mm', 'error_count')})
        rows.append(row)
    summary = dict(policy=VERSION, complete=len(results)==len(plans), rows=rows,
        elapsed_seconds=round(elapsed, 2), H6_enabled=False, old_P1_used_for_generation=False,
        replacement_penalty_validated=False, ranking_enabled=False, scope=audit['scope'])
    atomic_write(out/'summary.json', summary)
    table = ''
    for r in rows:
        change = 'NP2_001基准' if not r['changes'] else '; '.join(f'{c["operation_id"]} {c["before"]}→{c["after"]}' for c in r['changes'])
        def number(key):
            value = r.get(key)
            return '—' if value is None else f'{value:.6f}'
        table += (f'<tr><td>{r["plan_id"]}</td><td>{change}</td><td>{r["old_p1_inversions"]}</td>'
                  f'<td>{number("minimum_observed_clearance_mm")}</td><td>{r.get("max_close_pairs", "—")}</td>'
                  f'<td>{number("peak_moving_volume_fraction")}</td><td>{number("volume_weighted_centroid_travel_mm")}</td>'
                  f'<td>{r.get("error_count", "—")}</td><td>' +
                  (f'<a href="{r["plan_id"]}.json">明细</a>' if r['complete'] else '计算中') + '</td></tr>')
    page = '''<!doctype html><html lang="zh"><meta charset="utf-8"><title>P001 · H6停用与P1替代指标</title>
<style>body{font:15px system-ui;margin:30px;background:#f6f8fa;color:#223}table{border-collapse:collapse;background:white}th,td{padding:9px;border:1px solid #ccd}aside{background:#fff2d7;padding:16px}a{color:#2466a2}</style>
<h1>P001 · H6已停用，P1替代指标检查</h1>
<aside>新入口只读取原始Sheet1–4事实列，旧H6/Sheet5不启用，H7保留。以下均为未标定的三维观测量，不是新罚分或安全判定；P1只在生成完成后离线对照。</aside>
<p>间隙指标排除直接材料连接及本序相对刚性的区域对，以免永久接触淹没观测；独立碰撞诊断仍保留这些区域对。间隙统计范围≤5倍板厚，空值只代表该范围无有效观测（检查错误列），不代表间隙无限大。</p>
<p>接近对数对应0.5/1/2/5倍板厚（P001为0.4/0.8/1.6/4mm），仅用于敏感性观察，无安全阈值含义。体积比例不等于冲压力，质心路程不等于加工时间。</p>'''
    page += f'<p>完成 {len(results)}/{len(plans)}；未覆盖折弯带全对、工具/装夹和局部连续成形。</p>'
    page += '<p><a href="policy_audit.json">H6停用与关系审计</a> · <a href="inventory.json">全部分配</a> · <a href="summary.json">汇总</a> · <a href="inputs.json">输入指纹</a></p>'
    page += '<table><tr><th>方案</th><th>改序</th><th>旧P1反向对</th><th>最小间隙mm</th><th>最大接近对数</th><th>最大同时运动体积比</th><th>体积加权质心路程mm</th><th>未决</th><th>证据</th></tr>'+table+'</table></html>'
    (out/'index.html').write_text(page, encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEST)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(MODULE/'outputs') or out == MODULE/'outputs' or out.is_relative_to(OUT):
        raise ValueError('Dedicated experiment output required')
    protected = protected_hashes()
    context = load_context(WORKBOOK)
    viewer = read(OUT/'viewer_data.json')
    base = next(p for p in viewer['plans'] if p['plan_id']=='NP2_001')
    plans, rejected = enumerate_single_bend_moves(context, base['assignments'], [b['operation_id'] for b in viewer['model']['bends']])
    states = map_material_states(viewer, base, plans)
    # Only AFTER generating new allocations, load old Sheet5 for offline comparison.
    from src.inference.input import load_inference_context
    from src.constraints.v06 import HardConstraintEngine
    from src.constraints.v06.soft_priors import area_ordering, precedence_extra_gap
    old = load_inference_context(WORKBOOK)
    h6 = [r for r in old.precedence_relations if r.constraint_type=='H6']
    h7 = {(r.predecessor_id, r.operation_id) for r in context.feature_tree_relations}
    unique = lambda c: {(r.predecessor_id,r.operation_id) for r in c.precedence_relations}
    old_engine = HardConstraintEngine(old)
    all_decisions_same = all(old_engine.evaluate(p['assignments']).feasible for p in plans)
    for r in rejected:
        a = {**base['assignments'], r['change']['operation_id']:r['change']['after']}
        all_decisions_same = all_decisions_same and not old_engine.evaluate(a).feasible
    audit = dict(policy=VERSION, input_scope='Original Sheet1 fact columns, Sheet2, Sheet3, Sheet4; no original Sheet5 or expert values',
        relation_counts_before=dict(Counter(r.constraint_type for r in old.precedence_relations)),
        relation_counts_after=dict(Counter(r.constraint_type for r in context.precedence_relations)),
        h6_directly_covered_by_h7=sum((r.predecessor_id,r.operation_id) in h7 for r in h6),
        unique_precedence_pairs_unchanged=unique(old)==unique(context),
        all_354_move_feasibility_decisions_unchanged=all_decisions_same,
        old_p1={p['plan_id']:area_ordering(old,p['assignments'],1.15)['inversion_count'] for p in plans},
        p2_recomputed=[dict(plan_id=p['plan_id'], old=precedence_extra_gap(old,p['assignments'])['value'],
                            new=precedence_extra_gap(context,p['assignments'])['value']) for p in plans],
        scope='P001 controlled single-bend neighbourhood, not full new candidate generation; P1 replacement scoring remains uncalibrated.')
    files = {WORKBOOK, OUT/'viewer_data.json', Path(__file__),
             MODULE/'three_d/allocation_policy.py', MODULE/'three_d/allocation_metrics.py'}
    files.update(OUT/g['brep_file'] for g in viewer['geometries'].values())
    files.update((MODULE/'three_d').glob('*.py'))
    for name in ('src/constraints', 'src/inference', 'src/data'):
        files.update((ROOT/name).rglob('*.py'))
    files.update(ROOT/p for p in viewer['model']['source_sha256'])
    files.update((ROOT/'configs').rglob('*.json'))
    inputs = dict(files={p.relative_to(ROOT).as_posix():sha(p) for p in sorted(files)},
                  policy=VERSION, facts_fingerprint=context.facts_fingerprint,
                  plans_sha256=digest(plans), material_states_sha256=digest(states), steps=24)
    signature = digest(inputs)
    if (out/'inputs.json').exists() and read(out/'inputs.json')['signature'] != signature:
        raise ValueError('Sources changed; use another output directory')
    out.mkdir(parents=True, exist_ok=True)
    atomic_write(out/'inputs.json', dict(signature=signature, **inputs))
    atomic_write(out/'policy_audit.json', audit)
    atomic_write(out/'inventory.json', dict(plans=plans,rejected=rejected,material_states=states))
    probe = MetricProbe({**viewer,'plans':plans,'material_states':states}, OUT)
    results = []
    started = time.monotonic()
    publish(out, plans, results, audit, 0)
    for p in plans:
        target = out/(p['plan_id']+'.json')
        if args.resume and target.exists():
            m = read(target)
            if m['signature'] != signature or m['plan_id'] != p['plan_id']:
                raise ValueError('Saved metric input mismatch')
        else:
            m = dict(probe.evaluate(p), signature=signature)
            atomic_write(target, m)
        results.append(m)
        summary = publish(out, plans, results, audit, time.monotonic()-started)
        print(p['plan_id'],len(results),'/',len(plans), 'gap',m['minimum_observed_clearance_mm'],
              'near',m['max_close_pairs'],'volume',m['peak_moving_volume_fraction'],
              'travel',m['volume_weighted_centroid_travel_mm'],'errors',m['error_count'],flush=True)
    if any(sha(ROOT/p)!=h for p,h in inputs['files'].items()) or protected_hashes()!=protected:
        raise ValueError('Inputs changed during experiment')
    atomic_write(out/'verification.json', dict(protection=protected, input_files_unchanged=len(inputs['files']),
        disabled_H6=True, retained_H7_count=len(context.feature_tree_relations),
        p2_values_unchanged=all(abs(r['old']-r['new'])<1e-12 for r in audit['p2_recomputed']),
        old_feasibility_equivalent_on_354_moves=all_decisions_same,
        metrics_error_count=sum(r['error_count'] for r in results), elapsed_seconds=summary['elapsed_seconds']))
    print('COMPLETE', summary['elapsed_seconds'], flush=True)


if __name__=='__main__':
    main()
