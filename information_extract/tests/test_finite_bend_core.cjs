const fs=require('node:fs'),assert=require('node:assert/strict'),F=require('../three_d/web/finite_bend_core.js');
const root='information_extract/outputs/finite_bend_v01/P001/',model=JSON.parse(fs.readFileSync(root+'model.json')),ref=JSON.parse(fs.readFileSync(root+'kernel_reference.json'));
let checks=0;
for(const r of ref){const m=F.mapper(model,r.fractions);for(let i=0;i<3;i++){const p=[354.8,-8.9,10],a=m.map(i,p),matrix=r.transforms[i],b=matrix.slice(0,3).map(row=>row[0]*p[0]+row[1]*p[1]+row[2]*p[2]+row[3]);a.forEach((v,j)=>assert.ok(Math.abs(v-b[j])<1e-9));checks++;}for(let i=0;i<2;i++){assert.ok(Math.abs(m.frames.zones[i].angle-r.zones[i].angle)<1e-12);checks++;}}
assert.throws(()=>F.frames(model,{O009:0}));assert.throws(()=>F.frames(model,{O009:Infinity,O010:0}));
console.log(JSON.stringify({passed:true,python_js_comparisons:checks,invalid_fraction_checks:2}));
