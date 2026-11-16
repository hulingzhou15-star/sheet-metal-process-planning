"""Publish the P003 motion samples using the shared P001-style workspace."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import shutil

import numpy as np
from PIL import Image, ImageDraw

from information_extract.scripts.build_p002_stage_review import segment_points
from information_extract.three_d.artifacts import ROOT, read, relative, sha, verify_sources, write

MODULE = ROOT/'information_extract'
WEB = MODULE/'three_d/web'
DEST = MODULE/'outputs/p003_candidate_motion_v01'
PREP = MODULE/'outputs/p003_generation_v01'
ASSET = MODULE/'outputs/multi_part_step_v01/P003/asset.json'


def transform(points, pose):
    return np.asarray(points) @ pose[:3,:3].T + pose[:3,3]


def group(cell, paths, normal):
    return dict(cell=cell, paths=[np.asarray(p).round(7).tolist() for p in paths],
                normal=np.asarray(normal).round(10).tolist())


def features_for(model, skeleton, asset, reg, audits, paths):
    """Locate audited material outlines and both actions of every physical hinge."""
    by_op = {s['operation_id']:j for j in model['joints'] for s in j['steps']}
    poses = {c:np.asarray(p) for c,p in model['flat_transforms'].items()}
    inverse = {c:np.linalg.inv(p) for c,p in poses.items()}
    regions = {r['region_id']:r for r in skeleton['regions']}
    faces = {f['face_id']:f for f in asset['faces']}
    dxf_to_flat = np.array(reg['matrix'])
    shift_xy = np.array(reg['translation_mm'])
    data = {}
    for op in model['operations']:
        oid = op['operation_id']
        path_groups = [[np.asarray(segment_points(s)) @ dxf_to_flat.T + shift_xy for s in p['segments']]
                       for p in paths[oid]['paths']]
        flat = [p for row in path_groups for p in row]
        groups = []
        if oid in by_op:
            j = by_op[oid]
            cell = j['parent_region_id']
            lines = [s['endpoints'] for s in j['developed_axis_segment_evidence_mm']]
            groups.append(group(cell, [transform(line, inverse[cell]) for line in lines],
                                j['reference_surface_normals']['parent']))
        else:
            audit = audits.get(oid,{})
            parent = audits.get(op.get('cad_parent_operation_id'),{})
            edge_id = audit.get('match',{}).get('step_edge_id') or parent.get('match',{}).get('step_edge_id')
            circle = asset['edges'][edge_id-1].get('circle') if edge_id is not None else None
            parts = audit.get('parts') or [{'cell':audit.get('cell')}]
            for index,part in enumerate(parts):
                cell = part.get('cell')
                if cell not in regions:
                    continue
                attached = path_groups[index] if len(parts)==len(path_groups) else flat
                if not attached:
                    continue
                front = faces[regions[cell]['front_face_id']]
                flat_z = transform(front['plane']['point_mm'],poses[cell])[2]
                normal = np.asarray(front['plane']['normal'])
                if (poses[cell][:3,:3] @ normal)[2] > 0:
                    normal = -normal
                offset = np.zeros(2)
                if circle:
                    template = np.vstack(attached)
                    center = (template.min(0)+template.max(0))/2
                    offset = transform(circle['center_mm'],poses[cell])[:2]-center
                lines = [transform(np.column_stack((p+offset,np.full(len(p),flat_z))),inverse[cell])
                         for p in attached]
                groups.append(group(cell,lines,normal))
        data[oid] = dict(flat=[p.round(6).tolist() for p in flat], groups=groups,
                         spatial_location='AUDITED_REGION_OUTLINE_APPROXIMATION' if groups else 'DXF_ONLY')
    return data


FLAT_PIXELS_PER_MM = 6
VISIBLE_PAIR_STATUSES = {'OVERLAP', 'UNKNOWN_KERNEL_FAILURE', 'MESH_CROSSING',
                         'MESH_CONTAINMENT', 'MESH_CONTACT_OR_EDGE_CROSSING'}


def write_replay_plan(path, report):
    """Publish only the fields the replay reads, with shared IDs for equal poses."""
    poses = {}
    samples = []
    for source in report['runs']:
        sample = {key: source[key] for key in ('station_id', 'phase', 'tau', 'material_id',
                  'current_operation_ids', 'executed_operation_ids', 'joint_angles_deg',
                  'unapplied_material_event_ids')}
        sample['removalCount'] = len(source['executed_removal_ids'])
        sample['formingCount'] = len(source['executed_forming_ids'])
        transforms = {key: [v for row in matrix for v in row] for key, matrix in source['transforms'].items()}
        pose = tuple((key, tuple(matrix)) for key, matrix in sorted(transforms.items()))
        sample['poseId'] = poses.setdefault(pose, len(poses))
        sample['transforms'] = transforms
        diagnostic = source['reference_geometry']
        sample['reference_geometry'] = {
            'counts': diagnostic['counts'], 'pair_count': diagnostic['pair_count'],
            'narrow_phase_pairs': [pair for pair in diagnostic['narrow_phase_pairs']
                                   if pair['status'] in VISIBLE_PAIR_STATUSES],
        }
        samples.append(sample)
    replay = dict(assignments=report['assignments'], runs=samples,
                  summary={'issues': report['summary'].get('issues', [])})
    path.write_bytes(gzip.compress(json.dumps(replay, ensure_ascii=False, separators=(',', ':'),
                                              allow_nan=False).encode('utf8'), compresslevel=3, mtime=0))


def pack_mesh(path, triangles):
    points = triangles.reshape(-1, 3)
    normals = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    valid = lengths > 1e-12
    normals[valid] /= lengths[valid, None]
    normals[~valid] = [0, 0, 1]
    with path.open('wb') as stream:
        with gzip.GzipFile(filename='', fileobj=stream, mode='wb', compresslevel=3, mtime=0) as output:
            for values in (points.min(0), points.max(0), points, np.repeat(normals, 3, axis=0)):
                output.write(values.astype('<f4', copy=False).tobytes())


def raster_projection(path, triangles, pose, color, geom):
    projected = transform(triangles.reshape(-1, 3), np.asarray(pose))[:, :2].reshape(-1, 3, 2).round(5)
    ab, ac = projected[:, 1]-projected[:, 0], projected[:, 2]-projected[:, 0]
    projected = projected[abs(ab[:, 0]*ac[:, 1]-ab[:, 1]*ac[:, 0]) > 1e-8]
    if not len(projected) or not np.all(np.isfinite(projected)):
        raise ValueError(f'{geom} lacks a finite 2D material projection')
    points = projected.reshape(-1, 2)*FLAT_PIXELS_PER_MM
    lo = np.floor(points.min(0)).astype(int)-1
    hi = np.ceil(points.max(0)).astype(int)+1
    pixels = np.rint((points-[lo[0], hi[1]])*[1, -1]).astype(int).reshape(-1, 6)
    image = Image.new('P', tuple(hi-lo))
    image.putpalette([0, 0, 0, *bytes.fromhex(color.lstrip('#'))])
    draw = ImageDraw.Draw(image)
    for triangle in pixels.tolist():
        draw.polygon(triangle, fill=1)
    image.save(path, transparency=0, bits=1)
    return dict(lo=(lo/FLAT_PIXELS_PER_MM).tolist(), hi=(hi/FLAT_PIXELS_PER_MM).tolist())


def prepare_display_assets(source, out, variants, poses, color):
    flat_dir = out/'flat_raster'
    packed_dir = out/'packed'
    for directory in (flat_dir, packed_dir):
        directory.mkdir(parents=True, exist_ok=True)
    owners = {}
    for variant in variants:
        for cell,geom in variant['cells'].items():
            if owners.setdefault(geom,cell)!=cell:
                raise ValueError(f'{geom} has two material owners')
    rasters = {}
    for geom in sorted(owners.keys() | {'COMPLETED_STEP'}):
        with gzip.open(source/'meshes'/f'{geom}.json.gz','rt',encoding='utf8') as stream:
            triangles = np.asarray(json.load(stream)['triangles'])
        if triangles.ndim != 3 or triangles.shape[1:] != (3, 3) or not len(triangles) or not np.all(np.isfinite(triangles)):
            raise ValueError(f'{geom} lacks finite triangles')
        pack_mesh(packed_dir/f'{geom}.f32.gz', triangles)
        if geom in owners:
            rasters[geom] = raster_projection(flat_dir/f'{geom}.png', triangles, poses[owners[geom]], color, geom)
    return rasters


def once(source, before, after):
    if source.count(before)!=1:
        raise ValueError(f'P002 workspace template changed near {before[:48]}')
    return source.replace(before,after,1)


def replace_block(source, start, following, block):
    first = source.index('\n  '+start)
    last = source.index('\n  '+following, first+1)
    return source[:first]+'\n'+block+'\n'+source[last:]


def optimize_js(script):
    script = once(script,'  const datum = new Map();','''  const datum = new Map();
  const assets = P003ReplayAssets;
  let lastFlat, lastScene, lastFeatureRows, lastCollisionSample, lastCollisionMode;
  const pendingDraws=new Map();
  let sampleFrame, pendingSample, sampleBusy=false;

  function requestDraw(kind) {
    if (pendingDraws.has(kind)) return;
    const view=$(kind===2 ? 'flat' : 'scene').ownerDocument.defaultView;
    const owner=typeof view.requestAnimationFrame==='function' ? view : globalThis;
    const id=owner.requestAnimationFrame(()=>{
      pendingDraws.delete(kind);
      if (kind===2) draw2(); else draw3();
    });
    pendingDraws.set(kind,{owner,id});
  }

  function cancelDraw(kind) {
    const pending=pendingDraws.get(kind);
    if (!pending) return;
    pending.owner.cancelAnimationFrame(pending.id);
    pendingDraws.delete(kind);
  }''')
    script = once(script,"    $('error').hidden = false;",
                  """    $('state').textContent='回放加载失败。'+error.message;
    $('error').hidden = false;""")
    script = replace_block(script, 'async function load(', 'function cached(', '''  async function load(url, compressed = false) {
    const response = await assets.request(url);
    if (!compressed) return response.json();
    return new Response(response.body.pipeThrough(new DecompressionStream('gzip'))).json();
  }''')
    script = replace_block(script, 'function mapGroup(', 'function groupsFor(', '''  function mapGroup(group, sample, completed) {
    if (completed) return group.paths;
    const matrix=sample.transforms[group.cell];
    return group.paths.map(path=>path.map(point=>transform(matrix,point)));
  }''')
    script = replace_block(script, 'function prepareLines(', 'function trimGpu(', '''  function prepareLines(active) {
    const completed=displayed.completed;
    const ids=completed ? [] : active.filter(id=>id!==picked);
    if (picked) ids.push(picked);
    return ids.flatMap(id=>data.features[id].groups.map(({cell,paths},i)=>({
      key:id+':'+i, component:{kind:'panel',region_id:completed ? 'OUTLINE' : cell}, paths, selected:id===picked,
    })));
  }''')
    script = once(script,'    const lines = prepareLines();','''    const canvas=$('scene'), active=displayed.completed ? [] : activeIds();
    const pose=displayed.completed ? 'step' : [displayed.plan,displayed.sample.material_id,displayed.sample.poseId];
    const key=JSON.stringify([pose,canvas.width,canvas.height,camera,active,picked,displayed.zone]);
    if (key===lastScene) return;
    const lines = prepareLines(active);''')
    script = once(script,'    renderer.render(components,{},new Map(),{},transforms,camera,new Set(),lines);',
                  '''    renderer.render(components,{},noBends,{},transforms,camera,noActiveOperations,lines);
    lastScene=key;''')
    script = once(script,'  const assets = P003ReplayAssets;','''  const assets = P003ReplayAssets;
  const noBends = new Map(), noActiveOperations = new Set();''')
    script = once(script,'    trimGpu(renderer.cache,new Set(components.map(c=>c.geometry_id)),60);',
                  '    if (renderer.cache.size>60) trimGpu(renderer.cache,new Set(components.map(c=>c.geometry_id)),60);')
    script = once(script,'    trimGpu(renderer.lineCache,new Set(lines.map(c=>c.key)),220);',
                  '    if (renderer.lineCache.size>220) trimGpu(renderer.lineCache,new Set(lines.map(c=>c.key)),220);')
    script = replace_block(script, 'function draw2(', 'function boxPoints(', '''  function draw2() {
    if (!displayed) return;
    const canvas=$('flat'), {scale,center}=flatCamera, active=activeIds();
    const key=JSON.stringify([displayed.sample.material_id,canvas.width,canvas.height,scale,center,active,picked]);
    if (key===lastFlat) return;
    const ctx=canvas.getContext('2d'), ox=canvas.width/2-center[0]*scale, oy=canvas.height/2+center[1]*scale;
    ctx.setTransform(1,0,0,1,0,0);
    ctx.clearRect(0,0,canvas.width,canvas.height);
    for (const {image,lo,hi} of displayed.flatParts) {
      ctx.drawImage(image,ox+lo[0]*scale,oy-hi[1]*scale,(hi[0]-lo[0])*scale,(hi[1]-lo[1])*scale);
    }
    ctx.setTransform(scale,0,0,-scale,ox,oy);
    ctx.lineWidth=1.3*dpr(canvas)/scale;
    ctx.strokeStyle='#fff';
    for (const id of active) ctx.stroke(featureOutline(id));
    if (picked) {
      ctx.strokeStyle='#cf2e45';
      ctx.stroke(featureOutline(picked));
    }
    ctx.setTransform(1,0,0,1,0,0);
    lastFlat=key;
  }''')
    script = replace_block(script, 'function flatPart(', 'function table(', '''  function flatPart(id) {
    let entry=outlines.get(id);
    if (entry) outlines.delete(id);
    else {
      entry={bytes:0};
      entry.ready=assets.raster(id).then(image=>{
        entry.bytes=image.width*image.height*4;
        return {...data.flatRasters[id],image};
      }).catch(error=>{
        outlines.delete(id);
        throw error;
      });
    }
    outlines.set(id,entry);
    return entry.ready;
  }

  function trimFlat(keep) {
    // Budget decoded RGBA rather than compressed PNG bytes.
    const budget=64*1024*1024;
    let bytes=0;
    for (const entry of outlines.values()) bytes+=entry.bytes;
    for (const [id,entry] of outlines) {
      if (bytes<=budget) break;
      if (keep.has(id) || !entry.bytes) continue;
      outlines.delete(id);
      bytes-=entry.bytes;
    }
  }''')
    script = once(script,"        canvas.width=w; canvas.height=h;",
                  """        canvas.width=w; canvas.height=h;
        if (id==='flat') lastFlat=undefined;
        else lastScene=undefined;""")
    script = replace_block(script, 'async function show(', 'function localIndex(', '''  async function show(index, fit = false) {
    const token=++generation;
    index=Math.min(report.runs.length-1,Math.max(0,index));
    const next=report.runs[index], selectedPlan=planId;
    const completed=$('comparison').value==='step';
    const sameMaterial=displayed?.sample.material_id===next.material_id;
    const sameMesh=completed ? displayed?.completed : sameMaterial && !displayed.completed;
    const cells=variants.get(next.material_id).cells;
    const meshIds=completed ? {STEP:'COMPLETED_STEP'} : cells;
    const components=sameMesh ? displayed.components : Object.entries(meshIds).map(([cell,id])=>({
      kind:'panel', region_id:cell, geometry_id:id,
    }));
    let flatParts=displayed?.flatParts;
    if (!sameMaterial || !sameMesh) {
      const needMesh=components.filter(c=>!renderer.cache.has(c.geometry_id));
      const flatIds=Object.values(cells);
      if (needMesh.length || flatIds.some(id=>!outlines.has(id))) {
        $('state').textContent='正在载入 '+selectedPlan+' '+next.station_id+' 材料…';
      }
      let parts,meshes;
      try {
        [parts,meshes]=await Promise.all([
          sameMaterial ? flatParts : Promise.all(flatIds.map(flatPart)),
          Promise.all(needMesh.map(async ({geometry_id:id})=>[id,await cached(datum,id,12,()=>assets.mesh(id))])),
        ]);
      } catch (error) {
        if (token!==generation) return;
        throw error;
      }
      if (token!==generation) return;
      flatParts=parts;
      if (!sameMaterial) trimFlat(new Set(flatIds));
      for (const [id,mesh] of meshes) {
        if (!renderer.cache.has(id)) renderer.info(id,{[id]:mesh});
        datum.delete(id);
      }
    }
    const transforms=completed ? {STEP:identity,OUTLINE:identity} : {...next.transforms,OUTLINE:identity};
    state=index;
    displayed={sample:next,plan:selectedPlan,index,completed,flatParts,components,transforms};
    $('error').hidden=true;
    sizeCanvases();
    details();
    redraw(fit);
  }''')
    script = once(script,"r.executed_removal_ids.length","r.removalCount")
    script = once(script,"r.executed_forming_ids.length","r.formingCount")
    script = replace_block(script, "  table('featureList'", '  if (op && ', """    featureRows();
    collisionRows();""")
    script = once(script, '    const rows = diagnostic.narrow_phase_pairs.filter(p=>statusLabel[p.status]).map(p=>{',
                  """  }

  function featureRows() {
    if (!displayed || !$('stageFeatures').open) return;
    const r=displayed.sample, done=new Set(r.executed_operation_ids);
    const ids=r.current_operation_ids, key=JSON.stringify(ids.map(id=>[id,done.has(id)]));
    if (key===lastFeatureRows) return;
    table('featureList',['动作','名称','类型','状态'],ids.map(id=>{
      const o=operations.get(id), button=document.createElement('button');
      button.className='jump'; button.textContent=id;
      button.onclick=()=>selectFeature(id);
      return [button,name(o),typeLabel[o.operation_type],done.has(id) ? '已完成' : '本序加工'];
    }));
    lastFeatureRows=key;
  }

  function collisionRows() {
    if (!displayed || !$('sampleIssues').open) return;
    if (lastCollisionSample===displayed.sample && lastCollisionMode===displayed.completed) return;
    lastCollisionSample=displayed.sample;
    lastCollisionMode=displayed.completed;
    const rows = displayed.sample.reference_geometry.narrow_phase_pairs.map(p=>{""")
    script = once(script, '      const t = sample.curve_fractions[g.cell];\n',
                  '')
    view_first=script.index('      if (!displayed.completed && t!==undefined')
    view_last=script.index('      const n=C.norm(normal);', view_first)
    script = script[:view_first]+'''      if (!displayed.completed) normal=transform(sample.transforms[g.cell],normal,true);
'''+script[view_last:]
    script = once(script, "        draw3();\n      } else {\n        flatCamera.center",
                  "        requestDraw(3);\n      } else {\n        flatCamera.center")
    script = once(script, "        draw2();\n      }\n    };",
                  "        requestDraw(2);\n      }\n    };")
    script = once(script, "camera.scale*=Math.exp(-event.deltaY*.001); draw3();",
                  "camera.scale*=Math.exp(-event.deltaY*.001); requestDraw(3);")
    script = once(script, "flatCamera.scale*=Math.exp(-event.deltaY*.001); draw2();",
                  "flatCamera.scale*=Math.exp(-event.deltaY*.001); requestDraw(2);")
    script = once(script, "    popouts.delete(kind);", "    cancelDraw(kind);\n    popouts.delete(kind);")
    script = once(script, "    panel.replaceWith(placeholder);",
                  "    cancelDraw(kind);\n    panel.replaceWith(placeholder);")
    script = once(script, "    interactive=true;\n    sizeCanvases();",
                  """    if (!interactive) {
      interactive=true;
      sizeCanvases();
    }""")
    script = once(script, "      const next=await cached(reports,id,4,()=>load(\x60../\x24{id}/collision_report.json\x60));",
                  "      const next=await cached(reports,id,4,()=>load(\x60replay_plans/\x24{id}.json.gz\x60,true));")
    script = once(script, "      report=next;", """      report=next;
      report.motionStations??=new Set(next.runs.filter(s=>s.phase==='motion').map(s=>s.station_id));""")
    script = once(script, "    } finally {\n      if (token===planGeneration)",
                  """    } catch (error) {
      if (token===planGeneration) throw error;
    } finally {
      if (token===planGeneration)""")
    script = once(script, "report.runs.some(s=>s.station_id===r.station_id && s.phase==='motion')",
                  "report.motionStations.has(r.station_id)")
    script = once(script,"    data=await load('data.json');",'''    if (location.protocol==='file:') throw Error('请通过项目的本地 HTTP 回放地址打开此页，直接打开 HTML 无法读取回放文件。');
    data=await load('data.json?v=2');''')
    script = once(script,"    renderer=new FiniteMaterialRenderer($('scene'));",
                  "    renderer=new assets.Renderer($('scene'));")
    script = once(script,"      if (!loadingPlan || duringLoad) return Promise.resolve().then(fn).catch(fail);",
                  """      if (sampleFrame!==undefined) {
        cancelAnimationFrame(sampleFrame);
        sampleFrame=undefined;
      }
      pendingSample=undefined;
      if (!loadingPlan || duringLoad) return Promise.resolve().then(fn).catch(fail);""")
    script = once(script, "  function localIndex(station, phase) {", """  function queueSample() {
    if (sampleBusy || sampleFrame!==undefined || pendingSample===undefined) return;
    sampleFrame=requestAnimationFrame(async ()=>{
      sampleFrame=undefined;
      const index=pendingSample;
      pendingSample=undefined;
      sampleBusy=true;
      try {
        await show(index);
      } catch (error) {
        fail(error);
      } finally {
        sampleBusy=false;
        queueSample();
      }
    });
  }

  function localIndex(station, phase) {""")
    script = once(script, "    $('sample').oninput=run(()=>{stop();return show(Number($('sample').value));});",
                  """    $('sample').oninput=()=>{
      if (loadingPlan) return;
      if (playing) stop();
      pendingSample=Number($('sample').value);
      ++generation;
      queueSample();
    };
    $('stageFeatures').ontoggle=featureRows;
    $('sampleIssues').ontoggle=collisionRows;""")
    return script


def specialize_html(source):
    page = source.replace('P002','P003')
    page = once(page,'四处曲线折弯采用截面扫掠近似。','O069 正折 135°，O120 反折 45°，两步共用 J297。')
    page = once(page,'曲线折弯 · O088','第一步 · O069')
    page = once(page,'拉包 · O105','第二步 · O120')
    page = once(page,'<p id="selectedInfo"></p>','<p id="selectedInfo"></p><p id="jointAngle"></p>')
    page = once(page,'<a href="../replay_verification.json">16方案核验</a>',
                '<a href="verification.json">回放资产核验</a>')
    page = once(page,'<a href="../representative_contacts.json">网格穿越的实体复核</a>',
                '<a href="../summary.json">全部方案干涉汇总</a>')
    page = once(page,'<a href="../replay.html">单视图回放</a>', '')
    page = once(page,'<details><summary>本采样的干涉核查</summary>',
                '<details id="sampleIssues"><summary>本采样的干涉核查</summary><p class="note">交叠体积来自当前材料区域的实体求交。折弯带、模具、夹具和局部成形过渡未纳入检查。</p>')
    page = once(page,'  <div class="report-links">',
                '  <details id="planIssues"><summary>本方案的交叠与未决</summary><div id="planIssueRows" class="table-scroll"></div></details>\n  <div class="report-links">')
    page = once(page,'<script src="../core.js"></script><script src="../finite_material_renderer.js"></script>',
                '<script src="core.js"></script><script src="finite_material_renderer.js"></script><script src="p003_replay_assets.js"></script>')
    page = once(page,'<script src="review.js"></script>','<script src="review.js?v=2"></script>')
    return page


def specialize_js(source):
    script = source.replace('P002','P003').replace('p002','p003')
    script = once(script,"  function name(op) {\n    // The source label of O069 contains a local path fragment.\n    return op.operation_id==='O069' ? '曲线折弯4' : op.name;\n  }",
                  """  function name(op) {
    if (op.operation_id==='O069') return op.name+' (+135°)';
    if (op.operation_id==='O120') return op.name+' (−45°)';
    return op.name;
  }""")
    script = once(script,"jumpToFeature('O088','motion')","jumpToFeature('O069','completed')")
    script = once(script,"jumpToFeature('O105','completed')","jumpToFeature('O120','completed')")
    script = once(script,"${r.executed_removal_ids.length}/79","${r.executed_removal_ids.length}/${data.removalCount}")
    script = once(script,"${r.executed_forming_ids.length}/36","${r.executed_forming_ids.length}/${data.formingCount}")
    script = once(script,"        fit3(groupsFor(picked,displayed.sample,displayed.completed).flat(2),8);",
                  """        const shape=groupsFor(picked,displayed.sample,displayed.completed).flat(2);
        if (shape.length) fit3(shape,8);""")
    script = once(script,"      const g=data.features[picked].groups[0], sample=displayed.sample;",
                  """      const g=data.features[picked].groups[0], sample=displayed.sample;
      if (!g) return view('top');""")
    script = once(script,"    const diagnostic = r.reference_geometry;",
                  """    if (op && !data.features[picked].groups.length) $('selectedInfo').textContent += ' · 二维位置';
    const angle = r.joint_angles_deg?.J297;
    $('jointAngle').textContent = Number.isFinite(angle) ?
      `J297 累计折弯角 ${Math.abs(angle)<.00001 ? '0' : angle.toFixed(2)}°` : '';
    const diagnostic = r.reference_geometry;""")
    script = once(script,"    $('collisionStatus').textContent = `本采样检查 ${diagnostic.pair_count} 对材料区域。网格穿越提示的实体复核见下方链接。`;\n    table('collisionRows',['区域对','检查记录'],diagnostic.narrow_phase_pairs\n      .filter(p=>statusLabel[p.status]).map(p=>[p.region_ids.join(' / '),statusLabel[p.status]]));",
                  """    const positives = diagnostic.counts.OVERLAP || 0;
    const unresolved = diagnostic.counts.UNKNOWN_KERNEL_FAILURE || 0;
    const pending = r.unapplied_material_event_ids?.length || 0;
    $('collisionStatus').textContent = `本采样检查 ${diagnostic.pair_count} 对材料区域，${positives} 对交叠，${unresolved} 对未决。` +
      (pending ? ` 有 ${pending} 个材料动作尚未反映在实体中。` : '');
    const rows = diagnostic.narrow_phase_pairs.filter(p=>statusLabel[p.status]).map(p=>{
      const volume=Number.isFinite(p.intersection_volume_mm3) ? p.intersection_volume_mm3.toFixed(6) : '—';
      const bounds=p.intersection_world_aabb_mm;
      const focus=document.createElement('button');
      focus.textContent='定位';
      focus.disabled=!bounds || displayed.completed;
      focus.onclick=()=>{
        const [lo,hi]=[bounds.slice(0,3),bounds.slice(3)];
        displayed.zone=bounds;
        fit3(boxPoints(lo,hi),18);
        draw3();
      };
      return [p.region_ids.join(' / '),statusLabel[p.status],volume,focus];
    });
    table('collisionRows',['区域对','检查记录','交叠 mm³','位置'],rows);""")
    script = once(script,"    const lines = prepareLines();",
                  """    const lines = prepareLines();
    const zone=displayed.zone;
    if (zone) {
      const points=boxPoints(zone.slice(0,3),zone.slice(3)), paths=[];
      for(let i=0;i<8;i++) for(let j=i+1;j<8;j++) if ([1,2,4].includes(i^j)) paths.push([points[i],points[j]]);
      lines.push({key:`zone:${zone.join(',')}`,component:{kind:'panel',region_id:'OUTLINE'},paths,selected:true});
    }""")
    script = once(script,"      report=next;","""      report=next;
      table('planIssueRows',['工序','区域对','记录','峰值 mm³','样本数','位置'],
        (report.summary.issues || []).map(issue=>{
          const button=document.createElement('button');
          button.textContent='查看';
          button.onclick=async()=>{
            try {
              stop();
              $('comparison').value='stage';
              const i=report.runs.findIndex(sample=>sample.station_id===issue.station_id &&
                sample.phase===issue.phase && sample.tau===issue.tau);
              if(i<0) throw Error('找不到对应的运动样本。');
              await show(i);
              if(issue.intersection_world_aabb_mm) {
                const bounds=issue.intersection_world_aabb_mm;
                displayed.zone=bounds;
                fit3(boxPoints(bounds.slice(0,3),bounds.slice(3)),18);
                draw3();
              }
            } catch(error) {
              fail(error);
            }
          };
          const volume=issue.status==='OVERLAP' ? issue.max_volume_mm3.toFixed(6) : '—';
          return [issue.station_id,issue.cells.join(' / '),statusLabel[issue.status],volume,String(issue.samples),button];
        }));""")
    return optimize_js(script)


def build(source):
    output = source/'stage'
    model = read(source/'model.json')
    plans = read(source/'plans.json')
    staged = read(source/'material_states.json')
    audit = read(source/'material_audit.json')
    if model['part_id']!='P003' or len(plans['ranking'])!=16:
        raise ValueError('Expected the complete set of sixteen P003 plans')
    asset = read(ASSET)
    skeleton = read(PREP/'skeleton.json')
    registration = read(PREP/'registration.json')
    paths = read(PREP/'geometry_paths.json')
    variants = {v['material_id']:v for v in staged['variants']}
    faces = features_for(model,skeleton,asset,registration,audit['operations'],paths)
    references = [source/name for name in ('model.json','plans.json','material_states.json','material_audit.json')]
    replay_plans = output/'replay_plans'
    replay_plans.mkdir(parents=True, exist_ok=True)
    mesh_ids = {'COMPLETED_STEP'}
    for plan in plans['plans']:
        pid = plan['plan_id']
        report_path = source/pid/'collision_report.json'
        report = read(report_path)
        if report['assignments']!=plan['assignments'] or not report['runs']:
            raise ValueError(f'Wrong replay report for {pid}')
        for sample in report['runs']:
            expected = variants[sample['material_id']]['cells']
            if set(sample['transforms'])!=set(expected) or sample['geometry_ids']!=expected:
                raise ValueError(f'Broken material reference for {pid}')
            mesh_ids.update(expected.values())
        write_replay_plan(replay_plans/f'{pid}.json.gz', report)
        references.extend((report_path,source/pid/'completed.step'))
    references.extend(source/'meshes'/f'{gid}.json.gz' for gid in sorted(mesh_ids))
    references.extend((ASSET,PREP/'registration.json',PREP/'skeleton.json',PREP/'geometry_paths.json',Path(__file__)))
    template = WEB/'p002_stage_workspace.html'
    app = WEB/'p002_stage_workspace.js'
    css_template = WEB/'local_forming_review.html'
    theme = MODULE/'configs/local_forming_display_v03.json'
    renderers = [WEB/'core.js', WEB/'finite_material_renderer.js', WEB/'p003_replay_assets.js']
    references.extend([template,app,css_template,theme,*renderers])
    sources = {relative(p):sha(p) for p in references}
    output.mkdir(exist_ok=True)
    style = read(theme)
    rasters = prepare_display_assets(source, output, staged['variants'], model['flat_transforms'], style['baseCss'])
    actions = Counter(o['operation_type'] for o in model['operations'])
    display_model = {key: model[key] for key in ('operations', 'stations')}
    display_variants = [{key: v[key] for key in ('material_id', 'cells')} for v in staged['variants']]
    write(output/'data.json',dict(model=display_model,features=faces,variants=display_variants,ranking=plans['ranking'],flatRasters=rasters,
          removalCount=sum(actions[k] for k in ('CIRCLE_PUNCH','IRREGULAR_PUNCH','PRE_PUNCH')),
          formingCount=sum(actions[k] for k in ('FLANGING','DIMPLE'))))
    (output/'index.html').write_text(specialize_html(template.read_text(encoding='utf8')),encoding='utf8')
    (output/'review.js').write_text(specialize_js(app.read_text(encoding='utf8')),encoding='utf8')
    (output/'review.css').write_text(css_template.read_text(encoding='utf8').split('<style>',1)[1].split('</style>',1)[0],encoding='utf8')
    (output/'theme.js').write_text('globalThis.FiniteMaterialTheme='+json.dumps(style)+';\n',encoding='utf8')
    for renderer in renderers:
        shutil.copyfile(renderer,output/renderer.name)
    verify_sources(sources)
    write(output/'verification.json',dict(source_files=sources,plan_count=len(plans['plans']),feature_count=len(faces),
        spatial_feature_count=sum(bool(f['groups']) for f in faces.values()),material_projection_count=len(rasters),
        packed_mesh_count=len(rasters)+1, flat_pixels_per_mm=FLAT_PIXELS_PER_MM,
        source_replay_unchanged=True, browser_ui_tested=False))
    print('P003 review',len(faces),'features',len(rasters),'projections',output)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=DEST)
    args = parser.parse_args()
    source = (ROOT/args.input).resolve()
    if not source.is_relative_to(MODULE/'outputs'):
        raise ValueError('Replay inputs must be under information_extract/outputs')
    build(source)
