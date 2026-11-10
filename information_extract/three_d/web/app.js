(function(){
  'use strict';
  const data=JSON.parse(document.getElementById('data').textContent), C=IE3DCore;
  const $=id=>document.getElementById(id), asset=data.asset, facts=data.facts;
  const proposals=new Map(data.proposals.map(p=>[p.operation_id,p])), anchors=new Map((data.registration.anchors||[]).map(a=>[a.operation_id,a]));
  const flat=$('flat'), solid=$('solid'), ctx=flat.getContext('2d');
  let bindings=C.emptyBindings(data), op=facts.operations[0], selected=new Set(), suggested=new Set(), hidden=new Set(), faceHit=0, timeout;
  try{const saved=localStorage.getItem(C.storageKey(data));if(saved)bindings=C.validateBindings(JSON.parse(saved),data);}catch(e){toast(e.message);}
  const flatCam={center:[0,0],scale:1}, camera={yaw:-1.1,pitch:0.8,center:[0,0,0],pan:[0,0],scale:1};
  const gl=solid.getContext('webgl',{antialias:true,preserveDrawingBuffer:true});
  let program, buffers, flagTexture, pickFramebuffer, pickTexture, pickDepth, edgeBuffer, edgeCount=0;
  function toast(message){$('toast').textContent=message;$('toast').style.display='block';clearTimeout(timeout);timeout=setTimeout(()=>$('toast').style.display='none',4000);}
  function escape(s){return String(s??'').replace(/[&<>"']/g,v=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[v]));}
  function fit2(points){
    points=points||facts.operations.flatMap(o=>o.paths.flat());if(!points.length)return;
    points=points.map(flatPoint);
    let min=[Infinity,Infinity],max=[-Infinity,-Infinity];for(const p of points)for(let i=0;i<2;i++){min[i]=Math.min(min[i],p[i]);max[i]=Math.max(max[i],p[i]);}
    flatCam.center=min.map((v,i)=>(v+max[i])/2);flatCam.scale=Math.min(flat.width/(max[0]-min[0]+8),flat.height/(max[1]-min[1]+8))*.88;draw2();
  }
  function fit3(ids){
    const faces=ids?.size?asset.faces.filter(f=>ids.has(f.face_id)):asset.faces;
    const points=faces.flatMap(f=>{const b=f.bounds_mm;return [[b[0],b[1],b[2]],[b[3],b[4],b[5]]];});
    const low=[0,1,2].map(i=>Math.min(...points.map(p=>p[i]))),high=[0,1,2].map(i=>Math.max(...points.map(p=>p[i])));
    camera.center=low.map((v,i)=>(v+high[i])/2);camera.pan=[0,0];
    const b=C.basis(camera.yaw,camera.pitch),corners=[];
    for(const x of [low[0],high[0]])for(const y of [low[1],high[1]])for(const z of [low[2],high[2]])corners.push(C.sub([x,y,z],camera.center));
    const extent=v=>Math.max(...corners.map(p=>C.dot(p,v)))-Math.min(...corners.map(p=>C.dot(p,v)));
    camera.scale=Math.min(solid.width/Math.max(extent(b.right),10),solid.height/Math.max(extent(b.up),10))*.86;draw3();
  }
  function flatPoint(p){const m=data.registration.matrix;return m?[m[0][0]*p[0]+m[0][1]*p[1],m[1][0]*p[0]+m[1][1]*p[1]]:p;}
  function xy2(p){p=flatPoint(p);return [flat.width/2+(p[0]-flatCam.center[0])*flatCam.scale,flat.height/2-(p[1]-flatCam.center[1])*flatCam.scale];}
  function draw2(){
    ctx.clearRect(0,0,flat.width,flat.height);
    function draw(o,color,width){ctx.strokeStyle=color;ctx.lineWidth=width;ctx.beginPath();for(const path of o.paths){for(let i=0;i<path.length;i++){const p=xy2(path[i]);i?ctx.lineTo(...p):ctx.moveTo(...p);}}ctx.stroke();}
    for(const o of facts.operations)if(o!==op)draw(o,anchors.has(o.operation_id)?'#7299c4':'#9aaabb',1.2*devicePixelRatio);
    draw(op,'#e18a29',2.5*devicePixelRatio);
    if(!op.paths.length){ctx.fillStyle='#8d9aac';ctx.font=`${13*devicePixelRatio}px sans-serif`;ctx.fillText(`${op.operation_id} 尚无二维几何`,20*devicePixelRatio,30*devicePixelRatio);}
  }
  function list(){
    const query=$('search').value.toLowerCase(),filter=$('filter').value;
    const ops=facts.operations.filter(o=>[o.operation_id,o.source_feature_ref,o.operation_type].join(' ').toLowerCase().includes(query))
      .filter(o=>filter==='ALL'||(filter==='BEND'&&o.operation_type==='BEND')||(filter==='ANCHOR'&&anchors.has(o.operation_id))||(filter==='PROPOSAL'&&(proposals.get(o.operation_id)?.candidates.length||data.inspection?.operations[o.operation_id]?.face_ids.length))||(filter==='MISSING'&&!o.paths.length)||(filter==='BOUND'&&bindings.bindings[o.operation_id]?.face_ids.length)||(filter.startsWith('TYPE:')&&o.operation_type===filter.slice(5))||(filter.startsWith('STATUS:')&&data.inspection?.operations[o.operation_id]?.status===filter.slice(7)));
    $('list').replaceChildren(...ops.map(o=>{const b=document.createElement('button');b.dataset.op=o.operation_id;b.className=o===op?'active':'';
      b.innerHTML=`<span class="tag">${escape(o.operation_type)}</span><b>${escape(o.operation_id)}</b><small>${escape(o.source_feature_ref||'未命名')}</small>${data.inspection?'<small class="status-label">'+escape(data.inspection.operations[o.operation_id].label)+'</small>':''}${bindings.bindings[o.operation_id]?'<em>已有关联</em>':''}`;b.onclick=()=>choose(o.operation_id);return b;}));
  }
  function choose(id){
    op=facts.operations.find(o=>o.operation_id===id);if(!op)return;
    selected=new Set(bindings.bindings[id]?.face_ids||[]);suggested=new Set();faceHit=0;
    if(data.inspection)suggested=new Set(data.inspection.operations[id]?.face_ids||[]);
    $('note').value=bindings.bindings[id]?.note||'';list();detail();updateFlags();draw2();draw3();
    if(data.inspection&&$('autofocus')?.checked){if(op.paths.length)fit2(op.paths.flat());if(selected.size||suggested.size)fit3(new Set([...selected,...suggested]));}
  }
  function detail(){
    const row=bindings.bindings[op.operation_id], anchor=anchors.get(op.operation_id);
    $('opdetail').innerHTML=`<h2>${escape(op.operation_id)}</h2><div>${escape(op.source_feature_ref||'未命名')}</div><dl class="meta"><dt>动作类型</dt><dd>${escape(op.operation_type)}</dd><dt>材料表面侧</dt><dd>${escape(op.surface_side||'未记录')}</dd><dt>CAD 父动作</dt><dd>${escape(op.cad_parent_operation_id||'无')}</dd><dt>二维来源</dt><dd>${escape(op.geometry_id||'缺失')} · ${op.segments.length} 段</dd><dt>关联状态</dt><dd>${row?.status==='CONFIRMED'?'已确认对应':row?'草稿':'尚未关联'}</dd></dl>`;
    $('selection-count').textContent=selected.size;
    $('faces').replaceChildren(...[...selected].sort((a,b)=>a-b).map(id=>{const f=asset.faces[id-1],b=document.createElement('button');b.textContent=`F${id} · ${f.surface_type}`;b.title=`面积 ${f.area_mm2.toFixed(3)} mm²；点击移除`;b.onclick=()=>{selected.delete(id);detail();updateFlags();draw3();};return b;}));
    if(!selected.size)$('faces').textContent=faceHit?`最近点击 F${faceHit}`:'点击三维模型选择面，可连续多选。';
    $('proposals').replaceChildren();
    if(data.inspection){
      const r=data.inspection.operations[op.operation_id],box=document.createElement('div');box.className='inspection-detail';
      box.innerHTML=`<h3>当前建模覆盖</h3><p class="badge">${escape(r.label)}</p><p>${escape(r.meaning)}</p><dl class="meta"><dt>二维几何</dt><dd>${op.paths.length?'已有':'缺失'}</dd><dt>三维对应</dt><dd>${r.face_ids.length?'有可查看的证据 / 建议':'待确定，未自动投影'}</dd><dt>自身运动</dt><dd>${r.motion_model?'局部几何重建；非物理仿真':'尚未建立'}</dd></dl>`;
      if(r.joint_id){const p=document.createElement('p');p.className='footnote';p.textContent=`连接 ${r.joint_id}；几何目标转角 ${r.angle_deg.toFixed(1)}°；原零宽展开残差 ${r.residual_mm.toFixed(3)} mm。高亮仅为过渡面及相邻子板面，不等于完整移动区域。`;box.append(p);}
      if(r.face_ids.length){const b=document.createElement('button');b.id='focus-evidence';b.textContent='聚焦三维证据';b.onclick=()=>{suggested=new Set(r.face_ids);updateFlags();fit3(suggested);};box.append(b);}
      if(r.local_example){const a=document.createElement('a');a.id='local-model-link';a.href=data.inspection.local_model_url;a.target='_blank';a.rel='noopener';a.textContent='打开 O009／O010 局部阶段回放';box.append(a);}
      const note=document.createElement('p');note.className='footnote';note.textContent='蓝色是证据或建议，不会自动保存为已确认关联。全件画布始终显示成品 STEP，尚不是按工序变化的全件模型。';box.append(note);$('proposals').append(box);return;
    }
    const title=document.createElement('h3');title.textContent=anchor?'基准孔的几何证据':'折弯关联建议';$('proposals').append(title);
    if(anchor){const b=document.createElement('button');b.textContent=`查看对应孔壁 · CAD 边 E${anchor.edge_id}`;b.onclick=()=>{suggested=new Set(anchor.face_ids.filter(id=>id!==data.registration.base_face_id));updateFlags();draw3();toast('蓝色为几何证据；点击面后可保存关联。');};$('proposals').append(b);}
    const p=proposals.get(op.operation_id);
    if(p?.candidates.length){for(const c of p.candidates){const b=document.createElement('button');b.textContent=`查看 F${c.face_id} · 轴偏移 ${c.axis_offset_mm.toFixed(3)} mm · 投影重叠 ${(100*c.projected_overlap_ratio).toFixed(0)}%`;
      b.title=`基准邻接路径 ${c.base_adjacency_path.join(' → ')}；不是已确认的折弯轴或移动区域`;
      b.onclick=()=>{suggested=new Set([c.face_id]);updateFlags();draw3();toast('蓝色仅为关联建议；移动侧和角度仍待确定。');};$('proposals').append(b);}}
    else if(!anchor){const p=document.createElement('p');p.className='footnote';p.textContent=op.operation_type==='BEND'?'当前基准关联不足，需要建立局部材料坐标。':'可直接选择对应面，保存动作关联。';$('proposals').append(p);}
  }
  function save(status){
    if(!selected.size){toast('请先选择对应 CAD 面。');return;}
    const next=structuredClone(bindings);next.bindings[op.operation_id]={face_ids:[...selected].sort((a,b)=>a-b),status,note:$('note').value};
    try{C.validateBindings(next,data);localStorage.setItem(C.storageKey(data),JSON.stringify(next));bindings=next;$('saved').textContent='关联已保存在本机';list();detail();toast('已保存。下载关联备份可跨浏览器恢复。');}catch(e){toast(`保存失败：${e.message}`);}
  }
  function download(){const blob=new Blob([JSON.stringify(bindings,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`${facts.part_id}_3d_bindings.json`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);}
  function shader(type,source){const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s;}
  function initializeGPU(){
    program=gl.createProgram();
    gl.attachShader(program,shader(gl.VERTEX_SHADER,`
      attribute vec3 position; attribute vec3 normal; attribute float face;
      uniform vec3 center; uniform vec3 right; uniform vec3 up; uniform vec3 eye;
      uniform vec2 pan; uniform vec2 scale; uniform float depth;
      varying vec3 n; varying float fid;
      void main(){vec3 d=position-center;gl_Position=vec4((dot(d,right)+pan.x)*scale.x,(dot(d,up)+pan.y)*scale.y,-dot(d,eye)/depth,1.);n=normal;fid=face;}`));
    gl.attachShader(program,shader(gl.FRAGMENT_SHADER,`
      precision highp float; varying vec3 n; varying float fid; uniform sampler2D flags;
      uniform float texsize; uniform int mode; uniform int subset; uniform bool transparent;
      void main(){vec4 f=texture2D(flags,vec2((fid+.5)/texsize,.5));if(f.g>.5)discard;
      if(mode==1){gl_FragColor=vec4(mod(fid,256.)/255.,floor(fid/256.)/255.,0.,1.);return;}
      if(mode==2){gl_FragColor=vec4(.38,.46,.56,.65);return;}
      bool marked=f.r>.5||f.b>.5;if(subset==1&&marked)discard;if(subset==2&&!marked)discard;
      vec3 c=vec3(.67,.76,.85);if(f.b>.5)c=vec3(.22,.48,.80);if(f.r>.5)c=vec3(.94,.57,.20);
      float light=.60+.40*abs(dot(normalize(n),normalize(vec3(.3,-.5,1.))));
      gl_FragColor=vec4(c*light,transparent&&f.r<.5&&f.b<.5?.20:1.);}`));
    gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));
    const positions=new Float32Array(asset.mesh.positions), normals=new Float32Array(positions.length), ids=new Float32Array(positions.length/3);
    for(let i=0;i<positions.length;i+=9){const raw=C.cross(C.sub([...positions.slice(i+3,i+6)],[...positions.slice(i,i+3)]),C.sub([...positions.slice(i+6,i+9)],[...positions.slice(i,i+3)])),n=Math.hypot(...raw)>1e-15?C.norm(raw):[0,0,1];for(let j=0;j<3;j++){normals.set(n,i+j*3);ids[i/3+j]=asset.mesh.triangle_face_ids[i/9];}}
    const buffer=a=>{const b=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.bufferData(gl.ARRAY_BUFFER,a,gl.STATIC_DRAW);return b;};
    buffers={position:buffer(positions),normal:buffer(normals),face:buffer(ids),count:ids.length};
    flagTexture=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,flagTexture);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.NEAREST);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.NEAREST);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
    pickFramebuffer=gl.createFramebuffer();pickTexture=gl.createTexture();pickDepth=gl.createRenderbuffer();
    updateFlags();
  }
  function updateFlags(){
    if(!gl||!program)return;
    const size=2**Math.ceil(Math.log2(asset.face_count+1)),flags=new Uint8Array(size*4);
    for(const id of selected)flags[id*4]=255;for(const id of hidden)flags[id*4+1]=255;for(const id of suggested)flags[id*4+2]=255;
    gl.bindTexture(gl.TEXTURE_2D,flagTexture);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,size,1,0,gl.RGBA,gl.UNSIGNED_BYTE,flags);
    if(edgeBuffer){for(const b of Object.values(edgeBuffer))gl.deleteBuffer(b);}
    const pos=[],face=[];
    for(const e of asset.edges){const id=e.face_ids.find(id=>!hidden.has(id));if(!id)continue;for(let j=1;j<e.points.length;j++){pos.push(...e.points[j-1],...e.points[j]);face.push(id,id);}}
    function buffer(a){const b=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(a),gl.STATIC_DRAW);return b;}
    edgeBuffer={position:buffer(pos),normal:buffer(new Float32Array(pos.length)),face:buffer(face)};edgeCount=face.length;
  }
  function attributes(source){for(const [name,size] of [['position',3],['normal',3],['face',1]]){const loc=gl.getAttribLocation(program,name);gl.bindBuffer(gl.ARRAY_BUFFER,source[name]);gl.enableVertexAttribArray(loc);gl.vertexAttribPointer(loc,size,gl.FLOAT,false,0,0);}}
  function render(picking){
    gl.useProgram(program);gl.viewport(0,0,solid.width,solid.height);gl.bindFramebuffer(gl.FRAMEBUFFER,picking?pickFramebuffer:null);
    gl.clearColor(picking?0:.972,picking?0:.98,picking?0:.988,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);gl.disable(gl.CULL_FACE);
    const b=C.basis(camera.yaw,camera.pitch),uniform=(n)=>gl.getUniformLocation(program,n);
    for(const [name,v] of [['center',camera.center],['right',b.right],['up',b.up],['eye',b.eye]])gl.uniform3fv(uniform(name),v);
    gl.uniform2fv(uniform('pan'),camera.pan);gl.uniform2fv(uniform('scale'),[2*camera.scale/solid.width,2*camera.scale/solid.height]);gl.uniform1f(uniform('depth'),10000);
    gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,flagTexture);gl.uniform1i(uniform('flags'),0);gl.uniform1f(uniform('texsize'),2**Math.ceil(Math.log2(asset.face_count+1)));
    const transparent=$('transparent').checked&&!picking;
    gl.uniform1i(uniform('transparent'),transparent);gl.uniform1i(uniform('mode'),picking?1:0);
    picking?gl.disable(gl.DITHER):gl.enable(gl.DITHER);
    if(transparent){gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);}else gl.disable(gl.BLEND);
    attributes(buffers);gl.enable(gl.POLYGON_OFFSET_FILL);gl.polygonOffset(1,1);
    if(transparent){gl.uniform1i(uniform('subset'),1);gl.depthMask(false);gl.drawArrays(gl.TRIANGLES,0,buffers.count);gl.depthMask(true);gl.uniform1i(uniform('subset'),2);gl.drawArrays(gl.TRIANGLES,0,buffers.count);}
    else{gl.depthMask(true);gl.uniform1i(uniform('subset'),0);gl.drawArrays(gl.TRIANGLES,0,buffers.count);}
    gl.disable(gl.POLYGON_OFFSET_FILL);
    if(!picking&&$('edges').checked){gl.uniform1i(uniform('mode'),2);attributes(edgeBuffer);gl.drawArrays(gl.LINES,0,edgeCount);}
    gl.bindFramebuffer(gl.FRAMEBUFFER,null);
  }
  function draw3(){if(gl&&program)render(false);}
  function resize(){
    for(const canvas of [flat,solid]){const box=canvas.getBoundingClientRect();canvas.width=Math.round(box.width*devicePixelRatio);canvas.height=Math.round(box.height*devicePixelRatio);}
    if(gl&&program){gl.bindTexture(gl.TEXTURE_2D,pickTexture);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.NEAREST);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.NEAREST);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,solid.width,solid.height,0,gl.RGBA,gl.UNSIGNED_BYTE,null);gl.bindRenderbuffer(gl.RENDERBUFFER,pickDepth);gl.renderbufferStorage(gl.RENDERBUFFER,gl.DEPTH_COMPONENT16,solid.width,solid.height);gl.bindFramebuffer(gl.FRAMEBUFFER,pickFramebuffer);gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_2D,pickTexture,0);gl.framebufferRenderbuffer(gl.FRAMEBUFFER,gl.DEPTH_ATTACHMENT,gl.RENDERBUFFER,pickDepth);if(gl.checkFramebufferStatus(gl.FRAMEBUFFER)!==gl.FRAMEBUFFER_COMPLETE)throw Error('选面缓冲区初始化失败');gl.bindFramebuffer(gl.FRAMEBUFFER,null);}
    fit2();fit3();
  }
  function pick3(x,y){if(!gl||!program)return 0;render(true);const pixel=new Uint8Array(4);gl.bindFramebuffer(gl.FRAMEBUFFER,pickFramebuffer);gl.readPixels(Math.floor(x),solid.height-1-Math.floor(y),1,1,gl.RGBA,gl.UNSIGNED_BYTE,pixel);gl.bindFramebuffer(gl.FRAMEBUFFER,null);draw3();const id=C.decodePick(pixel);return id<=asset.face_count?id:0;}
  function pick2(x,y){let best=null,distance=9*devicePixelRatio;for(const o of facts.operations){for(const p of o.paths){for(let j=1;j<p.length;j++){const a=xy2(p[j-1]),b=xy2(p[j]),v=C.sub(b,a),den=C.dot(v,v),t=den?Math.max(0,Math.min(1,C.dot(C.sub([x,y],a),v)/den)):0,d=Math.hypot(x-a[0]-t*v[0],y-a[1]-t*v[1]);if(d<distance){distance=d;best=o;}}}}return best;}
  function bindPointer(canvas,is3){
    let drag;
    const point=e=>{const b=canvas.getBoundingClientRect();return [(e.clientX-b.left)*devicePixelRatio,(e.clientY-b.top)*devicePixelRatio];};
    canvas.oncontextmenu=e=>e.preventDefault();
    canvas.onpointerdown=e=>{drag={point:point(e),start:point(e),pan:e.button===2||e.shiftKey};canvas.setPointerCapture(e.pointerId);};
    canvas.onpointermove=e=>{if(!drag)return;const p=point(e),d=C.sub(p,drag.point);drag.point=p;if(is3){if(drag.pan){camera.pan[0]+=d[0]/camera.scale;camera.pan[1]-=d[1]/camera.scale;}else{camera.yaw-=d[0]*.006/devicePixelRatio;camera.pitch=Math.max(-1.56,Math.min(1.56,camera.pitch+d[1]*.006/devicePixelRatio));}draw3();}else{flatCam.center[0]-=d[0]/flatCam.scale;flatCam.center[1]+=d[1]/flatCam.scale;draw2();}};
    canvas.onpointerup=e=>{if(!drag)return;const p=point(e),click=Math.hypot(...C.sub(p,drag.start))<5*devicePixelRatio,pan=drag.pan;drag=null;if(click&&!pan){if(is3){const id=pick3(...p);if(id){faceHit=id;selected.has(id)?selected.delete(id):selected.add(id);detail();updateFlags();draw3();}}else{const o=pick2(...p);if(o)choose(o.operation_id);}}};
    canvas.onpointercancel=()=>drag=null;
    canvas.onwheel=e=>{e.preventDefault();const factor=Math.exp(-e.deltaY*.001);if(is3){camera.scale=Math.max(.01,Math.min(500*devicePixelRatio,camera.scale*factor));draw3();}else{flatCam.scale=Math.max(.01,Math.min(500*devicePixelRatio,flatCam.scale*factor));draw2();}};
  }
  $('search').oninput=list;$('filter').onchange=list;$('fit2').onclick=()=>fit2();$('focus2').onclick=()=>fit2(op.paths.flat());$('fit3').onclick=()=>fit3();$('focus3').onclick=()=>fit3(selected.size?selected:suggested);
  $('view').onchange=()=>{const angles={iso:[-1.1,.8],top:[-Math.PI/2,Math.PI/2],front:[-Math.PI/2,0],side:[0,0]};[camera.yaw,camera.pitch]=angles[$('view').value];fit3();};
  $('edges').onchange=draw3;$('transparent').onchange=draw3;
  $('clear').onclick=()=>{selected.clear();suggested.clear();detail();updateFlags();draw3();};
  $('neighbors').onclick=()=>{for(const id of [...selected])for(const neighbor of asset.faces[id-1].neighbor_face_ids)selected.add(neighbor);detail();updateFlags();draw3();};
  $('hide').onclick=()=>{for(const id of selected)hidden.add(id);updateFlags();draw3();};$('show').onclick=()=>{hidden.clear();updateFlags();draw3();};
  $('save').onclick=()=>save('DRAFT');$('confirm').onclick=()=>save('CONFIRMED');$('export').onclick=download;$('import').onclick=()=>$('file').click();
  $('file').onchange=async()=>{try{const file=$('file').files[0];if(!file)return;const next=C.validateBindings(JSON.parse(await file.text()),data);localStorage.setItem(C.storageKey(data),JSON.stringify(next));bindings=next;choose(op.operation_id);toast('关联备份已恢复。');}catch(e){toast(`导入失败：${e.message}`);}finally{$('file').value='';}};
  $('headline').textContent=`${facts.operations.length} 个动作 · ${asset.face_count} 个 CAD 面 · mm`;
  $('opcount').textContent=facts.operations.length;
  $('coverage').textContent=`二维几何 ${facts.coverage.with_2d_geometry}/${facts.coverage.operation_count}；缺失 ${facts.coverage.missing_2d.length} 个。灰蓝：参考；蓝：建议；橙：当前选择。`;
  $('registration').textContent=`基准配准：${data.registration.anchor_count||0} 个孔吻合 · ${data.registration.determinant===1?'无镜像':'待检查方向'}`;
  $('evidence').textContent=`配准仅适用于基准坐标，不代表全零件展开。基准面 F${data.registration.base_face_id}。基准孔：${[...anchors.keys()].join('、')}。最高残差 ${data.registration.max_residual_mm?.toExponential(2)} mm（数值吻合，不是 CAD 测量精度声明）。缺失二维：${facts.coverage.missing_2d.join('、')}。原始毛坯、移动区域、折弯参数、轨迹、工具与装夹仍待核验。资产 SHA256：${asset.asset_id}。`;
  if(gl){try{initializeGPU();}catch(e){program=null;$('gpu-error').hidden=false;$('gpu-error').textContent=e.message;}}else{$('gpu-error').hidden=false;$('gpu-error').textContent='当前浏览器未启用 WebGL，二维与数据清单仍可使用。';}
  bindPointer(flat,false);bindPointer(solid,true);resize();choose(op.operation_id);new ResizeObserver(resize).observe(document.querySelector('.workspace'));
  // Diagnostics for repeatable browser QA, no project writes.
  window.review3d={data,choose,projectPoint:p=>C.project(p,camera,solid.width,solid.height).map(v=>v/devicePixelRatio),pickFace:(x,y)=>pick3(x*devicePixelRatio,y*devicePixelRatio),getState:()=>({operation_id:op.operation_id,selected:[...selected],suggested:[...suggested],hidden:[...hidden],camera:structuredClone(camera),bindings:structuredClone(bindings),webgl:!!program})};
})();
