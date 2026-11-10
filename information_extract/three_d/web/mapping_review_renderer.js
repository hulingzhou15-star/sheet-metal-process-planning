/* Static true-CAD mesh, with a separate GPU face-picking pass. */
(function(root){
 'use strict';
 class MappingRenderer{
  constructor(canvas,mesh){
   const gl=this.gl=canvas.getContext('webgl',{antialias:true,preserveDrawingBuffer:true});if(!gl)throw Error('WebGL 不可用');
   this.canvas=canvas;this.mesh=mesh;
   const compile=(type,source)=>{const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s;};
   const p=this.program=gl.createProgram();
   gl.attachShader(p,compile(gl.VERTEX_SHADER,`attribute vec3 position,normal,color;attribute float face;uniform vec3 center,right,up,eye;uniform vec2 scale,pan;varying vec3 n,c,w;varying float id;void main(){vec3 d=position-center;gl_Position=vec4((dot(d,right)+pan.x)*scale.x,(dot(d,up)+pan.y)*scale.y,-dot(d,eye)/2000.,1.);n=normal;c=color;w=position;id=face;}`));
   gl.attachShader(p,compile(gl.FRAGMENT_SHADER,`precision highp float;varying vec3 n,c,w;varying float id;uniform bool picking,clip;uniform vec3 low,high;void main(){if(clip&&(any(lessThan(w,low))||any(greaterThan(w,high))))discard;if(picking){gl_FragColor=vec4(mod(id,256.)/255.,mod(floor(id/256.),256.)/255.,floor(id/65536.)/255.,1.);}else{float light=.68+.32*abs(dot(normalize(n),normalize(vec3(.3,-.7,1.))));gl_FragColor=vec4(c*light,1.);}}`));
   gl.linkProgram(p);if(!gl.getProgramParameter(p,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(p));
   const positions=new Float32Array(mesh.positions),normals=new Float32Array(positions.length),ids=new Float32Array(positions.length/3),colors=new Float32Array(positions.length);
   const C=IE3DCore;
   for(let i=0;i<positions.length;i+=9){const a=Array.from(positions.subarray(i,i+3)),b=Array.from(positions.subarray(i+3,i+6)),c=Array.from(positions.subarray(i+6,i+9)),raw=C.cross(C.sub(b,a),C.sub(c,a)),n=Math.hypot(...raw)>1e-12?C.norm(raw):[0,0,1];for(let j=0;j<3;j++){normals.set(n,i+j*3);ids[i/3+j]=mesh.triangle_face_ids[i/9];}}
   this.buffers={};for(const [name,array] of [['position',positions],['normal',normals],['face',ids],['color',colors]]){const b=this.buffers[name]=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.bufferData(gl.ARRAY_BUFFER,array,gl.STATIC_DRAW);}
   this.colors=colors;this.count=positions.length/3;this.framebuffer=gl.createFramebuffer();this.texture=gl.createTexture();this.depth=gl.createRenderbuffer();
  }
  colorize(selected,related){
   const gl=this.gl,colors=this.colors;
   for(let i=0;i<this.mesh.triangle_face_ids.length;i++){const id=this.mesh.triangle_face_ids[i],color=selected.has(id)?[.67,.30,.59]:related.has(id)?[.65,.73,.76]:[.83,.86,.89];for(let j=0;j<3;j++)colors.set(color,i*9+j*3);}
   gl.bindBuffer(gl.ARRAY_BUFFER,this.buffers.color);gl.bufferData(gl.ARRAY_BUFFER,colors,gl.DYNAMIC_DRAW);
  }
  resize(){
   const gl=this.gl,w=this.canvas.width,h=this.canvas.height;
   gl.bindTexture(gl.TEXTURE_2D,this.texture);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.NEAREST);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.NEAREST);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,w,h,0,gl.RGBA,gl.UNSIGNED_BYTE,null);
   gl.bindRenderbuffer(gl.RENDERBUFFER,this.depth);gl.renderbufferStorage(gl.RENDERBUFFER,gl.DEPTH_COMPONENT16,w,h);gl.bindFramebuffer(gl.FRAMEBUFFER,this.framebuffer);gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_2D,this.texture,0);gl.framebufferRenderbuffer(gl.FRAMEBUFFER,gl.DEPTH_ATTACHMENT,gl.RENDERBUFFER,this.depth);if(gl.checkFramebufferStatus(gl.FRAMEBUFFER)!==gl.FRAMEBUFFER_COMPLETE)throw Error('选面缓冲区初始化失败');gl.bindFramebuffer(gl.FRAMEBUFFER,null);
  }
  render(camera,box,picking=false){
   const gl=this.gl,p=this.program,C=IE3DCore,b=C.basis(camera.yaw,camera.pitch),u=n=>gl.getUniformLocation(p,n);
   gl.useProgram(p);gl.bindFramebuffer(gl.FRAMEBUFFER,picking?this.framebuffer:null);gl.viewport(0,0,this.canvas.width,this.canvas.height);gl.clearColor(picking?0:.98,picking?0:.985,picking?0:.99,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);gl.disable(gl.CULL_FACE);gl.disable(gl.BLEND);picking?gl.disable(gl.DITHER):gl.enable(gl.DITHER);
   for(const [n,v] of [['center',camera.center],['right',b.right],['up',b.up],['eye',b.eye]])gl.uniform3fv(u(n),v);
   gl.uniform2fv(u('scale'),[2*camera.scale/this.canvas.width,2*camera.scale/this.canvas.height]);gl.uniform2fv(u('pan'),camera.pan);gl.uniform1i(u('picking'),picking?1:0);gl.uniform1i(u('clip'),box?1:0);gl.uniform3fv(u('low'),box?box.slice(0,3):[0,0,0]);gl.uniform3fv(u('high'),box?box.slice(3):[0,0,0]);
   for(const [name,size] of [['position',3],['normal',3],['color',3],['face',1]]){const loc=gl.getAttribLocation(p,name);gl.bindBuffer(gl.ARRAY_BUFFER,this.buffers[name]);gl.enableVertexAttribArray(loc);gl.vertexAttribPointer(loc,size,gl.FLOAT,false,0,0);}
   gl.drawArrays(gl.TRIANGLES,0,this.count);gl.bindFramebuffer(gl.FRAMEBUFFER,null);
  }
  pick(x,y,camera,box){const gl=this.gl;this.render(camera,box,true);const pixel=new Uint8Array(4);gl.bindFramebuffer(gl.FRAMEBUFFER,this.framebuffer);gl.readPixels(Math.floor(x),this.canvas.height-1-Math.floor(y),1,1,gl.RGBA,gl.UNSIGNED_BYTE,pixel);gl.bindFramebuffer(gl.FRAMEBUFFER,null);this.render(camera,box);return IE3DCore.decodePick(pixel);}
 }
 root.MappingRenderer=MappingRenderer;
})(globalThis);
