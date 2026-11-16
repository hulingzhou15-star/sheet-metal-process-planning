(async function () {
  'use strict';

  const elements = new Map([...document.querySelectorAll('[id]')].map(node => [node.id, node]));
  const $ = id => elements.get(id);
  const C = IE3DCore;
  const identity = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1];
  const views = {iso:[-1.05,.65], top:[-Math.PI/2,Math.PI/2], front:[-Math.PI/2,0], side:[0,0]};
  const phaseLabel = {entry:'进入态',motion:'共同加工过程',completed:'完成态'};
  const typeLabel = {BEND:'折弯',CIRCLE_PUNCH:'圆孔',IRREGULAR_PUNCH:'异形孔',PRE_PUNCH:'预孔',
    FLANGING:'翻孔',DIMPLE:'凸米',DRAW_FORM:'拉包'};
  const statusLabel = {MESH_CROSSING:'网格穿越提示',MESH_CONTAINMENT:'网格包含提示',
    MESH_CONTACT_OR_EDGE_CROSSING:'网格接触或边界穿越',OVERLAP:'正体积交叠',UNKNOWN_KERNEL_FAILURE:'实体计算未决'};
  const dpr = canvas => Math.min(canvas.ownerDocument.defaultView.devicePixelRatio || 1,3);
  const datum = new Map();
  const outlines = new Map();
  const paths2d = new Map();
  const reports = new Map();
  const popouts = new Map();
  const camera = {yaw:views.iso[0],pitch:views.iso[1],center:[0,0,0],pan:[0,0],scale:1};
  const flatCamera = {center:[0,0],scale:1};
  let data, renderer, operations, variants, report, planId, state, displayed, picked;
  let generation = 0, planGeneration = 0, loadingPlan = false, playing = null, timer, qualityTimer, interactive = false;

  function fail(error) {
    stop();
    $('error').hidden = false;
    $('error').textContent = error.message;
  }

  async function load(url, compressed = false) {
    const response = await fetch(url);
    if (!response.ok) throw Error(`无法载入 ${url}（${response.status}）`);
    if (!compressed) return response.json();
    return new Response(response.body.pipeThrough(new DecompressionStream('gzip'))).json();
  }

  function cached(map, key, capacity, create) {
    const previous = map.get(key);
    map.delete(key);
    const value = previous || Promise.resolve().then(create).catch(error => {
      map.delete(key);
      throw error;
    });
    map.set(key,value);
    if (map.size>capacity) map.delete(map.keys().next().value);
    return value;
  }

  function transform(matrix,p,direction = false) {
    return [0,4,8].map(i => matrix[i]*p[0]+matrix[i+1]*p[1]+matrix[i+2]*p[2]+(direction ? 0 : matrix[i+3]));
  }

  function mapGroup(group, sample, completed) {
    if (completed) return group.paths;
    const t = sample.curve_fractions[group.cell];
    if (t===undefined || !group.sweep_sign) {
      return group.paths.map(path => path.map(point => transform(sample.transforms[group.cell].flat(),point)));
    }
    const angle = (1-t)*Math.PI/2, c = Math.cos(angle), s = Math.sin(angle), sign = group.sweep_sign;
    let i = 0;
    return group.paths.map(path => path.map(p => {
      const [nx,ny] = group.outward[i], [x,y] = group.origin[i++];
      const u = (p[0]-x)*nx+(p[1]-y)*ny-.4;
      const h = sign*(p[2]+.4);
      const offset = (c-1)*u+s*h-(1-t)*.4;
      return [p[0]+offset*nx,p[1]+offset*ny,-.4+sign*(c*h-s*u)];
    }));
  }

  function groupsFor(oid, sample, completed) {
    return data.features[oid].groups.map(g => mapGroup(g,sample,completed));
  }

  function activeIds() {
    if (!$('highlight').checked) return [];
    return displayed.sample.current_operation_ids.filter(id => $('showGuides').checked || operations.get(id).operation_type!=='BEND');
  }

  function prepareLines() {
    const {sample, index, plan, completed} = displayed;
    const active = completed ? [] : activeIds();
    const selected = picked ? [picked] : [];
    return [...active.filter(id => id!==picked), ...selected].flatMap(id =>
      groupsFor(id,sample,completed).map((paths,i) => ({
        key:`${completed ? 'step' : plan+':'+index}:${id}:${i}`,
        component:{kind:'panel',region_id:'OUTLINE'},paths,selected:id===picked,
      })));
  }

  function trimGpu(cache, keep, limit) {
    if (cache.size<=limit) return;
    for (const [key,value] of cache) {
      if (keep.has(key)) continue;
      for (const buffer of Object.values(value.buffers)) renderer.gl.deleteBuffer(buffer);
      cache.delete(key);
      if (cache.size<=limit) break;
    }
  }

  function draw3() {
    if (!displayed) return;
    const {components, transforms} = displayed;
    const lines = prepareLines();
    renderer.render(components,{},new Map(),{},transforms,camera,new Set(),lines);
    trimGpu(renderer.cache,new Set(components.map(c=>c.geometry_id)),60);
    trimGpu(renderer.lineCache,new Set(lines.map(c=>c.key)),220);
  }

  function outline(paths) {
    const path = new Path2D();
    for (const line of paths) {
      path.moveTo(...line[0]);
      for (const p of line.slice(1)) path.lineTo(...p);
    }
    return path;
  }

  function featureOutline(id) {
    if (!paths2d.has(id)) paths2d.set(id,outline(data.features[id].flat));
    return paths2d.get(id);
  }

  function draw2() {
    if (!displayed) return;
    const canvas = $('flat'), ctx = canvas.getContext('2d'), {scale,center} = flatCamera;
    ctx.setTransform(1,0,0,1,0,0);
    ctx.clearRect(0,0,canvas.width,canvas.height);
    ctx.setTransform(scale,0,0,-scale,canvas.width/2-center[0]*scale,canvas.height/2+center[1]*scale);
    ctx.fillStyle = FiniteMaterialStyle.baseCss;
    for (const flat of displayed.flatParts) ctx.fill(flat.path);
    ctx.lineWidth = 1.3*dpr(canvas)/scale;
    ctx.strokeStyle = '#fff';
    for (const id of activeIds()) ctx.stroke(featureOutline(id));
    if (picked) {
      ctx.strokeStyle = '#cf2e45';
      ctx.stroke(featureOutline(picked));
    }
    ctx.setTransform(1,0,0,1,0,0);
  }

  function boxPoints(lo,hi) {
    return [lo[0],hi[0]].flatMap(x => [lo[1],hi[1]].flatMap(y => [lo[2],hi[2]].map(z => [x,y,z])));
  }

  function extent(points) {
    const lo = points[0].slice(), hi = lo.slice();
    for (const p of points) p.forEach((v,k) => { lo[k]=Math.min(lo[k],v); hi[k]=Math.max(hi[k],v); });
    return [lo,hi];
  }

  function fit3(points, padding) {
    const [lo,hi] = extent(points);
    camera.center = lo.map((v,k)=>(v+hi[k])/2);
    camera.pan = [0,0];
    const b = C.basis(camera.yaw,camera.pitch);
    const [a,z] = extent(points.map(p=>[C.dot(p,b.right),C.dot(p,b.up)]));
    camera.scale = .86*Math.min($('scene').width/(z[0]-a[0]+padding),$('scene').height/(z[1]-a[1]+padding));
  }

  function fit2(points, padding) {
    const [lo,hi] = extent(points);
    flatCamera.center = lo.map((v,k)=>(v+hi[k])/2);
    flatCamera.scale = .86*Math.min($('flat').width/(hi[0]-lo[0]+padding),$('flat').height/(hi[1]-lo[1]+padding));
  }

  function redraw(fit = false) {
    if (!displayed) return;
    if (fit) {
      if (picked) {
        fit2(data.features[picked].flat.flat(),8);
        fit3(groupsFor(picked,displayed.sample,displayed.completed).flat(2),8);
      } else {
        fit2(displayed.flatParts.flatMap(p=>[p.lo,p.hi]),5);
        const points = displayed.components.flatMap(c=>{
          const g = renderer.cache.get(c.geometry_id);
          return boxPoints(g.lo,g.hi).map(p=>transform(displayed.transforms[c.region_id],p));
        });
        fit3(points,8);
      }
    }
    draw2();
    draw3();
  }

  function flatPart(id) {
    return cached(outlines,id,48,async ()=>{
      const vertices = await load(`flat/${id}.json.gz`,true);
      const path = new Path2D(), lo = [Infinity,Infinity], hi = [-Infinity,-Infinity];
      for (let i=0; i<vertices.length; i+=6) {
        path.moveTo(vertices[i],vertices[i+1]);
        path.lineTo(vertices[i+2],vertices[i+3]);
        path.lineTo(vertices[i+4],vertices[i+5]);
        path.closePath();
        for (let j=0;j<6;j++) {
          const k=j%2, v=vertices[i+j];
          lo[k]=Math.min(lo[k],v); hi[k]=Math.max(hi[k],v);
        }
      }
      return {path,lo,hi};
    });
  }

  function table(target, headings, rows) {
    const t = document.createElement('table');
    const header = t.createTHead().insertRow();
    for (const label of headings) {
      const th = document.createElement('th');
      th.textContent = label;
      header.append(th);
    }
    const body = t.createTBody();
    for (const row of rows) {
      const tr = body.insertRow();
      for (const v of row) {
        const td = tr.insertCell();
        if (typeof v==='string') td.textContent=v; else td.append(v);
      }
    }
    $(target).replaceChildren(rows.length ? t : document.createTextNode('无'));
  }

  function details() {
    const r = displayed.sample;
    $('station').value = r.station_id;
    $('phase').value = r.phase;
    $('phase').querySelector('[value="motion"]').disabled = !report.runs.some(s=>s.station_id===r.station_id && s.phase==='motion');
    $('sample').value = displayed.index;
    $('progress').textContent = `${Math.round(r.tau*100)}% · ${displayed.index+1}/${report.runs.length}`;
    $('prev').disabled = displayed.index===0;
    $('next').disabled = displayed.index===report.runs.length-1;
    const message = `${displayed.plan} · ${r.station_id} ${phaseLabel[r.phase]} · 已去料 ${r.executed_removal_ids.length}/79 · 已局部成形 ${r.executed_forming_ids.length}/36 · 材料版本 ${r.material_id}`;
    $('state').textContent = displayed.completed ? message+' · 右侧为 STEP 成品对照' : message;
    for (const p of popouts.values()) if (!p.win.closed) p.status.textContent = $('state').textContent;
    const op = operations.get(picked);
    $('selectedInfo').textContent = op ? `${op.operation_id} · ${name(op)} · ${report.assignments[picked]} · ${r.executed_operation_ids.includes(picked) ? '已执行' : '待执行'}` : '';
    $('featureSummary').textContent = `本序特征（${r.current_operation_ids.length}）`;
    table('featureList',['动作','名称','类型','状态'],r.current_operation_ids.map(id=>{
      const o=operations.get(id), button=document.createElement('button');
      button.className='jump'; button.textContent=id;
      button.onclick=()=>selectFeature(id);
      return [button,name(o),typeLabel[o.operation_type],r.executed_operation_ids.includes(id) ? '已完成' : '本序加工'];
    }));
    const diagnostic = r.reference_geometry;
    $('collisionStatus').textContent = `本采样检查 ${diagnostic.pair_count} 对材料区域。网格穿越提示的实体复核见下方链接。`;
    table('collisionRows',['区域对','检查记录'],diagnostic.narrow_phase_pairs
      .filter(p=>statusLabel[p.status]).map(p=>[p.region_ids.join(' / '),statusLabel[p.status]]));
  }

  function name(op) {
    // The source label of O069 contains a local path fragment.
    return op.operation_id==='O069' ? '曲线折弯4' : op.name;
  }

  function sizeCanvases() {
    for (const [id,cam] of [['flat',flatCamera],['scene',camera]]) {
      const canvas=$(id);
      const ratio = id==='scene' && interactive && $('quality').value==='fluid' ? 1 : dpr(canvas);
      const w=Math.max(1,Math.round(canvas.clientWidth*ratio)), h=Math.max(1,Math.round(canvas.clientHeight*ratio));
      if (canvas.width!==w || canvas.height!==h) {
        cam.scale *= w/Math.max(canvas.width,1);
        canvas.width=w; canvas.height=h;
      }
    }
  }

  async function show(index, fit = false) {
    const token = ++generation;
    index = Math.min(report.runs.length-1,Math.max(0,index));
    const next = report.runs[index], selectedPlan = planId;
    const material = variants.get(next.material_id);
    const completed = $('comparison').value==='step';
    const meshes = completed ? {STEP:'COMPLETED_STEP'} : next.geometry_ids;
    const components = Object.entries(meshes).map(([cell,id])=>({kind:'panel',region_id:cell,geometry_id:id}));
    const transforms = completed ? {STEP:identity,OUTLINE:identity} :
      {...Object.fromEntries(Object.entries(next.transforms).map(([id,m])=>[id,m.flat()])),OUTLINE:identity};
    const [flatParts, meshesReady] = await Promise.all([
      Promise.all(Object.values(material.cells).map(flatPart)),
      Promise.all(components.map(async c=>{
        const id=c.geometry_id;
        return [id, renderer.cache.has(id) ? null : await cached(datum,id,25,()=>load(`../meshes/${id}.json.gz`,true))];
      })),
    ]);
    if (token!==generation) return;
    for (const [id,mesh] of meshesReady) {
      if (!renderer.cache.has(id)) renderer.info(id,{[id]:mesh});
    }
    state=index;
    displayed={sample:next,plan:selectedPlan,index,completed,flatParts,components,transforms};
    $('error').hidden=true;
    sizeCanvases();
    details();
    redraw(fit);
  }

  function localIndex(station, phase) {
    const index=report.runs.findIndex(s=>s.station_id===station && s.phase===phase);
    return index>=0 ? index : report.runs.findIndex(s=>s.station_id===station && s.phase==='entry');
  }

  function stop() {
    clearTimeout(timer);
    playing=null;
    for (const [id,label] of [['playStation','播放本序'],['playPlan','播放全方案']]) {
      $(id).textContent=label;
      $(id).setAttribute('aria-pressed','false');
    }
  }

  async function play(id) {
    if (playing===id) return stop();
    stop();
    const run = {};
    playing=id;
    const station=$('station').value;
    $('comparison').value='stage';
    $(id).textContent='暂停';
    $(id).setAttribute('aria-pressed','true');
    const end=id==='playStation' ? localIndex(station,'completed') : report.runs.length-1;
    let i=id==='playStation' ? localIndex(station,'entry') : state>=end ? 0 : state;
    play.active=run;
    async function tick() {
      if (playing!==id || play.active!==run) return;
      await show(i);
      if (playing!==id || play.active!==run) return;
      if (i>=end) return stop();
      i++;
      timer=setTimeout(()=>tick().catch(fail),105);
    }
    await tick();
  }

  function selectFeature(id) {
    picked=id;
    $('operation').value=id;
    details();
    redraw(true);
  }

  function jumpToFeature(id,phase) {
    stop();
    picked=$('operation').value=id;
    $('comparison').value='stage';
    return show(localIndex(report.assignments[id],phase),true);
  }

  function view(mode) {
    if (mode==='normal') {
      if (!picked) picked=$('operation').value;
      const g=data.features[picked].groups[0], sample=displayed.sample;
      let normal=g.normal;
      const t = sample.curve_fractions[g.cell];
      if (!displayed.completed && t!==undefined && g.sweep_sign) {
        const [nx,ny]=g.outward[0], u=normal[0]*nx+normal[1]*ny, h=g.sweep_sign*normal[2];
        const a=(1-t)*Math.PI/2, c=Math.cos(a),s=Math.sin(a);
        const delta=(c-1)*u+s*h;
        normal=[normal[0]+delta*nx,normal[1]+delta*ny,g.sweep_sign*(c*h-s*u)];
      } else if (!displayed.completed) normal=transform(sample.transforms[g.cell].flat(),normal,true);
      const n=C.norm(normal);
      camera.yaw=Math.hypot(n[0],n[1])<1e-9 ? -Math.PI/2 : Math.atan2(n[1],n[0]);
      camera.pitch=Math.asin(Math.max(-1,Math.min(1,n[2])));
    } else {
      [camera.yaw,camera.pitch]=views[mode];
    }
    $('view').value=mode;
    details();
    redraw(true);
  }

  function interact() {
    interactive=true;
    sizeCanvases();
    clearTimeout(qualityTimer);
    qualityTimer=setTimeout(()=>{
      interactive=false;
      sizeCanvases();
      draw3();
    },160);
  }

  function bindPointer(element, three) {
    let drag;
    element.oncontextmenu=event=>event.preventDefault();
    element.onpointerdown=event=>{
      drag={x:event.clientX,y:event.clientY,pan:event.shiftKey||event.button===2};
      element.setPointerCapture(event.pointerId);
    };
    element.onpointermove=event=>{
      if (!drag || !displayed) return;
      const dx=event.clientX-drag.x, dy=event.clientY-drag.y;
      drag.x=event.clientX; drag.y=event.clientY;
      if (three) {
        interact();
        if (drag.pan) {
          const ratio=$('scene').width/$('scene').clientWidth;
          camera.pan[0]+=dx*ratio/camera.scale; camera.pan[1]-=dy*ratio/camera.scale;
        } else {
          camera.yaw-=dx*.006;
          camera.pitch=Math.max(-Math.PI/2,Math.min(Math.PI/2,camera.pitch+dy*.006));
        }
        draw3();
      } else {
        flatCamera.center[0]-=dx*dpr(element)/flatCamera.scale;
        flatCamera.center[1]+=dy*dpr(element)/flatCamera.scale;
        draw2();
      }
    };
    element.onpointerup=element.onpointercancel=()=>{drag=null;};
    element.onwheel=event=>{
      event.preventDefault();
      if (three) { interact(); camera.scale*=Math.exp(-event.deltaY*.001); draw3(); }
      else { flatCamera.scale*=Math.exp(-event.deltaY*.001); draw2(); }
    };
  }

  function restorePopup(kind, closing = false) {
    const p=popouts.get(kind);
    if (!p) return;
    popouts.delete(kind);
    p.placeholder.replaceWith(p.panel);
    $('popout'+kind).textContent='新窗口查看';
    if (!closing && !p.win.closed) p.win.close();
    sizeCanvases();
    redraw(true);
  }

  function popout(kind) {
    if (popouts.has(kind)) return restorePopup(kind);
    const win=window.open('',`p002-view-${kind}`,'popup=yes,width=1260,height=850,resizable=yes,scrollbars=yes');
    if (!win) return fail(Error('浏览器拦截了弹出窗口。'));
    const title=kind===2 ? '二维展开材料' : '三维工序状态';
    const panel=$('panel'+kind), placeholder=document.createElement('section');
    placeholder.className='panel placeholder';
    const back=document.createElement('button');
    back.textContent='收回主页面'; back.onclick=()=>restorePopup(kind);
    placeholder.append(title+'已在独立窗口打开',back);
    panel.replaceWith(placeholder);
    const doc=win.document;
    doc.title='P002 · '+title;
    const style=doc.createElement('link');
    style.rel='stylesheet'; style.href=new URL('review.css',location.href).href;
    doc.head.append(style);
    const layout=doc.createElement('style');
    layout.textContent='body{padding:8px}.canvasbox{height:calc(100vh - 140px)}.toolbar{margin:0 0 8px}';
    doc.head.append(layout);
    const toolbar=doc.createElement('div'), status=doc.createElement('span');
    toolbar.className='toolbar';
    for (const [label,fn] of [['聚焦特征',()=>selectFeature($('operation').value)],['整件',()=>$('whole').click()],['收回主页面',()=>restorePopup(kind)]]) {
      const button=doc.createElement('button'); button.textContent=label; button.onclick=fn; toolbar.append(button);
    }
    toolbar.append(status);
    doc.body.append(toolbar,panel);
    popouts.set(kind,{win,panel,placeholder,status});
    $('popout'+kind).textContent='收回主页面';
    const resize=()=>{sizeCanvases();redraw(true);};
    win.addEventListener('resize',resize);
    win.addEventListener('pagehide',()=>restorePopup(kind,true),{once:true});
    style.onload=resize;
    details(); resize(); win.focus();
  }

  async function loadPlan(id, station) {
    stop();
    const token=++planGeneration;
    loadingPlan=true;
    ++generation;
    $('state').textContent=`正在载入 ${id}…`;
    try {
      const next=await cached(reports,id,4,()=>load(`../${id}/collision_report.json`));
      if (token!==planGeneration) return;
      if (!next.runs.length) throw Error('候选没有采样状态');
      planId=id;
      report=next;
      $('sample').max=next.runs.length-1;
      $('step').href=`../${id}/completed.step`;
      $('report').href=`../${id}/collision_report.json`;
      await show(Math.max(0,localIndex(station,'entry')),true);
    } finally {
      if (token===planGeneration) loadingPlan=false;
    }
  }

  try {
    data=await load('data.json');
    operations=new Map(data.model.operations.map(o=>[o.operation_id,o]));
    variants=new Map(data.variants.map(v=>[v.material_id,v]));
    renderer=new FiniteMaterialRenderer($('scene'));
    for (const id of data.ranking) $('plan').add(new Option(id,id));
    for (const s of data.model.stations) $('station').add(new Option(s.station_id,s.station_id));
    for (const o of data.model.operations) $('operation').add(new Option(`${o.operation_id} · ${name(o)}`,o.operation_id));
    const params=new URLSearchParams(location.search);
    if (data.ranking.includes(params.get('plan'))) $('plan').value=params.get('plan');
    if (operations.has(params.get('operation'))) picked=$('operation').value=params.get('operation');
    const run = (fn,duringLoad = false) => () => {
      if (!loadingPlan || duringLoad) return Promise.resolve().then(fn).catch(fail);
    };
    $('plan').onchange=run(()=>loadPlan($('plan').value,$('station').value),true);
    $('station').onchange=run(()=>{stop();return show(localIndex($('station').value,'entry'),!!picked);});
    $('phase').onchange=run(()=>{stop();return show(localIndex($('station').value,$('phase').value),!!picked);});
    $('sample').oninput=run(()=>{stop();return show(Number($('sample').value));});
    for (const [id,offset] of [['prev',-1],['next',1]]) $(id).onclick=run(()=>{stop();return show(state+offset);});
    for (const id of ['playStation','playPlan']) $(id).onclick=run(()=>play(id));
    $('focus').onclick=$('operation').onchange=run(()=>selectFeature($('operation').value));
    for (const [id,phase] of [['before','entry'],['after','completed']]) $(id).onclick=run(()=>jumpToFeature($('operation').value,phase));
    $('curveExample').onclick=run(()=>jumpToFeature('O088','motion'));
    $('drawExample').onclick=run(()=>jumpToFeature('O105','completed'));
    $('comparison').onchange=run(()=>{stop();return show(state,true);});
    $('clear').onclick=run(()=>{picked=null;details();redraw();});
    $('whole').onclick=run(()=>{stop();picked=null;$('comparison').value='stage';return show(state,true);});
    $('view').onchange=run(()=>view($('view').value));
    $('top').onclick=run(()=>view('top'));
    $('normal').onclick=run(()=>view('normal'));
    $('highlight').onchange=$('showGuides').onchange=run(()=>redraw());
    $('quality').onchange=run(()=>{sizeCanvases();draw3();});
    $('popout2').onclick=run(()=>popout(2));
    $('popout3').onclick=run(()=>popout(3));
    bindPointer($('flat'),false);
    bindPointer($('overlay'),true);
    window.addEventListener('resize',()=>{sizeCanvases();redraw(true);});
    window.addEventListener('beforeunload',()=>{
      for (const p of popouts.values()) if (!p.win.closed) p.win.close();
    });
    await loadPlan($('plan').value,params.get('station'));
    window.p002StageReview={getState:()=>({
      plan:displayed.plan, station:displayed.sample.station_id, phase:displayed.sample.phase,
      tau:displayed.sample.tau, index:displayed.index, materialId:displayed.sample.material_id,
      selected:picked, comparison:$('comparison').value, playing, popouts:[...popouts.keys()],
    })};
  } catch (error) {
    fail(error);
  }
})();
