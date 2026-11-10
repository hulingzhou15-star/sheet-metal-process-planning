(function(root){
  'use strict';
  const add=(a,b)=>a.map((v,i)=>v+b[i]), sub=(a,b)=>a.map((v,i)=>v-b[i]);
  const dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0);
  const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
  const norm=a=>{const l=Math.hypot(...a);if(!l)throw Error('Zero vector');return a.map(v=>v/l);};
  function basis(yaw,pitch){
    const eye=[Math.cos(pitch)*Math.cos(yaw),Math.cos(pitch)*Math.sin(yaw),Math.sin(pitch)];
    const right=[-Math.sin(yaw),Math.cos(yaw),0], up=cross(eye,right);
    return {eye,right,up};
  }
  function project(p,c,width,height){
    const b=basis(c.yaw,c.pitch), d=sub(p,c.center);
    return [width/2+(dot(d,b.right)+c.pan[0])*c.scale,height/2-(dot(d,b.up)+c.pan[1])*c.scale];
  }
  function validateBindings(value,data){
    if(value.schema_version!==1 || value.part_id!==data.facts.part_id || value.asset_id!==data.asset.asset_id || value.topology_fingerprint!==data.asset.topology_fingerprint || value.facts_fingerprint!==data.facts.facts_fingerprint)
      throw Error('备份所属零件、CAD 版本或动作版本不匹配。');
    if(!value.bindings || Array.isArray(value.bindings) || typeof value.bindings!=='object')throw Error('关联格式无效。');
    const ids=new Set(data.facts.operations.map(o=>o.operation_id));
    for(const [op,row] of Object.entries(value.bindings)){
      if(!ids.has(op) || !Array.isArray(row.face_ids) || !['DRAFT','CONFIRMED'].includes(row.status))throw Error('动作或关联状态无效。');
      if(new Set(row.face_ids).size!==row.face_ids.length || row.face_ids.some(n=>!Number.isInteger(n)||n<1||n>data.asset.face_count))throw Error('面编号无效或重复。');
      if(row.status==='CONFIRMED' && !row.face_ids.length)throw Error('确认关联至少需要一个面。');
    }
    return JSON.parse(JSON.stringify(value));
  }
  function emptyBindings(data){return {schema_version:1,part_id:data.facts.part_id,asset_id:data.asset.asset_id,topology_fingerprint:data.asset.topology_fingerprint,facts_fingerprint:data.facts.facts_fingerprint,bindings:{}};}
  function storageKey(data){return `ie3d:v1:${data.facts.part_id}:${data.asset.asset_id}:${data.asset.topology_fingerprint}:${data.facts.facts_fingerprint}`;}
  function decodePick(pixel){return pixel[0]+pixel[1]*256+pixel[2]*65536;}
  const api={add,sub,dot,cross,norm,basis,project,validateBindings,emptyBindings,storageKey,decodePick};
  if(typeof module!=='undefined')module.exports=api;
  root.IE3DCore=api;
})(globalThis);
