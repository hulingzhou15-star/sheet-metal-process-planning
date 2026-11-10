"""Read-only P001 local order experiment; never modifies constraints or plans."""
from pathlib import Path
import sys
import json
import hashlib
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT/'information_extract'
sys.path.insert(0, str(ROOT))
from information_extract.three_d.local_collision import (panel_solids, components, inspect, trajectory, indexed)
from information_extract.three_d.finite_bend import band_area
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_FACE


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


LABELS = {'child_first': 'O010 子折弯先 → O009 父折弯后',
          'parent_first': 'O009 父折弯先 → O010 子折弯后',
          'simultaneous': 'O009 / O010 同序同时折弯'}


def summarize(samples):
    overlaps = [{'sample_index': i, 'fractions': s['fractions'], **p}
                for i, s in enumerate(samples) for p in s['pairs'] if p['status'] == 'OVERLAP']
    contacts = [{'sample_index': i, 'fractions': s['fractions'], **p}
                for i, s in enumerate(samples) for p in s['pairs'] if p['status'] == 'NONADJACENT_CONTACT']
    nearest = min(({'sample_index': i, 'fractions': s['fractions'], **p}
                   for i, s in enumerate(samples) for p in s['pairs'] if not p['adjacent']),
                  key=lambda p: p['distance_mm'])
    return {'sample_count': len(samples), 'pair_checks': sum(len(s['pairs']) for s in samples),
            'status': 'MODEL_OVERLAP_DETECTED' if overlaps else 'NO_OVERLAP_AT_SAMPLED_STATES',
            'overlap_sample_count': len({p['sample_index'] for p in overlaps}),
            'overlap_events': overlaps, 'nonadjacent_contacts': contacts,
            'minimum_nonadjacent_distance': nearest,
            'manufacturing_feasibility': 'UNKNOWN', 'h6_relaxation_authorized': False}


def write_summary(out, report):
    latest = report['runs'][-3:]
    text = ['# P001 局部三维干涉试验 v0.1', '',
            '检测器开发对照：H7 已确认 O010 必须先于 O009。父先子后与同序路径违反 H7，仅保留为开发测试，不能进入候选分析或用于放宽 H7。',
            '固定 H7 的候选诊断见 ../../h6_h7_audit_v01/P001/REPORT.md。', '',
            '## 结果', '', '|安排|采样状态|实体对检查|发生体积交叠的状态|基板—末端板最小距离 mm|',
            '|---|---:|---:|---:|---:|']
    for r in latest:
        text.append(f"|{r['label']}|{r['sample_count']}|{r['pair_checks']}|{r['overlap_sample_count']}|{min(p['distance_mm'] for v in r['samples'] for p in v['pairs'] if p['components'] == ['root', 'terminal']):.6f}|")
    text += ['', '该距离仅为基板与末端板之间的采样最小值，不是全件或工具安全间距。全部实体对的最小距离还包含零距离接触，原始记录全部保留。', '', '## 方法与边界', '',
             '- STEP 三个实际平面外边界向相对表皮挤出 0.8 mm；内孔填平，外切口仍来自成品。',
             '- 两段折弯带采用已有局部有限宽模型；没有使用 Sheet5 或专家分配推导先后。',
             '- 每个状态检查五个实体全部十对，包括相邻板面与折弯带。连接接触不算穿入；相邻实体若存在超过数值阈值的交叠体积，仍报告。',
             '- 距离与 Common 交叠体积由 OCCT 计算。距离 1e-6 mm、体积 1e-6 mm³ 是数值判定阈值，不是工艺安全间距。',
             '- 每段运动先取 12 等分，再 24 等分；条带截面先 16 等分，再 32 等分，分别观察采样加密影响。没有连续无碰撞证明。',
             '- 同序动作采用同步线性角度进度；不人为给同序动作排序，也未证明该运动就是实际模具轨迹。',
             '- 全部输入哈希前后相同。详细每一状态、每一实体对、距离及体积见 report.json。', '',
             '## 不能据此得出的结论', '',
             '本试验不检查整件其它区域、翻孔局部凸起、模具及装夹。恒厚折弯带插值仍不守恒，成形物理未验证。',
             '发现交叠是此几何假设下的风险证据；未发现交叠也只能说明已检查的离散状态没有可检测交叠。制造可行性保持 UNKNOWN。',
             '无论三种安排是否得到相同结果，都不能据此自动删除 H6/H7。',
             '几何交叠结果不用于重新判断已确认的 H7；未知的工具可达性也不能当作已经通过。',
             '接触分类按五实体链的直接连接关系记录；隔着折弯带的两块直板在尖内角也可能接触，不能仅据分类断言接触是否合理。', '',
             '## 查看与复现', '', '[交互检视](review.html) · [完整审计数据](report.json)', '',
             '`.venv-m3/Scripts/python.exe -B -X utf8 information_extract/scripts/check_local_bend_collision.py`',
             '', '`.venv-m3/Scripts/python.exe -B -X utf8 information_extract/scripts/build_local_collision_review.py`',
             '', '数值测试见 python_qa.json；实际浏览器测试见 browser_qa.json。']
    (out/'REPORT.md').write_text('\n'.join(text)+'\n', encoding='utf-8')


