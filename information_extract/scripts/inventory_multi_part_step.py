"""Inventory newly supplied STEP files without assigning operation correspondences."""
import json
from pathlib import Path
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_SOLID, TopAbs_FACE, TopAbs_EDGE
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TColStd import TColStd_SequenceOfAsciiString
from information_extract.three_d.assets import indexed, bounds, build_asset
from information_extract.three_d.local_collision import volume
from information_extract.three_d.facts import sha256

ROOT=Path(__file__).resolve().parents[2]
MODULE=ROOT/'information_extract'
OUT=MODULE/'outputs/multi_part_step_v01'


def main():
    config=json.loads((MODULE/'configs/multi_part_step_v01.json').read_text(encoding='utf-8'))
    summaries=[]
    for part,source in config['parts'].items():
        path=MODULE/source['step']
        if sha256(path)!=source['sha256']:raise ValueError(f'{part}: STEP changed')
        reader=STEPControl_Reader()
        if reader.ReadFile(str(path))!=IFSelect_RetDone:raise ValueError(f'{part}: read failed')
        length,angle,solid_angle=[TColStd_SequenceOfAsciiString() for _ in range(3)]
        reader.FileUnits(length,angle,solid_angle)
        reader.SetSystemLengthUnit(1.0)  # OCCT system length unit: millimetres.
        if not reader.TransferRoots():raise ValueError(f'{part}: transfer failed')
        roots=[]
        for i in range(1,reader.NbShapes()+1):
            shape=reader.Shape(i)
            roots.append({'imported_shape_index':i,'shape_type':str(shape.ShapeType()),
                          'solid_count':indexed(shape,TopAbs_SOLID).Extent(),
                          'face_count':indexed(shape,TopAbs_FACE).Extent(),
                          'edge_count':indexed(shape,TopAbs_EDGE).Extent(),'bounds_mm':bounds(shape)})
        shape=reader.OneShape();solids=indexed(shape,TopAbs_SOLID)
        solid_rows=[]
        for i in range(1,solids.Extent()+1):
            solid=solids.FindKey(i)
            solid_rows.append({'solid_index':i,'valid':BRepCheck_Analyzer(solid,True).IsValid(),
                               'volume_mm3':volume(solid),'bounds_mm':bounds(solid)})
        # The existing asset builder includes all roots (including loose edges).
        # Its importer must use the same mm unit; do not silently rescale data.
        probe=STEPControl_Reader();probe.ReadFile(str(path));probe.TransferRoots()
        if probe.SystemLengthUnit()!=1.0:raise ValueError('Unexpected asset importer length unit')
        asset=build_asset(path,{'unit':'mm','mesh':config['mesh'],'edge_sample_count':32})
        if max(abs(a-b) for a,b in zip(asset['bounds_mm'],bounds(shape)))>1e-6:
            raise ValueError('Asset bounds differ from explicit mm import')
        folder=OUT/part;folder.mkdir(parents=True,exist_ok=True)
        summary={'part_id':part,'source':path.relative_to(ROOT).as_posix(),'source_sha256':source['sha256'],
                 'file_length_units':[length.Value(i).ToCString() for i in range(1,length.Length()+1)],
                 'import_length_unit':'mm','root_count':reader.NbRootsForTransfer(),'roots':roots,
                 'solids':solid_rows,'bounds_mm':asset['bounds_mm'],'face_count':asset['face_count'],
                 'edge_count':asset['edge_count'],'surface_counts':asset['surface_counts'],
                 'topology_fingerprint':asset['topology_fingerprint'],
                 'triangle_count':len(asset['mesh']['triangle_face_ids']),
                 'operation_mapping_status':'NOT_ESTABLISHED',
                 'same_revision_as_dxf':'NOT_VERIFIED'}
        for name,data in [('asset.json',asset),('inventory.json',summary)]:
            (folder/name).write_text(json.dumps(data,ensure_ascii=False,allow_nan=False,separators=(',',':')),encoding='utf-8')
        if sha256(path)!=source['sha256']:raise ValueError(f'{part}: source changed during import')
        summaries.append(summary)
        print(part,summary['face_count'],'faces',summary['triangle_count'],'triangles',flush=True)
    (OUT/'inventory.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
