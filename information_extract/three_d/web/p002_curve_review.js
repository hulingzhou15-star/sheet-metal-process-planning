(async function(){'use strict';
 const $=id=>document.getElementById(id),load=async p=>{const r=await fetch(p);if(!r.ok)throw Error(p+': '+r.status);return r.json()};
 try{
 const [scene,payload,summary]=await Promise.all([load('scene.json'),load('plans.json'),load('summary.json')]);
 class Renderer extends FiniteMaterialRenderer{componentUniforms(c,b,f,t){super.componentUniforms(c,b,f,t);if(c.tint)this.gl.uniform3fv(this.uniforms.color,c.tint)}}
 const canvas=$('scene'),renderer=new Renderer(canvas),identity=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1];
 const camera={center:[0,0,0],pan:[0,0],yaw:-1.2,pitch:.75,scale:2};
 let report=null,playing=false,lastTick=0,generation=0;const cache=new Map();
 const ops=new Map(scene.model.operations.map(o=>[o.operation_id,o]));
 for(const id of payload.ranking)$('plan').add(new Option(id,id));
 for(const s of scene.model.stations)$('station').add(new Option(s.station_id,s.station_id));
 const param=new URLSearchParams(location.search).get('plan');if(payload.ranking.includes(param))$('plan').value=param;
 const current=()=>report.runs[Number($('sample').value)];
 function sizes(){const dpr=Math.min(devicePixelRatio||1,2);canvas.width=Math.round(canvas.clientWidth*dpr);canvas.height=Math.round(canvas.clientHeight*dpr)}
 function geometry(){
   if($('mode').value==='final')return {components:[{kind:'panel',region_id:'STEP',geometry_id:'COMPLETED_STEP'}],transforms:{STEP:identity}};
   const r=current(),hit=new Set(r.reference_geometry.narrow_phase_pairs.filter(p=>['OVERLAP','MESH_CROSSING','MESH_CONTAINMENT'].includes(p.status)).flatMap(p=>p.region_ids));
   const active=new Set(r.current_operation_ids),nodes=new Map(scene.model.nodes.map(n=>[n.region_id,n]));
   function isActive(id){const n=nodes.get(id);return active.has(scene.model.curve_guides[id]?.operation_id)||active.has(n.operation_id)||!!(n.parent_region_id&&isActive(n.parent_region_id))}
   return {components:Object.keys(r.transforms).map(id=>({kind:'panel',region_id:id,geometry_id:r.geometry_ids[id],tint:hit.has(id)?[.83,.22,.18]:isActive(id)?[.35,.65,.85]:[.82,.86,.89]})),transforms:Object.fromEntries(Object.entries(r.transforms).map(([k,v])=>[k,v.flat()]))};
 }
 function fit(){const g=geometry(),points=[];for(const c of g.components){const info=renderer.info(c.geometry_id,scene.geometries),m=g.transforms[c.region_id];for(const x of [info.lo[0],info.hi[0]])for(const y of [info.lo[1],info.hi[1]])for(const z of [info.lo[2],info.hi[2]])points.push([m[0]*x+m[1]*y+m[2]*z+m[3],m[4]*x+m[5]*y+m[6]*z+m[7],m[8]*x+m[9]*y+m[10]*z+m[11]])}
 const lo=[0,1,2].map(k=>Math.min(...points.map(p=>p[k]))),hi=[0,1,2].map(k=>Math.max(...points.map(p=>p[k])));camera.center=lo.map((x,k)=>(x+hi[k])/2);camera.pan=[0,0];camera.scale=.8*Math.min(canvas.width,canvas.height)/Math.max(1,Math.hypot(...hi.map((x,k)=>x-lo[k])));draw()}
 function draw(){if(!report)return;const g=geometry();renderer.render(g.components,scene.geometries,new Map(),{},g.transforms,camera)}
 function textRows(el,rows){el.replaceChildren();if(!rows.length){el.textContent='无';return}const t=document.createElement('table');for(const row of rows){const tr=document.createElement('tr');for(const x of row){const td=document.createElement('td');td.textContent=x;tr.append(td)}t.append(tr)}el.append(t)}
 function render(){const r=current();$('station').value=r.station_id;$('phase').textContent=`${r.station_id} · ${{entry:'进入',motion:'运动采样',completed:'完成'}[r.phase]} · ${Math.round(r.tau*100)}%`;
 const c=r.reference_geometry.counts;
 const hits=(c.OVERLAP||0)+(c.MESH_CROSSING||0)+(c.MESH_CONTAINMENT||0);
 $('caption').textContent=$('mode').value==='final'?'完整成品 STEP':`显示19个材料区域。交叠或网格穿越 ${hits} 对。工序材料版本 ${r.material_id}。`;
 const s=summary.plans.find(p=>p.plan_id===report.plan_id);
 $('coverage').textContent=`${report.sample_count} 个采样 · 本采样 ${r.reference_geometry.pair_count} 对 · ${s.samples_with_unapplied_material_events} 个采样仍含尚未还原的材料事件。`;
 const done=new Set(r.executed_operation_ids);textRows($('events'),r.current_operation_ids.map(id=>[id,ops.get(id).name,ops.get(id).operation_type,done.has(id)?'完成端事件':'本序待完成']));
 textRows($('unknown'),r.unapplied_material_event_ids.map(id=>[id,ops.get(id).name]));
 textRows($('issues'),r.reference_geometry.narrow_phase_pairs.filter(p=>['OVERLAP','UNKNOWN_KERNEL_FAILURE','MESH_CROSSING','MESH_CONTAINMENT','MESH_CONTACT_OR_EDGE_CROSSING'].includes(p.status)).map(p=>[p.region_ids.join(' / '),p.status,p.intersection_volume_mm3===undefined?'网格采样':p.intersection_volume_mm3.toFixed(6)+' mm³']));draw()}
 async function plan(){playing=false;$('play').textContent='播放采样';const id=$('plan').value,token=++generation;$('loading').textContent='正在载入 '+id;
 if(!cache.has(id))cache.set(id,await load(id+'/collision_report.json'));if(token!==generation)return;report=cache.get(id);$('sample').max=report.runs.length-1;$('sample').value=0;
 for(const [key,file] of [['step','completed.step'],['model','model.json'],['report','collision_report.json']])$(key).href=id+'/'+file;
 $('loading').textContent=`${id} · 曲线折弯及阶段材料已载入`;render();fit()}
 $('plan').onchange=()=>plan().catch(fail);$('mode').onchange=()=>{render();fit()};$('sample').oninput=render;
 $('station').onchange=()=>{$('sample').value=report.runs.findIndex(r=>r.station_id===$('station').value);render()};
 $('fit').onclick=fit;$('top').onclick=()=>{camera.pitch=Math.PI/2;camera.yaw=-Math.PI/2;draw()};$('iso').onclick=()=>{camera.pitch=.75;camera.yaw=-1.2;draw()};
 function tick(now){if(!playing)return;if(now-lastTick>100){lastTick=now;let n=Number($('sample').value)+1;if(n>=report.runs.length){playing=false;$('play').textContent='播放采样';return}$('sample').value=n;render()}requestAnimationFrame(tick)}
 $('play').onclick=()=>{playing=!playing;$('play').textContent=playing?'暂停':'播放采样';if(playing){if(Number($('sample').value)>=report.runs.length-1)$('sample').value=0;lastTick=0;requestAnimationFrame(tick)}};
 let drag=null;canvas.oncontextmenu=e=>e.preventDefault();canvas.onpointerdown=e=>{drag={x:e.clientX,y:e.clientY,pan:e.shiftKey||e.button===2};canvas.setPointerCapture(e.pointerId)};
 canvas.onpointermove=e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;drag.x=e.clientX;drag.y=e.clientY;if(drag.pan){const ratio=canvas.width/canvas.clientWidth;camera.pan[0]+=dx*ratio/camera.scale;camera.pan[1]-=dy*ratio/camera.scale}else{camera.yaw-=dx*.006;camera.pitch=Math.max(-1.56,Math.min(1.56,camera.pitch+dy*.006))}draw()};
 canvas.onpointerup=canvas.onpointercancel=()=>drag=null;canvas.onwheel=e=>{e.preventDefault();camera.scale*=Math.exp(-e.deltaY*.001);draw()};
 window.addEventListener('resize',()=>{sizes();fit()});sizes();await plan();
 }catch(e){fail(e)}
 function fail(e){$('loading').textContent='载入失败：'+e.message;$('loading').className='error';console.error(e)}
})();
