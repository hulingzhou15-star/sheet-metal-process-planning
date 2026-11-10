/** Pure Node DOM-stub regression: no service, browser, project files or DXF writes. */
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const moduleRoot=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
let documentStub,uid=0;
class NodeStub {
  constructor(tag='div') {
    Object.assign(this,{tagName:tag.toUpperCase(),children:[],listeners:{},dataset:{},attributes:{},style:{},value:'',checked:false,className:'',hidden:false});
    const self=this;
    this.classList={toggle(name,on){const names=new Set(self.className.split(' ').filter(Boolean));on?names.add(name):names.delete(name);self.className=[...names].join(' ');},add(name){this.toggle(name,true);},remove(name){this.toggle(name,false);}};
  }
  append(...nodes){for(const node of nodes){if(node.parent)node.remove();node.parent=this;this.children.push(node);}}
  replaceChildren(...nodes){this.children=[];this.append(...nodes);}
  setAttribute(key,value){this.attributes[key]=String(value);}
  getAttribute(key){return this.attributes[key];}
  removeAttribute(key){delete this.attributes[key];}
  addEventListener(key,handler){(this.listeners[key]||=[]).push(handler);}
  focus(){documentStub.activeElement=this;}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(child=>child!==this);}
  add(node){this.append(node);}
  get options(){return this.children;}
}
const html=fs.readFileSync(path.join(moduleRoot,'web/index.html'),'utf8'),nodes={};
for(const match of html.matchAll(/<([a-z-]+)[^>]*\bid="([^"]+)"[^>]*>/gi))nodes[match[2]]=new NodeStub(match[1]);
documentStub={body:new NodeStub('body'),activeElement:null,getElementById:id=>nodes[id],createElement:tag=>new NodeStub(tag),createElementNS:(_,tag)=>new NodeStub(tag),createDocumentFragment:()=>new NodeStub('fragment')};
const appSource=fs.readFileSync(path.join(moduleRoot,'web/app.js'),'utf8');
assert.doesNotMatch(appSource,/(?<![\w.])(?:confirm|prompt)\s*\(/,'Use in-page dialogs rather than blocking native dialogs.');
const instrumented=appSource.replace(/\nbootstrap\(\);\s*$/,'')+'\nwindow.test={ask,discardDraft,newOperation,setDraft,renderList,readForm,hasDraftChanges,deleteOperation,undo,redo,defaultOperation,applyOperation,setSelection,selectionSummary,refreshPairing,renderEditor,renderSelection,parentSourceIds,focusCanvasForSelection,setTestPaths:items=>{paths.clear();for(const [key,value] of items)paths.set(key,value);},setFeatureTypes:items=>{featureTypes=items;}};';
const sandbox={window:{},document:documentStub,crypto:{randomUUID:()=>`test_uid_${++uid}`},Set,Map,console,setTimeout:()=>0,clearTimeout:()=>{},Option:function(label,value){const node=new NodeStub('option');node.textContent=label;node.value=value;return node;}};
vm.runInNewContext(instrumented,sandbox,{filename:'web/app.js'});
const api=sandbox.window.test,state=sandbox.window.informationExtract.getState();
const dialog=()=>documentStub.body.children.at(-1);
const buttons=()=>dialog().children[0].children.at(-1).children;
let passed=0;
async function scenario(name,run){await run();passed++;console.log(`PASS ${String(passed).padStart(2,'0')} ${name}`);}

await scenario('ask confirms and removes its overlay',async()=>{
  const pending=api.ask('确认测试');assert.equal(dialog().children[0].getAttribute('role'),'dialog');buttons()[1].onclick();assert.equal(await pending,true);assert.equal(documentStub.body.children.length,0);
});
await scenario('ask cancels without native dialogs',async()=>{
  const pending=api.ask('取消测试');buttons()[0].onclick();assert.equal(await pending,false);
});
await scenario('ask returns edited input',async()=>{
  const pending=api.ask('输入测试',{input:true,initial:'原始原因'}),input=dialog().children[0].children[1];assert.equal(input.value,'原始原因');input.value='新的原因';buttons()[1].onclick();assert.equal(await pending,'新的原因');
});
await scenario('input cancellation returns null',async()=>{
  const pending=api.ask('输入取消',{input:true});buttons()[0].onclick();assert.equal(await pending,null);
});
await scenario('Escape settles ask as cancellation',async()=>{
  const pending=api.ask('Escape测试');dialog().listeners.keydown[0]({key:'Escape',stopPropagation(){},preventDefault(){}});assert.equal(await pending,false);
});
await scenario('Enter in input accepts its value',async()=>{
  const pending=api.ask('Enter测试',{input:true}),input=dialog().children[0].children[1];input.value='回车原因';dialog().listeners.keydown[0]({key:'Enter',target:input,stopPropagation(){},preventDefault(){}});assert.equal(await pending,'回车原因');
});

state.project={schema_version:1,part_id:'TEST',revision:4,operations:[],station_rows:[],fixed_rows:[],ignored_entities:{}};
state.revision=4;state.drawing={source:{sha256:'test_only'}};nodes['show-regions'].checked=true;
await scenario('clean discardDraft bypasses a dialog',async()=>{
  api.setDraft(api.defaultOperation());assert.equal(await api.discardDraft(),true);assert.equal(documentStub.body.children.length,0);
});
await scenario('preview form read does not make a pristine draft dirty',async()=>{
  api.readForm();assert.equal(api.hasDraftChanges(),false);assert.equal(state.draft.geometry_options.start_segment_id,null);
});
await scenario('newOperation cancellation preserves a dirty draft',async()=>{
  state.draft.name='未应用修改';const pending=api.newOperation();buttons()[0].onclick();await pending;assert.equal(state.draft.name,'未应用修改');
});
await scenario('newOperation confirmation creates a fresh draft',async()=>{
  const pending=api.newOperation();buttons()[1].onclick();await pending;assert.equal(state.draft.name,'');assert.equal(state.current,null);
});
const operation={...api.defaultOperation(),operation_id:'O001',name:'动作1',operation_type:'CIRCLE_PUNCH'};
state.project.operations=[operation];
await scenario('async list click opens a clean operation',async()=>{
  api.renderList();await nodes['operation-list'].children[0].listeners.click[0]({});assert.equal(state.current,operation.uid);
});
await scenario('async list click cancellation preserves draft edits',async()=>{
  state.draft.name='待放弃';api.renderList();const pending=nodes['operation-list'].children[0].listeners.click[0]({});buttons()[0].onclick();await pending;assert.equal(state.draft.name,'待放弃');
});
await scenario('async list click confirmation reloads the selected operation',async()=>{
  api.renderList();const pending=nodes['operation-list'].children[0].listeners.click[0]({});buttons()[1].onclick();await pending;assert.equal(state.draft.name,'动作1');
});
await scenario('delete remains undoable and does not roll back server revision',async()=>{
  api.deleteOperation();assert.equal(state.project.operations.length,0);assert.equal(documentStub.body.children.length,0);api.undo();assert.equal(state.project.operations.length,1);assert.equal(state.project.revision,4);
});

api.setFeatureTypes([{key:'CIRCLE_PUNCH',operation_type:'CIRCLE_PUNCH',shape:'CIRCULAR',label:'冲孔—圆形'}]);
state.project.operations=[];state.history=[];state.redo=[];
function draftFor(identifier,sources){return {...api.defaultOperation(),operation_id:identifier,name:`测试 ${identifier}`,operation_type:'CIRCLE_PUNCH',feature_type:'CIRCLE_PUNCH',geometry_shape:'CIRCULAR',source_segment_ids:sources};}
await scenario('explicit creation keeps bindings and opens an empty next action',async()=>{
  api.setDraft(draftFor('O001',['a','b','c','d']));
  assert.equal(api.applyOperation(true,{advanceAfterCreate:true}),true);
  assert.equal(state.project.operations.length,1);
  assert.deepEqual([...state.project.operations[0].source_segment_ids],['a','b','c','d']);
  assert.equal(state.current,null);assert.equal(state.selected.size,0);
  assert.equal(state.draft.operation_id,'');assert.equal(state.draft.name,'');
  assert.equal(api.hasDraftChanges(),false);
  assert.equal(nodes['draft-selection-count'].textContent,'新动作：本次已选 0 个来源片段');
});
await scenario('second action selection never inherits first action sources',async()=>{
  api.setSelection(['second_circle'],true,true);
  assert.deepEqual([...state.selected],['second_circle']);
  nodes['operation-id'].value='O002';nodes['operation-name'].value='第二个孔';nodes['operation-type'].value='CIRCLE_PUNCH';
  assert.equal(api.applyOperation(true,{advanceAfterCreate:true}),true);
  assert.deepEqual([...state.project.operations[0].source_segment_ids],['a','b','c','d']);
  assert.deepEqual([...state.project.operations[1].source_segment_ids],['second_circle']);
  assert.equal(state.selected.size,0);assert.equal(state.current,null);
});
await scenario('editing labels distinguish applied bindings from pending selection',async()=>{
  api.setDraft(state.project.operations[0]);api.setSelection(['extra'],true);
  assert.equal(api.selectionSummary(),'本次已选 5 个来源片段 · O001 已应用绑定 4 个');
  assert.deepEqual([...state.project.operations[0].source_segment_ids],['a','b','c','d']);
  assert.match(nodes['editor-context'].textContent,/正在修改 O001/);
});
await scenario('new action can discard accidental additions without changing original',async()=>{
  const pending=api.newOperation();buttons()[1].onclick();await pending;
  assert.equal(state.selected.size,0);assert.equal(state.current,null);
  assert.deepEqual([...state.project.operations[0].source_segment_ids],['a','b','c','d']);
});
await scenario('failed creation preserves the draft and its selection',async()=>{
  api.setDraft(draftFor('O001',['unsaved']));
  assert.equal(api.applyOperation(true,{advanceAfterCreate:true}),false);
  assert.equal(state.project.operations.length,2);assert.equal(state.selected.size,1);
  assert.equal(state.draft.operation_id,'O001');
});
await scenario('background apply used by save does not unexpectedly change editor',async()=>{
  api.setDraft(draftFor('O003',['third']));
  assert.equal(api.applyOperation(false),true);
  assert.equal(state.current,state.project.operations[2].uid);
  assert.deepEqual([...state.selected],['third']);
});
await scenario('editing an existing action remains in edit mode after apply',async()=>{
  api.setSelection(['third_extra'],true);assert.equal(api.applyOperation(true,{advanceAfterCreate:true}),true);
  assert.equal(state.current,state.project.operations[2].uid);
  assert.deepEqual([...state.project.operations[2].source_segment_ids],['third','third_extra']);
});
await scenario('undo and redo of creation preserve empty-next-action behavior',async()=>{
  api.setDraft(draftFor('O004',['fourth']));state.history=[];state.redo=[];
  api.applyOperation(true,{advanceAfterCreate:true});assert.equal(state.project.operations.length,4);
  api.undo();assert.equal(state.project.operations.length,3);assert.equal(api.hasDraftChanges(),false);
  api.redo();assert.equal(state.project.operations.length,4);assert.equal(state.selected.size,0);assert.equal(state.current,null);
});
await scenario('newest-first display preserves the underlying operation order',async()=>{
  const original=JSON.stringify(state.project.operations);
  api.renderList();
  assert.deepEqual(nodes['operation-list'].children.map(item=>item.dataset.uid),[...state.project.operations].reverse().map(op=>op.uid));
  assert.equal(JSON.stringify(state.project.operations),original);
});
await scenario('search retains newest-first presentation and does not rewrite facts',async()=>{
  const original=JSON.stringify(state.project.operations);nodes['operation-search'].value='O002';api.renderList();
  assert.equal(nodes['operation-list'].children.length,1);assert.equal(nodes['operation-list'].children[0].dataset.uid,state.project.operations[1].uid);
  nodes['operation-search'].value='';api.renderList();assert.equal(JSON.stringify(state.project.operations),original);
});
await scenario('creation scrolls to the new top row even after browsing old actions',async()=>{
  nodes['operation-list'].scrollTop=400;api.setDraft(draftFor('O005',['fifth']));
  assert.equal(api.applyOperation(true,{advanceAfterCreate:true}),true);
  assert.equal(nodes['operation-list'].scrollTop,0);
  assert.equal(nodes['operation-list'].children[0].dataset.uid,state.project.operations.at(-1).uid);
});
await scenario('manual list browsing position survives unrelated rerendering',async()=>{
  nodes['operation-list'].scrollTop=120;api.renderList();assert.equal(nodes['operation-list'].scrollTop,120);
});
await scenario('process group is automatic and read-only in the form',async()=>{
  assert.match(html,/<input id="process-group" readonly/);
  assert.match(html,/预孔—翻孔先后已由加工组表达/);
});
api.setFeatureTypes([{key:'PRE_PUNCH_CIRCULAR',operation_type:'PRE_PUNCH',shape:'CIRCULAR',label:'预孔—圆形'}]);
const flange={...api.defaultOperation(),uid:'flange',operation_id:'O008',operation_type:'FLANGING'},pre={...api.defaultOperation(),uid:'pre',operation_id:'O009',name:'预孔',feature_type:'PRE_PUNCH_CIRCULAR',operation_type:'PRE_PUNCH',geometry_shape:'CIRCULAR',cad_parent_operation_id:'O008'};
state.project.operations=[flange];
const autoPairing={assignments:[{uid:'pre',process_group_id:'PG_O008',process_group_source:'AUTO_CAD_PAIR',process_group_pair_uids:['flange','pre']}],statuses:{pre:{status:'PAIRED',message:'已与 O008 自动配对'}}};
await scenario('metadata preview fills the group without manual entry or changing CAD',async()=>{
  api.setDraft(pre);sandbox.fetch=async()=>({ok:true,json:async()=>autoPairing});await api.refreshPairing();
  assert.equal(state.draft.process_group_id,'PG_O008');assert.equal(nodes['process-group'].value,'PG_O008');
  assert.equal(state.draft.cad_parent_operation_id,'O008');assert.equal(state.draft.required_predecessor_ids.length,0);
  assert.equal(state.project.operations[0].process_group_id,null);
});
await scenario('stale pairing responses cannot overwrite a subsequently changed parent',async()=>{
  api.setDraft(pre);let finish,startedResolve;const started=new Promise(resolve=>startedResolve=resolve);sandbox.fetch=async()=>({ok:true,json:()=>new Promise(resolve=>{finish=resolve;startedResolve();})});
  const pending=api.refreshPairing();await started;
  state.draft.cad_parent_operation_id='O_OTHER';finish(autoPairing);await pending;
  assert.equal(state.draft.process_group_id,null);assert.equal(state.draft.cad_parent_operation_id,'O_OTHER');
});

await scenario('geometry selection does not rebuild the CAD datalist',async()=>{
  const parent={...draftFor('PARENT',['parent_line']),uid:'parent'},child={...draftFor('CHILD',['child_circle']),cad_parent_operation_id:'PARENT'};
  state.project.operations=[parent];api.setDraft(child);nodes['cad-parent'].focus();
  const before=nodes['operation-options'].children.slice();api.setSelection(['another'],true);
  assert.equal(nodes['operation-options'].children.length,before.length);
  assert.ok(nodes['operation-options'].children.every((node,i)=>node===before[i]));
  assert.equal(nodes['cad-parent'].value,'PARENT');
  api.focusCanvasForSelection();assert.equal(documentStub.activeElement,nodes.drawing);
  assert.match(appSource,/pointerdown[^\n]+focusCanvasForSelection\(\)/);
});
await scenario('CAD suggestions refresh when an operation name changes',async()=>{
  const before=nodes['operation-options'].children[0];state.project.operations[0].name='新的父动作名称';api.renderEditor();
  assert.notEqual(nodes['operation-options'].children[0],before);
  assert.equal(nodes['operation-options'].children[0].label,'新的父动作名称');
});
await scenario('parent geometry is highlighted without entering the child binding',async()=>{
  const parentPath=new NodeStub('path'),childPath=new NodeStub('path');api.setTestPaths([['parent_line',parentPath],['child_circle',childPath]]);
  state.statuses={parent_line:'bound',child_circle:'unhandled'};api.renderSelection();
  assert.match(parentPath.getAttribute('class'),/cad-parent/);assert.doesNotMatch(parentPath.getAttribute('class'),/selected/);
  assert.match(childPath.getAttribute('class'),/selected/);assert.doesNotMatch(childPath.getAttribute('class'),/cad-parent/);
  assert.equal(state.selected.has('parent_line'),false);assert.equal(nodes['parent-highlight-status'].hidden,false);
});
await scenario('changing or clearing the parent removes stale highlight',async()=>{
  state.draft.cad_parent_operation_id='MISSING';api.renderSelection();assert.deepEqual([...api.parentSourceIds()],[]);
  assert.doesNotMatch(nodes['parent-highlight-status'].textContent,/PARENT/);assert.equal(nodes['parent-highlight-status'].hidden,true);
  state.draft.cad_parent_operation_id=null;api.renderSelection();assert.equal(api.parentSourceIds().size,0);
});
await scenario('ambiguous parent IDs and self references do not select a parent',async()=>{
  const a={...draftFor('DUP',['a']),uid:'a'},b={...draftFor('DUP',['b']),uid:'b'};state.project.operations=[a,b];state.draft.cad_parent_operation_id='DUP';assert.equal(api.parentSourceIds().size,0);
  state.project.operations=[state.draft];state.draft.cad_parent_operation_id=state.draft.operation_id;assert.equal(api.parentSourceIds().size,0);
  api.setTestPaths([]);
});
await scenario('selected coincident paths paint above unselected duplicates without merging IDs',async()=>{
  const a=new NodeStub('path'),duplicate=new NodeStub('path'),parallel=new NodeStub('path');
  nodes['source-layer'].replaceChildren(a,duplicate,parallel);
  api.setTestPaths([['a',a],['duplicate',duplicate],['parallel',parallel]]);
  state.project.operations=[];const projectBefore=JSON.stringify(state.project);
  api.setDraft(draftFor('BEND_TEST',['a','parallel']));
  assert.deepEqual(nodes['source-layer'].children,[duplicate,a,parallel]);
  assert.match(a.getAttribute('class'),/selected/);assert.doesNotMatch(duplicate.getAttribute('class'),/selected/);
  assert.deepEqual([...state.selected],['a','parallel']);
  assert.deepEqual([...state.draft.source_segment_ids],['a','parallel']);
  assert.equal(JSON.stringify(state.project),projectBefore);
});
await scenario('toggling duplicate selections keeps paint order correct and source paths unique',async()=>{
  api.setSelection(['a'],true,true);api.setSelection(['duplicate'],true,true);api.setSelection(['parallel'],true,true);
  const children=nodes['source-layer'].children;
  assert.equal(children.length,3);assert.equal(new Set(children).size,3);
  assert.match(children.at(-1).getAttribute('class'),/selected/);
  assert.deepEqual([...state.selected],['duplicate']);
  api.setTestPaths([]);
});
api.setFeatureTypes([
  {key:'BEND_STRAIGHT',operation_type:'BEND',shape:'STRAIGHT',label:'一般折弯'},
  {key:'BEND_CURVED',operation_type:'BEND',shape:'CURVED',label:'曲线折弯'},
  {key:'CIRCLE_PUNCH',operation_type:'CIRCLE_PUNCH',shape:'CIRCULAR',label:'冲孔—圆形'}
]);
state.fieldDefaults={bend_angle_deg:90};state.project.operations=[];
await scenario('new straight bend gets the configured default through input then change reads',async()=>{
  api.setDraft(api.defaultOperation());nodes['operation-type'].value='BEND_STRAIGHT';api.readForm();api.readForm();api.renderEditor();
  assert.equal(state.draft.bend_angle_deg,90);assert.equal(nodes['bend-angle'].value,90);
  assert.equal(nodes['bend-angle-row'].hidden,false);
});
await scenario('edited bend angle survives source selection and rerender',async()=>{
  nodes['bend-angle'].value='45.5';api.readForm();api.setSelection(['angle_line'],true);api.renderEditor();
  assert.equal(state.draft.bend_angle_deg,45.5);assert.equal(nodes['bend-angle'].value,45.5);
});
await scenario('curved bends expose the same angle field without resetting its value',async()=>{
  nodes['operation-type'].value='BEND_CURVED';api.readForm();api.renderEditor();
  assert.equal(state.draft.bend_angle_deg,45.5);assert.equal(nodes['bend-angle-row'].hidden,false);
});
await scenario('an explicitly cleared bend angle remains unknown',async()=>{
  nodes['bend-angle'].value='';api.readForm();api.renderEditor();api.readForm();
  assert.equal(state.draft.bend_angle_deg,null);assert.equal(nodes['bend-angle'].value,'');
});
await scenario('opening an older bend without an angle does not invent one',async()=>{
  const old={...api.defaultOperation(),operation_id:'OLD',operation_type:'BEND',feature_type:'BEND_STRAIGHT'};
  delete old.bend_angle_deg;api.setDraft(old);api.readForm();
  assert.equal(nodes['bend-angle'].value,'');assert.equal(state.draft.bend_angle_deg,null);
});
await scenario('non-bend types hide and clear angle while the next new bend uses default',async()=>{
  nodes['operation-type'].value='CIRCLE_PUNCH';api.readForm();api.renderEditor();
  assert.equal(nodes['bend-angle-row'].hidden,true);assert.equal(state.draft.bend_angle_deg,null);
  api.setDraft(api.defaultOperation());nodes['operation-type'].value='BEND_CURVED';api.readForm();api.renderEditor();
  assert.equal(state.draft.bend_angle_deg,90);assert.equal(nodes['bend-angle-row'].hidden,false);
});
console.log(`DOM-stub regression: ${passed} scenarios passed. No browser or project data was modified.`);
