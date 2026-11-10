(function(){
 'use strict';const $=id=>document.getElementById(id),data=JSON.parse($('data').textContent),C=IE3DCore,M=MaterialMotion;
 const canvas=$('overlay'),scene=$('scene'),ctx=canvas.getContext('2d'),renderer=new MotionRenderer(scene),camera={yaw:-1.05,pitch:.55,center:[0,0,0],pan:[0,0],scale:1};
 const colors=[[196,208,219],[113,163,213],[226,158,79],[201,88,66]],opMap=new Map(data.operations.map(o=>[o.operation_id,o]));
 let selected=null,playing=false,lastTick=0,state,geometry;
 const run=()=>data.runs.find(r=>r.station_id===$('station').value),issue=()=>data.issues.find(i=>i.issue_id===selected);
 function current(){if($('mode').value==='stage')return run().samples[Number($('sample').value)];const value=$('mode').value==='flat'?0:1;return {tau:null,joint_fractions:Object.fromEntries(data.skeleton.joints.map(j=>[j.joint_id,value])),...data.baselines[$('mode').value]};}
 function boxes(b){return [0,1].flatMap(x=>[0,1].flatMap(y=>[0,1].map(z=>[b[x?3:0],b[y?4:1],b[z?5:2]])));}
 function movingRegions(){const result=new Set();for(const rid of data.skeleton.region_order.slice(1)){const j=data.skeleton.joints.find(j=>j.child_region_id===rid),op=data.skeleton.joint_to_operation[j.joint_id];if(data.plan.assignments[op]===$('station').value||result.has(j.parent_region_id))result.add(rid);}return result;}
 function makeGeometry(s){const ts=M.poses(data.skeleton,s.joint_fractions),selectedIssue=issue(),moving=movingRegions(),triangles=[],allPoints=[];
  for(const panel of data.panels){const which=selectedIssue?.region_ids.indexOf(panel.region_id)??-1;if($('isolate').checked&&selectedIssue&&which<0)continue;
   const color=which>=0&&panel.region_id!==data.skeleton.root_region_id?which+2:($('mode').value==='stage'&&moving.has(panel.region_id)?1:0);
   for(const tri of panel.triangles){const points=tri.map(p=>M.point(ts[panel.region_id],p));triangles.push({region:color,points});allPoints.push(...points);}}
  return {transforms:ts,triangles,points:allPoints};}
 function activePair(s){const i=issue();return i?s.narrow_phase_pairs.find(p=>p.region_ids.join('/')===i.region_ids.join('/')):null;}
 function paragraph(parent,text,cls){const p=document.createElement('p');p.textContent=text;if(cls)p.className=cls;parent.append(p);}
 function detail(s){const panel=$('detail');panel.replaceChildren();const i=issue();if(!i){paragraph(panel,'选择问题查看关联动作、驱动折弯和交叠位置。');return;}
  const title=document.createElement('h3');title.textContent=i.issue_id+' · '+i.region_ids.join(' / ');panel.append(title);
  const p=activePair(s);paragraph(panel,p?.status==='OVERLAP'?`当前交叠 ${p.intersection_volume_mm3.toFixed(6)} mm³`:p?.status==='UNKNOWN_KERNEL_FAILURE'?'当前内核计算未决':'当前样本未检出该板面对的体积交叠','key');
  paragraph(panel,`直接铰链相邻：${i.cad_hinge_adjacent?'是':'否'}；本序问题采样点 ${i.samples.length} 个。`);
  paragraph(panel,`展开基线也交叠：${i.also_in_flat_baseline?'是':'否'}；成品基线也交叠：${i.also_in_formed_reference?'是':'否'}。`);
  const heading=t=>{const h=document.createElement('h4');h.textContent=t;panel.append(h);};heading('本序驱动折弯');
  paragraph(panel,i.current_driving_bend_ids.map(op=>op+' '+(opMap.get(op)?.source_feature_ref||'')).join('；')||'本序没有直接驱动，可能为既存模型交叠。');
  heading('区域关联动作（不等于致因动作）');paragraph(panel,i.associated_operation_ids.join('、')||'暂无可靠动作关联。');
  heading('敏感性对照');const a=data.artifact_sensitivity.rows.find(r=>r.issue_id===i.issue_id);
  paragraph(panel,a?`在本组最大交叠样本（进度 ${(a.tau*100).toFixed(1)}%）保留成品内孔后：${a.overlap_disappears_with_final_inner_wires_at_tested_sample?'交叠消失':'仍有交叠或计算未决'}。`:'未完成该项对照。');
  paragraph(panel,'成品内孔未必在该工序真实存在，对照不用于自动消除问题。当前仍为零宽转轴和板体包络，不能据此淘汰方案。','warn');
 }
 function draw(){const s=current();geometry=makeGeometry(s);renderer.render(geometry.triangles,[],[],camera,colors);ctx.clearRect(0,0,canvas.width,canvas.height);const p=activePair(s);
  if(issue())for(const panel of data.panels){const k=issue().region_ids.indexOf(panel.region_id);if(k<0)continue;const ps=panel.display_boundary.map(p=>C.project(M.point(geometry.transforms[panel.region_id],p),camera,canvas.width,canvas.height));ctx.beginPath();ps.forEach((p,i)=>i?ctx.lineTo(...p):ctx.moveTo(...p));ctx.strokeStyle='#ffffff';ctx.lineWidth=3.2*devicePixelRatio;ctx.stroke();ctx.strokeStyle=k===0?'#d58325':'#b44334';ctx.lineWidth=1.4*devicePixelRatio;ctx.stroke();}
  if(p?.intersection_world_aabb_mm){const ps=boxes(p.intersection_world_aabb_mm),project=ps.map(p=>C.project(p,camera,canvas.width,canvas.height));ctx.setLineDash([5*devicePixelRatio,3*devicePixelRatio]);ctx.beginPath();for(let a=0;a<8;a++)for(let b=a+1;b<8;b++)if([1,2,4].includes(a^b)){ctx.moveTo(...project[a]);ctx.lineTo(...project[b]);}ctx.strokeStyle='#ffffff';ctx.lineWidth=3.2*devicePixelRatio;ctx.stroke();ctx.strokeStyle='#b44334';ctx.lineWidth=1.5*devicePixelRatio;ctx.stroke();ctx.setLineDash([]);}
  $('progress').textContent=$('mode').value==='stage'?`${(s.tau*100).toFixed(1)}% · ${Number($('sample').value)+1}/${run().samples.length}`:($('mode').value==='flat'?'完全展开参考':'成品位置参考');
  $('status').textContent=`当前样本：${s.counts.OVERLAP||0} 对包络交叠，${s.counts.CONTACT||0} 对接触，${s.counts.UNKNOWN_KERNEL_FAILURE||0} 对未决；共检查 ${s.pair_count} 对。`;
  state={station_id:$('station').value,mode:$('mode').value,sample_index:Number($('sample').value),fractions:s.joint_fractions,selected_issue:selected,counts:s.counts,highlighted_region_ids:issue()?.region_ids||[],camera:structuredClone(camera)};detail(s);
 }
 function setFit(points,padding){if(!points.length)return;const lo=[0,1,2].map(i=>Math.min(...points.map(p=>p[i]))),hi=[0,1,2].map(i=>Math.max(...points.map(p=>p[i])));camera.center=lo.map((v,i)=>(v+hi[i])/2);camera.pan=[0,0];const b=C.basis(camera.yaw,camera.pitch),extent=v=>{const ds=points.map(p=>C.dot(C.sub(p,camera.center),v));return Math.max(...ds)-Math.min(...ds);};camera.scale=.9*Math.min(canvas.width/(extent(b.right)+padding),canvas.height/(extent(b.up)+padding));draw();}
 function fit(){const s=current();geometry=makeGeometry(s);setFit(geometry.points,20);}
 function focus(){const p=activePair(current());if(p?.intersection_world_aabb_mm)setFit(boxes(p.intersection_world_aabb_mm),14);else if(issue()){const ts=geometry.transforms,pts=data.panels.filter(p=>issue().region_ids.includes(p.region_id)).flatMap(p=>p.triangles.flatMap(t=>t.map(v=>M.point(ts[p.region_id],v))));setFit(pts,20);}}
 function stop(){playing=false;$('play').textContent='播放采样状态';}
 function list(){const query=$('search').value.trim().toLowerCase(),filter=$('filter').value;const items=data.issues.filter(i=>i.station_id===$('station').value&&(filter!=='nonadjacent'||!i.cad_hinge_adjacent)&&(filter!=='formed'||i.also_in_formed_reference)&&[i.issue_id,...i.region_ids,...i.associated_operation_ids,...i.current_driving_bend_ids].join(' ').toLowerCase().includes(query));$('count').textContent=items.length;
  $('issues').replaceChildren(...items.map(i=>{const b=document.createElement('button');b.dataset.issue=i.issue_id;b.className=i.issue_id===selected?'active':'';b.textContent=`${i.issue_id} · ${i.cad_hinge_adjacent?'连接相邻':'非直接相邻'}\n${i.region_ids.join(' / ')}\n最大 ${i.max_intersection_volume_mm3.toFixed(3)} mm³`;b.onclick=()=>choose(i.issue_id);return b;}));}
 function choose(id){const i=data.issues.find(x=>x.issue_id===id);if(!i)return false;stop();selected=id;$('station').value=i.station_id;$('mode').value='stage';$('sample').disabled=false;$('play').disabled=false;$('sample').max=run().samples.length-1;const s=i.samples.reduce((a,b)=>(a.intersection_volume_mm3||0)>=(b.intersection_volume_mm3||0)?a:b);$('sample').value=s.index;list();draw();focus();return true;}
 $('station').onchange=()=>{stop();selected=null;$('mode').value='stage';$('sample').disabled=false;$('play').disabled=false;$('sample').max=run().samples.length-1;$('sample').value=0;list();draw();fit();};$('mode').onchange=()=>{stop();const base=$('mode').value!=='stage';$('sample').disabled=base;$('play').disabled=base;draw();};$('sample').oninput=()=>{stop();draw();};$('filter').onchange=$('search').oninput=list;$('isolate').onchange=()=>draw();$('fit').onclick=fit;$('focus').onclick=focus;
 $('view').onchange=()=>{[camera.yaw,camera.pitch]=({iso:[-1.05,.55],top:[-Math.PI/2,Math.PI/2],side:[0,0]})[$('view').value];draw();selected?focus():fit();};
 function tick(now){if(!playing)return;if(now-lastTick>180){lastTick=now;$('sample').value=Number($('sample').value)+1;draw();if(Number($('sample').value)>=run().samples.length-1){stop();return;}}requestAnimationFrame(tick);}
 $('play').onclick=()=>{if(playing){stop();return;}if(Number($('sample').value)===run().samples.length-1)$('sample').value=0;playing=true;lastTick=performance.now();$('play').textContent='暂停';requestAnimationFrame(tick);};
 let drag;canvas.oncontextmenu=e=>e.preventDefault();canvas.onpointerdown=e=>{drag={x:e.clientX,y:e.clientY,pan:e.shiftKey||e.button===2};canvas.setPointerCapture(e.pointerId);};canvas.onpointermove=e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;drag.x=e.clientX;drag.y=e.clientY;if(drag.pan){camera.pan[0]+=dx*devicePixelRatio/camera.scale;camera.pan[1]-=dy*devicePixelRatio/camera.scale;}else{camera.yaw-=dx*.006;camera.pitch=Math.max(-1.56,Math.min(1.56,camera.pitch+dy*.006));}draw();};canvas.onpointerup=canvas.onpointercancel=()=>drag=null;canvas.onwheel=e=>{e.preventDefault();camera.scale=Math.max(.05,Math.min(2000,camera.scale*Math.exp(-e.deltaY*.001)));draw();};
 $('overview').textContent=`60 个板体包络；${data.summary.fine_sample_count} 个细采样状态；${data.summary.issue_group_count} 个待核对问题组。${data.artifact_sensitivity.disappears_at_tested_sample_count} 组在最大交叠样本保留成品内孔后消失，不能将原交叠直接解释为工艺错误。`;
 const tr=values=>{const row=document.createElement('tr');for(const value of values){const td=document.createElement('td');td.textContent=value;row.append(td);}return row;};
 $('refinement').replaceChildren(...data.refinement.map(r=>tr([r.station_id,r.coarse_pairs,r.fine_pairs,r.new_pairs_on_refinement.length])),...Object.entries(data.baselines).map(([k,v])=>tr([k==='flat'?'完全展开基线':'成品位置基线','—',v.counts.OVERLAP||0,'不依赖工序顺序'])));
 for(const text of [`未建立运动归属的材料区域：${data.summary.unconnected_material_regions.join('、')}`,`未可靠定位动作：${data.summary.unmapped_operations.join('、')}`,'外边界仍来自成品，内孔填平；折弯过渡带、局部凸起、工具、装夹均未完整建模。相邻问题没有自动豁免，基线问题也没有自动删除。'])paragraph($('coverage'),text);
 function resize(){for(const c of [scene,canvas]){const b=c.getBoundingClientRect();c.width=Math.round(b.width*devicePixelRatio);c.height=Math.round(b.height*devicePixelRatio);}draw();selected?focus():fit();}window.addEventListener('resize',resize);list();resize();window.wholePanelReview={data,choose,getState:()=>structuredClone(state)};
})();
