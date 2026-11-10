const fs=require('node:fs'),assert=require('node:assert/strict');
require('../three_d/web/motion_core.js');const S=require('../three_d/web/stage_core.js');
const data=JSON.parse(fs.readFileSync('information_extract/outputs/stage_review_v01/P001/stage_data.json','utf8'));
assert.equal(data.plans.length,16);assert.equal(data.operations.length,224);assert.equal(data.sheet5_used,false);assert.equal(data.physical_simulation,false);
const orders=Object.fromEntries(data.stations.map(s=>[s.station_id,s.order]));let stages=0;
for(const plan of data.plans){let previous=new Set();for(const station of data.stations){const st=S.evaluate(data,plan.plan_id,station.station_id);const active=new Set(st.active);
 assert.equal(active.size,st.active.length);assert.equal(st.active.length+st.future.length,224);for(const id of previous)assert.ok(active.has(id));
 for(const op of data.operations){const expected=orders[plan.assignments[op.operation_id]]<=station.order;assert.equal(active.has(op.operation_id),expected);
  assert.equal(S.paths2(op,st).length,expected?op.flat_paths.length:0);assert.equal(S.paths3(op,st).length,expected&&op.region_id?op.formed_paths.length:0);
  for(const p of S.paths3(op,st).flat())assert.ok(p.every(Number.isFinite));}
 for(const joint of data.skeleton.joints){const id=data.skeleton.joint_to_operation[joint.joint_id];assert.equal(st.fractions[joint.joint_id],active.has(id)?1:0);}
 previous=active;stages++;}
 assert.equal(previous.size,224);
}
assert.throws(()=>S.evaluate(data,'bad','OP10'));assert.throws(()=>S.evaluate(data,data.plans[0].plan_id,'bad'));
// Order is semantic, not station ID sorting; all actions in a station complete together.
const reversed=structuredClone(data);reversed.stations.reverse();assert.deepEqual(S.evaluate(reversed,data.plans[0].plan_id,'OP40').active,S.evaluate(data,data.plans[0].plan_id,'OP40').active);
// Future actions never leak even when explicitly requested for highlighting.
const first=S.evaluate(data,data.plans[0].plan_id,'OP10');for(const id of first.future){const op=data.operations.find(o=>o.operation_id===id);assert.deepEqual(S.paths2(op,first),[]);assert.deepEqual(S.paths3(op,first),[]);}
console.log(JSON.stringify({passed:true,plans:16,stages,checks:'complete membership, monotonic visibility, 2D/3D gates, simultaneous joint states, finite positions, invalid inputs, station-order independence'}));
