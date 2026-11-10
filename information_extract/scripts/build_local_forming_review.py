from information_extract.three_d.artifacts import relative
"""Offline v0.3 review with local STEP endpoint comparison and stage controls."""
from information_extract.scripts.build_local_forming_states import OUT,BASE,MODULE,read,write,sha
import json,gzip,base64,hashlib
from information_extract.three_d.band_material import state_transforms,map_points
from information_extract.three_d.local_forming import box,intersect,save_shape
from information_extract.scripts.build_finite_material_states import STEPControl_Reader,IFSelect_RetDone,mesh,CFG
import numpy as np


def hole_step_context(data):
    """Original STEP near each missing cut, in CAD world coordinates (display only)."""
    path=OUT/'hole_step_comparison.json';cfg=read(MODULE/'configs/three_d_state_v01.json');step=MODULE/cfg['step']
    hashes={relative(OUT/'viewer_data.json'):sha(OUT/'viewer_data.json'),relative(step):sha(step)}
    if path.exists():
        old=read(path)
        if old['source_sha256']==hashes:return old
    reader=STEPControl_Reader();assert reader.ReadFile(str(step))==IFSelect_RetDone;assert reader.TransferRoots()
    model=data['model'];fr={b['joint_id']:1. for b in model['bends']};ts=state_transforms(model,fr)
    initial=next(v for v in data['variants'] if not v['executed_removal_ids']);geometries={};components={}
    for oid in ['O196','O197','O198','O199','O200']:
        world=[]
        for c in initial['components']:
            if c.get('restored_by_operation_id')!=oid:continue
            points=[p for t in data['geometries'][c['geometry_id']]['triangles'] for p in t]
            world+=map_points(model,c,points,fr,ts)
        points=np.array(world);crop=box(points.min(axis=0)-3,points.max(axis=0)+3)
        shape=intersect(reader.OneShape(),crop);gid='STEP_CONTEXT_'+oid;geometries[gid]=mesh(shape,CFG)
        save_shape(OUT/'brep'/f'{gid}.brep',shape)
        components[oid]={'component_id':gid,'kind':'panel','region_id':model['root_region_id'],'geometry_id':gid,'coordinate_frame':'CAD_WORLD'}
    result={'source_sha256':hashes,'components':components,'geometries':geometries,'interpretation':'DISPLAY_ONLY_ORIGINAL_STEP_CROP_AT_FORMED_POSITION'};write(path,result);return result


def build():
    data=read(OUT/'viewer_data.json');highlight=read(BASE/'feature_highlights.json')
    step_context=hole_step_context(data);data['step_comparison'].update(step_context['components']);data['geometries'].update(step_context['geometries'])
    for path,h in highlight['source_sha256'].items():assert sha(__import__('pathlib').Path(path))==h
    restoration={r['operation_id']:r for r in read(OUT/'restoration_audit.json')}
    components={c['component_id']:c for v in data['variants'] for c in v['components']}
    b=next(b for b in data['model']['bends'] if b['operation_id']=='O025');levels=[b['start_flat_mm'][1],b['start_flat_mm'][1]+b['width_mm']]
    for op in highlight['operations']:
        oid=op['operation_id']
        if oid in restoration:
            paths={k:[] for k in ['parent','band','child']}
            for line in op['flat_paths']:
                for a,z in zip(line,line[1:]):
                    ts=[0.,1.]
                    if abs(z[1]-a[1])>1e-10:ts+= [(level-a[1])/(z[1]-a[1]) for level in levels if 0<(level-a[1])/(z[1]-a[1])<1]
                    ts=sorted(ts)
                    for lo,hi in zip(ts,ts[1:]):
                        mid=a[1]+(z[1]-a[1])*(lo+hi)/2;kind='parent' if mid<levels[0] else 'band' if mid<levels[1] else 'child'
                        paths[kind].append([[a[i]+(z[i]-a[i])*t for i in range(2)]+[0.] for t in [lo,hi]])
            op['parts']=[{'component':components[f'RESTORE:{oid}:{kind}'],'paths':p} for kind,p in paths.items() if p]
            op['display_kind']='PUNCH_CONTOUR'
        elif oid in data['step_comparison']:op['display_kind']='LOCAL_FORMING_ENDPOINT'
    highlight['source_sha256']={relative(OUT/'viewer_data.json'):sha(OUT/'viewer_data.json'),relative(BASE/'feature_highlights.json'):sha(BASE/'feature_highlights.json')}
    data['feature_highlights']=highlight;data['flange_dimensions']=read(OUT/'flanging_step_inventory.json')['operations'];write(OUT/'feature_highlights.json',highlight)
    web=MODULE/'three_d/web';html=(web/'local_forming_review.html').read_text(encoding='utf-8')
    renderer=(web/'finite_material_renderer.js').read_text(encoding='utf-8')
    old='active=band&&this.activeOperations.has(b.operation_id)'
    assert renderer.count(old)==1
    renderer=renderer.replace(old,'active=band?this.activeOperations.has(b.operation_id):!!c.forming_operation_id&&this.activeOperations.has(c.forming_operation_id)')
    theme=read(MODULE/'configs/local_forming_display_v03.json')
    renderer='globalThis.FiniteMaterialTheme='+json.dumps(theme,separators=(',',':'))+';\n'+renderer
    serialized=json.dumps(data,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8');compressed=gzip.compress(serialized,compresslevel=6,mtime=0)
    for name,content in [('DATA',base64.b64encode(compressed).decode()),('CORE',(web/'core.js').read_text(encoding='utf-8')),('MOTION',(web/'motion_core.js').read_text(encoding='utf-8')),('BAND',(web/'band_material_core.js').read_text(encoding='utf-8')),('RENDERER',renderer),('APP',(web/'local_forming_review.js').read_text(encoding='utf-8'))]:html=html.replace('/*__'+name+'__*/',content)
    (OUT/'review.html').write_text(html,encoding='utf-8')
    write(OUT/'viewer_pack.json',{'encoding':'gzip+base64','lossless':True,'decoded_payload_sha256':hashlib.sha256(serialized).hexdigest(),'viewer_data_sha256':sha(OUT/'viewer_data.json'),'html_bytes':(OUT/'review.html').stat().st_size})
    print(OUT/'review.html')


if __name__=='__main__':build()
