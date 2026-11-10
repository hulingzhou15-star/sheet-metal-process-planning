"""Verify coincident STEP bodies, then export one as an independent work copy."""
from pathlib import Path
import sys
import importlib.util
import json
import hashlib
import math
import re
import time

MODULE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE/'.runtime/cad_inspect_deps'))
spec = importlib.util.spec_from_file_location('cad_audit', MODULE/'.runtime/cad_export_audit_20261102.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.TopTools import TopTools_ListOfShape
from OCP.STEPControl import STEPControl_Reader, STEPControl_Writer, STEPControl_AsIs
from OCP.Interface import Interface_Static
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_SOLID, TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX
from OCP.BRepCheck import BRepCheck_Analyzer

OUT = MODULE/'outputs/cad_single_solid_20261103/P001'
SOURCE = Path('C:/Users/hulin/Desktop/图纸文件夹/P001/P001_formed.stp')
OUT.mkdir(parents=True, exist_ok=True)
EXPECTED = '14412ff83c5a8efb7c530bf91b88f70b552675990dac2d40e0a4dfb69ab5f3af'
FUZZY_MM = 1e-6


def load(path):
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        raise RuntimeError('STEP reader failed')
    if not reader.TransferRoots():
        raise RuntimeError('STEP transfer failed')
    return reader, reader.OneShape()


def metric(shape):
    volume, center = audit.prop(shape, True)
    area, _ = audit.prop(shape)
    return {'solid_count':len(audit.indexed(shape,TopAbs_SOLID)),
            'face_count':len(audit.indexed(shape,TopAbs_FACE)),
            'edge_count':len(audit.indexed(shape,TopAbs_EDGE)),
            'vertex_count':len(audit.indexed(shape,TopAbs_VERTEX)),
            'valid':BRepCheck_Analyzer(shape,True).IsValid(),
            'volume_mm3':volume,'surface_area_mm2':area,
            'center_of_mass_mm':center,'bounds':audit.bounds(shape)}


def difference(a,b,label):
    args,tools=TopTools_ListOfShape(),TopTools_ListOfShape()
    args.Append(a);tools.Append(b)
    algorithm=BRepAlgoAPI_Cut()
    algorithm.SetArguments(args);algorithm.SetTools(tools)
    algorithm.SetNonDestructive(True)
    algorithm.SetRunParallel(True)
    algorithm.SetFuzzyValue(FUZZY_MM)
    start=time.monotonic()
    print(json.dumps({'stage':label,'status':'started'}),flush=True)
    algorithm.Build()
    if not algorithm.IsDone():
        raise RuntimeError(f'{label}: Boolean operation failed')
    result=algorithm.Shape()
    faces=len(audit.indexed(result,TopAbs_FACE))
    solids=len(audit.indexed(result,TopAbs_SOLID))
    volume,_=audit.prop(result,True)
    row={'direction':label,'done':True,
         'detailed_error_warning_api':'not exposed by this Python binding; checked IsDone and result topology',
         'fuzzy_tolerance_mm':FUZZY_MM,'remaining_face_count':faces,
         'remaining_solid_count':solids,'remaining_volume_mm3':volume,
         'elapsed_seconds':time.monotonic()-start}
    print(json.dumps(row),flush=True)
    if faces or solids or abs(volume)>1e-8:
        raise RuntimeError(f'{label}: nonempty difference; no cleanup allowed')
    return row


def main():
    if audit.digest(SOURCE)!=EXPECTED:
        raise RuntimeError('Source changed since initial audit; re-audit needed')
    xt=SOURCE.with_suffix('.x_t')
    source_hashes={str(p):audit.digest(p) for p in [SOURCE,xt]}
    staged=OUT/'input_for_check.stp'
    staged.write_bytes(SOURCE.read_bytes())
    reader,shape=load(staged)
    solids=audit.indexed(shape,TopAbs_SOLID)
    if len(solids)!=2 or not all(BRepCheck_Analyzer(s,True).IsValid() for s in solids):
        raise RuntimeError('Expected exactly two valid solids')
    report={'source_hashes':source_hashes,'source_solid_count':2,
            'kept_import_solid_index':1,'omitted_import_solid_index':2,
            'source_step_brep_ids':re.findall(r'#(\d+)=MANIFOLD_SOLID_BREP\(',SOURCE.read_text(encoding='utf-8')),
            'equivalence_method':'bidirectional Boolean difference, OCCT 7.8.1, 1e-6 mm fuzzy tolerance',
            'differences':[]}
    for a,b,label in [(solids[0],solids[1],'solid_1_minus_2'),(solids[1],solids[0],'solid_2_minus_1')]:
        report['differences'].append(difference(a,b,label))
        (OUT/'verification_in_progress.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    before=metric(solids[0])
    writer=STEPControl_Writer()
    assert Interface_Static.SetCVal_s('write.step.schema','AP214IS')
    assert Interface_Static.SetCVal_s('write.step.unit','MM')
    Interface_Static.SetCVal_s('write.step.product.name','P001_formed_single')
    writer.Model(True)
    if writer.Transfer(solids[0],STEPControl_AsIs)!=IFSelect_RetDone:
        raise RuntimeError('STEP transfer for writing failed')
    target=OUT/'P001_formed_single.stp'
    if writer.Write(str(target))!=IFSelect_RetDone:
        raise RuntimeError('STEP writing failed')
    _,reread=load(target)
    after=metric(reread)
    if after['solid_count']!=1 or not after['valid']:
        raise RuntimeError('Output must contain one valid solid')
    for key,tolerance in [('volume_mm3',1e-5),('surface_area_mm2',1e-4)]:
        if abs(before[key]-after[key])>tolerance:
            raise RuntimeError(f'Round-trip {key} changed')
    for key in ['min_mm','max_mm']:
        if any(abs(a-b)>1e-5 for a,b in zip(before['bounds'][key],after['bounds'][key])):
            raise RuntimeError('Round-trip bounds changed')
    if before['face_count']!=after['face_count']:
        raise RuntimeError('Face count changed')
    output_text=target.read_text(encoding='utf-8')
    if 'AUTOMOTIVE_DESIGN' not in output_text or 'SI_UNIT(.MILLI.,.METRE.)' not in output_text:
        raise RuntimeError('AP214/mm verification failed')
    if not all(audit.digest(Path(p))==sha for p,sha in source_hashes.items()):
        raise RuntimeError('Original file changed')
    report.update({'verification_passed':True,'before':before,'after':after,
                   'output_file':str(target),'output_sha256':audit.digest(target),
                   'originals_unchanged':True,
                   'export_scope':'Single geometry work copy; CAD colours/layers/feature history not carried over by this geometry-only writer.',
                   'intermediate_states':{'present_as_distinct_bodies':False,
                     'reason':'Both source solids occupy the same material volume within the stated tolerance. No process-step state series was found in these STEP bodies.',
                     'wkf_history_checked':False,'parasolid_native_history_checked':False}})
    (OUT/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# P001 单实体工作文件核验','',
           '两个原实体经双向布尔差集核验，A−B 与 B−A 均没有剩余面或实体。',
           '计算采用 OCCT 7.8.1，布尔比较容差 1e-6 mm；这是该容差下的几何等价核验。','',
           '已保留导入实体1，导出 P001_formed_single.stp；原 STEP 和 Parasolid 未改动。',
           '输出重新读取：1个实体，几何/拓扑有效，1247个面；体积、表面积和坐标范围通过往返核验。',
           '输出 AP214、毫米、原绝对坐标；几何工作副本不携带原 VISI 颜色/图层/特征历史。','',
           '## 中间态','',
           '原 STEP 的两个实体为同一材料体积的副本，没有提供两个不同加工阶段。',
           '本次没有检查原 WKF 中是否另存特征历史或阶段模型，也没有原生读取 Parasolid。',
           '后续中间态需要独立的几何关联、工序分配与运动/变形模型，或读取原 CAD 中明确存在的阶段状态。',
           '原有 Sheet5、专家分配、冻结业务逻辑均未用于本次几何核验与导出。','']
    (OUT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'stage':'complete','target':str(target),'valid':after['valid'],'solid_count':after['solid_count']},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
