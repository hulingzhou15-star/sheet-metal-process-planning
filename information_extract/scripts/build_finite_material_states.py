"""Exact material-coordinate removal feeding finite-strip stage states."""
from pathlib import Path
import sys,json
from collections import Counter,defaultdict
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT));MODULE=ROOT/'information_extract'
from information_extract.three_d.artifacts import read,write,sha
from information_extract.three_d.meshing import mesh
from information_extract.three_d.local_collision import indexed,volume,transformed
from information_extract.three_d.material_removal import source_prism
from information_extract.three_d.band_material_solids import checked,subtract,prism,common_volume,domain_overlaps,split_triangle_grid,boxes_intersect
from information_extract.three_d.assets import bounds
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.BRepTools import BRepTools
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.gp import gp_Vec

CFG={'mesh_linear_deflection_mm':.025,'mesh_angular_deflection_rad':.15}


def build_domains(model,stage,asset,faces):
    domains={};components=[];raw={};bands={};t=model['thickness_mm']
    for r in model['regions']:
        rid=r['region_id'];f=TopoDS.Face_s(faces.FindKey(r['front_face_id']))
        filled=BRepBuilderAPI_MakeFace(BRepTools.OuterWire_s(f),True)
        if not filled.IsDone():raise ValueError('Outer support failed')
        flat_face=transformed(filled.Face(),r['flat_from_cad'])
        raw[rid]=checked(BRepPrimAPI_MakePrism(TopoDS.Face_s(flat_face),gp_Vec(0,0,-t)).Shape())
    for b in model['bends']:
        a=np.array(b['start_flat_mm']);e=np.array(b['axis_flat']);d=np.array(b['forward_flat'])
        for i,(lo,hi) in enumerate(b['axis_intervals_mm']):
            key=b['joint_id']+f':S{i+1}'
            shape=prism([a+e*lo,a+e*hi,a+e*hi+d*b['width_mm'],a+e*lo+d*b['width_mm']],t)
            bands[key]=shape;components.append({'component_id':key,'kind':'band','joint_id':b['joint_id'],'operation_id':b['operation_id']})
    children=defaultdict(list)
    for b in model['bends']:children[b['parent_region_id']].append(b['child_region_id'])
    def descendants(rid):return [c for child in children[rid] for c in [child,*descendants(child)]]
    for r in model['regions']:
        rid=r['region_id'];below=set(descendants(rid));tools=[raw[x] for x in below]
        # Filled CAD holes contain the footprints of retained, folded tabs.
        # Assign those footprints to their own descendants BEFORE punching.
        tools+=[s for k,s in bands.items() if next(b for b in model['bends'] if b['joint_id']==k.split(':')[0])['child_region_id'] in below]
        domains[rid]=subtract(raw[rid],tools)
        components.append({'component_id':rid,'kind':'panel','region_id':rid})
    domains.update(bands)
    return domains,components


def prepare():
    out=MODULE/'outputs/finite_material_v02/P001';model=read(out/'model.json')
    for path,h in model['source_sha256'].items():
        if sha(Path(path))!=h:raise ValueError('Stale source')
    stage=read(MODULE/'outputs/stage_review_v01/P001/stage_data.json');asset=read(MODULE/'outputs/three_d_state_v01/P001/asset.json')
    cad=read(MODULE/'configs/three_d_state_v01.json');reader=STEPControl_Reader()
    if reader.ReadFile(str(MODULE/cad['step']))!=IFSelect_RetDone or not reader.TransferRoots():raise ValueError('STEP failed')
    faces=indexed(reader.OneShape(),TopAbs_FACE)
    domains,components=build_domains(model,stage,asset,faces)
    overlaps=domain_overlaps(domains)
    write(out/'domain_audit.json',{'overlaps':overlaps,'component_count':len(domains),'components':components,
        'reference_volume_mm3':sum(volume(s) for s in domains.values()),'initial_blank_recovered':False})
    if overlaps:raise ValueError(f'Material domains overlap: {len(overlaps)} pairs; inspect domain_audit.json')
    print('Material domains',len(domains),'no positive-volume overlaps',flush=True)
    tools={};coverage=[];affected={};boxes={k:bounds(v) for k,v in domains.items()}
    for op in stage['operations']:
        if op['operation_type'] not in {'CIRCLE_PUNCH','IRREGULAR_PUNCH','PRE_PUNCH'}:continue
        oid=op['operation_id'];tool=source_prism(op,stage['display_outline'],0,np.eye(4),[0,0,-model['thickness_mm']])
        tools[oid]=tool;bb=bounds(tool);parts=[]
        for key,body in domains.items():
            if not boxes_intersect(bb,boxes[key]):continue
            v=common_volume(tool,body)
            if v>1e-8:parts.append({'component_id':key,'removed_volume_mm3':v})
        affected[oid]=[p['component_id'] for p in parts];total=sum(p['removed_volume_mm3'] for p in parts);expected=volume(tool)
        status='FULL_SUPPORTED_DOMAIN' if abs(total-expected)<1e-5 else 'PARTIAL_SUPPORTED_DOMAIN' if total>1e-8 else 'OUTSIDE_SUPPORTED_REFERENCE'
        coverage.append({'operation_id':oid,'operation_type':op['operation_type'],'status':status,'source_volume_mm3':expected,
            'supported_removed_volume_mm3':total,'coverage_fraction':total/expected,'components':parts,
            'cut_geometry_source':'ORIGINAL_2D_CLOSED_OPERATION_REGION','forming_not_substituted_for_punch':True})
    write(out/'removal_coverage.json',coverage);print('Removal coverage',dict(Counter(r['status'] for r in coverage)),flush=True)
    return model,stage,domains,components,tools,affected,coverage


