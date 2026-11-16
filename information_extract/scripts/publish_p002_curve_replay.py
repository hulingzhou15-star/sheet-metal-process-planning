"""Split the replay meshes into individually loaded, compressed geometry files."""
import argparse
import gzip
import json
from pathlib import Path
import re

from information_extract.scripts.check_p002_curve_motion import DEST
from information_extract.three_d.artifacts import read,write


def mesh_records(path):
    """Stream top-level geometry objects from the project's indented JSON writer."""
    with path.open(encoding='utf8') as source:
        for line in source:
            if line.strip() == '"geometries": {':
                break
        for line in source:
            match = re.fullmatch(r'    "([A-Za-z0-9_.]+)": \{\n',line)
            if not match:
                if line == '  }\n':
                    return
                raise ValueError('Unexpected geometry index')
            rows = ['{\n']
            for row in source:
                if row in ('    },\n','    }\n'):
                    rows.append('}\n')
                    break
                rows.append(row)
            yield match[1],json.loads(''.join(rows))


def publish(folder, reuse_meshes=False):
    mesh_dir = folder/'meshes'
    mesh_dir.mkdir(exist_ok=True)
    count = 0
    if reuse_meshes:
        count = len(list(mesh_dir.glob('*.json.gz')))
    else:
        for gid, geom in mesh_records(folder/'scene.json'):
            with gzip.open(mesh_dir/f'{gid}.json.gz','wt',encoding='utf8',compresslevel=3) as output:
                json.dump(geom,output,separators=(',',':'),allow_nan=False)
            count += 1
    if not count:
        raise ValueError('Scene contains no mesh records')
    write(folder/'replay_scene.json',dict(model=read(folder/'model.json')))
    source = (folder/'p002_curve_review.js').read_text(encoding='utf8')
    source = source.replace("load('scene.json')","load('replay_scene.json')")
    source = source.replace('class Renderer extends', 'scene.geometries = {};\n class Renderer extends',1)
    at = source.index(' function fit()')
    source = source[:at] + """ let pendingDraw = 0;
 async function ensureGeometry(components){
   await Promise.all(components.map(async c=>{
     if(renderer.cache.has(c.geometry_id)||scene.geometries[c.geometry_id])return;
     const response=await fetch('meshes/'+c.geometry_id+'.json.gz');
     if(!response.ok)throw Error('当前材料网格载入失败 '+c.geometry_id);
     const stream=response.body.pipeThrough(new DecompressionStream('gzip'));
     scene.geometries[c.geometry_id]=await new Response(stream).json();
   }));
 }
""" + source[at:]
    source = source.replace(' function fit(){const g=geometry(),points=[];',
        ' async function fit(){if(!report)return;const token=++pendingDraw;const g=geometry(),points=[];await ensureGeometry(g.components);if(token!==pendingDraw)return;')
    source = source.replace(' function draw(){if(!report)return;const g=geometry();renderer.render(g.components,scene.geometries,new Map(),{},g.transforms,camera)}',
        """ async function draw(){
   if(!report)return;
   const token=++pendingDraw,g=geometry();
   try{
     await ensureGeometry(g.components);
     if(token!==pendingDraw)return;
     renderer.render(g.components,scene.geometries,new Map(),{},g.transforms,camera);
     for(const c of g.components)delete scene.geometries[c.geometry_id];
     const visible=new Set(g.components.map(c=>c.geometry_id));
     if(renderer.cache.size>60)for(const [id,row] of renderer.cache){
       if(visible.has(id))continue;
       for(const b of Object.values(row.buffers))renderer.gl.deleteBuffer(b);
       renderer.cache.delete(id);
     }
   }catch(e){fail(e)}
 }""")
    source = source.replace('render();fit()}','render();await fit()}',1)
    if "load('replay_scene.json')" not in source or 'async function draw()' not in source or 'async function fit()' not in source:
        raise ValueError('Viewer adapter did not match source')
    (folder/'p002_curve_lazy_review.js').write_text(source,encoding='utf8')
    page = (folder/'index.html').read_text(encoding='utf8')
    page = page.replace('p002_curve_review.js','p002_curve_lazy_review.js')
    page = page.replace('<a href="verification.json">核验记录</a>',
        '<a href="replay_verification.json">核验记录</a> · <a href="material_audit.json">材料动作</a>'
        ' · <a href="representative_contacts.json">网格穿越的实体复核</a>')
    (folder/'replay.html').write_text(page,encoding='utf8')
    print('Published',count,'compressed replay meshes')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=DEST)
    parser.add_argument('--reuse-meshes',action='store_true')
    args=parser.parse_args()
    publish(args.input.resolve(),args.reuse_meshes)
