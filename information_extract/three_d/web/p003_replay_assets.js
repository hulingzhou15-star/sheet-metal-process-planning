/* Display assets for P003. The source mesh and diagnostic reports are separate. */
globalThis.P003ReplayAssets = (() => {
  'use strict';

  async function request(url) {
    let response;
    try {
      response = await fetch(url, {signal:AbortSignal.timeout(20000)});
    } catch (error) {
      const reason = error.name==='TimeoutError' ? '请求超时' : '请检查本地回放服务';
      throw Error('无法载入 '+url+'（'+reason+'）',{cause:error});
    }
    if (!response.ok) throw Error(`无法载入 ${url}（${response.status}）`);
    return response;
  }

  async function mesh(id) {
    const response = await request(`packed/${id}.f32.gz`);
    const body = new Response(response.body.pipeThrough(new DecompressionStream('gzip')));
    const buffer = await body.arrayBuffer();
    const vertexBytes = buffer.byteLength - 6 * Float32Array.BYTES_PER_ELEMENT;
    if (vertexBytes <= 0 || vertexBytes % (18 * Float32Array.BYTES_PER_ELEMENT)) {
      throw Error(`三维显示网格格式错误（${id}）`);
    }
    const values = new Float32Array(buffer), mid = 6 + (values.length - 6) / 2;
    return {lo:[...values.subarray(0,3)], hi:[...values.subarray(3,6)],
      position:values.subarray(6,mid), normal:values.subarray(mid)};
  }

  async function raster(id) {
    const response = await request(`flat_raster/${id}.png`);
    return createImageBitmap(await response.blob());
  }

  class Renderer extends FiniteMaterialRenderer {
    info(id, geometries) {
      if (this.cache.has(id)) return this.cache.get(id);
      const {lo,hi,position,normal} = geometries[id], gl = this.gl;
      const buffers = {};
      for (const [name,values] of Object.entries({position,normal})) {
        buffers[name] = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buffers[name]);
        gl.bufferData(gl.ARRAY_BUFFER, values, gl.STATIC_DRAW);
      }
      const count = position.length / 3;
      const result = {buffers,count,lo,hi,triangleCount:count/3};
      this.cache.set(id, result);
      this.uploadCount++;
      return result;
    }
  }

  return {request,mesh,raster,Renderer};
})();