def build():
    out=MODULE/'outputs/finite_material_v02/P001';model,stage,domains,components,tools,affected,coverage=prepare()
    orders={s['station_id']:s['order'] for s in stage['stations']};states={};variants=[];geometry_cache={};geometries={}
    # Material variants are keyed by executed punch IDs, independently of bend pose.
    def variant(events):
        signature=tuple(sorted(events))
        if signature in states:return states[signature]['material_id']
        mid=f'M{len(states):02d}';result=[];evidence=[]
        for c in components:
            key=c['component_id'];oids=tuple(o for o in signature if key in affected[o]);cache_key=(key,oids)
            if cache_key not in geometry_cache:
                shape=subtract(domains[key],[tools[o] for o in oids]);v=volume(shape)
                if v<1e-8:geometry_cache[cache_key]=None
                else:
                    triangles=mesh(shape,CFG)['triangles']
                    if c['kind']=='band':
                        b=next(b for b in model['bends'] if b['joint_id']==c['joint_id'])
                        triangles=[small for tri in triangles for small in split_triangle_grid(tri,b,24)]
                    gid=f'G{len(geometries):04d}';geometries[gid]={'triangles':triangles,'flat_volume_mm3':v}
                    geometry_cache[cache_key]=gid
            gid=geometry_cache[cache_key]
            if gid is None:continue
            v=geometries[gid]['flat_volume_mm3']
            result.append({**c,'geometry_id':gid,'flat_volume_mm3':v})
            evidence.append({'component_id':key,'before_mm3':volume(domains[key]),'after_mm3':v,'removed_mm3':volume(domains[key])-v})
        row={'material_id':mid,'executed_removal_ids':list(signature),'components':result,
            'flat_volume_mm3':sum(c['flat_volume_mm3'] for c in result),'cut_evidence':evidence}
        states[signature]=row;variants.append(row);print(mid,len(signature),'cuts',sum(len(geometries[c['geometry_id']]['triangles']) for c in result),'triangles',flush=True)
        return mid
    records=[]
    for plan in stage['plans']:
        for station in stage['stations']:
            sid=station['station_id'];current=station['order']
            for phase in ['entry','completed']:
                executed=[o for o in tools if orders[plan['assignments'][o]]<current or phase=='completed' and orders[plan['assignments'][o]]==current]
                records.append({'plan_id':plan['plan_id'],'station_id':sid,'phase':phase,'material_id':variant(executed)})
    viewer={'model':model,'plans':stage['plans'],'stations':stage['stations'],'material_states':records,'variants':variants,'geometries':geometries,
        'removal_coverage':coverage,'operations':[{k:o[k] for k in ['operation_id','operation_type','flat_paths']} for o in stage['operations'] if o['operation_id'] in tools],
        'summary':{'panel_count':60,'bend_count':59,'band_component_count':sum(c['kind']=='band' for c in components),
            'material_variant_count':len(variants),'plan_count':len(stage['plans']),
            'same_station_cutting_trajectory':'UNKNOWN; motion uses entry material, completed state includes this station cuts',
            'initial_blank_recovered':False,'physical_forming_validated':False,'candidate_filter_enabled':False,
            'unconnected_regions':model['unconnected_regions'],'volume_conservation_claimed':False}}
    (out/'viewer_data.json').write_text(json.dumps(viewer,ensure_ascii=False,separators=(',',':'),allow_nan=False),encoding='utf-8')
    for path,h in model['source_sha256'].items():
        if sha(Path(path))!=h:raise ValueError('Protected source changed')
    print(viewer['summary'],flush=True)


if __name__=='__main__':build()
