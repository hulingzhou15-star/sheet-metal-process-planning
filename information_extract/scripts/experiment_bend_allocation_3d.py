"""P001 independent single-bend allocation experiment; never writes baseline inputs.

Enumerate the COMPLETE H1-H7-feasible one-bend neighbourhood of NP2_001.
P1 is measured after enumeration, never used to select or reject a variant.
Only bend allocations change: exact removal/forming signatures select existing
material solids; missing signatures fail closed. This is NOT a safety classifier.
"""
import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import html
import json
from pathlib import Path
import time

from src.inference.input import load_inference_context
from src.constraints.v06 import HardConstraintEngine, load_config
from src.constraints.v06.soft_priors import area_ordering
from information_extract.scripts.check_local_forming_collision import (
    OUT, MODULE, read, sha, run, make_samples, checked_checkpoint, atomic_write,
)
from information_extract.scripts.check_all_local_forming_collisions import input_signature, summarize
from information_extract.three_d.local_forming import box, intersect
from information_extract.three_d.local_collision import volume

ROOT = MODULE.parent
DEST = MODULE / 'outputs/bend_allocation_3d_v01/P001'
WORKBOOK = ROOT / 'data/parts/P001/钣金加工数据表_P001.xlsx'
FROZEN = ROOT / 'baselines/P001/plans/all_16_complete_assignments.json'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def protected_hashes():
    rows = read(ROOT / 'registry/protected_inputs.json')['files']
    actual = {r['path']: sha(ROOT / r['path']) if (ROOT / r['path']).exists() else None for r in rows}
    mismatches = [{**r, 'actual_sha256': actual[r['path']]} for r in rows if actual[r['path']] != r['sha256']]
    # Explicit existing user edits, NOT inputs to this experiment. Preserve the
    # registry expectations and report these as mismatches, never as a full pass.
    known = {f'information_extract/projects/P00{i}/project.json' for i in range(1, 5)}
    archived = 'information_extract/sources/P004/P004_01.dxf'
    for row in mismatches:
        if row['path'] in known:
            row['reason'] = 'Existing user-authorized annotation revisions; not consumed by this experiment. See CURRENT_STATUS.md.'
        elif row['path'] == archived and row['actual_sha256'] is None:
            path = ROOT / 'information_extract/sources/P004/history/P004_01.dxf'
            if sha(path) != row['sha256']:
                raise ValueError('Archived P004 source no longer matches registry baseline')
            row['reason'] = 'User replaced drawing; exact original preserved in history. See P004_REVISION_20261111.md.'
            row['archive_path'] = path.relative_to(ROOT).as_posix()
        else:
            raise ValueError('Unexplained protected input mismatch: ' + repr(row))
    return dict(actual=actual, matches=len(rows)-len(mismatches), preexisting_mismatches=mismatches,
                full_registry_pass=not mismatches)


def enumerate_neighbourhood(context, viewer, base):
    engine = HardConstraintEngine(context)
    if not engine.evaluate(base['assignments']).feasible:
        raise ValueError('Base plan is not H1-H7 feasible')
    bends = sorted({b['operation_id'] for b in viewer['model']['bends']})
    if set(bends) != {o.operation_id for o in context.operations.values() if o.operation_type == 'BEND'}:
        raise ValueError('Model does not cover every allocated BEND')
    rows = [dict(plan_id='EXP_BASE', assignments=dict(base['assignments']), changes=[])]
    rejected = []
    for oid in bends:
        for station in sorted(viewer['stations'], key=lambda s: s['order']):
            sid = station['station_id']
            if sid == base['assignments'][oid]:
                continue
            assignments = {**base['assignments'], oid: sid}
            result = engine.evaluate(assignments)
            change = dict(operation_id=oid, before=base['assignments'][oid], after=sid)
            if not result.feasible:
                rejected.append(dict(change=change, hard=result.to_dict()))
                continue
            rows.append(dict(plan_id=f'EXP_{len(rows):03d}', assignments=assignments, changes=[change]))
    ratio = load_config()['bend_area_ordering']['significance_ratio']
    for p in rows:
        p['assignment_sha256'] = digest(p['assignments'])
        p['hard'] = engine.evaluate(p['assignments']).to_dict()
        p['area_audit'] = area_ordering(context, p['assignments'], ratio)
    return rows, rejected


