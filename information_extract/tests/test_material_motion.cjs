const assert=require('node:assert/strict'),fs=require('node:fs');
const M=require('../three_d/web/motion_core.js');
const data=JSON.parse(fs.readFileSync('information_extract/outputs/material_state_v01/P001/viewer_data.json','utf8'));
let checks=0;const ok=(condition,message)=>{assert.ok(condition,message);checks++;};
const close=(a,b)=>a.every((v,i)=>Math.abs(v-b[i])<1e-7);
ok(close(M.point(M.rotation([1,0,0],[0,0,1],90),[2,0,0]),[1,1,0]),'offset rotation');
const assignment=M.validateAssignment(data.default_assignment,data),orders=Object.fromEntries(data.stations.map(s=>[s.station_id,s.order]));
const feature=data.feature;
let state=M.schedule(data.example,assignment,orders,'OP40',0);
ok(close(M.point(state.transforms[feature.region_id],feature.formed_surface_center_mm),[354.8,1.1,0]),'flat reference');
state=M.schedule(data.example,assignment,orders,'OP40',1);
ok(close(M.point(state.transforms[feature.region_id],feature.formed_surface_center_mm),[354.8,-3.7,-5.6]),'child formed before parent');
state=M.schedule(data.example,assignment,orders,'OP50',1);
ok(close(M.point(state.transforms[feature.region_id],feature.formed_surface_center_mm),feature.formed_surface_center_mm),'formed closure');
for(const raw of [{O009:'OP40',O010:'OP40'},{O009:'OP10',O010:'OP40'},{O009:'OP50'},{assignments:[{operation_id:'O009',station_id:'OP50'},{operation_id:'O009',station_id:'OP50'}]}]){assert.throws(()=>M.validateAssignment(raw,data));checks++;}
ok(M.validateAssignment({assignments:[{operation_id:'O010',station_id:'OP40'},{operation_id:'O009',station_id:'OP50'}]},data).O010==='OP40','list import');
const same=M.schedule(data.example,{O009:'S',O010:'S'},{S:1},'S',.5);
ok(Object.values(same.fractions).every(v=>v===.5),'common station parameter');
for(const fractions of [{},{J651:NaN,J1245:.5},{J651:.5,J1245:2}]){assert.throws(()=>M.poses(data.example,fractions));checks++;}
console.log(`Motion core: ${checks} checks passed`);
