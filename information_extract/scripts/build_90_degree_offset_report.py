"""Report and before/after overlays for the independent static strip experiment."""
from pathlib import Path
import sys,json
from collections import Counter
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
MODULE=ROOT/'information_extract'
from information_extract.scripts.check_whole_panel_collision import read,write,sha
from information_extract.three_d.state import transform_point


def main():
    out=MODULE/'outputs/hole_offset_90_audit_v01/P001';a=read(out/'audit.json');p=read(out/'strip_model_probe.json')
    review=read(MODULE/'outputs/remaining_mapping_review_v01/P001/review_data.json')
    source={o['operation_id']:o for o in review['operations']};wires=review['wires']
    for path,h in p['source_sha256'].items():
        if sha(Path(path))!=h:raise ValueError('Diagnostic source changed')
    rows=[]
    for r in a['hole_rows']:
        if r['operation_type']=='PRE_PUNCH' or not r.get('ninety_degree_path'):continue
        op=source[r['operation_id']];candidate=r if 'wire_id' in r else r['nearest_candidate'];w=wires[candidate['wire_id']]
        matrix=p['flat_transforms'][r['region_id']]
        after=[transform_point(matrix,v)[:2] for v in w['formed_path']]
        valid=next((v for v in p['independent_hole_validation'] if v['operation_id']==r['operation_id']),None)
        rows.append({'operation_id':r['operation_id'],'source_feature_ref':r['source_feature_ref'],
            'region_id':r['region_id'],'bend_path':[j['operation_id'] for j in r['bend_path']],
            'full_shape_congruent':r['status']=='UNIQUE_TRANSLATION_CONGRUENT_PROPOSAL',
            'old_delta_xyz_mm':candidate['flat_delta_xyz_mm'],
            'new_center_error_mm':valid['new_error_mm'] if valid else None,
            'shape_difference_mm3':candidate['translation_aligned_symmetric_difference_mm3'],
            'source_paths':op['flat_paths'],'old_cad_path':w['flat_path'],'new_cad_path':after})
    payload={'summary':a['summary'],'probe_summary':p['summary'],'rows':rows,
        'joint_rows':[{**j,'width_mm':next(r['developed_strip_width_mm'] for r in p['strip_rows'] if r['joint_id']==j['joint_id'])} for j in a['joint_rows'] if j['is_90_degrees']]}
    write(out/'viewer_data.json',payload)
    html=(MODULE/'three_d/web/hole_offset_90_review.html').read_text(encoding='utf-8')
    html=html.replace('/*__DATA__*/',json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(',',':')).replace('<','\\u003c'))
    (out/'review.html').write_text(html,encoding='utf-8')
    lines=['# P001 全部 90° 折弯的孔偏移与重建模试验','',
        '结论：可比较完整孔形的偏移具有明确的 0.4 / 0.8 mm 累积规律；不能把所有孔和预孔的差异都概括为一个固定平移量。',
        '当前已经从独立展开边界和 CAD 切点建立静态展开试验，18 个完整孔形验证全部对齐，没有用孔位拟合。',
        '', '[打开前后对照 HTML](review.html) · [全部动作/板面/折弯证据](audit.json) · [展开带及模型证据](strip_model_probe.json)',
        '', '## 检测范围与结果','',
        '- 全部 59 个折弯中，53 个为 90°，6 个为约 13.93° 转动；本次分别处理。',
        '- 全部 54 个普通冲孔、46 个预孔均登记。已有支持板面中，26 个普通冲孔的整条祖先路径均为 90°。',
        '- 其中 18 个是完整孔形不变的唯一平移对应：12 个偏移 0.4 mm，6 个偏移 0.8 mm。',
        '- 另 8 个不能作为完整孔形平移对应：O018/O019 的孔形仍有小差异；O193–O195、O212–O214 的成品轮廓含成形差异。',
        '- 其他普通冲孔仍保留：基板、跨面未定位和大开口包含动作的分类；没有用它们证明 90° 孔全部吻合。',
        '- 31 个 90° 关节的下游有普通孔或预孔的已映射几何；22 个没有孔观测支持，仍只具备折弯截面与展开边界证据。',
        '', '|完整孔形组|数量|既有展开参考中的 CAD−动作偏移|折弯路径|', '|---|---:|---|---|',
        '|O166–O174|9|Δy = −0.4 mm|O144|',
        '|O088–O090|3|Δy = +0.4 mm|O025|',
        '|O058、O073、O076|3|Δy = +0.8 mm|O025 后再经一次 90° 折弯|',
        '|O147、O150、O162|3|Δy = −0.8 mm|O144 后再经一次 90° 折弯|',
        '', '## 独立的折弯带证据','',
        '- 53 个 90° 折弯的每段 CAD 圆柱证据均有外半径 0.8 mm，并在另一侧找到经过圆柱轴的共享尖角边。这里是导出 CAD 的几何，不是实测材料内半径。',
        '- 在已验证配准的 DXF 展开视图中，53 个折弯均找到宽 0.4 mm 的双边界。52 个具有至少 95% 轴长覆盖；O025 的六段边界与六段 CAD 圆柱的轴向区间逐一吻合，未填补切口间隙。',
        '- 本次仅读取既已识别的展开视图；这仍是离线图纸辅助模型开发证据，不把专家加工图变成正式推理输入。',
        '', '## 重新建模试验','',
        '以 CAD 两侧前表面切点为端点，展开时在两端之间保留图纸测得的 0.4 mm 折弯带，沿材料连接树累积板面位置。整套板面平移由 CAD 和展开边界确定，拟合孔数量为 0。',
        '得到 54 个板面的静态展开位置（基板 + 53 个 90° 子板面）。18 个完整孔形验证的最大中心误差小于 1e-6 mm；实际数值约 3e-13 mm，只表示同源数值吻合，不是加工或测量精度。',
        '这解释了单次折弯常见 0.4 mm、两次同向材料偏移累积为 0.8 mm。方向相反时可抵消；转向不同可出现两个分量各 0.4 mm，合位移约 0.566 mm。不能按折弯次数机械乘 0.4。',
        '', '## 预孔与未完全解决的孔','',
        '- 29 个位于纯 90° 路径上的已映射预孔，各有唯一的同板面邻近最终圆形开口；新映射下其中心对齐。预孔半径与最终翻孔开口不同，因此这里只检查圆心，不宣称孔形吻合或提前形成翻孔。',
        '- O064、O085、O156、O159 的旧圆心偏移分别具有两个 ±0.4 mm 分量，长度约 0.566 mm。这是“所有孔的偏移长度都等于 0.4 或 0.8”的明确例外。',
        '- O018/O019 的位置偏差被解释，但完整孔形仍有约 0.04438 mm³ 的对称差，不能宣称全部几何问题消失。',
        '- 六个折弯异形孔修正后仍有形状/成形差异，不能用其质心差当作真实孔心偏差。',
        '- 5 个普通冲孔和 12 个预孔的板面归属未决；不推定它们符合上述规律。',
        '', '## 建议的实施方式','',
        '1. 将材料域分成刚性板面与有限宽折弯带；每个带保留方向、展开宽度、两端切线和内外表面身份。',
        '2. 先以本试验的 53 个 90° 带逐关节验证，再实现从展开到成品的连续板面位姿及带内映射；同时检验厚度、体积与连接连续性。静态吻合不足以证明动态成形正确。',
        '3. 所有孔由原二维材料坐标定位并随板面/折弯带运动；此前工序真实去料按该映射进入后序。跨带孔应在二维材料域分割后分别映射，不能拿成品大开口整体当作冲掉区域。',
        '4. 保留预孔→翻孔的独立事件。完成新状态模型和显示/检测一致性验证后，再扩大原 12 孔去料集合并重查干涉。',
        '5. 6 个非 90° 折弯另建参数核查；不套用本次 0.4 mm 宽度。H7 与同序共同加工语义不变。',
        '', '本次已实现的是独立静态展开试验和诊断页面。没有替换生产运动、构造全件折弯带实体或扩大检测去料，也未重新扫描碰撞。冻结候选、约束、评分和 reference 均未改。',
        '', '## 复现','',
        '```powershell',
        '.venv-m3/Scripts/python.exe -B -X utf8 information_extract/scripts/audit_90_degree_hole_offsets.py',
        '.venv-m3/Scripts/python.exe -B -X utf8 information_extract/scripts/probe_90_degree_strip_model.py',
        '.venv-m3/Scripts/python.exe -B -X utf8 information_extract/scripts/build_90_degree_offset_report.py',
        '.venv-m3/Scripts/python.exe -B -X utf8 -m unittest information_extract.tests.test_hole_offset_audit -v',
        '```','']
    (out/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(out/'review.html')


if __name__=='__main__':main()
