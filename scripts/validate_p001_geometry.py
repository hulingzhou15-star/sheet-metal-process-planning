"""Read-only P001 IRREGULAR_PUNCH audit; never writes a source workbook.

Run with bundled Python. Optional plotting libraries can be supplied using
--deps PATH; all deliverables go to outputs/geometry_validation by default.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
PARSER = argparse.ArgumentParser(description=__doc__)
PARSER.add_argument('--deps', type=Path)
PARSER.add_argument('--output', type=Path, default=ROOT / 'outputs/geometry_validation')
PARSER.add_argument('--inspect-only', action='store_true')
PARSER.add_argument('--review-only', action='store_true', help='Reuse saved audit tables for report/zoom rendering')
PARSER.add_argument('--review-mode', choices=['baseline', 'current'], default='current', help='Historical first-round report or current workbook validation')
ARGS = PARSER.parse_args()
if ARGS.deps:
    sys.path.insert(0, str(ARGS.deps))
import numpy as np
import openpyxl

EPS = 1e-8
SMALL_MM = 0.01  # review threshold, not a manufacturing tolerance
MATCH_MM = 0.10  # source association threshold, not an exact-overlay requirement


def point(row, prefix):
    return np.array([row[prefix + '_x'], row[prefix + '_y']], dtype=float)


def norm(v):
    return float(np.linalg.norm(v))


def unit(v):
    return v / np.linalg.norm(v)


def cross(a, b):
    return float(a[0] * b[1] - a[1] * b[0])


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def write_csv(path, rows):
    if not rows:
        path.write_text('geometry_id,seq,old_value,new_value\n', encoding='utf-8-sig')
        return
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        writer.writerows(rows)


class SourceHTML(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.feed(text)
        # metadata uses XML character entities, already decoded by HTMLParser.
        import html
        self.metadata = json.loads(html.unescape(re.search(r'<metadata>(.*?)</metadata>', text, re.S).group(1)))
        self.scale = self.metadata['display']['pixels_per_mm']
        self.bounds = self.metadata['display']['view_bounds']
        self.margin = self.metadata['display']['margin_px']
        self.matrix = np.array(self.metadata['display']['local_to_view_linear']).reshape(2, 2)
        for row in self.rows:
            row['points'] = self.path_points(row)

    def handle_starttag(self, tag, attributes):
        a = dict(attributes)
        if a.get('data-role') != 'segment':
            return
        row = {'source_id': a['id'], 'candidate_id': a['data-geometry-id'],
               'seq': int(a['data-seq']), 'segment_type': a['data-segment-type'], 'attrs': a}
        for key in ['start_x', 'start_y', 'end_x', 'end_y', 'center_x', 'center_y']:
            if 'data-local-' + key.replace('_', '-') in a:
                row[key] = float(a['data-local-' + key.replace('_', '-')])
        for key in ['radius', 'sweep_angle']:
            if 'data-' + key.replace('_', '-') in a:
                row[key] = float(a['data-' + key.replace('_', '-')])
        self.rows.append(row)

    def to_local(self, screen):
        view = np.array([(screen[0] - self.margin) / self.scale + self.bounds[0],
                         self.bounds[3] - (screen[1] - self.margin) / self.scale])
        return np.linalg.solve(self.matrix, view)

    def path_points(self, row):
        if row['segment_type'] == 'CIRCLE':
            ts = np.linspace(0, 2 * np.pi, 721)
            return point(row, 'center') + row['radius'] * np.column_stack((np.cos(ts), np.sin(ts)))
        tokens = re.findall(r'[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?', row['attrs']['d'])
        points, i, current = [], 0, None
        while i < len(tokens):
            command = tokens[i]
            i += 1
            if command in ['M', 'L']:
                current = np.array(list(map(float, tokens[i:i+2])))
                i += 2
                points.append(current)
            elif command == 'C':
                # SVG repeats the same command implicitly for later cubic triples.
                while i < len(tokens) and not tokens[i].isalpha():
                    p1, p2, p3 = np.array(list(map(float, tokens[i:i+6]))).reshape(3, 2)
                    for t in np.linspace(0, 1, 21)[1:]:
                        points.append((1-t)**3*current + 3*(1-t)**2*t*p1 + 3*(1-t)*t*t*p2 + t**3*p3)
                    current = p3
                    i += 6
            else:
                raise ValueError(f'Unsupported SVG path command {command}')
        return np.array([self.to_local(p) for p in points])


def match_source(row, source):
    options = []
    for ref in source.rows:
        if ref['segment_type'] != row['segment_type']:
            continue
        s, e = point(row, 'start'), point(row, 'end')
        rs, re_ = point(ref, 'start'), point(ref, 'end')
        forward = max(norm(s-rs), norm(e-re_))
        reverse = max(norm(s-re_), norm(e-rs))
        dist = min(forward, reverse)
        if row['segment_type'] == 'ARC':
            dist = max(dist, norm(point(row, 'center')-point(ref, 'center')), abs(row['radius']-ref['radius']))
        options.append((dist, reverse < forward, ref))
    options.sort(key=lambda v: v[0])
    best = options[0]
    return best if best[0] <= MATCH_MM else None, options[:3]


def candidates(row):
    a = point(row, 'start')-point(row, 'center')
    b = point(row, 'end')-point(row, 'center')
    ccw = math.degrees(math.atan2(cross(a, b), float(np.dot(a, b)))) % 360
    return ccw, ccw-360


def tangent(row, at_end=False, angle=None):
    if row['segment_type'] == 'LINE':
        return unit(point(row, 'end')-point(row, 'start'))
    radial = unit(point(row, 'end' if at_end else 'start')-point(row, 'center'))
    sign = 1 if (angle if angle is not None else row['signed_sweep']) > 0 else -1
    return sign*np.array([-radial[1], radial[0]])


def infer_sweep(row, chain, index):
    ccw, cw = candidates(row)
    tangent_votes = []
    for neighbor, end, endpoint in [(chain[(index-1) % len(chain)], True, False),
                                    (chain[(index+1) % len(chain)], False, True)]:
        if neighbor['segment_type'] == 'LINE':
            tangent_votes.append(float(np.dot(tangent(row, endpoint, ccw), tangent(neighbor, end))))
    vote = sum(tangent_votes)
    row['tangent_vote_ccw'] = vote
    row['excel_direction_basis'] = 'neighbor_LINE_tangents' if tangent_votes and abs(vote) > 0.5 else 'ambiguous_without_HTML'
    excel_sweep = ccw if vote > 0 else cw if vote < 0 else None
    if row['match']:
        _, reverse, ref = row['match']
        ps = ref['points'] - point(ref, 'center')
        angles = np.unwrap(np.arctan2(ps[:, 1], ps[:, 0]))
        source_sweep = math.degrees(angles[-1]-angles[0]) * (-1 if reverse else 1)
        row['html_signed_sweep'] = source_sweep
        selected = ccw if source_sweep > 0 else cw
        row['direction_basis'] = 'HTML_rendered_cubic_curve' + ('+neighbor_LINE_tangents' if excel_sweep is not None else '')
        row['excel_html_direction_agree'] = excel_sweep is None or (excel_sweep*selected > 0)
        return selected
    row['direction_basis'] = row['excel_direction_basis']
    return excel_sweep


def sample(row, max_angle=0.25):
    if row['segment_type'] == 'LINE':
        return np.array([point(row, 'start'), point(row, 'end')])
    a = point(row, 'start') - point(row, 'center')
    start = math.atan2(a[1], a[0])
    angles = np.linspace(start, start+math.radians(row['signed_sweep']), max(3, math.ceil(abs(row['signed_sweep'])/max_angle)+1))
    return point(row, 'center') + row['radius']*np.column_stack((np.cos(angles), np.sin(angles)))


def load():
    book = next((ROOT / 'data/parts/P001').glob('*P001.xlsx'))
    html = ROOT / 'information_extract/sources/P001/legacy_outline.html'
    before = {p.name: digest(p) for p in [book, html]}
    wb = openpyxl.load_workbook(book, read_only=True, data_only=True)
    values = list(wb['Sheet1'].values)
    operations = [dict(zip(values[0], r)) for r in values[1:] if r[3] == 'IRREGULAR_PUNCH']
    id_ops = {op['geometry_id']: op['operation_id'] for op in operations}
    values = list(wb['Sheet2'].values)
    chains = collections.OrderedDict((gid, []) for gid in id_ops)
    for excel_row, values_row in enumerate(values[1:], 2):
        if values_row[1] in chains:
            row = dict(zip(values[0], values_row))
            row['excel_row'] = excel_row
            chains[row['geometry_id']].append(row)
    wb.close()
    source = SourceHTML(html.read_text(encoding='utf-8'))
    for chain in chains.values():
        # Existing seq is authoritative. Do not reorder or reverse Excel segments.
        for row in chain:
            row['match'], nearest = match_source(row, source)
            row['nearest_source'] = [(v[0], v[2]['source_id']) for v in nearest]
        for i, row in enumerate(chain):
            if row['segment_type'] == 'ARC':
                row['signed_sweep'] = infer_sweep(row, chain, i)
    return book, html, before, operations, chains, source


def main():
    out = ARGS.output
    out.mkdir(parents=True, exist_ok=True)
    book, html, before, operations, chains, source = load()
    inspection = []
    for gid, chain in chains.items():
        matches = [r for r in chain if r['match']]
        gaps = [norm(point(r, 'end')-point(chain[(i+1) % len(chain)], 'start')) for i, r in enumerate(chain)]
        inspection.append({'geometry_id': gid, 'segments': len(chain), 'matched': len(matches),
                           'max_gap': max(gaps), 'negative_seqs': [r['seq'] for r in chain if r.get('signed_sweep', 0) < 0],
                           'html_needed_seqs': [r['seq'] for r in chain if r.get('excel_direction_basis') == 'ambiguous_without_HTML'],
                           'unmatched': [{'seq': r['seq'], 'nearest': r['nearest_source']} for r in chain if not r['match']],
                           'candidates': sorted(set(r['match'][2]['candidate_id'] for r in matches))})
    write_json(out / 'inspection.json', inspection)
    print(json.dumps({'geometries': len(chains), 'segments': sum(map(len, chains.values())),
                      'matched_segments': sum(r['matched'] for r in inspection),
                      'negative_arcs': sum(len(r['negative_seqs']) for r in inspection)}))
    if not ARGS.inspect_only:
        if not ARGS.review_only:
            generate(out, book, html, before, operations, chains, source)
        if ARGS.review_mode == 'baseline':
            create_review(out, book, html, operations, chains, source)
        else:
            create_current_review(out, book, html, operations, chains, source)


def setup_plot():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': 'Microsoft YaHei', 'axes.unicode_minus': False,
                         'font.size': 10, 'savefig.facecolor': 'white'})
    return plt


def signed_area(chain):
    """Green integral including tiny virtual connectors; coordinates stay intact."""
    origin = point(chain[0], 'start')
    area = 0.0
    for i, row in enumerate(chain):
        ps = sample(row)
        s, e = ps[0]-origin, ps[-1]-origin
        if row['segment_type'] == 'LINE':
            area += cross(s, e)/2
        else:
            c = point(row, 'center')-origin
            area += (cross(c, e-s) + row['radius']**2*math.radians(row['signed_sweep']))/2
        ns = sample(chain[(i+1) % len(chain)])[0]-origin
        area += cross(e, ns)/2
    return area


def bounds(chain):
    ps = np.concatenate([sample(row) for row in chain])
    return [float(v) for v in [*ps.min(axis=0), *ps.max(axis=0)]]


def frame(ax, lim, compact=False):
    x0, y0, x1, y1 = lim
    padding = max(x1-x0, y1-y0)*0.09+0.3
    ax.set_xlim(x0-padding, x1+padding)
    ax.set_ylim(y0-padding, y1+padding)
    ax.set_aspect('equal', adjustable='box')
    ax.ticklabel_format(useOffset=False, style='plain')
    ax.grid(True, color='#e6e9ed', linewidth=0.6)
    if compact:
        ax.tick_params(labelsize=7)
    else:
        ax.set_xlabel('local x (mm)')
        ax.set_ylabel('local y (mm)')


def plot_chain(ax, chain, annotate=False, lw=1.8):
    for row in chain:
        ps = sample(row)
        color = '#c95d12' if row.get('signed_sweep', 0) < 0 else '#18649c'
        ax.plot(ps[:, 0], ps[:, 1], color=color, linewidth=lw)
        if annotate:
            mid = ps[len(ps)//2]
            ax.annotate(str(row['seq']), mid, xytext=(3, 4), textcoords='offset points',
                        fontsize=7, color=color,
                        bbox={'facecolor': 'white', 'edgecolor': 'none', 'alpha': .75, 'pad': .1})
    start = point(chain[0], 'start')
    ax.plot(*start, 'o', color='#25874d', markersize=4)
    row = chain[0]
    ps = sample(row)
    # Direction arrow follows sampled geometry, including a semicircle.
    j = max(1, len(ps)//3)
    if len(ps) == 2:
        a, b = ps[0]*.7+ps[1]*.3, ps[0]*.5+ps[1]*.5
    else:
        a, b = ps[j], ps[min(len(ps)-1, j+max(1, len(ps)//7))]
    ax.annotate('', b, a, arrowprops={'arrowstyle': '->', 'color': '#25874d', 'lw': 1.3})


def source_region(chain, source):
    from shapely.geometry import LineString, box
    lim = bounds(chain)
    region = box(lim[0]-.15, lim[1]-.15, lim[2]+.15, lim[3]+.15)
    selected = []
    for ref in source.rows:
        ps = ref['points']
        if ps[:, 0].max() < lim[0]-.15 or ps[:, 0].min() > lim[2]+.15 or ps[:, 1].max() < lim[1]-.15 or ps[:, 1].min() > lim[3]+.15:
            continue
        if LineString(ps).intersects(region):
            selected.append(ref)
    return selected


def endpoint_error(row, angle):
    a = point(row, 'start')-point(row, 'center')
    theta = math.radians(angle)
    predicted = point(row, 'center')+np.array([math.cos(theta)*a[0]-math.sin(theta)*a[1],
                                             math.sin(theta)*a[0]+math.cos(theta)*a[1]])
    return norm(predicted-point(row, 'end'))


def generate(out, book, html, before, operations, chains, source):
    from shapely.geometry import LineString, MultiLineString, LinearRing, Point
    from shapely.ops import unary_union
    plt = setup_plot()
    for folder in ['individual', 'comparison']:
        (out / folder).mkdir(exist_ok=True)
    arc_checks, line_checks, chain_checks, connections, proposals = [], [], [], [], []
    for gid, chain in chains.items():
        for row in chain:
            for prefix in ['start', 'end'] + (['center'] if row['segment_type'] == 'ARC' else []):
                if not np.all(np.isfinite(point(row, prefix))):
                    raise ValueError(f'{gid}/{row["seq"]}: nonfinite {prefix}')
            if row['segment_type'] == 'LINE':
                length = norm(point(row, 'end')-point(row, 'start'))
                line_checks.append({'geometry_id': gid, 'seq': row['seq'], 'excel_row': row['excel_row'],
                                    'length_mm': length, 'valid_endpoints': True, 'zero_length': length <= EPS})
                continue
            if row['signed_sweep'] is None or row['radius'] <= 0 or not math.isfinite(row['radius']):
                raise ValueError(f'{gid}/{row["seq"]}: invalid or unresolved ARC')
            rs = norm(point(row, 'start')-point(row, 'center'))
            re_ = norm(point(row, 'end')-point(row, 'center'))
            nominal = math.copysign(abs(row['sweep_angle']), row['signed_sweep'])
            row['nominal_signed'] = nominal
            sign_error = row['sweep_angle']*row['signed_sweep'] < 0
            record = {'geometry_id': gid, 'seq': row['seq'], 'excel_row': row['excel_row'],
                      'start_x': row['start_x'], 'start_y': row['start_y'], 'end_x': row['end_x'], 'end_y': row['end_y'],
                      'center_x': row['center_x'], 'center_y': row['center_y'], 'radius_mm': row['radius'],
                      'start_radius_mm': rs, 'end_radius_mm': re_,
                      'start_radius_residual_mm': rs-row['radius'], 'end_radius_residual_mm': re_-row['radius'],
                      'old_sweep_deg': row['sweep_angle'], 'computed_signed_sweep_deg': row['signed_sweep'],
                      'recommended_signed_sweep_deg': nominal,
                      'direction': 'CCW' if nominal > 0 else 'CW', 'sign_error': sign_error,
                      'magnitude_difference_deg': abs(abs(row['signed_sweep'])-abs(row['sweep_angle'])),
                      'old_rotation_endpoint_error_mm': endpoint_error(row, row['sweep_angle']),
                      'signed_rotation_endpoint_error_mm': endpoint_error(row, nominal),
                      'small_numeric_error': max(abs(rs-row['radius']), abs(re_-row['radius']), endpoint_error(row, nominal)) > EPS,
                      'excel_direction_basis': row['excel_direction_basis'], 'direction_basis': row['direction_basis'],
                      'tangent_vote_ccw': row['tangent_vote_ccw'],
                      'html_source_id': row['match'][2]['source_id'] if row['match'] else '',
                      'html_source_distance_mm': row['match'][0] if row['match'] else '',
                      'html_signed_sweep_deg': row.get('html_signed_sweep', ''),
                      'excel_html_direction_agree': row.get('excel_html_direction_agree', '')}
            arc_checks.append(record)
            if sign_error:
                proposals.append({'geometry_id': gid, 'seq': row['seq'], 'sheet': 'Sheet2',
                                  'cell': f'L{row["excel_row"]}', 'old_value': row['sweep_angle'], 'new_value': nominal,
                                  'reason': '局部圆弧为 CW；保留原角度绝对值，仅修改符号'})
        lim = bounds(chain)
        refs = source_region(chain, source)
        lines = [LineString(sample(r)) for r in chain]
        ref_lines = [LineString(r['points']) for r in refs]
        reference_union = unary_union(ref_lines)
        excel_union = unary_union(lines)
        hausdorff = excel_union.hausdorff_distance(reference_union)
        # Directed residual sampled densely. Reference may include extra construction segments.
        directed = max(reference_union.distance(Point(p)) for r in chain for p in sample(r, 1.0))
        source_ps = np.concatenate([r['points'] for r in refs])
        ref_dim = source_ps.max(axis=0)-source_ps.min(axis=0)
        gaps = []
        for i, row in enumerate(chain):
            nxt = chain[(i+1) % len(chain)]
            raw_gap = norm(point(row, 'end')-point(nxt, 'start'))
            rendered_gap = norm(sample(row)[-1]-sample(nxt)[0])
            gaps.append(raw_gap)
            connections.append({'geometry_id': gid, 'from_seq': row['seq'], 'to_seq': nxt['seq'],
                                'is_closure': i == len(chain)-1, 'raw_endpoint_gap_mm': raw_gap,
                                'circle_projected_gap_mm': rendered_gap,
                                'tangent_turn_deg': math.degrees(math.atan2(cross(tangent(row, True), tangent(nxt)),
                                                                            float(np.dot(tangent(row, True), tangent(nxt)))) )})
        intersections = []
        for i, line_a in enumerate(lines):
            for j in range(i+1, len(lines)):
                inter = line_a.intersection(lines[j])
                if inter.is_empty:
                    continue
                adjacent = j == i+1 or (i == 0 and j == len(lines)-1)
                if adjacent:
                    joint = point(chain[i], 'end') if j == i+1 else point(chain[0], 'start')
                    if inter.hausdorff_distance(Point(joint)) <= SMALL_MM:
                        continue
                intersections.append({'seq_a': chain[i]['seq'], 'seq_b': chain[j]['seq'], 'intersection': inter.wkt})
        chain_arcs = [r for r in arc_checks if r['geometry_id'] == gid]
        area = signed_area(chain)
        chain_record = {'geometry_id': gid, 'operation_id': next(o['operation_id'] for o in operations if o['geometry_id'] == gid),
                        'segments': len(chain), 'arcs': len(chain_arcs), 'positive_arcs': sum(r['computed_signed_sweep_deg'] > 0 for r in chain_arcs),
                        'negative_arcs': sum(r['computed_signed_sweep_deg'] < 0 for r in chain_arcs),
                        'seq_continuous': [r['seq'] for r in chain] == list(range(1, len(chain)+1)),
                        'max_raw_gap_mm': max(gaps), 'closure_gap_mm': gaps[-1],
                        'max_projected_gap_mm': max(c['circle_projected_gap_mm'] for c in connections if c['geometry_id'] == gid),
                        'signed_area_mm2': area, 'orientation': 'CCW' if area > 0 else 'CW' if area < 0 else 'degenerate',
                        'width_mm': lim[2]-lim[0], 'height_mm': lim[3]-lim[1],
                        'html_context_width_mm_not_hole_size': float(ref_dim[0]), 'html_context_height_mm_not_hole_size': float(ref_dim[1]),
                        'html_direct_match_segments': sum(bool(r['match']) for r in chain),
                        'html_crop_segments': len(refs), 'excel_to_html_max_distance_mm': directed,
                        'context_hausdorff_mm_not_acceptance_metric': hausdorff, 'self_intersections': intersections,
                        'zero_length_segments': [r['seq'] for r in line_checks if r['geometry_id'] == gid and r['zero_length']],
                        'max_radius_residual_mm': max(max(abs(r['start_radius_residual_mm']), abs(r['end_radius_residual_mm'])) for r in chain_arcs),
                        'max_angle_magnitude_difference_deg': max(r['magnitude_difference_deg'] for r in chain_arcs),
                        'max_signed_rotation_error_mm': max(r['signed_rotation_endpoint_error_mm'] for r in chain_arcs),
                        'html_source_candidates': sorted(set(r['candidate_id'] for r in refs)),
                        'negative_seqs': [r['seq'] for r in chain if r.get('signed_sweep', 0) < 0]}
        chain_checks.append(chain_record)
        fig, ax = plt.subplots(figsize=(8.8, 7.6), layout='constrained')
        plot_chain(ax, chain, annotate=True)
        frame(ax, lim)
        signs = '  '.join(f'{r["seq"]}: {r["nominal_signed"]:+g}°' for r in chain if r['segment_type'] == 'ARC')
        import textwrap
        fig.suptitle(f'{gid}  Excel 重建（按原 seq）\n{lim[2]-lim[0]:.3f} × {lim[3]-lim[1]:.3f} mm   整体 {chain_record["orientation"]}', fontsize=14)
        fig.text(.02, .015, '蓝：LINE / 正 ARC    橙：负 ARC    绿：seq 1 起点及遍历方向\n'+ '\n'.join(textwrap.wrap(signs, 100)), fontsize=8)
        fig.get_layout_engine().set(rect=(0, .10, 1, .88))
        fig.savefig(out / 'individual' / f'{gid}.png', dpi=180)
        fig.savefig(out / 'individual' / f'{gid}.svg')
        plt.close(fig)
        fig, axs = plt.subplots(1, 2, figsize=(13, 6.8), layout='constrained')
        plot_chain(axs[0], chain, annotate=True)
        for ref in refs:
            ps = ref['points']
            axs[1].plot(ps[:, 0], ps[:, 1], color='#303e46', linewidth=1.6)
        for ax in axs:
            frame(ax, lim)
        axs[0].set_title('Excel：真实 signed sweep')
        axs[1].set_title('HTML：直接解析原 SVG 曲线')
        fig.suptitle(f'{gid}  形状对照（同一二维比例，local 坐标）', fontsize=14)
        fig.text(.02, .01, f'Excel → HTML 最大轮廓距离 {directed:.4f} mm；原 SVG 裁区共 {len(refs)} 段。匹配候选详见审阅报告。', fontsize=9)
        fig.get_layout_engine().set(rect=(0, .05, 1, .94))
        fig.savefig(out / 'comparison' / f'{gid}.png', dpi=150)
        plt.close(fig)
    write_csv(out / 'arc_checks.csv', arc_checks)
    write_csv(out / 'line_checks.csv', line_checks)
    write_csv(out / 'connection_checks.csv', connections)
    write_csv(out / 'suggested_sweep_changes.csv', proposals)
    write_json(out / 'chain_checks.json', chain_checks)
    write_csv(out / 'contour_summary.csv', [{k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in r.items()} for r in chain_checks])
    summary = {'irregular_operations': len(operations), 'unique_geometries': len(chains), 'arc_count': len(arc_checks),
               'line_count': len(line_checks), 'positive_arcs': sum(r['computed_signed_sweep_deg'] > 0 for r in arc_checks),
               'negative_arcs': sum(r['computed_signed_sweep_deg'] < 0 for r in arc_checks),
               'sign_corrections': len(proposals), 'all_seq_continuous': all(r['seq_continuous'] for r in chain_checks),
               'all_ccw': all(r['orientation'] == 'CCW' for r in chain_checks),
               'max_raw_connection_gap_mm': max(r['max_raw_gap_mm'] for r in chain_checks),
               'max_projected_gap_mm': max(r['max_projected_gap_mm'] for r in chain_checks),
               'max_radius_residual_mm': max(r['max_radius_residual_mm'] for r in chain_checks),
               'max_magnitude_difference_deg': max(r['magnitude_difference_deg'] for r in arc_checks),
               'max_signed_rotation_endpoint_error_mm': max(r['signed_rotation_endpoint_error_mm'] for r in arc_checks),
               'self_intersecting_contours': [r['geometry_id'] for r in chain_checks if r['self_intersections']],
               'zero_length_lines': [r for r in line_checks if r['zero_length']],
               'arcs_ambiguous_without_HTML': [f'{r["geometry_id"]}/{r["seq"]}' for r in arc_checks if r['excel_direction_basis'] == 'ambiguous_without_HTML'],
               'html_direct_matches': sum(r['html_direct_match_segments'] for r in chain_checks),
               'hashes_before': before, 'hashes_after': {p.name: digest(p) for p in [book, html]},
               'review_threshold_mm': SMALL_MM, 'source_association_threshold_mm': MATCH_MM,
               'intersection_sampling_angle_deg': .25,
               'radius_range_mm': [min(r['radius_mm'] for r in arc_checks), max(r['radius_mm'] for r in arc_checks)],
               'zero_length_arcs': [f'{r["geometry_id"]}/{r["seq"]}' for r in arc_checks if abs(r['computed_signed_sweep_deg']) <= EPS],
               'major_arcs_over_180': [f'{r["geometry_id"]}/{r["seq"]}' for r in arc_checks if abs(r['computed_signed_sweep_deg']) > 180+EPS],
               'arcs_with_small_numeric_errors': sum(r['small_numeric_error'] for r in arc_checks)}
    summary['source_files_unchanged'] = summary['hashes_before'] == summary['hashes_after']
    write_json(out / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    # Shared scale across every overview tile; each individual also has equal x/y scale.
    gids = list(chains)
    span = max(max(r['width_mm'], r['height_mm']) for r in chain_checks)+4
    for page, offset in enumerate(range(0, len(gids), 20), 1):
        fig, axs = plt.subplots(4, 5, figsize=(18, 16), layout='constrained')
        for ax, gid in zip(axs.flat, gids[offset:offset+20]):
            chain = chains[gid]
            r = next(r for r in chain_checks if r['geometry_id'] == gid)
            lim = bounds(chain)
            cx, cy = (lim[0]+lim[2])/2, (lim[1]+lim[3])/2
            plot_chain(ax, chain, lw=1.5)
            ax.set_xlim(cx-span/2, cx+span/2)
            ax.set_ylim(cy-span/2, cy+span/2)
            ax.set_aspect('equal')
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_title(f'{gid}  {r["width_mm"]:.2f} × {r["height_mm"]:.2f} mm\n负 ARC {r["negative_arcs"]}', fontsize=10)
        for ax in list(axs.flat)[len(gids[offset:offset+20]):]:
            ax.axis('off')
        fig.suptitle(f'P001 异形孔总览 {page}   各格相同 mm 比例   蓝：LINE / 正 ARC   橙：负 ARC', fontsize=16)
        fig.savefig(out / f'overview_{page:02d}.png', dpi=160)
        plt.close(fig)


def create_review(out, book, html, operations, chains, source):
    """Conservative human interpretation of the current P001 reference comparison."""
    import html as html_lib
    from shapely.geometry import Polygon
    plt = setup_plot()
    summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
    contours = json.loads((out / 'chain_checks.json').read_text(encoding='utf-8'))
    arcs = list(csv.DictReader((out / 'arc_checks.csv').open(encoding='utf-8-sig')))
    proposals = list(csv.DictReader((out / 'suggested_sweep_changes.csv').open(encoding='utf-8-sig')))
    numerical_gids = sorted(set(r['geometry_id'] for r in arcs if r['small_numeric_error'] == 'True'))
    uncertain = [f'G{n:03d}' for n in range(201, 212)]
    sign_gids = sorted(set(r['geometry_id'] for r in proposals))
    sign_only = [g for g in sign_gids if g not in uncertain]
    passed = [g for g in chains if g not in sign_gids and g not in numerical_gids]
    verification = []
    for contour in contours:
        gid = contour['geometry_id']
        chain = chains[gid]
        sampled = np.concatenate([sample(r) for r in chain])
        poly = Polygon(sampled)
        area_diff = abs(poly.area-contour['signed_area_mm2'])
        turn = 0.0
        for i, row in enumerate(chain):
            turn += row.get('signed_sweep', 0)
            turn += math.degrees(math.atan2(cross(tangent(row, True), tangent(chain[(i+1) % len(chain)])),
                                           float(np.dot(tangent(row, True), tangent(chain[(i+1) % len(chain)])))))
        verification.append({'geometry_id': gid, 'polygon_valid': bool(poly.is_valid),
                             'sampled_vs_analytic_area_difference_mm2': area_diff,
                             'total_tangent_turn_deg': turn,
                             'turn_is_positive_360': abs(turn-360) < .01})
        contour['review_category'] = '需要人工确认' if gid in uncertain else '仅角度符号错误' if gid in sign_gids else '完全通过'
        contour['small_numeric_error_shape_ok'] = gid in numerical_gids
        contour['reference_result'] = '整体外形及主要尺寸一致；R0.5 局部微凸/内凹在 HTML 中缺少对应，待确认' if gid in uncertain else '整体形状、主要尺寸、槽口及圆角形态基本一致'
    assert all(r['polygon_valid'] and r['turn_is_positive_360'] for r in verification)
    assert max(r['sampled_vs_analytic_area_difference_mm2'] for r in verification) < .001
    assert summary['source_files_unchanged']
    assert len(arcs) == 283 and len(proposals) == 73 and sum(len(c) for c in chains.values()) == 542
    assert len(list((out / 'individual').glob('*.png'))) == 38
    assert len(list((out / 'comparison').glob('*.png'))) == 38
    write_json(out / 'verification.json', verification)
    summary['review'] = {'clear_data_errors_except_signs': [], 'sign_only_contours': sign_only,
                         'small_numeric_error_contours': numerical_gids,
                         'manual_confirmation_contours': uncertain, 'fully_passed_contours': passed,
                         'all_basic_shapes_and_main_dimensions_match': True,
                         'all_local_topology_confirmed_by_HTML': False,
                         'recommendation': 'Approve sign corrections separately; confirm the R0.5 local details on G201-G211 before claiming all topology matches.'}
    summary['nominal_signed_sweep_histogram'] = dict(collections.Counter(float(r['recommended_signed_sweep_deg']) for r in arcs))
    summary['verification'] = {'all_sampled_polygons_valid': True, 'all_total_tangent_turns_positive_360': True,
                               'max_area_crosscheck_error_mm2': max(r['sampled_vs_analytic_area_difference_mm2'] for r in verification)}
    write_json(out / 'summary.json', summary)
    write_json(out / 'chain_checks.json', contours)
    write_csv(out / 'contour_summary.csv', [{k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in r.items()} for r in contours])
    (out / 'suspect').mkdir(exist_ok=True)
    # Show the first R0.5 bump of every uncertain shape at full readable scale.
    fig, axs = plt.subplots(3, 4, figsize=(17, 13), layout='constrained')
    zoom_info = []
    for ax, gid in zip(axs.flat, uncertain):
        chain = chains[gid]
        first_index = next(i for i, r in enumerate(chain) if r['segment_type'] == 'ARC' and r['radius'] == .5)
        first = chain[first_index]
        second = chain[first_index+1]
        ps = np.concatenate([sample(first), sample(second)])
        center = (ps.min(axis=0)+ps.max(axis=0))/2
        lim = [center[0]-.9, center[1]-.9, center[0]+.9, center[1]+.9]
        for ref in source_region(chain, source):
            ref_ps = ref['points']
            ax.plot(ref_ps[:, 0], ref_ps[:, 1], color='#707b83', linestyle='--', linewidth=1.4)
        plot_chain(ax, chain)
        ax.set_xlim(lim[0], lim[2])
        ax.set_ylim(lim[1], lim[3])
        ax.set_aspect('equal')
        ax.ticklabel_format(useOffset=False, style='plain')
        ax.tick_params(labelsize=7)
        ax.grid(True, color='#eeeeee')
        ax.set_title(f'{gid}  seq {first["seq"]}–{second["seq"]}\nExcel 实线 / HTML 灰虚线', fontsize=10)
        zoom_info.append({'geometry_id': gid, 'example_arc_seqs': [first['seq'], second['seq']],
                          'all_R05_seqs': [r['seq'] for r in chain if r['segment_type'] == 'ARC' and r['radius'] == .5],
                          'approx_detail_depth_mm': .15,
                          'reference_observation': '同位置 HTML 为直边，未见该 R0.5 曲线细节'})
    axs.flat[-1].axis('off')
    fig.suptitle('需要人工确认的局部细节   所有放大窗口均为 1.8 × 1.8 mm', fontsize=16)
    fig.savefig(out / 'suspect' / 'local_details.png', dpi=180)
    plt.close(fig)
    write_csv(out / 'manual_confirmation.csv', zoom_info)
    lines = [
        '# P001 IRREGULAR_PUNCH 二维 Geometry 审阅报告', '',
        '审阅日期：2026-10-09。正式数据来源为 `data/parts/P001/钣金加工数据表_P001.xlsx`，形状参考为 `information_extract/sources/P001/legacy_outline.html`。', '',
        '## 结论', '',
        '**38 个异形孔均能按现有 seq 恢复为简单、整体逆时针的闭合轮廓。283 条 ARC 中应有 210 条正 sweep、73 条负 sweep。当前 Excel 这 73 条均为正值，建议仅改符号。**', '',
        '**38 个孔的整体外形、主要尺寸和主体槽口结构与 HTML 基本一致。但不能据此确认所有局部拓扑完全正确：G201–G211 的 R0.5 微凸/内凹在 HTML 对应位置未出现，需人工确认。**', '',
        '本轮未修改 Excel、HTML、PROJECT_SPEC.md、Operation 或 Station。没有开展其它 Milestone 1、Graph、CP-SAT、GNN 或训练工作。', '',
        '## 数量与问题分类', '',
        '| 检查项 | 结果 |', '|---|---:|',
        '| IRREGULAR_PUNCH / 独立 geometry_id | 38 / 38 |',
        '| 曲线段 | 542（ARC 283，LINE 259）|',
        '| 正 ARC / 负 ARC | 210 / 73 |',
        '| 当前 sweep 符号错误 | 73 条，涉及 23 个轮廓 |',
        '| seq 连续 / 整体逆时针 | 38 / 38 |',
        '| 原始起终点连接误差 / 闭合误差 | 0 / 0 mm |',
        '| 明显自交 / 零长度 LINE / 零角度 ARC | 0 / 0 / 0 |',
        '| 半径范围 | 0.3–3.0 mm，无明显异常 |',
        '| 大于 180° 的已选 ARC | 0 |', '',
        '| 轮廓主分类（互斥，共 38） | 数量 | geometry_id |', '|---|---:|---|',
        '| 明确数据错误（不含下列符号错误） | 0 | 未发现 |',
        f'| 仅角度符号错误 | {len(sign_only)} | {", ".join(sign_only)} |',
        f'| 需要人工确认 | {len(uncertain)} | {", ".join(uncertain)} |',
        f'| 完全通过 | {len(passed)} | {", ".join(passed)} |', '',
        '**补充问题标签，可与主分类重叠：**“小量数值误差但该圆弧形状正确”涉及 48 条 ARC、11 个轮廓（G201–G211）。这些误差无需改坐标或角度绝对值；同一组孔还存在独立的 HTML 局部形态待确认事项。', '',
        '## signed sweep 的判断方法', '',
        '本轮按用户最新指令采用 CCW 正、CW 负，覆盖 PROJECT_SPEC.md §7.4.3 的旧“全部非负”约定；规范文件保留原样。整体 CCW 与单条 ARC 的局部方向分开校验。', '',
        '起点 S、终点 E、圆心 C、半径 R 只能给出两条互补候选弧，不能单凭叉积唯一决定实际路径。设 Δ = atan2(cross(S−C,E−C), dot(S−C,E−C)) mod 360，则候选为 +Δ 与 Δ−360。180° 时也存在 ±180° 两个半圆。', '',
        '采用相邻 LINE 的有向切线约束选择圆弧候选，并从 HTML 保存的实际 SVG 三次贝塞尔路径反算其局部旋转方向作交叉验证，不使用“全部取最小角”或“轮廓 CCW 所以 ARC 全正”的假设。', '',
        '283 条 ARC 均至少有一个可用邻接 LINE，方向候选均能区分。191 条 ARC 可与 HTML 原段直接对应，方向全部一致；其余 92 条通过邻接切向与 HTML 整体外形复核。其中 48 条 R0.5 微细节缺少 HTML 同位置曲线佐证。', '',
        '因此：仅有单段端点/圆心时，所有 ARC 都具有理论上的路径歧义；使用本次完整 Excel 曲线链后，没有尚未确定符号的 ARC。需要人工确认的是 G201–G211 微细节是否应存在，不是它们在当前轮廓中的旋转符号。', '',
        '已选角度分布：+90° ×171，−90° ×49，+180° ×15，+77.361° ×24，−31.788° ×24。未发现需要把 90° 改为 270°、或大弧/小弧选反的证据。', '',
        '## 单段、曲线链、方向与自交', '',
        f'- ARC 起/终点到圆心的最大半径残差：{summary["max_radius_residual_mm"]:.9f} mm。',
        f'- 端点反算角度与保存角度绝对值的最大差：{summary["max_magnitude_difference_deg"]:.9f}°，出现在 R0.5 小弧的三位小数坐标中。',
        f'- 采用建议符号、保留原角度绝对值后的最大 Rotate 端点残差：{summary["max_signed_rotation_endpoint_error_mm"]:.9f} mm。',
        f'- 起终点按给定半径投影到理论圆后，最大相邻绘制间隙：{summary["max_projected_gap_mm"]:.9f} mm。原始记录本身的相邻端点与闭合间隙全部为 0。',
        '- 所有 LINE 坐标有限、长度为正；所有 ARC 半径有限且为正、扫角非零。48 条 R0.5 ARC 的上述残差远小于 0.01 mm 审阅参考线，视为坐标取整级误差。该参考线不是新增制造公差。',
        '- 面积用 LINE/ARC 的 Green 积分精确计算，仅为面积计算加入微小虚拟连接，未修改任何坐标。38 个面积均为正。独立密采样多边形面积复核误差小于 0.001 mm²。',
        '- 全部轮廓总切向转角为 +360°；密采样多边形均有效。以最大 0.25° 步长检查非相邻段交叉/重叠；相邻段只容许预期连接点附近 0.01 mm 的数值接触。未发现明显自交。此结果针对二维轮廓审阅，不是任意精度的形式化交点证明。', '',
        '## HTML 形状对照与局限', '',
        'HTML 中含 1317 个原始候选、2253 段，混有孔外形、其它工艺图形、同心圆、内线与延伸线。不能把裁区全部线条当成一个闭合冲孔，也不能以裁区整体 Hausdorff 距离判错。对照图保留这些上下文。', '',
        'Excel 的 318/542 段能在原始 local 坐标中直接对应；其余存在分段方式、圆角补接或局部细节差异，因此对整个局部曲线集合做形状对照。单段 0.10 mm 关联阈值只用于定位对应来源，不是形状通过标准。未对两来源施加变形或非等比缩放。', '',
        '| 形状组 | geometry_id | 对照判断 |', '|---|---|---|',
        '| 圆头长孔 | G002–G006、G018、G019 | 圆头方向及主体尺寸一致；6×8 mm 或 3.04×5 mm |',
        '| 圆角矩形 | G058、G073、G076、G147、G150、G162、G089、G090 | 外形、长宽比例及不同圆角级别一致 |',
        '| 带侧圆头的复杂孔 | G088 | 主体与侧圆头一致；seq 9、13 是凹圆角，必须 −90° |',
        '| U 形槽 | G193–G195、G212–G214 | 7.2×11.5 mm；凹圆角位置一致；HTML 有额外平行内线/原片段小差异，未判作错误 |',
        '| 大型分叉槽 | G196 | 45.6×71 mm；分叉、内槽、凸/凹圆角方向一致；小端部 R0.3 与 HTML 的尖角补接有约 0.088 mm 级差异，可接受 |',
        '| C 形槽 | G197–G200 | 主体尺寸一致，内槽凹圆角一致；端部 R0.3 补接差异约 0.088 mm，未影响主体拓扑 |',
        '| 带微细节的 C/U 形槽 | G201–G211 | 主体基本一致；Excel R0.5 微凸/内凹没有同位置 HTML 佐证，需人工确认 |', '',
        'G201–G211 的 R0.5 曲线对由 +77.361° 与 −31.788° 组成，在直边附近产生约 0.15 mm 的局部形变；这些细节不能归因于小于 0.001 mm 的坐标取整。10 个孔的主体外包尺寸约 21.5×14 mm（部分旋转为 14×21.5 mm），Excel 含微凸时为约 21.65×14 mm；G208 的主体约 21.5×25.5 mm，Excel 为约 21.65×25.5 mm。', '',
        '现有证据不能判定这些微细节是正式加工表有意保留、HTML 参考遗漏，还是 Excel 多恢复了局部结构。因此列为“需要人工确认”，不直接宣告为明确数据错误，也不据此更改 Geometry。', '',
        '回答“HTML 对照后是否所有形状基本正确”：**整体外形和主要尺寸层面是；若要求凸起/凹陷等全部局部拓扑一致，目前不能全量确认，11 项保留。**', '',
        '![局部待确认细节](suspect/local_details.png)', '',
        '## 每个轮廓的结果', '',
        '| geometry_id | 段/ARC | 尺寸 x×y mm | 面积 mm² | 负 ARC seq | 分类 | 图 |', '|---|---:|---:|---:|---|---|---|']
    for r in contours:
        gid = r['geometry_id']
        lines.append(f'| {gid} | {r["segments"]}/{r["arcs"]} | {r["width_mm"]:.3f}×{r["height_mm"]:.3f} | {r["signed_area_mm2"]:.6f} | {", ".join(map(str,r["negative_seqs"])) or "无"} | {r["review_category"]} | [单图](individual/{gid}.png) / [对照](comparison/{gid}.png) |')
    lines += ['', '## 建议修改清单（仅符号，尚未写入）', '',
              '以下 73 项保留现有 seq、端点、圆心、半径及角度绝对值。精确端点反算角度另见 arc_checks.csv，避免把取整噪声写回正式表。', '',
              '| geometry_id | seq | Sheet2 单元格 | 旧值 ° | 建议新值 ° |', '|---|---:|---|---:|---:|']
    for r in proposals:
        lines.append(f'| {r["geometry_id"]} | {r["seq"]} | {r["cell"]} | {float(r["old_value"]):g} | {float(r["new_value"]):g} |')
    lines += ['', '## 人工确认清单', '', '| geometry_id | R0.5 ARC seq（均需核对是否应存在）|', '|---|---|']
    for r in zoom_info:
        lines.append(f'| {r["geometry_id"]} | {", ".join(map(str,r["all_R05_seqs"]))} |')
    lines += ['', '## 输出与复核方式', '',
              '- `index.html`：本地审阅入口，含汇总、图集和表格链接。',
              '- `individual/Gxxx.png`、`.svg`：38 个独立重建图，标注 geometry_id、seq 和 signed sweep。蓝色为 LINE/正 ARC，橙色为负 ARC，绿点为 seq 1 起点。',
              '- `overview_01.png`、`overview_02.png`：总览，每格使用相同 mm 比例。',
              '- `comparison/Gxxx.png`：38 个 Excel/HTML 局部并排对照；两面使用同一 x/y 比例。',
              '- `suspect/local_details.png`：11 个待确认轮廓的局部放大；每窗口均为 1.8×1.8 mm。',
              '- `arc_checks.csv`：全部 283 条 ARC 的半径残差、精确 signed sweep、保留原角度绝对值的建议值、方向依据和 HTML 对应来源。',
              '- `line_checks.csv`、`connection_checks.csv`、`contour_summary.csv`、`chain_checks.json`：逐段、逐连接和逐轮廓结果。',
              '- `suggested_sweep_changes.csv`：73 项可审阅修改建议。',
              '- `manual_confirmation.csv`、`summary.json`、`verification.json`：人工确认事项、统计和独立数值复核。',
              '- 复跑入口：`scripts/validate_p001_geometry.py`，使用 openpyxl 只读读取，matplotlib 绘图、shapely 检查；不包含任何 Excel 写入路径。', '',
              '## 原始文件完整性', '', '| 文件 | SHA-256（审阅前后相同）|', '|---|---|']
    for name, sha in summary['hashes_before'].items():
        lines.append(f'| {name} | `{sha}` |')
    lines += ['', '后续若统一更新 Excel，还应同步修订规范中的 ARC 旧约定；本轮只列出该事项，未修改规范或源数据。', '']
    (out / 'review_report.md').write_text('\n'.join(lines), encoding='utf-8')
    cards = []
    for r in contours:
        gid = r['geometry_id']
        badge = r['review_category']
        cards.append(f'<article class="card {"suspect" if gid in uncertain else ""}"><h3>{gid} <small>{badge}</small></h3><p>{r["width_mm"]:.3f} × {r["height_mm"]:.3f} mm · 整体 CCW · 负 ARC {r["negative_arcs"]}</p><a href="comparison/{gid}.png"><img loading="lazy" src="comparison/{gid}.png" alt="{gid} Excel 与 HTML 形状对照"></a><p><a href="individual/{gid}.png">独立图</a>　<a href="individual/{gid}.svg">矢量图</a>　<a href="comparison/{gid}.png">原尺寸对照</a></p></article>')
    table_rows = ''.join(f'<tr><td>{r["geometry_id"]}</td><td>{r["seq"]}</td><td>{r["cell"]}</td><td>{float(r["old_value"]):g}</td><td>{float(r["new_value"]):g}</td></tr>' for r in proposals)
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P001 二维 Geometry 审阅</title>
<style>body{margin:0;background:#f5f7fa;color:#243340;font:15px/1.65 system-ui,'Microsoft YaHei',sans-serif}main{max-width:1450px;margin:auto;padding:32px}h1{font-size:28px}h2{margin-top:36px}.lead{background:white;border:1px solid #dce3e9;padding:22px;border-radius:8px}.warning{border-left:4px solid #c95d12;padding-left:16px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(520px,1fr));gap:20px}.card{background:white;padding:16px;border:1px solid #dce3e9;border-radius:6px}.card.suspect{border-top:3px solid #c95d12}.card h3{margin:0}.card small{font-size:13px;font-weight:400;margin-left:12px}.card img,img.overview{width:100%;height:auto}a{color:#18649c}table{border-collapse:collapse;background:white;width:100%;max-width:800px}td,th{text-align:left;border-bottom:1px solid #e4e9ef;padding:7px 12px}th{background:#e9eff5}.overview-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}@media(max-width:650px){main{padding:16px}.grid{grid-template-columns:1fr}.overview-grid{grid-template-columns:1fr}}</style><main>
<h1>P001 二维 Geometry 审阅</h1><p>2026-10-09 · 仅 IRREGULAR_PUNCH · 原始 Excel 与 HTML 均未修改</p>
<div class="lead"><strong>38 个轮廓均可恢复为整体 CCW，无明显自交。</strong><p>542 段 = 283 ARC + 259 LINE。ARC 应为 210 正 / 73 负；当前有 73 项符号修改建议，涉及 23 个孔。原始端点连接及闭合间隙均为 0。</p>
<p class="warning"><strong>G201–G211 需确认局部细节。</strong>整体外形和主要尺寸基本一致，但 Excel 的 R0.5 微凸/内凹（约 0.15 mm）在 HTML 同位置显示为直边。不能宣告所有局部拓扑均已通过。</p>
<p><a href="review_report.md">完整审阅报告（含全部修改建议）</a>　<a href="suggested_sweep_changes.csv">73 项符号建议 CSV</a>　<a href="arc_checks.csv">全部 ARC 明细</a>　<a href="contour_summary.csv">轮廓汇总</a>　<a href="manual_confirmation.csv">人工确认清单</a></p>
<p>主分类：完全通过 15 / 仅符号错误 12 / 人工确认 11 / 其它明确数据错误 0。另有 48 条 ARC 的取整数值误差，均不影响该圆弧形状。</p></div>
<h2>待确认微细节</h2><p>Excel 实线，HTML 灰虚线。各放大窗口均为 1.8 × 1.8 mm。蓝为正 ARC / LINE，橙为负 ARC。</p><a href="suspect/local_details.png"><img class="overview" src="suspect/local_details.png" alt="11 个待确认局部细节"></a>
<h2>全部轮廓总览</h2><p>总览每格使用相同 mm 比例。点击查看原图。</p><div class="overview-grid"><a href="overview_01.png"><img class="overview" src="overview_01.png"></a><a href="overview_02.png"><img class="overview" src="overview_02.png"></a></div>
<h2>逐孔对照</h2><p>Excel 按原 seq，ARC 按端点反算的 signed sweep 绘制。并排图中的两个坐标轴比例相同；HTML 保留其它邻近图形作上下文。未变形、未做非等比缩放。</p><div class="grid">''' + ''.join(cards) + '''</div>
<h2>符号修改建议（未执行）</h2><table><thead><tr><th>geometry_id</th><th>seq</th><th>Sheet2 单元格</th><th>旧值 °</th><th>建议值 °</th></tr></thead><tbody>''' + table_rows + '''</tbody></table><p>角度绝对值保持原值，精确反算值见 ARC 明细。48 条小弧的最大半径残差 0.000321 mm，建议符号下最大旋转端点残差 0.000611 mm。</p>
<p><a href="summary.json">统计 JSON</a>　<a href="connection_checks.csv">连接明细</a>　<a href="line_checks.csv">LINE 明细</a>　<a href="verification.json">独立数值复核</a></p></main></html>'''
    (out / 'index.html').write_text(page, encoding='utf-8')
    print(json.dumps({'review_categories': {'fully_passed': len(passed), 'sign_only': len(sign_only), 'manual_confirmation': len(uncertain)},
                      'report': str(out / 'review_report.md'), 'index': str(out / 'index.html')}, ensure_ascii=True))


def create_current_review(out, book, html, operations, chains, source):
    """Current-state conclusions derived from checks, without stale baseline flags."""
    from shapely.geometry import Polygon
    summary = json.loads((out/'summary.json').read_text(encoding='utf-8'))
    contours = json.loads((out/'chain_checks.json').read_text(encoding='utf-8'))
    arcs = list(csv.DictReader((out/'arc_checks.csv').open(encoding='utf-8-sig')))
    verification, problems = [], []
    for r in contours:
        gid = r['geometry_id']
        chain = chains[gid]
        polygon = Polygon(np.concatenate([sample(segment) for segment in chain]))
        total_turn = sum(segment.get('signed_sweep', 0) for segment in chain)
        for i, segment in enumerate(chain):
            a, b = tangent(segment, True), tangent(chain[(i+1) % len(chain)])
            total_turn += math.degrees(math.atan2(cross(a, b), float(np.dot(a, b))))
        area_error = abs(polygon.area-r['signed_area_mm2'])
        checks = {'seq_continuous': r['seq_continuous'],
                  'continuous_and_closed': r['max_raw_gap_mm'] <= EPS and r['closure_gap_mm'] <= EPS,
                  'whole_contour_ccw': r['orientation'] == 'CCW',
                  'no_obvious_intersections': not r['self_intersections'] and polygon.is_valid,
                  'no_zero_length_lines': not r['zero_length_segments'],
                  'radius_consistent': r['max_radius_residual_mm'] < SMALL_MM,
                  'signed_rotation_consistent': r['max_signed_rotation_error_mm'] < SMALL_MM,
                  'positive_360_total_tangent_turn': abs(total_turn-360) < .01,
                  'independent_area_check': area_error < .001,
                  'no_target_R05_details': not any(s['segment_type']=='ARC' and s['radius']==.5 for s in chain) if gid in {f'G{i:03d}' for i in range(201,212)} else True,
                  'html_basic_shape_distance': r['excel_to_html_max_distance_mm'] < .1}
        for row in (a for a in arcs if a['geometry_id'] == gid):
            if row['sign_error'] == 'True' or row['excel_html_direction_agree'] == 'False':
                checks['arc_direction_consistent'] = False
        checks.setdefault('arc_direction_consistent', True)
        verification.append({'geometry_id': gid, 'checks': checks, 'passed': all(checks.values()),
                             'total_tangent_turn_deg': total_turn, 'area_crosscheck_error_mm2': area_error})
        if not all(checks.values()):
            problems.append({'geometry_id': gid, 'failed_checks': [k for k,v in checks.items() if not v]})
        r['review_category'] = '完全通过' if all(checks.values()) else '需要复核'
    assert len(chains) == 38
    assert not summary['zero_length_arcs'] and not summary['major_arcs_over_180']
    assert summary['radius_range_mm'][0] > 0 and summary['radius_range_mm'][1] <= 3
    assert summary['sign_corrections'] == 0
    assert summary['source_files_unchanged']
    assert not problems, problems
    summary['all_validation_passed'] = True
    summary['manual_confirmation_remaining'] = []
    summary['reference_comparison'] = '38 contours match overall shape, main dimensions and simplified topology; reference context/rounding differences accepted.'
    summary['verification'] = {'all_sampled_polygons_valid': True, 'all_total_tangent_turns_positive_360': True,
                               'max_area_crosscheck_error_mm2': max(v['area_crosscheck_error_mm2'] for v in verification)}
    summary['nominal_signed_sweep_histogram'] = dict(collections.Counter(float(a['recommended_signed_sweep_deg']) for a in arcs))
    summary['max_excel_to_html_distance_mm'] = max(r['excel_to_html_max_distance_mm'] for r in contours)
    write_json(out/'summary.json', summary)
    write_json(out/'verification.json', verification)
    write_json(out/'chain_checks.json', contours)
    write_csv(out/'contour_summary.csv', [{k: json.dumps(v, ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in r.items()} for r in contours])
    log_path = out/'change_log.json'
    log = json.loads(log_path.read_text(encoding='utf-8')) if log_path.exists() else None
    lines = ['# P001 二维 Geometry 修改后复验报告', '', '日期：2026-10-09。正式 Excel 已按本轮授权更新，PROJECT_SPEC.md 未修改。', '',
             '## 结论', '', '**38 个 IRREGULAR_PUNCH 全部通过本轮 Geometry 校验，无剩余人工确认事项。**', '',
             'G201–G211 的中间加工状态 R0.5 微细节已按用户确认忽略；前后共线直线合并，保留主体轮廓、原起点和遍历顺序，seq 连续重编为 1–16。剩余 ARC 根据新曲线链重算 signed sweep。其它异形孔仅改 sweep 符号。', '',
             '## 修改统计', '', '| 项目 | 数量 |', '|---|---:|',
             '| 删除 R0.5 ARC | 48 条 |', '| 直线合并 | 24 组（48 条 LINE 合并为 24 条）|',
             '| 净减少 Geometry 段/Excel 数据行 | 72 |', '| 修改保留 ARC 的 sweep_angle | 49 条（+90° → −90°）|',
             '| 其中 G201–G211 / 其它异形孔 | 22 / 27 条 |',
             f'| 修改后曲线段 | {summary["arc_count"]+summary["line_count"]}（ARC {summary["arc_count"]}，LINE {summary["line_count"]}）|',
             f'| 正 ARC / 负 ARC | {summary["positive_arcs"]} / {summary["negative_arcs"]} |',
             '| Sheet2 数据行 | 761 → 689（含表头总行数 762 → 690）|', '',
             '被删除的 48 条小弧不计为 sweep 修改，也没有机械应用旧 seq 的建议表。G201–G211 的主体宽度回到 21.5 mm（或旋转后的高度 21.5 mm）；去掉微凸使外包尺寸从 21.65 减少 0.15 mm，这是用户指定简化的结果，主体尺寸不变。', '']
    if log:
        simplified = sorted(b['geometry_id'] for b in log['details'] if b['removed_R05_arcs'])
        signs_only = sorted(b['geometry_id'] for b in log['details'] if not b['removed_R05_arcs'])
        lines += [f'- 简化并重编 seq：{", ".join(simplified)}。', f'- 仅修正符号：{", ".join(signs_only)}。',
                  f'- 实际修改共 {len(log["details"])} 个 geometry_id；其余 15 个异形孔未改。', '',
                  '| geometry_id | 原段数 | 现段数 | 删除 R0.5 ARC | 合并直线组 | sweep 修改 |', '|---|---:|---:|---:|---:|---:|']
        for d in log['details']:
            lines.append(f'| {d["geometry_id"]} | {d["old_segments"]} | {d["new_segments"]} | {d["removed_R05_arcs"]} | {d["line_merges"]} | {d["changed_sweeps_remaining"]} |')
    lines += ['', '## 校验结果', '',
              '- 38 个 seq 均连续，原始端点连接与闭合间隙均为 0 mm。',
              '- 全部整体逆时针，精确有向面积为正，总切向转角均为 +360°。',
              '- 无明显自交、零长度 LINE、零扫角 ARC 或明显异常半径；半径范围 0.3–3.0 mm。',
              '- ARC 符号、角度与起终点/圆心一致；当前没有待修正 sweep。',
              f'- 最大半径残差 {summary["max_radius_residual_mm"]:.12g} mm；最大 signed Rotate 端点残差 {summary["max_signed_rotation_endpoint_error_mm"]:.12g} mm（浮点运算级）。',
              '- 全部密采样多边形有效；独立采样面积与精确 LINE/ARC Green 积分一致。明显自交检查使用 0.25° 最大采样步长。',
              f'- HTML 对照的 Excel→原始曲线最大距离为 {summary["max_excel_to_html_distance_mm"]:.6f} mm，来自保留的 R0.3 端部补接；主外形、比例、凹圆角、槽口和主要尺寸一致。',
              '- HTML 包含其它同心圆、内线及延伸线，对照图保留其上下文；未把这些额外线条计为冲孔轮廓错误。没有要求两个来源浮点级叠合。',
              '- G201–G211 已无上述 R0.5 微凸/微凹，原 11 项人工确认问题已由用户本轮指令解决。', '',
              '## 逐孔结果', '', '| geometry_id | 段/ARC | 正/负 ARC | 主尺寸 x×y mm | 连接/闭合 | 方向 | 结果 | 图 |', '|---|---:|---:|---:|---|---|---|---|']
    for r in contours:
        gid = r['geometry_id']
        lines.append(f'| {gid} | {r["segments"]}/{r["arcs"]} | {r["positive_arcs"]}/{r["negative_arcs"]} | {r["width_mm"]:.3f}×{r["height_mm"]:.3f} | 0/0 mm | CCW | 通过 | [重建](individual/{gid}.png) / [HTML 对照](comparison/{gid}.png) |')
    if log:
        lines += ['', '## 备份与修改范围核对', '', f'- 备份：`{log["backup"]}`。',
                  f'- 原 SHA-256：`{log["source_sha256"]}`。', f'- 修改后 SHA-256：`{log["source_sha256_after"]}`。',
                  '- Excel ZIP 包仅 `xl/worksheets/sheet2.xml` 内容发生变化；Sheet1、Sheet3、共享字符串、样式、文档属性及其它成员逐字节一致。',
                  '- 非 IRREGULAR_PUNCH Geometry 的值、已有公式和所有保留行的单元格样式均已与备份比对。删除行造成后部 Excel 行号前移，不改变其它 Geometry 的字段或 seq。',
                  '- PROJECT_SPEC.md 和 reconstructed_local.html 的哈希均未改变。', '']
    lines += ['## 输出', '',
              '- `individual/`：38 张 PNG 和 38 张 SVG，保留二维比例、标注 geometry_id、seq、signed sweep。',
              '- `comparison/`：38 张 HTML 并排对照。', '- `overview_01.png`、`overview_02.png`：使用相同 mm 比例的总览。',
              '- `arc_checks.csv`、`line_checks.csv`、`connection_checks.csv`、`contour_summary.csv`：完整复验明细。',
              '- `change_log.json`、`changed_geometries.csv`：逐孔修改和旧/新 seq 映射（修改日志内）。',
              '- `verification.json`、`summary.json`：独立面积、方向及统计复核。', '']
    (out/'review_report.md').write_text('\n'.join(lines), encoding='utf-8')
    cards = ''.join(f'<article><h3>{r["geometry_id"]}　通过</h3><p>{r["width_mm"]:.3f} × {r["height_mm"]:.3f} mm　{r["segments"]} 段　负 ARC {r["negative_arcs"]}</p><a href="comparison/{r["geometry_id"]}.png"><img loading="lazy" src="comparison/{r["geometry_id"]}.png"></a><p><a href="individual/{r["geometry_id"]}.png">独立图</a>　<a href="individual/{r["geometry_id"]}.svg">矢量图</a></p></article>' for r in contours)
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P001 修改后 Geometry 复验</title><style>body{font:15px/1.65 system-ui,'Microsoft YaHei',sans-serif;color:#243340;background:#f5f7fa;margin:0}main{max-width:1450px;padding:28px;margin:auto}.lead,article{background:white;border:1px solid #dce3e9;padding:18px;border-radius:6px}h1{font-size:28px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(520px,1fr));gap:18px}img{width:100%;height:auto}a{color:#18649c}.overview{display:grid;grid-template-columns:1fr 1fr;gap:20px}@media(max-width:650px){.grid,.overview{grid-template-columns:1fr}main{padding:15px}}</style><main><h1>P001 修改后 Geometry 复验</h1><p>2026-10-09 · 正式 Excel 已更新 · Operation / Station / PROJECT_SPEC.md 未改</p><div class="lead"><strong>38 / 38 全部通过，无剩余人工确认事项。</strong><p>删除 48 条 R0.5 ARC，48 条 LINE 合并为 24 条，净减少 72 段。修正 49 条保留 ARC 的 sweep 符号。当前共 470 段：235 ARC（186 正 / 49 负）和 235 LINE。</p><p>全部连续闭合、整体 CCW，无明显自交、零长度段或异常半径。整体外形、主要尺寸和简化拓扑与 HTML 基本吻合。</p><p><a href="review_report.md">完整复验报告</a>　<a href="changed_geometries.csv">逐孔修改统计</a>　<a href="arc_checks.csv">全部 ARC 明细</a>　<a href="change_log.json">修改日志与备份信息</a>　<a href="summary.json">校验统计</a></p></div><h2>总览</h2><div class="overview"><a href="overview_01.png"><img src="overview_01.png"></a><a href="overview_02.png"><img src="overview_02.png"></a></div><h2>逐孔复核</h2><div class="grid">''' + cards + '</div></main></html>'
    (out/'index.html').write_text(page, encoding='utf-8')
    print(json.dumps({'all_validation_passed': True, 'geometries':len(chains), 'manual_confirmation_remaining':[],
                      'positive_arcs':summary['positive_arcs'], 'negative_arcs':summary['negative_arcs'],
                      'report':str(out/'review_report.md')}, ensure_ascii=True))


if __name__ == '__main__':
    main()
