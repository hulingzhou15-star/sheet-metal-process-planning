"""Build v0.3 punch restoration and STEP local forming endpoint states."""
from information_extract.scripts.build_finite_material_states import *
from information_extract.three_d.local_forming import *
from OCP.TopAbs import TopAbs_SOLID
from information_extract.three_d.band_material import state_transforms

OUT=MODULE/'outputs/local_forming_v03/P001'
BASE=MODULE/'outputs/finite_material_v02/P001'


def prepare_local():
    OUT.mkdir(parents=True,exist_ok=True)
    model=read(BASE/'model.json');stage=read(MODULE/'outputs/stage_review_v01/P001/stage_data.json')
    for path,h in model['source_sha256'].items():assert sha(Path(path))==h,path
    asset=read(MODULE/'outputs/three_d_state_v01/P001/asset.json');cfg=read(MODULE/'configs/three_d_state_v01.json')
    reader=STEPControl_Reader();assert reader.ReadFile(str(MODULE/cfg['step']))==IFSelect_RetDone;assert reader.TransferRoots()
    cad=reader.OneShape();faces=indexed(cad,TopAbs_FACE)
    domains,components=build_domains(model,stage,asset,faces)
    tools={o['operation_id']:source_prism(o,stage['display_outline'],0,np.eye(4),[0,0,-model['thickness_mm']]) for o in stage['operations'] if o['operation_type'] in {'CIRCLE_PUNCH','IRREGULAR_PUNCH','PRE_PUNCH'}}
    restoration=restore_punch_material(model,domains,components,tools)
    write(OUT/'restoration_audit.json',restoration);print('Five punch regions restored',flush=True)
    flange=read(OUT/'flanging_step_inventory.json');bridges=read(BASE/'missing_features_audit.json')['bridges']
    patches,local_rows=extract_local_patches(model,stage,cad,flange['operations'],bridges,domains)
    write(OUT/'local_patch_audit.json',local_rows)
    for oid,p in patches.items():
        for kind in ['mask','flat','formed','cad']:save_shape(OUT/'brep'/f'{oid}_{kind}.brep',p[kind])
    print('Local STEP patches',len(patches),flush=True)
    return model,stage,domains,components,tools,patches


