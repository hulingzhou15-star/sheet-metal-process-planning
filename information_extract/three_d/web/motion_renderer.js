(function(root){
  'use strict';
  class MotionRenderer{
    constructor(canvas){
      this.canvas=canvas;const gl=this.gl=canvas.getContext('webgl',{antialias:true,preserveDrawingBuffer:true});if(!gl)throw Error('WebGL unavailable');
      const compile=(type,source)=>{const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s;};
      const p=this.program=gl.createProgram();
      gl.attachShader(p,compile(gl.VERTEX_SHADER,`attribute vec3 position;attribute vec3 normal;attribute vec3 color;uniform vec3 center,right,up,eye;uniform vec2 pan,scale;varying vec3 n,c;void main(){vec3 d=position-center;gl_Position=vec4((dot(d,right)+pan.x)*scale.x,(dot(d,up)+pan.y)*scale.y,-dot(d,eye)/1000.,1.);n=normal;c=color;}`));
      gl.attachShader(p,compile(gl.FRAGMENT_SHADER,`precision highp float;varying vec3 n,c;uniform bool line;uniform float alpha;void main(){float light=line?1.:.65+.35*abs(dot(normalize(n),normalize(vec3(.3,-.7,1.))));gl_FragColor=vec4(c*light,alpha);}`));
      gl.linkProgram(p);if(!gl.getProgramParameter(p,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(p));
      this.buffers={position:gl.createBuffer(),normal:gl.createBuffer(),color:gl.createBuffer()};
    }
    upload(position,normal,color){const g=this.gl;for(const [name,values] of [['position',position],['normal',normal],['color',color]]){g.bindBuffer(g.ARRAY_BUFFER,this.buffers[name]);g.bufferData(g.ARRAY_BUFFER,new Float32Array(values),g.DYNAMIC_DRAW);const loc=g.getAttribLocation(this.program,name);g.enableVertexAttribArray(loc);g.vertexAttribPointer(loc,3,g.FLOAT,false,0,0);}}
    render(triangles,lines,target,camera,colors){
      const C=IE3DCore,g=this.gl,p=this.program,uniform=name=>g.getUniformLocation(p,name),basis=C.basis(camera.yaw,camera.pitch);
      g.viewport(0,0,this.canvas.width,this.canvas.height);g.clearColor(0,0,0,0);g.clear(g.COLOR_BUFFER_BIT|g.DEPTH_BUFFER_BIT);g.useProgram(p);g.enable(g.DEPTH_TEST);g.depthMask(true);g.disable(g.CULL_FACE);g.disable(g.BLEND);
      for(const [name,value] of [['center',camera.center],['right',basis.right],['up',basis.up],['eye',basis.eye]])g.uniform3fv(uniform(name),value);
      g.uniform2fv(uniform('pan'),camera.pan);g.uniform2fv(uniform('scale'),[2*camera.scale/this.canvas.width,2*camera.scale/this.canvas.height]);
      let positions=[],normals=[],rgb=[];
      for(const t of triangles){const raw=C.cross(C.sub(t.points[1],t.points[0]),C.sub(t.points[2],t.points[0])),n=Math.hypot(...raw)>1e-12?C.norm(raw):[0,0,1];for(const v of t.points){positions.push(...v);normals.push(...n);rgb.push(...colors[t.region].map(v=>v/255));}}
      this.upload(positions,normals,rgb);g.uniform1i(uniform('line'),0);g.uniform1f(uniform('alpha'),1);g.enable(g.POLYGON_OFFSET_FILL);g.polygonOffset(1,1);g.drawArrays(g.TRIANGLES,0,positions.length/3);g.disable(g.POLYGON_OFFSET_FILL);
      const drawLines=(edges,color,alpha)=>{positions=[];rgb=[];for(const edge of edges)for(let i=1;i<edge.length;i++){positions.push(...edge[i-1],...edge[i]);rgb.push(...color,...color);}this.upload(positions,new Float32Array(positions.length),rgb);g.uniform1i(uniform('line'),1);g.uniform1f(uniform('alpha'),alpha);g.enable(g.BLEND);g.blendFunc(g.SRC_ALPHA,g.ONE_MINUS_SRC_ALPHA);g.drawArrays(g.LINES,0,positions.length/3);};
      drawLines(lines,[.38,.48,.61],.7);
      if(target.length){g.disable(g.DEPTH_TEST);drawLines(target,[.48,.58,.69],.55);g.enable(g.DEPTH_TEST);}
    }
  }
  root.MotionRenderer=MotionRenderer;
})(globalThis);
