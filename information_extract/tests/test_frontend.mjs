// Pure frontend geometry tests; no browser automation or external dependencies.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const code=fs.readFileSync(path.join(root,'web/app.js'),'utf8').replace(/bootstrap\(\);\s*$/,'');
const html=fs.readFileSync(path.join(root,'web/index.html'),'utf8');
const context=vm.createContext({window:{},console,crypto:globalThis.crypto,document:{getElementById:()=>({checked:true})}});
vm.runInContext(code,context);
const run=expression=>JSON.parse(JSON.stringify(vm.runInContext(expression,context)));
let passed=0;
function check(name,fn){fn();passed++;console.log('PASS '+name);}
check('270-degree display and inverse',()=>{
  assert.deepEqual(run('display([3,8])'),[8,3]);
  assert.deepEqual(run('material(display([3,8]))'),[3,8]);
});
check('original display and inverse',()=>{
  vm.runInContext("state.orientation='0'",context);
  assert.deepEqual(run('display([3,8])'),[3,-8]);
  assert.deepEqual(run('material(display([3,8]))'),[3,8]);
});
check('true line distance',()=>assert.equal(run("distance([4,3],{segment_type:'LINE',start_x:0,start_y:0,end_x:8,end_y:0})"),3));
check('circle boundary distance',()=>assert.equal(run("distance([3,0],{segment_type:'CIRCLE',center_x:0,center_y:0,radius:2})"),1));
check('positive arc hit',()=>assert.ok(run("distance([Math.SQRT1_2,Math.SQRT1_2],{segment_type:'ARC',start_x:1,start_y:0,end_x:0,end_y:1,center_x:0,center_y:0,radius:1,sweep_angle:90})")<1e-12));
check('outside arc is not full circle hit',()=>assert.ok(run("distance([-1,0],{segment_type:'ARC',start_x:1,start_y:0,end_x:0,end_y:1,center_x:0,center_y:0,radius:1,sweep_angle:90})")>1));
check('negative major arc hit',()=>assert.ok(run("distance([-1,0],{segment_type:'ARC',start_x:1,start_y:0,end_x:0,end_y:1,center_x:0,center_y:0,radius:1,sweep_angle:-270})")<1e-12));
check('signed major arc SVG flags',()=>assert.match(run("pathData({segment_type:'ARC',start_x:1,start_y:0,end_x:0,end_y:1,center_x:0,center_y:0,radius:1,sweep_angle:-270})"),/ A 1 1 0 1 0 /));
check('bridge diagonal footprint',()=>assert.equal(run("pathData({segment_type:'BRIDGE',start_x:1,start_y:2,end_x:4,end_y:6})"),'M 1 2 H 4 V 6 H 1 Z'));
check('unique HTML IDs',()=>{
  const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]);
  assert.equal(new Set(ids).size,ids.length);
});
check('all literal JS element IDs exist',()=>{
  const ids=new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]));
  const referenced=[...code.matchAll(/\$\('([^']+)'\)/g)].map(x=>x[1]);
  assert.deepEqual(referenced.filter(id=>!ids.has(id)),[]);
});
check('whole-curve bbox supplied by backend is preserved',()=>assert.deepEqual(run("bbox({bbox:[-2,-3,4,5]})"),[-2,-3,4,5]));
check('no blocking native confirm or prompt',()=>assert.doesNotMatch(code,/\b(?:confirm|prompt)\(/));
check('new draft includes stable empty start selector',()=>assert.equal(run('defaultOperation().geometry_options.start_segment_id'),null));
check('original text orientation and placement',()=>{
  vm.runInContext("state.orientation='0'",context);
  assert.equal(run('annotationTransform({x:3,y:8,rotation:30,width_factor:1.2})'),'translate(3 -8) rotate(-30) scale(1.2 1)');
});
check('270-degree text orientation follows drawing',()=>{
  vm.runInContext("state.orientation='270'",context);
  assert.equal(run('annotationTransform({x:3,y:8,rotation:30,width_factor:1})'),'translate(8 3) rotate(60) scale(1 1)');
});
check('SVG annotations toggle uses CSS display',()=>assert.match(code,/layer\.style\.display=\$\('show-annotations'\)\.checked/));
check('annotation UI exposes batch action, no technical generation dropdown',()=>{
  assert.match(html,/id="compile-geometry"/);
  assert.doesNotMatch(html,/id="geometry-mode"/);
  assert.match(html,/整图动作解释完成/);
});
vm.runInContext(`
  renderEditor=()=>{};renderSelection=()=>{};schedulePreview=()=>{};updateDirty=()=>{};toast=()=>{};
  screenToMaterial=(x,y)=>[x,y];screenToDisplay=(x,y)=>[x,y];pixelScale=()=>0;
  state.project={operations:[]};state.draft=defaultOperation();state.draftBase=JSON.stringify(state.draft);
  var testHits=[{id:'first'}];nearby=()=>testHits;
`,context);
check('plain clicks append and repeated clicks toggle without duplicates',()=>{
  vm.runInContext(`clickCanvas({clientX:0,clientY:0});testHits=[{id:'second'}];clickCanvas({clientX:0,clientY:0});`,context);
  assert.deepEqual(run('[...state.selected]'),['first','second']);
  vm.runInContext(`clickCanvas({clientX:0,clientY:0});`,context);
  assert.deepEqual(run('[...state.selected]'),['first']);
});
check('empty canvas click keeps selection',()=>{
  vm.runInContext(`testHits=[];clickCanvas({clientX:0,clientY:0});`,context);
  assert.deepEqual(run('[...state.selected]'),['first']);
});
check('box selection appends and never duplicates existing IDs',()=>{
  vm.runInContext(`state.orientation='0';segments=new Map([['box',{id:'box',bbox:[0,-2,1,-1]}]]);boxSelect([0,0],[2,3],false);boxSelect([0,0],[2,3],false);`,context);
  assert.deepEqual(run('[...state.selected]'),['first','box']);
});
check('all default action bindings use a unique ID set',()=>{
  vm.runInContext(`setSelection(['a','a','b'],true);`,context);
  assert.deepEqual(run('state.draft.source_segment_ids'),['first','box','a','b']);
});
check('box selection excludes hidden background',()=>{
  vm.runInContext(`document.getElementById=()=>({checked:false});state.selected=new Set();state.statuses={hidden:'ignored',visible:'unhandled'};segments=new Map([['hidden',{id:'hidden',bbox:[0,-2,1,-1]}],['visible',{id:'visible',bbox:[0,-2,1,-1]}]]);boxSelect([0,0],[2,3],false);`,context);
  assert.deepEqual(run('[...state.selected]'),['visible']);
});
check('frontend has no automatic geometry preview API request',()=>{
  assert.doesNotMatch(code,/request\('\/api\/preview'/);
});
console.log(`${passed} frontend checks passed.`);
