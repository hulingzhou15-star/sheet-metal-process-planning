"""Publish DXF/STEP audit and three O094 endpoint solids, without candidate edits."""
from pathlib import Path
import html
import numpy as np
from OCP.STEPControl import STEPControl_Writer,STEPControl_Reader,STEPControl_AsIs
from OCP.IFSelect import IFSelect_RetDone
from OCP.BRepCheck import BRepCheck_Analyzer
from information_extract.three_d.artifacts import ROOT,read,write,sha,verify_sources
from information_extract.three_d.local_forming import load_shape
from information_extract.three_d.local_collision import transformed
from information_extract.three_d.assets import bounds
from information_extract.three_d.state import rotation_about_axis

M=ROOT/'information_extract';OUT=M/'outputs/p002_stage_evidence_v01'


def main():
    r=read(OUT/'stage_geometry.json');verify_sources(r['inputs']);c=r['o094_carrier']
    later=read(OUT/'later_side_validation.json')
    if later['stage_evidence_sha256']!=sha(OUT/'stage_geometry.json'):raise ValueError('Stale later view check')
    carrier=np.array(c['unformed_parent_transform']);j=c['local_joint']
    before=carrier@rotation_about_axis(j['hinge_point_mm'],j['axis_direction'],-j['angle_deg'])
    before[:3,3]+=np.array(c['initial_world_translation_correction_mm'])
    solid=load_shape(M/'outputs/p002_material_cells_v01/brep/C007.brep');exports=[]
    for name,matrix in [('before_O094_reference',before),('after_O094_before_O088_reference',carrier),('completed_O094_reference',np.eye(4))]:
        shape=transformed(solid,matrix)
        if not BRepCheck_Analyzer(shape).IsValid():raise ValueError('Invalid rigid endpoint')
        path=OUT/(name+'.step');writer=STEPControl_Writer()
        if writer.Transfer(shape,STEPControl_AsIs)!=IFSelect_RetDone or writer.Write(str(path))!=IFSelect_RetDone:raise ValueError('STEP export failed')
        reader=STEPControl_Reader()
        if reader.ReadFile(str(path))!=IFSelect_RetDone:raise ValueError('STEP reload failed')
        reader.SetSystemLengthUnit(1.);reader.TransferRoots();loaded=reader.OneShape()
        error=float(np.max(np.abs(np.array(bounds(shape))-np.array(bounds(loaded)))))
        if not BRepCheck_Analyzer(loaded).IsValid() or error>1e-6:raise ValueError('STEP round trip failed')
        exports.append(dict(file=path.name,sha256=sha(path),transform=matrix.tolist(),bounds_roundtrip_error_mm=error,
                            geometry_scope='O094 rigid cell only; finite bend band excluded'))
    write(OUT/'endpoint_exports.json',dict(exports=exports,source_sha256=sha(OUT/'stage_geometry.json'),implementation_sha256=sha(Path(__file__))))
    table=[]
    for op in r['operations']:
        b=op['step_bounds_mm'];side=op.get('side_projection')
        comment=(f"侧投影 {side['within_0_001_mm']}/{side['sample_count']} 点距图线≤0.001 mm；最大 {side['max_residual_mm']:.6g} mm" if side else '前后自由边轮廓 3×170 点吻合；独立侧视尺寸 5.2 mm')
        table.append(f"<tr><td>{op['operation_id']}</td><td>{op['expert_station_for_validation_only']}</td><td>{b[2]:.3f}～{b[5]:.3f}</td><td>{comment}</td></tr>")
    source_links=' · '.join(f'<a href="view_{i:02d}.png">原图视图{i}</a>' for i in (5,7,8,9,10,11))
    export_links=' · '.join(f'<a href="{e["file"]}">{html.escape(e["file"])}</a>' for e in exports)
    page=f'''<!doctype html><meta charset="utf-8"><title>P002 DXF阶段与STEP核对</title>
<style>body{{font:16px system-ui;max-width:1200px;margin:30px auto;padding:0 20px;color:#243747;line-height:1.7}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #cdd5dd;padding:10px}}img{{width:100%}}aside{{padding:15px;background:#fff1d7}}a{{color:#156396}}</style>
<h1>P002：DXF阶段形状与STEP核对</h1>
<p>加工高亮、主视图和侧视图分别解释。专家工序仅定位证据；16个候选仍按各自分配执行。</p>
<aside>本轮只新增O094局部刚性参考：端态经图纸校准，中间角度及补偿采用线性插值假设。O088曲面运动期间，O094仍未知。四处曲线连续变形、逐序材料切换未完成，不能据此判定制造安全。</aside>
<table><tr><th>动作</th><th>图纸加工序</th><th>STEP Z范围/mm</th><th>实际核对范围</th></tr>{''.join(table)}</table>
<p>继续核对OP90侧视后，四处曲线最远壁边全部吻合：O069 288点、O088 72点、O096 72点、O104 144点，最大残差约3.64×10⁻¹² mm。O069/O104较早侧视仍有3 mm差异，原因未定，不把这些早期轮廓补造成已验证阶段。O096全部侧投影最大差约0.479 mm，14 mm最高边吻合；O088侧投影2210点全部在0.001 mm内。投影相合不等于全三维实体或连续过程被唯一确定。</p>
<h2>O094端态对照</h2><p>灰色为DXF线，橙色为STEP刚性小翻边。自由边经X/Y各0.4 mm端态补偿，与OP50/70/80吻合；橙色内部切口属于省略折弯带后的人工边界，不作为原图自由边验收。图中未绘制全部DXF圆弧，定量核对使用解析圆弧。</p>
<img src="o094_stage_projection.png" alt="O094端态投影对照"><p>{export_links}</p>
<h2>原图及原始测量</h2><p>{source_links}</p><p><a href="stage_geometry.json">逐动作测量及来源指纹</a> · <a href="later_side_validation.json">四曲线后续侧视补证</a> · <a href="annotations.json">全部文字、239尺寸及24引线</a> · <a href="endpoint_exports.json">端态STEP回读验证</a> · <a href="../p002_candidate_motion_v02/index.html">16候选更新后运动参考</a></p>
<p>原图80°/90°文字位于产品图区域，尚无目标动作的引线对应，不赋给这五个动作。人工标注、候选评分、冻结基线均未修改。</p>'''
    (OUT/'index.html').write_text(page,encoding='utf8')
    generated=[*OUT.glob('*.json'),*OUT.glob('*.png'),*OUT.glob('*.step'),OUT/'index.html']
    write(OUT/'output_manifest.json',dict(files={p.relative_to(ROOT).as_posix():sha(p) for p in generated if p.name!='output_manifest.json'}))
    print('Published audit and three reimported valid endpoint STEP files')


if __name__=='__main__':main()
