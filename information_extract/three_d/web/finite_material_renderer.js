/* Static material-space buffers; panel and bend transforms are evaluated on GPU. */
(function(root){'use strict';
 const style=root.FiniteMaterialStyle={base:[.925,.932,.942],baseCss:'#d8dadd',softReflection:0,gloss:.075,glossExponent:40,highlight:[.447,.71,.863],highlightFill:[.64,.804,.902],highlightCss:'#72b5dc',highlightFillCss:'#a3cde5',lineWidthCss:1,selected:[.83,.18,.23],...root.FiniteMaterialTheme};
 class FiniteMaterialRenderer{
  constructor(canvas){
   const gl=this.gl=canvas.getContext('webgl',{antialias:true,preserveDrawingBuffer:false});if(!gl)throw Error('WebGL unavailable');this.canvas=canvas;this.cache=new Map();this.lineCache=new Map();this.uploadCount=0;this.frameCount=0;this.activeOperations=new Set();this.lastActiveBandIds=[];
   const compile=(type,source)=>{const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s;};
   const p=this.program=gl.createProgram();
   gl.attachShader(p,compile(gl.VERTEX_SHADER,`
    attribute vec3 position,normal;attribute float strokeSide;
    uniform mat4 materialToWorld;
    uniform vec3 center,right,up,eye,start,forward,axis;
    uniform vec2 pan,scale,viewportSize;
    uniform float angle,width,offset,strokeWidth;
    uniform bool band,diagnostic,lineMode;
    varying vec3 n,worldPoint;
    vec3 materialPoint(vec3 source){
     if(!band)return source;
      vec3 v=source-start;float along=dot(v,axis),u=clamp(dot(v,forward)/width,0.,1.),alpha=angle*u;
      float sn=sin(alpha),cs=cos(alpha),sinc,cosc;
      if(abs(alpha)<.0001){sinc=1.-alpha*alpha/6.;cosc=alpha/2.-alpha*alpha*alpha/24.;}
      else{sinc=sn/alpha;cosc=(1.-cs)/alpha;}
      vec3 z=vec3(0.,0.,1.),nowNormal=-sn*forward+cs*z;
      return start+along*axis-offset*z+width*u*sinc*forward+width*u*cosc*z+(offset+v.z)*nowNormal;
    }
    void main(){
     vec3 point=materialPoint(position),localNormal=normal;
     if(band&&!lineMode){
      vec3 v=position-start;float alpha=angle*clamp(dot(v,forward)/width,0.,1.);float sn=sin(alpha),cs=cos(alpha);
      vec3 z=vec3(0.,0.,1.),nowNormal=-sn*forward+cs*z,tangent=cs*forward+sn*z;
      float stretch=max(.00001,1.-angle/width*(offset+v.z));
      localNormal=dot(normal,axis)*axis+dot(normal,forward)/stretch*tangent+normal.z*nowNormal;
     }
     vec3 world=(materialToWorld*vec4(point,1.)).xyz,d=world-center;
     gl_Position=vec4((dot(d,right)+pan.x)*scale.x,(dot(d,up)+pan.y)*scale.y,-dot(d,eye)/1000.,1.);
     if(lineMode){vec3 other=(materialToWorld*vec4(materialPoint(normal),1.)).xyz-world;vec2 delta=vec2(dot(other,right),dot(other,up));float len=length(delta);vec2 perpendicular=len>.000001?vec2(-delta.y,delta.x)/len:vec2(1.,0.);gl_Position.xy+=perpendicular*strokeSide*strokeWidth/viewportSize;}
     if(diagnostic){gl_Position=vec4(0.,0.,0.,1.);gl_PointSize=1.;}
     n=mat3(materialToWorld)*localNormal;worldPoint=world;
    }`));
   gl.attachShader(p,compile(gl.FRAGMENT_SHADER,`precision highp float;varying vec3 n,worldPoint;uniform vec3 color,probeOrigin,eye;uniform bool diagnostic,lineMode,active;void main(){if(diagnostic){gl_FragColor=vec4(clamp((worldPoint-probeOrigin)/.02+.5,0.,1.),1.);}else if(lineMode){gl_FragColor=vec4(color,1.);}else{vec3 N=normalize(n),L=normalize(vec3(.3,-.7,1.)),H=normalize(L+normalize(eye));float light=.78+.22*abs(dot(N,L)),specular=(active?.025:${Number(style.gloss).toFixed(6)})*pow(abs(dot(N,H)),${Number(style.glossExponent).toFixed(1)});gl_FragColor=vec4(min(vec3(1.),color*light+specular+(active?0.:${Number(style.softReflection).toFixed(6)})*pow(abs(dot(N,H)),6.)),1.);}}`));
   gl.linkProgram(p);if(!gl.getProgramParameter(p,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(p));
   this.uniforms={};for(const name of ['materialToWorld','center','right','up','eye','start','forward','axis','pan','scale','angle','width','offset','band','color','diagnostic','probeOrigin','lineMode','active','viewportSize','strokeWidth'])this.uniforms[name]=gl.getUniformLocation(p,name);
   this.attributes={position:gl.getAttribLocation(p,'position'),normal:gl.getAttribLocation(p,'normal')};this.strokeAttribute=gl.getAttribLocation(p,'strokeSide');
  }
  info(id,geometries){
   if(this.cache.has(id))return this.cache.get(id);
   const triangles=geometries[id].triangles,count=triangles.length*3,positions=new Float32Array(count*3),normals=new Float32Array(count*3),lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];let at=0;
   for(const tri of triangles){const a=tri[0],b=tri[1],c=tri[2],x=b[0]-a[0],y=b[1]-a[1],z=b[2]-a[2],u=c[0]-a[0],v=c[1]-a[1],w=c[2]-a[2];let nx=y*w-z*v,ny=z*u-x*w,nz=x*v-y*u;const length=Math.hypot(nx,ny,nz);if(length>1e-12){nx/=length;ny/=length;nz/=length;}else{nx=0;ny=0;nz=1;}
    for(const p of tri){for(let k=0;k<3;k++){positions[at+k]=p[k];lo[k]=Math.min(lo[k],p[k]);hi[k]=Math.max(hi[k],p[k]);}normals[at]=nx;normals[at+1]=ny;normals[at+2]=nz;at+=3;}}
   const gl=this.gl,buffers={};for(const [name,values] of [['position',positions],['normal',normals]]){buffers[name]=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffers[name]);gl.bufferData(gl.ARRAY_BUFFER,values,gl.STATIC_DRAW);}
   const result={buffers,count,lo,hi,triangleCount:triangles.length};this.cache.set(id,result);this.uploadCount++;return result;
  }
  render(components,geometries,bends,fr,ts,camera,activeOperations=new Set(),outlines=[]){
   const gl=this.gl,u=this.uniforms,basis=IE3DCore.basis(camera.yaw,camera.pitch);this.frameCount++;
   this.activeOperations=activeOperations;this.lastActiveBandIds=[];
   gl.useProgram(this.program);gl.uniform1i(u.diagnostic,0);gl.uniform1i(u.lineMode,0);gl.disableVertexAttribArray(this.strokeAttribute);gl.vertexAttrib1f(this.strokeAttribute,0);gl.viewport(0,0,this.canvas.width,this.canvas.height);gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);gl.depthMask(true);gl.depthFunc(gl.LEQUAL);gl.disable(gl.CULL_FACE);gl.disable(gl.BLEND);
   for(const [name,value] of [['center',camera.center],['right',basis.right],['up',basis.up],['eye',basis.eye]])gl.uniform3fv(u[name],value);
   gl.uniform2fv(u.pan,camera.pan);gl.uniform2fv(u.scale,[2*camera.scale/this.canvas.width,2*camera.scale/this.canvas.height]);
   gl.enable(gl.POLYGON_OFFSET_FILL);gl.polygonOffset(1,1);
   for(const c of components){
    const info=this.info(c.geometry_id,geometries);this.componentUniforms(c,bends,fr,ts);
    for(const [name,location] of Object.entries(this.attributes)){gl.bindBuffer(gl.ARRAY_BUFFER,info.buffers[name]);gl.enableVertexAttribArray(location);gl.vertexAttribPointer(location,3,gl.FLOAT,false,0,0);}
    gl.drawArrays(gl.TRIANGLES,0,info.count);
   }
   gl.disable(gl.POLYGON_OFFSET_FILL);
   gl.uniform1i(u.lineMode,1);gl.uniform2fv(u.viewportSize,[this.canvas.width,this.canvas.height]);gl.uniform1f(u.strokeWidth,Math.max(1,style.lineWidthCss*this.canvas.width/Math.max(1,this.canvas.clientWidth)));
   for(const group of outlines){this.componentUniforms(group.component,bends,fr,ts);gl.uniform3fv(u.color,group.selected?style.selected:style.highlight);const info=this.lineInfo(group.key,group.paths);for(const [name,location] of Object.entries(this.attributes)){gl.bindBuffer(gl.ARRAY_BUFFER,info.buffers[name]);gl.enableVertexAttribArray(location);gl.vertexAttribPointer(location,3,gl.FLOAT,false,0,0);}gl.bindBuffer(gl.ARRAY_BUFFER,info.buffers.side);gl.enableVertexAttribArray(this.strokeAttribute);gl.vertexAttribPointer(this.strokeAttribute,1,gl.FLOAT,false,0,0);gl.drawArrays(gl.TRIANGLES,0,info.count);}
   gl.uniform1i(u.lineMode,0);gl.disableVertexAttribArray(this.strokeAttribute);
  }
  componentUniforms(c,bends,fr,ts){const gl=this.gl,u=this.uniforms,band=c.kind==='band',b=band?bends.get(c.joint_id):null,t=ts[band?b.parent_region_id:c.region_id],active=band&&this.activeOperations.has(b.operation_id);if(active&&c.operation_id&&!this.lastActiveBandIds.includes(c.operation_id))this.lastActiveBandIds.push(c.operation_id);gl.uniformMatrix4fv(u.materialToWorld,false,new Float32Array([t[0],t[4],t[8],t[12],t[1],t[5],t[9],t[13],t[2],t[6],t[10],t[14],t[3],t[7],t[11],t[15]]));gl.uniform1i(u.band,band?1:0);gl.uniform1i(u.active,active?1:0);gl.uniform3fv(u.color,active?style.highlightFill:style.base);if(band){gl.uniform3fv(u.start,b.start_flat_mm);gl.uniform3fv(u.forward,b.forward_flat);gl.uniform3fv(u.axis,b.axis_flat);gl.uniform1f(u.angle,b.target_angle_deg*Math.PI/180*fr[c.joint_id]);gl.uniform1f(u.width,b.width_mm);gl.uniform1f(u.offset,b.geometric_offset_from_front_mm);}}
  lineInfo(id,paths){if(this.lineCache.has(id))return this.lineCache.get(id);const positions=[],others=[],sides=[];for(const path of paths)for(let i=1;i<path.length;i++){const a=path[i-1],b=path[i];for(const [p,q,side] of [[a,b,-1],[a,b,1],[b,a,1],[b,a,1],[a,b,1],[b,a,-1]]){positions.push(...p);others.push(...q);sides.push(side);}}const gl=this.gl,buffers={};for(const [name,values] of [['position',positions],['normal',others],['side',sides]]){buffers[name]=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffers[name]);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(values),gl.STATIC_DRAW);}const info={buffers,count:sides.length};this.lineCache.set(id,info);return info;}
  verifyPoints(samples,bends,fr,ts){
   // QA only: use the actual display shader to encode a point residual into
   // one RGB pixel (0.02mm full range, ~0.0000784mm quantization).
   const gl=this.gl,u=this.uniforms,fb=gl.createFramebuffer(),texture=gl.createTexture(),buffer=gl.createBuffer(),pixel=new Uint8Array(4);let maxError=0,count=0;
   gl.bindTexture(gl.TEXTURE_2D,texture);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.NEAREST);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.NEAREST);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,1,1,0,gl.RGBA,gl.UNSIGNED_BYTE,null);gl.bindFramebuffer(gl.FRAMEBUFFER,fb);gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_2D,texture,0);
   if(gl.checkFramebufferStatus(gl.FRAMEBUFFER)!==gl.FRAMEBUFFER_COMPLETE)throw Error('GPU probe framebuffer failed');
   gl.useProgram(this.program);gl.uniform1i(u.diagnostic,1);gl.uniform1i(u.lineMode,0);gl.disableVertexAttribArray(this.strokeAttribute);gl.vertexAttrib1f(this.strokeAttribute,0);gl.viewport(0,0,1,1);gl.disable(gl.DEPTH_TEST);gl.disable(gl.BLEND);gl.disable(gl.DITHER);
   for(const sample of samples){this.componentUniforms(sample.component,bends,fr,ts);for(let i=0;i<sample.flat_points.length;i++){const point=sample.flat_points[i],expected=sample.world_points[i];gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(point),gl.STREAM_DRAW);gl.enableVertexAttribArray(this.attributes.position);gl.vertexAttribPointer(this.attributes.position,3,gl.FLOAT,false,0,0);gl.disableVertexAttribArray(this.attributes.normal);gl.vertexAttrib3f(this.attributes.normal,0,0,1);gl.uniform3fv(u.probeOrigin,expected);gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT);gl.drawArrays(gl.POINTS,0,1);gl.readPixels(0,0,1,1,gl.RGBA,gl.UNSIGNED_BYTE,pixel);if(pixel[3]!==255)throw Error('GPU coordinate probe did not draw');for(let k=0;k<3;k++)maxError=Math.max(maxError,Math.abs((pixel[k]/255-.5)*.02));count++;}}
   gl.uniform1i(u.diagnostic,0);gl.bindFramebuffer(gl.FRAMEBUFFER,null);gl.deleteFramebuffer(fb);gl.deleteTexture(texture);gl.deleteBuffer(buffer);gl.enable(gl.DITHER);return {count,max_encoded_error_mm:maxError,quantization_step_mm:.02/255};
  }
 }
 root.FiniteMaterialRenderer=FiniteMaterialRenderer;
})(globalThis);
