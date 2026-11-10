"""NP2_001 auditable removal subset and recheck of all original issue samples."""
from pathlib import Path
import sys
from collections import Counter
import numpy as np
ROOT=Path(__file__).resolve().parents[2]; MODULE=ROOT/'information_extract'
sys.path.insert(0,str(ROOT))
from information_extract.scripts.check_whole_panel_collision import read,write,sha
from information_extract.three_d.material_removal import audit_cuts,active_cuts,cut_panels
from information_extract.three_d.local_collision import panel_solids,indexed,volume,transformed
from information_extract.three_d.local_motion import poses
from information_extract.three_d.panel_collision import narrow_pair
from information_extract.three_d.h6_h7_audit import fractions_for_stage
from information_extract.scripts.build_local_collision_review import mesh
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_FACE


def main():
    cfg=read(MODULE/'configs/P001_whole_panel_collision_v01.json')
    old=MODULE/cfg['output']; out=MODULE/'outputs/staged_removal_v01/P001';out.mkdir(parents=True,exist_ok=True)
    report=read(old/'report.json');stage=read(MODULE/cfg['stage_data']);asset=read(MODULE/cfg['asset'])
    protected={**report['source_sha256'],str((old/'report.json').resolve()):sha(old/'report.json')}
    for p,h in protected.items():
        if sha(Path(p))!=h: raise ValueError('Stale source: '+p)
    # The original verified candidate and hard-constraint evidence remain fixed.
    plan=next(p for p in stage['plans'] if p['plan_id']==cfg['plan_id']);sk=stage['skeleton']
    orders={s['station_id']:s['order'] for s in stage['stations']}
    step=MODULE/read(MODULE/cfg['cad_config'])['step'];reader=STEPControl_Reader()
    if reader.ReadFile(str(step))!=IFSelect_RetDone or not reader.TransferRoots():raise ValueError('STEP failed')
    faces=indexed(reader.OneShape(),TopAbs_FACE)
    if faces.Extent()!=asset['face_count']:raise ValueError('Topology mismatch')
    bodies,ev=panel_solids(faces,sk['regions'],asset,read(MODULE/cfg['material_config'])['stock_thickness_mm'],cfg['contact_tolerance_mm'])
    base=dict(zip([r['region_id'] for r in sk['regions']],bodies))
    flat=poses(sk,{j['joint_id']:0 for j in sk['joints']})
    audit,cutters=audit_cuts(faces,stage,flat,ev)
    for row in audit['operations']:row['station_id']=plan['assignments'][row['operation_id']]
    for row in audit['accepted']:row['station_id']=plan['assignments'][row['operation_id']]
    write(out/'mapping_audit.json',audit);print(audit['status_counts'],flush=True)
    states={};state_rows=[];meshes={}
    def material(sid,phase):
        events=active_cuts(audit['accepted'],plan['assignments'],orders,sid,phase)
        signature=tuple(sorted(e['wire_id'] for e in events))
        if signature not in states:
            solids,evidence=cut_panels(base,cutters,events);key=f'M{len(states):02d}'
            states[signature]=(key,solids,evidence)
            meshes[key]=[{'region_id':rid,**mesh(body,cfg)} for rid,body in solids.items()]
        return states[signature]
    for station in stage['stations']:
        sid=station['station_id']
        for phase in ['entry','during','completed']:
            key,solids,evidence=material(sid,phase)
            state_rows.append({'station_id':sid,'phase':phase,'material_id':key,
                'removed_operation_ids':[e['operation_id'] for e in evidence],
                'volume_mm3':sum(volume(s) for s in solids.values()),'cut_evidence':evidence,
                'same_station_removal_trajectory':'UNKNOWN_NOT_MODELED'})
    write(out/'material_states.json',state_rows)
    fine={r['station_id']:r for r in report['runs'] if r['intervals']==cfg['time_intervals'][-1]}
    groups=[];cache={}
    for issue in report['issues']:
        sid=issue['station_id'];rows=[]
        for sample in issue['samples']:
            state=fine[sid]['samples'][sample['index']];phase='completed' if state['tau']==1 else 'during'
            mid,solids,_=material(sid,phase);ts=poses(sk,state['joint_fractions']);a,b=issue['region_ids']
            relative=np.linalg.inv(ts[a])@ts[b]
            key=(mid,a,b,*np.round(relative,cfg['relative_transform_cache_decimals']).flat)
            if key not in cache:
                try:cache[key]=narrow_pair(solids[a],transformed(solids[b],relative),cfg)
                except Exception as exc:cache[key]={'status':'UNKNOWN_KERNEL_FAILURE','error':str(exc)}
            rows.append({'index':sample['index'],'tau':sample['tau'],'material_id':mid,
                         'before_mm3':sample['intersection_volume_mm3'],'after':cache[key]})
        groups.append({'issue_id':issue['issue_id'],'station_id':sid,'region_ids':issue['region_ids'],'samples':rows,
                       'remaining_overlap_samples':sum(r['after']['status']=='OVERLAP' for r in rows),
                       'unknown_samples':sum(r['after']['status']=='UNKNOWN_KERNEL_FAILURE' for r in rows)})
        print(issue['issue_id'],groups[-1]['remaining_overlap_samples'],flush=True)
    summary={'accepted_cut_count':len(audit['accepted']),'audited_inner_wire_count':len(audit['inner_wires']),
        'rechecked_issue_groups':len(groups),'rechecked_original_overlap_samples':sum(len(g['samples']) for g in groups),
        'groups_with_remaining_overlap':sum(g['remaining_overlap_samples']>0 for g in groups),
        'groups_without_overlap_at_original_samples':sum(g['remaining_overlap_samples']==0 and g['unknown_samples']==0 for g in groups),
        'unknown_samples':sum(g['unknown_samples'] for g in groups),'candidate_filter_enabled':False,
        'manufacturing_feasibility':'UNKNOWN','all_pairs_rescanned':False,'initial_blank_recovered':False,
        'same_station_cutting_trajectory_modeled':False,'h7_preserved':True,
        'unconnected_regions':sk['unconnected_region_proposal_ids']}
    for p,h in protected.items():
        if sha(Path(p))!=h:raise ValueError('Protected source modified: '+p)
    write(out/'report.json',{'summary':summary,'source_sha256':protected,'groups':groups})
    viewer={'plan_id':cfg['plan_id'],'skeleton':sk,'stations':stage['stations'],'states':state_rows,'meshes':meshes,
            'operations':[{k:o[k] for k in ['operation_id','flat_paths','formed_paths','region_id']} for o in stage['operations'] if o['operation_id'] in {e['operation_id'] for e in audit['accepted']}],
            'poses':{s['station_id']:{phase:{r:m.tolist() for r,m in poses(sk,fractions_for_stage(sk,plan['assignments'],orders,s['station_id'],0 if phase=='entry' else 1)).items()} for phase in ['entry','completed']} for s in stage['stations']}}
    write(out/'viewer_data.json',viewer)
    lines=['# NP2_001 逐序实体去料首批验证','',
        f"完整几何唯一吻合的普通冲孔：{len(audit['accepted'])} 个；审计 CAD 内环 {len(audit['inner_wires'])} 个。",'',
        '按 Station.order 生成进入态、过程保留态、完成态。此前工序已冲出的孔进入后续实体；本序新孔只在完成态加入。过程保留态不是同序加工过程的物理预测。',
        '实体以精确圆/直线/圆弧和 CAD 内环交集核对，显示网格直接由检测所用切孔实体生成。旧检视页及旧检测报告保留原样。','',
        f"原 62 组的全部 {summary['rechecked_original_overlap_samples']} 个交叠采样记录已重查：{summary['groups_without_overlap_at_original_samples']} 组在这些样本中不再交叠，{summary['groups_with_remaining_overlap']} 组仍有交叠，内核未决 {summary['unknown_samples']} 条。",
        '这不是全体板面对重新扫描，不是连续运动证明，也不表示候选成功或失败。','',
        '## 审计与缺口','', '|状态|动作数|','|---|---:|']
    lines += [f'|{k}|{v}|' for k,v in audit['status_counts'].items()]
    lines += ['', '未匹配内环不自动认作冲孔；预孔不套用最终翻孔轮廓；切边缺原始毛坯及闭合去料区。未匹配普通孔保留填实，并列出具体动作。零宽转轴、局部成形、4 个未连接区域仍未解决。',
        '', '## 文件','', '- [对应审计](mapping_audit.json)：全部动作与内环、匹配误差、未决原因。',
        '- [工序实体证据](material_states.json)：每序切前/切后体积及动作集合。',
        '- [前后交叠证据](report.json)：原问题每个交叠采样的前后值及输入哈希。',
        '- [检视页](review.html)：切孔完成态与进入态；二维与三维使用同一已完成动作集合。',
        '', '复现：`.venv-m3/Scripts/python.exe -B -X utf8 information_extract/scripts/check_staged_material_removal.py`',
        '然后运行 `information_extract/scripts/build_staged_removal_review.py`。H7、候选、评分、reference 均未修改。']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(summary)


if __name__=='__main__':main()
