const assert=require('node:assert/strict');
const c=require('../three_d/web/core.js');
const data={facts:{part_id:'P001',facts_fingerprint:'facts',operations:[{operation_id:'O1'}]},asset:{asset_id:'cad',topology_fingerprint:'topology',face_count:12}};
const b=c.emptyBindings(data);b.bindings.O1={face_ids:[1,2],status:'CONFIRMED'};
assert.deepEqual(c.validateBindings(b,data),b);
for(const bad of [
  {...b,part_id:'P002'}, {...b,asset_id:'different'}, {...b,facts_fingerprint:'changed'},
  {...b,topology_fingerprint:'changed'},
  {...b,bindings:{O2:{face_ids:[1],status:'DRAFT'}}},
  {...b,bindings:{O1:{face_ids:[1,1],status:'DRAFT'}}},
  {...b,bindings:{O1:{face_ids:[13],status:'DRAFT'}}},
  {...b,bindings:{O1:{face_ids:[],status:'CONFIRMED'}}}
]) assert.throws(()=>c.validateBindings(bad,data));
assert.notEqual(c.storageKey(data),c.storageKey({...data,facts:{...data.facts,part_id:'P002'}}));
assert.equal(c.decodePick([255,4,0,255]),1279);
const cam={yaw:0,pitch:0,center:[0,0,0],pan:[0,0],scale:2};
assert.deepEqual(c.project([0,1,1],cam,200,100),[102,48]);
console.log('Core: 12 assertions passed');