def map_material_states(viewer, base, plans):
    """Recompute executed events per station; never borrow a record by plan index."""
    bends = {b['operation_id'] for b in viewer['model']['bends']}
    orders = {s['station_id']: s['order'] for s in viewer['stations']}
    removals, forming, lookup = set(), set(), {}
    for variant in viewer['variants']:
        cuts, forms = variant['executed_removal_ids'], variant['executed_forming_ids']
        key = (tuple(sorted(cuts)), tuple(sorted(forms)))
        if key in lookup:
            raise ValueError('Ambiguous material signature')
        lookup[key] = variant['material_id']
        removals.update(cuts)
        forming.update(forms)
    if (removals | forming) & bends:
        raise ValueError('Bend actions also change material: full rebuild required')
    records = []
    for p in plans:
        a = p['assignments']
        if set(a) != set(base['assignments']) or any(
            a[o] != sid for o, sid in base['assignments'].items() if o not in bends
        ):
            raise ValueError('Only bend allocation changes supported')
        if not (removals | forming) <= a.keys() or not set(a.values()) <= orders.keys():
            raise ValueError('Incomplete assignment or unknown station')
        for s in sorted(viewer['stations'], key=lambda s: s['order']):
            for phase in ('entry', 'completed'):
                def executed(o):
                    return orders[a[o]] < s['order'] or (phase == 'completed' and orders[a[o]] == s['order'])
                key = (tuple(sorted(o for o in removals if executed(o))),
                       tuple(sorted(o for o in forming if executed(o))))
                if key not in lookup:
                    raise ValueError(f'Missing material signature: {p["plan_id"]}/{s["station_id"]}/{phase}')
                records.append(dict(plan_id=p['plan_id'], station_id=s['station_id'],
                                    phase=phase, material_id=lookup[key]))
    return records


def audit_h6(context):
    h7 = {(r.predecessor_id, r.operation_id) for r in context.precedence_relations if r.constraint_type == 'H7'}
    rows = [{**asdict(r), 'same_direct_H7_edge': (r.predecessor_id, r.operation_id) in h7}
            for r in context.precedence_relations if r.constraint_type == 'H6']
    return dict(rows=rows, count=len(rows), direct_H7_duplicate_count=sum(r['same_direct_H7_edge'] for r in rows),
                interpretation='H6 duplicates cannot be independently challenged while retaining H7; this pilot tests P1 discrimination, not H6 replacement.')


def kernel_controls():
    a = box([0, 0, 0], [2, 2, 2])
    overlap = volume(intersect(a, box([1, 0, 0], [3, 2, 2])))
    apart = volume(intersect(a, box([3, 0, 0], [4, 2, 2])))
    touch = volume(intersect(a, box([2, 0, 0], [3, 2, 2])))
    assert abs(overlap - 4) < 1e-8 and apart < 1e-8 and touch < 1e-8
    return dict(overlap_mm3=overlap, separated_mm3=apart, touching_mm3=touch,
                purpose='Synthetic kernel controls only, not independently labelled real manufacturing cases')


