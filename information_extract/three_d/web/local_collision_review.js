(function(){
 'use strict';
 const $=id=>document.getElementById(id),data=JSON.parse($('data').textContent),M=data.model,F=FiniteBend,C=IE3DCore;
 const scene=$('scene'),canvas=$('motion'),section=$('section'),sc=section.getContext('2d'),renderer=new MotionRenderer(scene);
 const colors=[[190,204,216],[70,134,195],[227,158,77],[153,111,183],[153,111,183]],camera={yaw:-1.05,pitch:.5,center:[354.8,-7,1],pan:[0,0],scale:10};
 const names={root:'基板',middle:'中间板',terminal:'末端板',band_O009:'O009 折弯带',band_O010:'O010 折弯带'},status={SEPARATED:'分离',CONNECTED_CONTACT:'连接处接触',NONADJACENT_CONTACT:'接触（需核对）',OVERLAP:'体积交叠'};
 let playing=false,previous=0,state,sectionBounds;
 const run=()=>data.runs[Number($('path').value)],sample=()=>run().samples[Number($('sample').value)];
 function geometry(s){const map=F.mapper(M,s.fractions),triangles=[],lines=[];data.panels.forEach((panel,i)=>panel.triangles.forEach(t=>triangles.push({region:i,points:t.map(p=>map.map(i,p))})));
  const quad=(a,b,c,d,region)=>triangles.push({region,points:[a,b,c]},{region,points:[a,c,d]});
  map.frames.zones.forEach((z,i)=>{const xs=M.bends[i].x_limits_mm,n=data.config.band_intervals.at(-1),p=(x,depth,u)=>[x,...F.point(z,depth,u)];
   for(let k=0;k<n;k++){const a=k/n,b=(k+1)/n;for(const depth of [0,M.thickness_mm])quad(p(xs[0],depth,a),p(xs[1],depth,a),p(xs[1],depth,b),p(xs[0],depth,b),i+3);for(const x of xs)quad(p(x,0,a),p(x,M.thickness_mm,a),p(x,M.thickness_mm,b),p(x,0,b),i+3);}
   for(const u of [0,1])quad(p(xs[0],0,u),p(xs[1],0,u),p(xs[1],M.thickness_mm,u),p(xs[0],M.thickness_mm,u),i+3);
   for(const x of xs)for(const depth of [0,M.thickness_mm])lines.push(Array.from({length:n+1},(_,k)=>p(x,depth,k/n)));
  });return {triangles,lines,map};}
 function sectionPolygons(f){const root=M.root_start_top_yz_mm,rows=[{points:[[root[0]-8,root[1]],root,[root[0],root[1]-M.thickness_mm],[root[0]-8,root[1]-M.thickness_mm]],color:'#becbd7'}];
  f.zones.forEach((z,i)=>{rows.push({points:[...Array.from({length:33},(_,k)=>F.point(z,0,k/32)),...Array.from({length:33},(_,k)=>F.point(z,M.thickness_mm,1-k/32))],color:'#996fb7'});
   const p=F.point(z),q=F.point(z,M.thickness_mm),phi=z.phi+z.angle,len=i===0?M.middle_straight_length_mm:M.terminal_straight_length_mm,v=[len*Math.cos(phi),len*Math.sin(phi)];rows.push({points:[p,p.map((a,j)=>a+v[j]),q.map((a,j)=>a+v[j]),q],color:i===0?'#4686c3':'#e39e4d'});});return rows;}
 function drawSection(g){sc.clearRect(0,0,section.width,section.height);
  if(!sectionBounds){const points=data.runs.flatMap(r=>r.samples.flatMap(s=>sectionPolygons(F.frames(M,s.fractions)).flatMap(p=>p.points)));sectionBounds={lo:[0,1].map(i=>Math.min(...points.map(p=>p[i]))),hi:[0,1].map(i=>Math.max(...points.map(p=>p[i])))};}
  const {lo,hi}=sectionBounds,scale=.9*Math.min(section.width/(hi[0]-lo[0]),section.height/(hi[1]-lo[1])),xy=p=>[section.width/2+(p[0]-(lo[0]+hi[0])/2)*scale,section.height/2-(p[1]-(lo[1]+hi[1])/2)*scale];
  const polygon=(pts,color)=>{sc.beginPath();pts.map(xy).forEach((p,i)=>i?sc.lineTo(...p):sc.moveTo(...p));sc.closePath();sc.fillStyle=color;sc.fill();};
  sectionPolygons(g.map.frames).forEach(p=>polygon(p.points,p.color));}
 function cells(row,values){for(const value of values){const td=document.createElement('td');td.textContent=value;row.append(td);}return row;}
 function draw(){const s=sample(),g=geometry(s);state={kind:run().kind,sample_index:Number($('sample').value),fractions:s.fractions,pairs:s.pairs};renderer.render(g.triangles,g.lines,[],camera,colors);drawSection(g);
  $('progress').textContent=`状态 ${Number($('sample').value)+1}/${run().samples.length}`;
  $('angles').textContent=`运动段 ${s.phase} · 本段 ${(s.phase_fraction*100).toFixed(1)}%　`+M.bends.map(b=>`${b.operation_id} 转角 ${(b.target_angle_deg*s.fractions[b.operation_id]).toFixed(2)}°`).join('　');
  const overlap=s.pairs.filter(p=>p.status==='OVERLAP');$('status').textContent=overlap.length?'当前采样状态：检测到体积交叠':'当前采样状态：未检出体积交叠';$('status').className=overlap.length?'error':'';
  $('detail').textContent=`五个实体，共检查 ${s.pairs.length} 对。此结果不包含工具、装夹或整件其它区域。`;
  $('pairs').replaceChildren(...s.pairs.map(p=>cells(document.createElement('tr'),[p.components.map(x=>names[x]).join(' / '),p.distance_mm.toFixed(6),p.intersection_volume_mm3.toFixed(6),status[p.status]])));
 }
 function fit(){const points=[];for(const r of data.runs)for(const s of [r.samples[0],r.samples[Math.floor(r.samples.length/2)],r.samples.at(-1)]){const g=geometry(s);for(const t of g.triangles)if(t.region!==0)points.push(...t.points);}
  const low=[0,1,2].map(i=>Math.min(...points.map(p=>p[i]))),high=[0,1,2].map(i=>Math.max(...points.map(p=>p[i])));camera.center=low.map((v,i)=>(v+high[i])/2);camera.pan=[0,0];const basis=C.basis(camera.yaw,camera.pitch),extent=v=>{const x=points.map(p=>C.dot(C.sub(p,camera.center),v));return Math.max(...x)-Math.min(...x)};camera.scale=.85*Math.min(canvas.width/(extent(basis.right)+8),canvas.height/(extent(basis.up)+8));draw();}
 function stop(){playing=false;$('play').textContent='播放已检查状态';}
 data.runs.forEach((r,i)=>{const option=document.createElement('option');option.value=i;option.textContent=r.label+(r.kind==='child_first'?' · 符合 H7':' · 违反 H7，仅开发对照');$('path').append(option);});
 $('summary').replaceChildren(...data.runs.map(r=>{const gap=Math.min(...r.samples.flatMap(s=>s.pairs.filter(p=>p.components.join('/')==='root/terminal').map(p=>p.distance_mm)));return cells(document.createElement('tr'),[r.label,r.sample_count,r.overlap_sample_count,gap.toFixed(6),data.refinement.find(x=>x.kind===r.kind).sampled_classification_stable?'一致':'不一致']);}));
 $('path').onchange=()=>{stop();$('sample').max=run().samples.length-1;$('sample').value=Math.floor(run().samples.length/2);draw();};$('sample').oninput=()=>{stop();draw();};$('view').onchange=()=>{[camera.yaw,camera.pitch]=({iso:[-1.05,.5],side:[0,0],top:[-Math.PI/2,Math.PI/2]})[$('view').value];fit();};$('fit').onclick=fit;
 function tick(now){if(!playing)return;if(now-previous>150){previous=now;$('sample').value=Number($('sample').value)+1;draw();if(Number($('sample').value)>=run().samples.length-1){stop();return;}}requestAnimationFrame(tick);}
 $('play').onclick=()=>{if(playing){stop();return;}if(Number($('sample').value)===run().samples.length-1)$('sample').value=0;playing=true;previous=performance.now();$('play').textContent='暂停';requestAnimationFrame(tick);};
 let drag;canvas.oncontextmenu=e=>e.preventDefault();canvas.onpointerdown=e=>{drag={x:e.clientX,y:e.clientY,pan:e.shiftKey||e.button===2};canvas.setPointerCapture(e.pointerId);};canvas.onpointermove=e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;drag.x=e.clientX;drag.y=e.clientY;if(drag.pan){camera.pan[0]+=dx*devicePixelRatio/camera.scale;camera.pan[1]-=dy*devicePixelRatio/camera.scale;}else{camera.yaw-=dx*.006;camera.pitch=Math.max(-1.56,Math.min(1.56,camera.pitch+dy*.006));}draw();};canvas.onpointerup=canvas.onpointercancel=()=>drag=null;canvas.onwheel=e=>{e.preventDefault();camera.scale=Math.max(.1,Math.min(500,camera.scale*Math.exp(-e.deltaY*.001)));draw();};
 function resize(){for(const c of [scene,canvas,section]){const b=c.getBoundingClientRect();c.width=Math.round(b.width*devicePixelRatio);c.height=Math.round(b.height*devicePixelRatio);}fit();}
 $('path').value=0;$('sample').max=run().samples.length-1;$('sample').value=Math.floor(run().samples.length/2);window.addEventListener('resize',resize);resize();window.localCollisionReview={data,getState:()=>structuredClone(state)};
})();