def build():
    model,stage,domains,components,tools,patches=prepare_local()
    local_rows=read(OUT/'local_patch_audit.json');local_by_id={r['operation_id']:r for r in local_rows}
    before_total=sum(volume(s) for s in domains.values())
    # Remove each flat patch from its host before installing the local component.
    for oid,p in patches.items():
        rid=p['region_id'];removed=common_volume(domains[rid],p['mask'])
        assert abs(removed-volume(p['flat']))<1e-5,(oid,'overlapping patch ownership')
        domains[rid]=subtract(domains[rid],[p['mask']])
        key='LOCAL:'+oid;domains[key]=p['flat']
        components.append({'component_id':key,'kind':'panel','region_id':rid,'forming_operation_id':oid,'local_forming':True})
    overlaps=domain_overlaps(domains)
    assert not overlaps,overlaps
    assert abs(sum(volume(s) for s in domains.values())-before_total)<1e-5
    write(OUT/'domain_audit.json',{'component_count':len(components),'overlaps':overlaps,'reference_volume_mm3':before_total,'initial_blank_recovered':False})
    print('Disjoint material domains',len(domains),flush=True)
    tools_boxes={k:bounds(v) for k,v in tools.items()};boxes={k:bounds(v) for k,v in domains.items()}
    affected={};coverage=[]
    for oid,tool in tools.items():
        parts=[]
        for key,body in domains.items():
            if not boxes_intersect(tools_boxes[oid],boxes[key]):continue
            v=common_volume(tool,body)
            if v>1e-8:parts.append({'component_id':key,'removed_volume_mm3':v})
        total=sum(p['removed_volume_mm3'] for p in parts);expected=volume(tool)
        assert abs(total-expected)<1e-5,(oid,total,expected)
        affected[oid]=[p['component_id'] for p in parts]
        coverage.append({'operation_id':oid,'status':'FULL_SUPPORTED_DOMAIN','source_volume_mm3':expected,'supported_removed_volume_mm3':total,'coverage_fraction':total/expected,'components':parts})
    assert len(coverage)==100
    write(OUT/'removal_coverage.json',coverage);print('Full supported punch coverage 100/100',flush=True)
    flange_rows={r['operation_id']:r for r in read(OUT/'flanging_step_inventory.json')['operations']}
    # Only its own pre-hole can intersect a local flange replacement; otherwise
    # another removal event could silently be activated too early by the STEP crop.
    for oid,p in patches.items():
        hits=[o for o,keys in affected.items() if 'LOCAL:'+oid in keys]
        expected=[flange_rows[oid]['pre_punch_operation_id']] if oid in flange_rows else []
        assert sorted(hits)==expected,(oid,hits,expected)
        local_by_id[oid]['intersecting_punch_ids']=hits
        # Boundary attachment and non-overlap against the host are checked in
        # material coordinates, which also covers later rigid parent motion.
        host=domains[p['region_id']];assert common_volume(host,p['formed'])<1e-6
        host_count=indexed(host,TopAbs_SOLID).Extent()
        joined=fuse(host,p['formed']);joined_count=indexed(joined,TopAbs_SOLID).Extent()
        assert joined_count<=host_count,(oid,'formed patch disconnected',host_count,joined_count)
        local_by_id[oid].update(host_solid_count=host_count,joined_solid_count=joined_count,host_overlap_mm3=common_volume(host,p['formed']),attached_to_host=True)
        # Independent round-trip to CAD endpoint, quantified by symmetric difference.
        r=next(r for r in model['regions'] if r['region_id']==p['region_id'])
        final=transformed(p['formed'],state_transforms(model,{b['joint_id']:1. for b in model['bends']})[p['region_id']])
        err=volume(subtract(final,[p['cad']]))+volume(subtract(p['cad'],[final]))
        assert err<1e-5,(oid,err)
        local_by_id[oid]['cad_endpoint_symmetric_difference_mm3']=err
    write(OUT/'local_patch_audit.json',local_rows)
    print('50 local patches attached; CAD endpoint equality verified',flush=True)
    # Persist exact source and resulting B-reps for verification and collision.
    for key,body in domains.items():save_shape(OUT/'brep'/('domain_'+key.replace(':','_')+'.brep'),body)
    for oid,tool in tools.items():save_shape(OUT/'brep'/('tool_'+oid+'.brep'),tool)
    orders={s['station_id']:s['order'] for s in stage['stations']};states={};variants=[];cache={};geometries={}
    def geometry(key,shape,c):
        if key in cache:return cache[key]
        if volume(shape)<1e-8:cache[key]=None;return None
        triangles=mesh(shape,CFG)['triangles']
        if c['kind']=='band':
            b=next(b for b in model['bends'] if b['joint_id']==c['joint_id'])
            triangles=[small for tri in triangles for small in split_triangle_grid(tri,b,24)]
        gid=f'G{len(geometries):04d}'
        geometries[gid]={'triangles':triangles,'flat_volume_mm3':volume(shape),'brep_file':f'brep/{gid}.brep'}
        save_shape(OUT/'brep'/f'{gid}.brep',shape);cache[key]=gid
        return gid
    comparison={}
    for oid,p in patches.items():
        c=next(c for c in components if c.get('forming_operation_id')==oid)
        gid=geometry(('formed',oid),p['formed'],c)
        comparison[oid]={**c,'geometry_id':gid}
    def variant(cuts,formed):
        signature=(tuple(sorted(cuts)),tuple(sorted(formed)))
        if signature in states:return states[signature]['material_id']
        result=[]
        for c in components:
            key=c['component_id'];oids=tuple(o for o in signature[0] if key in affected[o]);foid=c.get('forming_operation_id')
            flatkey=('flat',key,oids)
            if flatkey not in cache:geometry(flatkey,subtract(domains[key],[tools[o] for o in oids]),c)
            flatgid=cache[flatkey]
            if foid in formed:
                if foid in flange_rows:assert flange_rows[foid]['pre_punch_operation_id'] in cuts,(foid,'forming before pre-hole')
                gid=comparison[foid]['geometry_id']
            else:gid=flatgid
            if gid is None:continue
            result.append({**c,'geometry_id':gid,'flat_geometry_id':flatgid,'flat_volume_mm3':geometries[gid]['flat_volume_mm3'],
                           **({'forming_status':'FORMED' if foid in formed else 'UNFORMED'} if foid else {})})
        mid=f'M{len(states):02d}';row={'material_id':mid,'executed_removal_ids':list(signature[0]),'executed_forming_ids':list(signature[1]),'components':result,'flat_volume_mm3':sum(c['flat_volume_mm3'] for c in result)}
        states[signature]=row;variants.append(row);print(mid,len(cuts),'cuts',len(formed),'forming',flush=True);return mid
    records=[]
    for plan in stage['plans']:
        for station in stage['stations']:
            sid=station['station_id'];current=station['order']
            for phase in ['entry','completed']:
                def executed(oid):return orders[plan['assignments'][oid]]<current or phase=='completed' and orders[plan['assignments'][oid]]==current
                records.append({'plan_id':plan['plan_id'],'station_id':sid,'phase':phase,'material_id':variant([o for o in tools if executed(o)],[o for o in patches if executed(o)])})
    bridge_rows=read(BASE/'missing_features_audit.json')['bridges']
    assert set(model['unconnected_regions'])=={r['region_id'] for r in bridge_rows},'Do not hide other unresolved regions'
    model['version']='FINITE_MATERIAL_LOCAL_FORMING_V03';model['local_forming_regions']=bridge_rows;model['unconnected_regions']=[]
    viewer={'model':model,'plans':stage['plans'],'stations':stage['stations'],'material_states':records,'variants':variants,'geometries':geometries,'removal_coverage':coverage,
            'operations':[{k:o[k] for k in ['operation_id','operation_type','flat_paths']} for o in stage['operations'] if o['operation_id'] in tools or o['operation_id'] in patches],
            'local_forming':local_rows,'step_comparison':comparison,'summary':{'panel_count':60,'bend_count':59,'band_component_count':sum(c['kind']=='band' for c in components),'material_variant_count':len(variants),'plan_count':len(stage['plans']),'local_forming_count':len(patches),'unconnected_regions':[],
            'same_station_cutting_trajectory':'UNKNOWN; motion uses entry material, completed state switches all simultaneous local actions together',
            'local_forming_trajectory':'UNKNOWN_ENDPOINTS_ONLY','initial_blank_recovered':False,'physical_forming_validated':False,'candidate_filter_enabled':False,'volume_conservation_claimed':False}}
    (OUT/'viewer_data.json').write_text(json.dumps(viewer,ensure_ascii=False,separators=(',',':'),allow_nan=False),encoding='utf-8')
    write(OUT/'model.json',model)
    for path,h in model['source_sha256'].items():assert sha(Path(path))==h,path
    mark_integrated()
    print(viewer['summary'],flush=True)


def mark_integrated():
    v=read(OUT/'viewer_data.json');digest=sha(OUT/'viewer_data.json');inventory=read(OUT/'flanging_step_inventory.json')
    integrated={r['operation_id'] for r in v['local_forming']}
    for r in inventory['operations']:
        r['integrated_into_stage_solids']=r['operation_id'] in integrated
        r['integration_artifact']='viewer_data.json';r['integration_sha256']=digest
    write(OUT/'flanging_step_inventory.json',inventory)
    source_audit=read(BASE/'missing_features_audit.json')
    for path,h in source_audit['source_sha256'].items():assert sha(Path(path))==h,path
    write(OUT/'integration_manifest.json',{'viewer_data_sha256':digest,'forming_operations':sorted(integrated),'protected_source_sha256':v['model']['source_sha256'],'v02_preserved_sha256':source_audit['source_sha256'],
          'implementation_sha256':{str(p):sha(p) for p in [Path(__file__),MODULE/'three_d/local_forming.py']},'cad_endpoint_only':True,'continuous_forming_validated':False})


if __name__=='__main__':build()
