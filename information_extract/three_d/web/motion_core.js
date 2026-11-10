(function(root){
  'use strict';
  const identity=()=>[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1];
  function multiply(a,b){const m=new Array(16).fill(0);for(let i=0;i<4;i++)for(let j=0;j<4;j++)for(let k=0;k<4;k++)m[i*4+j]+=a[i*4+k]*b[k*4+j];return m;}
  function point(m,p){return [0,1,2].map(i=>m[i*4]*p[0]+m[i*4+1]*p[1]+m[i*4+2]*p[2]+m[i*4+3]);}
  function rotation(p,d,degrees){
    if([...p,...d,degrees].some(v=>!Number.isFinite(v))||Math.hypot(...d)===0)throw Error('Invalid axis');
    const l=Math.hypot(...d),[x,y,z]=d.map(v=>v/l),a=degrees*Math.PI/180,c=Math.cos(a),s=Math.sin(a),v=1-c;
    const m=[c+x*x*v,x*y*v-z*s,x*z*v+y*s,0,y*x*v+z*s,c+y*y*v,y*z*v-x*s,0,z*x*v-y*s,z*y*v+x*s,c+z*z*v,0,0,0,0,1];
    for(let i=0;i<3;i++)m[i*4+3]=p[i]-(m[i*4]*p[0]+m[i*4+1]*p[1]+m[i*4+2]*p[2]);return m;
  }
  function poses(skeleton,fractions){
    if(Object.keys(fractions).length!==skeleton.joints.length||skeleton.joints.some(j=>!Number.isFinite(fractions[j.joint_id])||fractions[j.joint_id]<0||fractions[j.joint_id]>1))throw Error('Incomplete/invalid joint fractions');
    const byChild=new Map(skeleton.joints.map(j=>[j.child_region_id,j])),out={[skeleton.root_region_id]:identity()};
    for(const id of skeleton.region_order.slice(1)){const j=byChild.get(id);if(!j||!out[j.parent_region_id])throw Error('Invalid material tree');out[id]=multiply(out[j.parent_region_id],rotation(j.hinge_point_mm,j.axis_direction,(fractions[j.joint_id]-1)*j.angle_deg));}return out;
  }
  function schedule(skeleton,assignment,orders,station,tau){
    if(!(station in orders)||!Number.isFinite(tau)||tau<0||tau>1)throw Error('Invalid station/process parameter');
    const fractions={};for(const j of skeleton.joints){const op=skeleton.joint_to_operation[j.joint_id],s=assignment[op];if(!(s in orders))throw Error('Local operation assignment missing');fractions[j.joint_id]=orders[s]<orders[station]?1:orders[s]===orders[station]?tau:0;}
    return {fractions,transforms:poses(skeleton,fractions)};
  }
  function validateAssignment(raw,data){
    const source=raw.assignments??raw.assignment??raw;
    const map={};
    if(Array.isArray(source)){for(const r of source){if(!r||typeof r.operation_id!=='string'||typeof r.station_id!=='string'||r.operation_id in map)throw Error('分配行无效或动作重复。');map[r.operation_id]=r.station_id;}}
    else if(source&&typeof source==='object'){Object.assign(map,source);}else throw Error('需要动作到工序的分配。');
    const result={},stations=new Map(data.stations.map(s=>[s.station_id,s]));
    for(const op of data.local_operations){const station=stations.get(map[op.operation_id]);if(!station)throw Error(`缺少 ${op.operation_id} 的有效工序。`);if(!station.allowed_operation_types.split(',').map(v=>v.trim()).includes(op.operation_type))throw Error(`${op.operation_id} 不允许进入 ${station.station_id}。`);result[op.operation_id]=station.station_id;}
    for(const op of data.local_operations){
      if(op.cad_parent_operation_id in result&&stations.get(result[op.operation_id]).order>=stations.get(result[op.cad_parent_operation_id]).order)throw Error('这组动作不满足既有 child → parent 先后要求。');
      for(const predecessor of (op.required_predecessor_ids||'').split(/[,;\s]+/).filter(Boolean))if(predecessor in result&&stations.get(result[predecessor]).order>=stations.get(result[op.operation_id]).order)throw Error('这组动作不满足明确前置关系。');
    }
    return result;
  }
  const api={identity,multiply,point,rotation,poses,schedule,validateAssignment};
  if(typeof module!=='undefined')module.exports=api;root.MaterialMotion=api;
})(globalThis);
