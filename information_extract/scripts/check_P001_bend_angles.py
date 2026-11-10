"""Compare six CAD folds, DXF evidence and actual Python motion poses; read-only inputs."""
from pathlib import Path
import sys
import json
import math
import hashlib
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
MODULE=ROOT/'information_extract'
sys.path.insert(0,str(ROOT))
from information_extract.three_d.local_motion import oriented_normals,poses

import ezdxf


def read(p):return json.loads(p.read_text(encoding='utf-8'))
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def signed_angle(a,b,axis):
    return math.degrees(math.atan2(float(np.dot(axis,np.cross(a,b))),float(np.dot(a,b))))


def main():
    cfg=read(MODULE/'configs/P001_bend_angle_check.json')
    cadcfg=read(MODULE/'configs/three_d_state_v01.json')
    dxfcfg=read(MODULE/'configs/P001_process_drawing_assistance.json')
    step=MODULE/cadcfg['step'];source=Path(dxfcfg['source'])
    before={'step':digest(step),'dxf':digest(source)}
    data=read(MODULE/'outputs/stage_review_v01/P001/stage_data.json')
    asset=read(MODULE/'outputs/three_d_state_v01/P001/asset.json')
    assert before['step']==asset['asset_id']==data['asset_id']
    assert before['dxf']==dxfcfg['expected_sha256']
    normals=oriented_normals(step,asset)  # Re-read oriented normals from actual STEP.
    skeleton=data['skeleton'];regions={r['region_id']:r for r in skeleton['regions']}
    operations={o['operation_id']:o for o in data['operations']}
    joints={j['joint_id']:j for j in skeleton['joints']}
    rows=[];cases=[]
    for oid in cfg['operation_ids']:
        j=joints[operations[oid]['joint_id']]
        pf=regions[j['parent_region_id']]['front_face_id'];cf=regions[j['child_region_id']]['front_face_id']
        a=np.array(normals[pf]);b=np.array(normals[cf]);axis=np.array(j['axis_direction'])
        measured=signed_angle(a,b,axis)
        row={'operation_id':oid,'joint_id':j['joint_id'],'parent_front_face_id':pf,'child_front_face_id':cf,
             'fresh_step_parent_normal':a.tolist(),'fresh_step_child_normal':b.tolist(),
             'cad_signed_rotation_deg':measured,'cad_included_angle_deg':180-abs(measured),
             'model_signed_rotation_deg':j['angle_deg'],'samples':[]}
        assert abs(measured-j['angle_deg'])<cfg['normal_rotation_check_tolerance_deg']
        assert round(180-abs(measured),2)==cfg['confirmed_included_angle_deg_rounded']
        for fraction in cfg['fractions']:
            fractions={joint['joint_id']:fraction for joint in skeleton['joints']}
            transforms=poses(skeleton,fractions)
            rp=transforms[j['parent_region_id']][:3,:3];rc=transforms[j['child_region_id']][:3,:3]
            actual=signed_angle(rp@a,rc@b,rp@axis);expected=measured*fraction
            sample={'fraction':fraction,'measured_signed_rotation_deg':actual,'expected_signed_rotation_deg':expected,
                    'included_angle_deg':180-abs(actual),'residual_deg':abs(actual-expected)}
            assert sample['residual_deg']<cfg['normal_rotation_check_tolerance_deg']
            row['samples'].append(sample)
            cases.append({'operation_id':oid,'joint_id':j['joint_id'],'fraction':fraction,
                          'parent_normal':a.tolist(),'child_normal':b.tolist(),'expected_deg':expected})
        rows.append(row)
    # Search native text and LINE geometry. Line slope matches are supporting
    # drawing evidence, NOT six independently established operation assignments.
    doc=ezdxf.readfile(source);owners={b.block_record_handle:b.name for b in doc.blocks}
    hits=[];texts=[];superseded_hits=[]
    expected=abs(rows[0]['cad_signed_rotation_deg'])
    for e in doc.entitydb.values():
        if not e.is_alive:continue
        if e.dxftype() in ('TEXT','MTEXT','ATTRIB','ATTDEF'):
            text=e.plain_text()
            if '166.07' in text or '166.67' in text:texts.append({'handle':e.dxf.handle,'text':text})
        if e.dxftype()!='LINE':continue
        v=e.dxf.end-e.dxf.start
        if math.hypot(v.x,v.y)<1e-12:continue
        angle=math.degrees(math.atan2(abs(v.y),abs(v.x)));angle=min(angle,90-angle)
        row={'handle':e.dxf.handle,'block':owners.get(e.dxf.owner),'acute_axis_angle_deg':angle,
             'length':v.magnitude,'start':list(e.dxf.start),'end':list(e.dxf.end)}
        if abs(angle-expected)<cfg['dxf_line_angle_search_tolerance_deg']:hits.append(row)
        if abs(angle-(180-cfg['superseded_reported_angle_deg']))<cfg['dxf_line_angle_search_tolerance_deg']:superseded_hits.append(row)
    assert before=={'step':digest(step),'dxf':digest(source)}
    result={'config':cfg,'source_sha256':before,'source_unchanged':True,'operations':rows,'kernel_cases':cases,
            'dxf_angle_text_hits':texts,'dxf_line_slope_support':hits,'superseded_angle_line_slope_hits':superseded_hits,
            'max_motion_residual_deg':max(s['residual_deg'] for r in rows for s in r['samples']),
            'passed':True,'scope':'Rigid orientation checks only. No finite bend-zone, neutral layer, springback or collision validation.'}
    out=MODULE/'outputs/bend_angle_check/P001';out.mkdir(parents=True,exist_ok=True)
    (out/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    table='\n'.join(f"|{r['operation_id']}|{r['joint_id']}|{r['cad_included_angle_deg']:.6f}°|{r['model_signed_rotation_deg']:.6f}°|通过|" for r in rows)
    report=f'''# P001 六处非直角折弯检查

用户确认采用现有 CAD 的约 166.07°，替代最初口述的 166.67°。没有修改 STEP、DXF、工作表、候选分配或原运动目标值。

## 数值与定义

这里的 166.07° 为两侧板面的内夹角；从展开平直状态开始的转角大小为 180° 减该内夹角，约 13.93°。模型采用有向法向及已记录的轴方向计算符号；负号表示绕该轴的旋转方向，不表示负内夹角。

|动作|连接|STEP 内夹角|当前模型有向转角|逐态检查|
|---|---|---:|---:|---|
{table}

这六处此前已从 STEP 自动取得 -13.930264°，并未使用默认 90°。本次不需要把旋转量替换成 166.07°。

## 实际模型核验

重新读取 STEP 中父子板面的有向法向，不仅检查缓存参数。使用现有 poses() 对全部连接同时设置 0、25%、50%、75%、100% 进度，再从变换后的实际法向反算相对转角。六处合计 30 个检查，最大残差 {result['max_motion_residual_deg']:.3e}°；展开时板面平行，最终内夹角约 166.069736°，方向与 CAD 一致。

这是刚性转轴模型的方向与角度检查，不是完整成形仿真。有限宽折弯带、中性层、回弹、碰撞仍不在本次验证范围；最终姿态闭合也不能单独证明物理加工正确。

## DXF 证据

源文件：{source}。在可解码的文字实体中找到 166.07/166.67 的直接标注 {len(texts)} 条，因此没有声称读到了这些数值的角度文字。全实体库找到 {len(hits)} 条与 13.930264° 相符的直线方向，其中包括标注块及重复投影，不代表 {len(hits)} 个独立折弯。

例如 Model Space 的 1BAC、1BAE 及 Plotview 2 的 2471/248B/24A4 的斜率与 STEP 一致。图纸斜率仅作补充证据，未把这些投影线强行逐条绑定到六个动作。与 13.33° 相符的直线方向数量为 {len(superseded_hits)}。完整句柄和坐标见 audit.json。

STEP SHA256：`{before['step']}`；DXF SHA256：`{before['dxf']}`。读前读后校验一致。
'''
    (out/'REPORT.md').write_text(report,encoding='utf-8')
    print(json.dumps({'passed':True,'operations':len(rows),'pose_checks':len(cases),'max_residual_deg':result['max_motion_residual_deg'],'dxf_supporting_lines':len(hits)},ensure_ascii=False))


if __name__=='__main__':main()