def main():
    config_path = MODULE/'configs/P001_local_collision_v01.json'
    cfg = read(config_path)
    out = MODULE/cfg['output']; out.mkdir(parents=True, exist_ok=True)
    model = read(MODULE/cfg['model']); old = read(MODULE/cfg['material'])
    asset = read(MODULE/cfg['asset']); cadcfg = read(MODULE/cfg['cad_config'])
    step = MODULE/cadcfg['step']
    if digest(step) != model['source_step_sha256'] or asset['asset_id'] != digest(step):
        raise ValueError('STEP provenance mismatch')
    drawing = read(MODULE/'outputs/process_drawing_assistance/P001/evidence.json')
    if digest(Path(drawing['source'])) != model['source_dxf_sha256']:
        raise ValueError('DXF changed; rebuild local evidence before checking')
    if [b['operation_id'] for b in model['bends']] != cfg['operation_ids']:
        raise ValueError('This experiment is restricted to O009/O010')
    # Record original sources and frozen business inputs before and after.
    files = [config_path, MODULE/cfg['model'], MODULE/cfg['material'], MODULE/cfg['asset'], step,
             Path(drawing['source']), ROOT/'data/parts/P001/钣金加工数据表_P001.xlsx',
             ROOT/'outputs/selected_plans_v02/all_16_complete_assignments.json']
    files += sorted((ROOT/'src/constraints').rglob('*.py'))
    before = {str(p): digest(p) for p in files}
    reader = STEPControl_Reader()
    if reader.ReadFile(str(step)) != IFSelect_RetDone or not reader.TransferRoots():
        raise ValueError('STEP could not be read')
    face_map = indexed(reader.OneShape(), TopAbs_FACE)
    if face_map.Extent() != asset['face_count']:
        raise ValueError('Topology indexing mismatch')
    panels, evidence = panel_solids(face_map, old['example']['regions'], asset,
                                    model['thickness_mm'], cfg['contact_tolerance_mm'])
    runs = []
    # Independent temporal refinement, then independent band tessellation refinement.
    levels = [(cfg['temporal_intervals'][0], cfg['band_intervals'][0]),
              (cfg['temporal_intervals'][1], cfg['band_intervals'][0]),
              (cfg['temporal_intervals'][1], cfg['band_intervals'][1])]
    cache = {}
    for intervals, bands in levels:
        for kind, label in LABELS.items():
            samples = []
            for index, sample in enumerate(trajectory(kind, intervals)):
                key = (bands, *sample['fractions'].values())
                if key not in cache:
                    solids = components(model, panels, sample['fractions'], bands, cfg['contact_tolerance_mm'])
                    cache[key] = inspect(solids, cfg)
                samples.append({**sample, 'pairs': cache[key]})
                if index % 12 == 0:
                    print(f'{kind} time={intervals} band={bands} sample={index+1}', flush=True)
            run = {'kind': kind, 'label': label, 'temporal_intervals': intervals,
                   'band_intervals': bands, 'samples': samples, **summarize(samples)}
            runs.append(run)
            write(out/'checkpoint.json', {'completed_runs': runs})
    stability = []
    for kind in LABELS:
        rows = [r for r in runs if r['kind'] == kind]
        statuses = [r['status'] for r in rows]
        gaps = [r['minimum_nonadjacent_distance']['distance_mm'] for r in rows]
        stability.append({'kind': kind, 'statuses': statuses, 'sampled_classification_stable': len(set(statuses)) == 1,
                          'minimum_distances_mm': gaps, 'temporal_gap_change_mm': gaps[1]-gaps[0],
                          'band_gap_change_mm': gaps[2]-gaps[1], 'continuous_collision_certified': False})
    after = {p: digest(Path(p)) for p in before}
    if before != after:
        raise ValueError('Input changed during experiment; results cannot be trusted')
    report = {'scope': 'LOCAL_PANEL_ENVELOPE_DIAGNOSTIC_ONLY', 'configuration': cfg,
              'panel_evidence': evidence, 'source_sha256': before, 'inputs_unchanged': True,
              'sheet5_used': False, 'expert_assignment_used': False,
              'drawing_assisted_geometry': True, 'tools_checked': False, 'fixture_checked': False,
              'whole_part_checked': False, 'continuous_collision_certified': False,
              'physical_forming_validated': False, 'candidate_filter_enabled': False,
              'bands': [{'operation_id': b['operation_id'], 'flat_area_mm2': band_area(model, i, 0),
                         'final_area_mm2': band_area(model, i, 1)} for i, b in enumerate(model['bends'])],
              'limitations': ['Only three CAD-derived straight panel envelopes and two reconstructed bend bands.',
                              'Inner holes filled; outer final contours retained. Not the original blank.',
                              'Other panels, local flanging protrusions, tools and fixtures excluded.',
                              'Constant-thickness bend interpolation is not volume conserving.',
                              'Finite temporal samples and faceted bands, not continuous collision detection.',
                              'No observed overlap does not prove manufacturability or authorize weakening H6/H7.'],
              'runs': runs, 'refinement': stability}
    write(out/'report.json', report)
    latest = runs[-3:]
    write_summary(out, report)
    print(json.dumps({'output': str(out), 'results': [{k: r[k] for k in ('kind', 'status', 'overlap_sample_count')} for r in latest]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