def publish(destination, plans, reports, inventory, elapsed):
    by_id = {r['plan_id']: r for r in reports}
    rows = []
    for p in plans:
        row = dict(plan_id=p['plan_id'], changes=p['changes'], hard_feasible=p['hard']['feasible'],
                   p1_inversions=p['area_audit']['inversion_count'],
                   p1_comparable_pairs=p['area_audit']['comparable_pair_count'],
                   p1_rate=p['area_audit']['inversion_rate'], completed=p['plan_id'] in by_id)
        if row['completed']:
            row.update(summarize(by_id[p['plan_id']]))
            row['geometric_status'] = ('UNRESOLVED_KERNEL' if row['unknown_pair_states'] else
                'MODEL_OVERLAP_DETECTED' if row['positive_volume_pair_states'] else 'NO_OVERLAP_AT_SAMPLES')
        rows.append(row)
    complete = len(reports) == len(plans)
    summary = dict(complete=complete, requested_count=len(plans), completed_count=len(reports),
        elapsed_seconds=round(elapsed, 2), rows=rows, h6_audit=inventory['h6_audit'],
        totals={k: sum(r.get(k, 0) for r in rows) for k in ('sample_count', 'pair_state_count',
            'positive_volume_pair_states', 'unknown_pair_states')},
        p1_inversion_range=[min(r['p1_inversions'] for r in rows), max(r['p1_inversions'] for r in rows)],
        independent_real_case_labels_available=False, manufacturing_safety_proved=False,
        replacement_implemented=False, frozen_inputs_modified=False,
        scope='All rigid region pairs with local STEP endpoint patches; no finite bands, continuous local forming, tools/fixtures; discrete shared station progress.')
    atomic_write(destination / 'summary.json', summary)
    conclusion = '实验正在运行。'
    if complete:
        if not summary['totals']['positive_volume_pair_states'] and not summary['totals']['unknown_pair_states']:
            conclusion = '本轮三维自干涉未区分这些分配；不能替代影响面积排序，也未证明这些分配等价或可制造。'
        else:
            conclusion = '发现模型交叠或计算未决，需核查具体实体和加工假设；尚不能将结果直接判为制造失败。'
    table = ''
    for r in rows:
        change = '基准 NP2_001' if not r['changes'] else '；'.join(
            f'{c["operation_id"]} {c["before"]} → {c["after"]}' for c in r['changes'])
        table += (f'<tr><td>{r["plan_id"]}</td><td>{change}</td><td>{r["p1_inversions"]}</td>'
                  f'<td>{r.get("sample_count", "待跑")}</td><td>{r.get("positive_volume_pair_states", "—")}</td>'
                  f'<td>{r.get("unknown_pair_states", "—")}</td><td>' +
                  (f'<a href="{r["plan_id"]}/collision_report.json">采样明细</a>' if r['completed'] else '待跑') + '</td></tr>')
    page = '''<!doctype html><html lang="zh"><meta charset="utf-8"><title>P001 · 三维分配对照实验</title>
<style>body{font:16px system-ui;max-width:1250px;margin:32px auto;padding:0 24px;color:#223;background:#f6f8fa}table{border-collapse:collapse;background:white;width:100%}td,th{border:1px solid #ccd4dd;padding:9px;text-align:left}aside{background:#fff1d7;padding:16px;line-height:1.6}a{color:#2265a5}</style>
<h1>P001 · 折弯分配三维对照实验</h1>'''
    page += f'<aside>{conclusion}</aside><p>完成 {len(reports)}/{len(plans)}。全部合法单折弯改序均纳入；未按 P1 预筛。H1–H7 全保留，同序同时加工。</p>'
    page += '<p>H6 的 32 条边均与 H7 直接重复，所以本轮无法独立证明 H6 可被替代。P1 是面积排序偏好，不是碰撞真值；缺少独立标注的真实干涉样例，不能统计误报率/漏报率。</p>'
    page += '<p>运动中用进入态材料，完成端统一切换；检查刚性板面与局部 STEP 端态。未覆盖折弯带全对、连续局部成形、工具和装夹。交叠不自动判失败；无交叠不证明安全。</p>'
    page += '<p><a href="summary.json">汇总</a> · <a href="inventory.json">全部分配、P1 逐对审计与拒绝原因</a> · <a href="inputs.json">输入指纹</a> · <a href="verification.json">验证</a></p>'
    page += '<table><tr><th>实验编号</th><th>改序</th><th>P1 反向对数 / 1451</th><th>状态</th><th>正体积交叠对×状态</th><th>计算未决</th><th>报告</th></tr>' + table + '</table></html>'
    (destination / 'index.html').write_text(page, encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEST)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    destination = args.output.resolve()
    if not destination.is_relative_to(MODULE / 'outputs') or destination == MODULE / 'outputs':
        raise ValueError('Output must be a dedicated directory under information_extract/outputs')
    if destination == OUT or destination.is_relative_to(OUT):
        raise ValueError('Keep experiment separate from baseline model outputs')
    protected = protected_hashes()
    viewer = read(OUT / 'viewer_data.json')
    context = load_inference_context(WORKBOOK)
    frozen = {p['plan_id']: p['assignments'] for p in read(FROZEN)['plans']}
    if {p['plan_id']: p['assignments'] for p in viewer['plans']} != frozen:
        raise ValueError('Viewer candidates differ from frozen assignments')
    base = next(p for p in viewer['plans'] if p['plan_id'] == 'NP2_001')
    plans, rejected = enumerate_neighbourhood(context, viewer, base)
    states = map_material_states(viewer, base, plans)
    expected = {(r['station_id'], r['phase']): r['material_id'] for r in viewer['material_states'] if r['plan_id'] == base['plan_id']}
    if any(r['material_id'] != expected[r['station_id'], r['phase']] for r in states):
        raise ValueError('Bend-only variants unexpectedly changed material event signatures')
    _, inputs = input_signature(viewer, 24)
    # Convert inherited absolute provenance keys to repository-relative paths.
    inputs['files'] = {Path(p).relative_to(ROOT).as_posix(): h for p, h in inputs['files'].items()}
    sources = {WORKBOOK, FROZEN, Path(__file__), ROOT / 'registry/protected_inputs.json'}
    for directory in ('src/constraints', 'src/inference', 'src/data'):
        sources.update((ROOT / directory).rglob('*.py'))
    sources.update((ROOT / 'configs').rglob('*.json'))
    sources.update(Path(__file__).parent / s for s in ('build_local_forming_states.py', 'build_finite_material_model.py'))
    inputs['files'].update({p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(sources)})
    inputs.update(plans_sha256=digest(plans), material_states_sha256=digest(states),
                  facts_fingerprint=context.facts_fingerprint, protected_snapshot=protected)
    signature = digest(inputs)
    manifest = destination / 'inputs.json'
    if manifest.exists() and read(manifest)['signature'] != signature:
        raise ValueError('Experiment inputs changed; choose a new output directory')
    destination.mkdir(parents=True, exist_ok=True)
    atomic_write(manifest, dict(signature=signature, **inputs))
    inventory = dict(base_plan_id=base['plan_id'], plans=plans, rejected=rejected,
        attempted_changes=len(plans) - 1 + len(rejected), feasible_changes=len(plans) - 1,
        enumeration='All single BEND moves to all other stations; no P1 filter',
        h6_audit=audit_h6(context), material_states=states,
        reference_used_for_generation=False, kernel_controls=kernel_controls(),
        frozen_p1=[dict(plan_id=p['plan_id'], **{k: area_ordering(context, p['assignments'], 1.15)[k]
            for k in ('inversion_count', 'comparable_pair_count')}) for p in viewer['plans']])
    atomic_write(destination / 'inventory.json', inventory)
    v = {**viewer, 'plans': plans, 'material_states': states}
    reports, shared = [], {}
    started = time.monotonic()
    publish(destination, plans, reports, inventory, 0)
    print(f'PREPARED {len(plans)} plans; {len(rejected)} rejected; H6 audit {inventory["h6_audit"]["direct_H7_duplicate_count"]}', flush=True)
    if args.prepare_only:
        return
    for p in plans:
        folder = destination / p['plan_id']
        saved = folder / 'collision_report.json'
        if args.resume and saved.exists():
            report = read(saved)
            samples = make_samples(v, p)
            checked_checkpoint(report, signature, samples)
            if report['plan_id'] != p['plan_id'] or len(report['runs']) != len(samples):
                raise ValueError('Wrong/incomplete saved report')
        else:
            report = run(p['plan_id'], v, folder, shared, signature, args.resume)
        reports.append(report)
        summary = publish(destination, plans, reports, inventory, time.monotonic() - started)
        print(f'EXPERIMENT {len(reports)}/{len(plans)} ' + json.dumps(summarize(report)), flush=True)
    changed = [p for p, h in inputs['files'].items() if sha(ROOT / p) != h]
    if changed or protected_hashes() != protected:
        raise ValueError('Inputs changed during experiment: ' + repr(changed))
    old = read(OUT / 'collision_batch_v01/NP2_001/collision_report.json')
    fields = ('station_id', 'phase', 'tau', 'material_id', 'pair_count', 'counts', 'issues')
    pick = lambda r: [{k: s[k] for k in fields} for s in r['runs']]
    baseline_equal = pick(reports[0]) == pick(old)
    if not baseline_equal:
        raise ValueError('Base sample results differ from earlier frozen run')
    atomic_write(destination / 'verification.json', dict(protection=protected,
        input_files_unchanged=len(inputs['files']), baseline_sample_outcomes_identical=baseline_equal,
        all_hard_feasible=True, exact_material_signature_records=len(states),
        all_non_bend_assignments_unchanged=True, kernel_controls=inventory['kernel_controls'],
        no_real_case_false_positive_negative_rate_claim=True))
    print(json.dumps({k: v for k, v in summary.items() if k not in ('rows', 'h6_audit')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
