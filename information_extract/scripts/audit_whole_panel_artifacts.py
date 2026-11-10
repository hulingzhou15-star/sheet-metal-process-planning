"""Sensitivity experiment: retain final CAD inner wires, never adopt as stage stock."""
from pathlib import Path
import sys
from collections import Counter
import numpy as np
ROOT=Path(__file__).resolve().parents[2];MODULE=ROOT/'information_extract'
sys.path.insert(0,str(ROOT))
from information_extract.scripts.check_whole_panel_collision import read,write,sha
from information_extract.three_d.local_collision import indexed,transformed,valid_solid
from information_extract.three_d.panel_collision import narrow_pair
from information_extract.three_d.local_motion import poses
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.gp import gp_Vec
from OCP.BRepCheck import BRepCheck_Analyzer


def main():
    cfg=read(MODULE/'configs/P001_whole_panel_collision_v01.json');out=MODULE/cfg['output']
    report=read(out/'report.json');data=read(out/'viewer_data.json');sk=data['skeleton']
    for p,h in report['source_sha256'].items():
        if sha(Path(p))!=h:raise ValueError('Diagnostic source changed: '+p)
    reader=STEPControl_Reader();step=MODULE/read(MODULE/cfg['cad_config'])['step']
    if reader.ReadFile(str(step))!=IFSelect_RetDone or not reader.TransferRoots():raise ValueError('STEP read failed')
    shape=reader.OneShape();faces=indexed(shape,TopAbs_FACE);solids={};failed={}
    for ev in report['panel_evidence']:
        rid=ev['region_id']
        try:
            face=TopoDS.Face_s(faces.FindKey(ev['front_face_id']))
            solids[rid]=valid_solid(BRepPrimAPI_MakePrism(face,gp_Vec(*ev['extrusion_vector_mm'])).Shape())
        except Exception as exc:failed[rid]=str(exc)
    rows=[];cache={}
    for issue in report['issues']:
        sample=max(issue['samples'],key=lambda x:x.get('intersection_volume_mm3',0))
        run=next(r for r in data['runs'] if r['station_id']==issue['station_id'])
        state=run['samples'][sample['index']];ts=poses(sk,state['joint_fractions'])
        a,b=issue['region_ids'];relative=np.linalg.inv(ts[a])@ts[b]
        key=(a,b,*np.round(relative,cfg['relative_transform_cache_decimals']).flat)
        if a in failed or b in failed:
            result={'status':'UNKNOWN_KERNEL_FAILURE','message':'trimmed solid unavailable'}
        elif key in cache:result=cache[key]
        else:
            try:result=narrow_pair(solids[a],transformed(solids[b],relative),cfg)
            except Exception as exc:result={'status':'UNKNOWN_KERNEL_FAILURE','message':str(exc)}
            cache[key]=result
        disappeared=(result['status'] in ('CONTACT','SEPARATED') and sample['status']=='OVERLAP')
        rows.append({'issue_id':issue['issue_id'],'station_id':issue['station_id'],
                     'region_ids':issue['region_ids'],'tested_sample_index':sample['index'],'tau':sample['tau'],
                     'filled_envelope_volume_mm3':sample.get('intersection_volume_mm3'),
                     'final_inner_wires_preserved_result':result,
                     'overlap_disappears_with_final_inner_wires_at_tested_sample':disappeared,
                     'production_geometry_changed':False,'manufacturing_verdict':'UNKNOWN'})
    formed=report['baselines']['formed_reference']
    final_issues=[]
    for pair in formed['narrow_phase_pairs']:
        if pair['status']!='OVERLAP':continue
        j=next((j for j in sk['joints'] if {j['parent_region_id'],j['child_region_id']}==set(pair['region_ids'])),None)
        final_issues.append({'region_ids':pair['region_ids'],'volume_mm3':pair['intersection_volume_mm3'],
                             'joint_id':j['joint_id'] if j else None,
                             'bend_operation_id':sk['joint_to_operation'][j['joint_id']] if j else None,
                             'rotation_angle_deg':j['angle_deg'] if j else None,
                             'interpretation':'FORMED_REFERENCE_ENVELOPE_OVERLAP_REVIEW_REGION_DECOMPOSITION'})
    result={'source_report_sha256':sha(out/'report.json'),'source_step_sha256':sha(step),
            'original_step_topology_valid':BRepCheck_Analyzer(shape).IsValid(),
            'scope':'MAXIMUM_OVERLAP_SAMPLE_PER_ISSUE_ONLY_NOT_ALL_STATES',
            'inner_wires_variant_is_real_stage_geometry':False,
            'tested_issue_count':len(rows),'disappears_at_tested_sample_count':sum(r['overlap_disappears_with_final_inner_wires_at_tested_sample'] for r in rows),
            'variant_failed_panels':failed,'rows':rows,'formed_reference_issues':final_issues}
    write(out/'artifact_sensitivity.json',result)
    lines=['# 板体包络建模敏感性核对','',
           '不修改原诊断几何、不隐藏原问题，不改候选或 H7。每个问题组选取交叠体积最大的一个已检查状态。','',
           f"共核对 {len(rows)} 个问题组，其中 {result['disappears_at_tested_sample_count']} 组在恢复成品 CAD 内孔后，该样本的交叠消失。",
           '这表明该样本的填孔包络对交叠结论有影响；不代表真实加工时这些孔必然已形成，不能把对照几何直接当作工序实体。','',
           f"原 STEP 拓扑有效：{result['original_step_topology_valid']}。成品位置的板体包络仍有 {len(final_issues)} 对交叠：",'']
    for row in final_issues:lines.append(f"- {row['bend_operation_id']}：转角 {row['rotation_angle_deg']:.6f}°，包络交叠 {row['volume_mm3']:.6f} mm³。")
    lines+=['','成品位置不随工序分配改变。因此上述交叠不能作为 NP2_001 工序安排错误的证据；需要核对直板挤出分区及实际过渡几何。','',
            '零宽转轴仍未在全件用有限宽模型替换；本次没有通过调整容差、删掉连接对或改变角度消除交叠。',
            '局部 O009/O010 的有限宽模型不能未经验证推广至全件，尤其是非 90° 折弯。']
    (out/'ARTIFACT_REVIEW.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print({k:v for k,v in result.items() if k not in ['rows','formed_reference_issues']})


if __name__=='__main__':main()
