(function(){
  'use strict';
  const data=JSON.parse(document.getElementById('data').textContent),C=IE3DCore,M=MaterialMotion,$=id=>document.getElementById(id);
  const canvas=$('motion'),ctx=canvas.getContext('2d'),flat=$('flat'),fctx=flat.getContext('2d');
  const scene=document.createElement('canvas');scene.id='motion-scene';canvas.before(scene);let renderer;
  try{renderer=new MotionRenderer(scene);}catch(e){$('message').textContent='当前使用简化绘图，表面遮挡可能不准确。';}
  const orders=Object.fromEntries(data.stations.map(s=>[s.station_id,s.order]));
  let assignment=M.validateAssignment(data.default_assignment,data),state,playing=false,startTime=0;
  const camera={yaw:-1.05,pitch:.55,center:[0,0,0],pan:[0,0],scale:1};
  const colors=[[150,170,189],[59,120,183],[223,150,59]];
  function compute(){state=M.schedule(data.example,assignment,orders,$('station').value,Number($('tau').value)/100);return state;}
  function currentTriangles(){return data.surfaces.flatMap((s,i)=>s.triangles.map(tri=>({points:tri.map(p=>M.point(state.transforms[s.region_id],p)),region:i})));}
  function allExtentPoints(){const all=[];for(const station of Object.keys(orders))for(const tau of [0,.5,1]){const st=M.schedule(data.example,assignment,orders,station,tau);for(const surface of data.surfaces)for(const tri of surface.triangles)for(const p of tri)all.push(M.point(st.transforms[surface.region_id],p));}return all;}
  function fit(){
    const points=allExtentPoints(),low=[0,1,2].map(i=>Math.min(...points.map(p=>p[i]))),high=[0,1,2].map(i=>Math.max(...points.map(p=>p[i])));
    camera.center=low.map((v,i)=>(v+high[i])/2);camera.pan=[0,0];const b=C.basis(camera.yaw,camera.pitch);
    const extent=v=>{const values=points.map(p=>C.dot(C.sub(p,camera.center),v));return Math.max(...values)-Math.min(...values);};
    camera.scale=Math.min(canvas.width/(extent(b.right)+6),canvas.height/(extent(b.up)+6))*.88;draw();
  }
  function draw(){
    compute();ctx.clearRect(0,0,canvas.width,canvas.height);
    const b=C.basis(camera.yaw,camera.pitch),triangles=currentTriangles();
    for(const t of triangles)t.depth=t.points.reduce((s,p)=>s+C.dot(C.sub(p,camera.center),b.eye),0)/3;
    triangles.sort((a,b)=>a.depth-b.depth);
    if(renderer){const lines=[],target=[];for(const surface of data.surfaces.slice(1))for(const edge of surface.front_edges){lines.push(edge.map(p=>M.point(state.transforms[surface.region_id],p)));if($('target').checked)target.push(edge);}renderer.render(triangles,lines,target,camera,colors);}
    else for(const t of triangles){const p=t.points.map(p=>C.project(p,camera,canvas.width,canvas.height)),raw=C.cross(C.sub(t.points[1],t.points[0]),C.sub(t.points[2],t.points[0]));
      const n=Math.hypot(...raw)>1e-12?C.norm(raw):[0,0,1],light=.65+.35*Math.abs(C.dot(n,C.norm([.3,-.7,1]))),color=colors[t.region];
      ctx.fillStyle=`rgb(${color.map(v=>Math.round(v*light)).join(',')})`;ctx.beginPath();p.forEach((p,i)=>i?ctx.lineTo(...p):ctx.moveTo(...p));ctx.closePath();ctx.fill();}
    // Draw actual CAD skin edges in current pose; crop boundary is display only.
    ctx.strokeStyle='#ffffff70';ctx.lineWidth=.45*devicePixelRatio;
    if(!renderer)for(const surface of data.surfaces.slice(1)){for(const edge of surface.front_edges){const p=edge.map(p=>C.project(M.point(state.transforms[surface.region_id],p),camera,canvas.width,canvas.height));ctx.beginPath();p.forEach((p,i)=>i?ctx.lineTo(...p):ctx.moveTo(...p));ctx.stroke();}}
    if(!renderer&&$('target').checked){ctx.strokeStyle='#7991ac80';ctx.lineWidth=1*devicePixelRatio;ctx.setLineDash([4*devicePixelRatio,4*devicePixelRatio]);for(const surface of data.surfaces.slice(1))for(const edge of surface.front_edges){const p=edge.map(p=>C.project(p,camera,canvas.width,canvas.height));ctx.beginPath();p.forEach((p,i)=>i?ctx.lineTo(...p):ctx.moveTo(...p));ctx.stroke();}ctx.setLineDash([]);}
    if(data.feature){const p=M.point(state.transforms[data.feature.region_id],data.feature.formed_surface_center_mm),xy=C.project(p,camera,canvas.width,canvas.height);ctx.beginPath();ctx.arc(...xy,data.config.display.marker_radius_px*devicePixelRatio,0,Math.PI*2);ctx.fillStyle='#fff';ctx.fill();ctx.lineWidth=2*devicePixelRatio;ctx.strokeStyle='#bd5860';ctx.stroke();ctx.fillStyle='#9d4550';ctx.font=`${12*devicePixelRatio}px sans-serif`;ctx.fillText(data.feature.operation_ids.join(' / '),xy[0]+10*devicePixelRatio,xy[1]-10*devicePixelRatio);$('marker-location').textContent=`X ${p[0].toFixed(3)} · Y ${p[1].toFixed(3)} · Z ${p[2].toFixed(3)} mm`;}
    details();
  }
  function details(){
    const station=$('station').value,tau=Number($('tau').value)/100;
    $('stage-label').textContent=`${station} · 本序过程 ${(tau*100).toFixed(0)}% · 同序动作共用该过程参数`;
    $('assignment').replaceChildren(...data.example.joints.map(j=>{const tr=document.createElement('tr'),op=data.example.joint_to_operation[j.joint_id];for(const v of [op,assignment[op],`${(j.angle_deg*state.fractions[j.joint_id]).toFixed(1)}°`]){const td=document.createElement('td');td.textContent=v;tr.append(td);}return tr;}));
  }
  function drawFlat(){
    fctx.clearRect(0,0,flat.width,flat.height);
    const rows=data.example.association_evidence,points=rows.flatMap(r=>[...r.recorded_endpoints_xy_mm,...r.developed_endpoints_xy_mm]);
    if(data.feature)points.push(data.feature.source_center_xy_mm,data.feature.developed_surface_center_mm.slice(0,2));
    const low=[0,1].map(i=>Math.min(...points.map(p=>p[i]))),high=[0,1].map(i=>Math.max(...points.map(p=>p[i]))),scale=Math.min((flat.width-140*devicePixelRatio)/(high[0]-low[0]+4),(flat.height-65*devicePixelRatio)/(high[1]-low[1]+5));
    const xy=p=>[flat.width/2+(p[0]-(low[0]+high[0])/2)*scale,flat.height/2-(p[1]-(low[1]+high[1])/2)*scale];
    function line(points,color,dashed){fctx.strokeStyle=color;fctx.lineWidth=1.8*devicePixelRatio;fctx.setLineDash(dashed?[5*devicePixelRatio,4*devicePixelRatio]:[]);fctx.beginPath();points.map(xy).forEach((p,i)=>i?fctx.lineTo(...p):fctx.moveTo(...p));fctx.stroke();fctx.setLineDash([]);}
    rows.forEach((r,i)=>{line(r.recorded_endpoints_xy_mm,'#8d9bac',true);line(r.developed_endpoints_xy_mm,i?'#d79a43':'#3b78b7',false);const p=xy(r.developed_endpoints_xy_mm[1]);fctx.fillStyle='#617995';fctx.font=`${11*devicePixelRatio}px sans-serif`;fctx.fillText(`${r.operation_id} · Δ ${r.endpoint_residual_mm.toFixed(3)} mm`,p[0]+8*devicePixelRatio,p[1]);});
    if(data.feature){const f=data.feature;for(const [center,radii,color,dashed] of [[f.source_center_xy_mm,f.source_flange_radii_mm,'#8d9bac',true],[f.developed_surface_center_mm.slice(0,2),[f.cad_front_edge_radius_mm],'#bd5860',false]]){fctx.strokeStyle=color;fctx.lineWidth=1.4*devicePixelRatio;fctx.setLineDash(dashed?[4*devicePixelRatio,3*devicePixelRatio]:[]);for(const radius of radii){fctx.beginPath();fctx.arc(...xy(center),radius*scale,0,2*Math.PI);fctx.stroke();}fctx.setLineDash([]);}const p=xy(f.developed_surface_center_mm);fctx.fillStyle='#9d4550';fctx.fillText(`${f.operation_ids.join('/')} · Δ ${f.center_residual_mm.toFixed(3)} mm`,p[0]+45*devicePixelRatio,p[1]);}
  }
  function stop(){playing=false;$('play').textContent='播放本序';}
  function frame(now){if(!playing)return;$('tau').value=Math.min(100,(now-startTime)/35);draw();if(Number($('tau').value)<100)requestAnimationFrame(frame);else stop();}
  function updateAssignment(raw){const next=M.validateAssignment(raw,data);assignment=next;stop();$('message').textContent='已读取这组折弯的分配；未检查其余动作的三维状态。';draw();fit();}
  $('station').replaceChildren(...data.stations.sort((a,b)=>a.order-b.order).map(s=>new Option(s.station_id,s.station_id)));
  $('station').value=Object.values(assignment).sort((a,b)=>orders[a]-orders[b])[0];
  $('tau').oninput=()=>{stop();draw();};$('station').onchange=()=>{stop();$('tau').value=0;draw();};
  $('play').onclick=()=>{if(playing){stop();return;}playing=true;$('play').textContent='暂停';if(Number($('tau').value)===100)$('tau').value=0;startTime=performance.now()-Number($('tau').value)*35;requestAnimationFrame(frame);};
  $('next').onclick=()=>{const options=[...$('station').options],i=options.findIndex(o=>o.value===$('station').value);if(i<options.length-1){$('station').value=options[i+1].value;$('station').onchange();}};
  $('import').onclick=()=>$('file').click();$('file').onchange=async()=>{try{const file=$('file').files[0];if(file)updateAssignment(JSON.parse(await file.text()));}catch(e){$('message').textContent=`导入失败：${e.message}`;}finally{$('file').value='';}};
  $('reset').onclick=()=>updateAssignment(data.default_assignment);$('fit').onclick=fit;$('target').onchange=draw;
  $('view').onchange=()=>{[camera.yaw,camera.pitch]=({iso:[-1.05,.55],front:[0,0],top:[-Math.PI/2,Math.PI/2]})[$('view').value];fit();};
  let drag;canvas.oncontextmenu=e=>e.preventDefault();canvas.onpointerdown=e=>{drag={x:e.clientX,y:e.clientY,pan:e.button===2||e.shiftKey};canvas.setPointerCapture(e.pointerId);};
  canvas.onpointermove=e=>{if(!drag)return;const dx=(e.clientX-drag.x)*devicePixelRatio,dy=(e.clientY-drag.y)*devicePixelRatio;drag.x=e.clientX;drag.y=e.clientY;if(drag.pan){camera.pan[0]+=dx/camera.scale;camera.pan[1]-=dy/camera.scale;}else{camera.yaw-=dx*.006/devicePixelRatio;camera.pitch=Math.max(-1.56,Math.min(1.56,camera.pitch+dy*.006/devicePixelRatio));}draw();};canvas.onpointerup=canvas.onpointercancel=()=>drag=null;
  canvas.onwheel=e=>{e.preventDefault();camera.scale=Math.max(.01,Math.min(300*devicePixelRatio,camera.scale*Math.exp(-e.deltaY*.001)));draw();};
  $('panels').textContent=data.summary.panel_proposal_count;$('joints').textContent=data.summary.joint_proposal_count;$('matched').textContent=`${data.summary.unique_bend_proposal_count}/${data.summary.bend_operation_count}`;$('thickness').textContent=data.summary.thickness_estimate_mm.toFixed(3);
  $('example-title').textContent=data.summary.example_operations.join(' / ')+' · 局部样例';$('scope').textContent='显示基准、连接板面及末端板面。两侧表面来自成品 CAD，折弯过渡带未建模；保留成品孔，仅验证位置与位姿。';
  $('feature-title').textContent=data.feature?data.feature.operation_ids.join(' / ')+' · 同一加工组的位置建议':'没有唯一的附属特征建议';
  $('residuals').replaceChildren(...data.example.association_evidence.map(r=>{const tr=document.createElement('tr');for(const v of [r.operation_id,r.endpoint_residual_mm.toFixed(3)+' mm','待校准']){const td=document.createElement('td');td.textContent=v;tr.append(td);}return tr;}));
  if(data.feature){const row=document.createElement('tr');for(const v of [data.feature.operation_ids.join('/'),data.feature.center_residual_mm.toFixed(3)+' mm','尺寸未确认']){const td=document.createElement('td');td.textContent=v;row.append(td);}$('residuals').append(row);}
  $('details').textContent=`连接树由三维面邻接建立，不读取 Sheet5 或专家工序。${data.summary.kinematic_region_count} 组板面进入基准连接树，另 ${data.summary.unconnected_region_proposal_ids.length} 组待关联。目标转角来自相邻板面的有向法向。${data.summary.unmatched_bends.length?data.summary.unmatched_bends.join('、')+' 尚无唯一对应。':'全部折弯均有对应建议，但定量展开尚未验证通过。'}关联后独立比较的 CAD 父子关系 ${data.summary.semantic_consistent_count}/${data.summary.semantic_crosscheck_count} 一致。附属特征二维圆环半径 ${data.feature?.source_flange_radii_mm.join('/')} mm，CAD 观察半径 ${data.feature?.cad_observed_radii_mm.join('/')} mm，几何含义尚需核对。回到成品姿态是构造闭合检查，不是独立工艺验证。`;
  function resize(){for(const c of [scene,canvas,flat]){const b=c.getBoundingClientRect();c.width=Math.round(b.width*devicePixelRatio);c.height=Math.round(b.height*devicePixelRatio);}compute();fit();drawFlat();}
  resize();new ResizeObserver(resize).observe(document.querySelector('.views'));
  window.materialReview={data,getState:()=>({assignment:{...assignment},station:$('station').value,tau:Number($('tau').value)/100,...compute(),camera:structuredClone(camera),depth_renderer:!!renderer}),updateAssignment};
})();
