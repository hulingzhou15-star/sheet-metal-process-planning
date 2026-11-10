/* Test-only minimal DOM; execute the actual inline UI handlers without a browser. */
const fs=require('fs');
class Element {
  constructor(tag,attrs={},children=[]) {
    this.tagName=tag;this.attrs={...attrs};this.parentElement=null;this.children=[];this.events={};this.style={};
    this.dataset={};for(const [k,v] of Object.entries(attrs)) if(k.startsWith('data-'))this.dataset[k.slice(5).replace(/-([a-z])/g,(_,s)=>s.toUpperCase())]=v;
    this.value=attrs.value || '';this.disabled='disabled' in attrs;this.hidden='hidden' in attrs;this.checked='checked' in attrs;
    this.classList={toggle:(name,yes)=>{const s=new Set((this.attrs.class||'').split(/\s+/).filter(Boolean));if(yes)s.add(name);else s.delete(name);this.attrs.class=[...s].join(' ');}};
    for(const child of children)this.append(typeof child==='string'?child:new Element(child.tag,child.attrs,child.children));
    if(tag==='select'){const options=this.children.filter(c=>c.tagName==='option');this.value=(options.find(c=>'selected' in c.attrs)||options[0]||{attrs:{}}).attrs.value||'';}
  }
  set className(v){this.attrs.class=v;}get className(){return this.attrs.class||'';}
  set textContent(v){this.replaceChildren(String(v));}get textContent(){return this.children.map(c=>typeof c==='string'?c:c.textContent).join('');}
  append(...nodes){for(const n of nodes){this.children.push(n);if(typeof n!=='string')n.parentElement=this;}}
  replaceChildren(...nodes){for(const c of this.children)if(typeof c!=='string')c.parentElement=null;this.children=[];this.append(...nodes);}
  setAttribute(k,v){this.attrs[k.toLowerCase()]=String(v);}getAttribute(k){return this.attrs[k.toLowerCase()] ?? null;}
  matches(selector){if(selector.startsWith('.'))return (this.attrs.class||'').split(/\s+/).includes(selector.slice(1));if(selector.startsWith('['))return selector.slice(1,-1) in this.attrs;return this.tagName===selector;}
  querySelectorAll(selector){let rows=[];for(const c of this.children)if(typeof c!=='string'){if(c.matches(selector))rows.push(c);rows.push(...c.querySelectorAll(selector));}return rows;}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  closest(selector){for(let n=this;n;n=n.parentElement)if(n.matches(selector))return n;return null;}
  addEventListener(name,handler){(this.events[name]||=[]).push(handler);}
  emit(name,event={}){for(const h of this.events[name]||[])h({target:this,preventDefault(){},...event});}
  setPointerCapture(id){this.capture=id;}
  click(){this.emit('click');}
  remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(c=>c!==this);this.parentElement=null;}
  focus(){this.focused=true;}
  scrollIntoView(){this.scrolled=true;}
  getBoundingClientRect(){return {left:this.attrs.id==='canvas-B'?800:0,top:0,width:800,height:500};}
}
if(require.main===module) {
let input='';process.stdin.on('data',c=>input+=c);process.stdin.on('end',()=>{
  const snapshot=JSON.parse(input),root=new Element('document',{},snapshot.tree);
  const find=(n,id)=>{if(n.attrs.id===id)return n;for(const c of n.children)if(typeof c!=='string'){const hit=find(c,id);if(hit)return hit;}return null;};
  root.getElementById=id=>find(root,id);root.createElement=tag=>new Element(tag);
  global.document=root;const logic=require(process.argv[2]);
  const get=id=>root.getElementById(id),a=get('canvas-A'),b=get('canvas-B');
  const boxes=()=>[a.getAttribute('viewBox'),b.getAttribute('viewBox')];
  const matrices=()=>[a.querySelector('.display-scene').getAttribute('transform'),b.querySelector('.display-scene').getAttribute('transform')];
  const choose=id=>get(id).emit('change');
  const output={initial_boxes:boxes(),initial_matrices:matrices()};
  const operation=a.querySelectorAll('.operation').find(n=>n.dataset.operation===snapshot.operation_id);
  const hit=operation.querySelector('.hit-geometry').children.find(n=>typeof n!=='string');
  a.emit('click',{target:hit});output.clicked_detail=get('operation-detail').textContent;
  get('station-controls').emit('click',{target:get('station-controls').querySelectorAll('[data-station-index]')[4]});
  output.stage_counts=[get('stage-count-A').textContent,get('stage-count-B').textContent];output.station_boxes=boxes();
  get('display-orientation').value='0';choose('display-orientation');output.original_matrices=matrices();output.original_boxes=boxes();
  get('display-orientation').value='270';choose('display-orientation');output.rotated_matrices=matrices();output.detail_after_rotation=get('operation-detail').textContent;
  get('show-regions').checked=true;choose('show-regions');get('region-filter').value=snapshot.region_id;choose('region-filter');
  output.visible_regions=a.querySelectorAll('.influence-region').filter(n=>n.getAttribute('visibility')==='visible').map(n=>n.dataset.region);
  get('difference-only').checked=true;choose('difference-only');
  output.unchanged_states=a.querySelectorAll('.operation').filter(n=>!snapshot.changed_ids.includes(n.dataset.operation)).map(n=>n.dataset.focus);
  get('fit-region').emit('click');output.fit_boxes=boxes();get('zoom-in').emit('click');output.zoom_boxes=boxes();
  a.emit('pointerdown',{button:0,pointerId:5,clientX:200,clientY:100});a.emit('pointermove',{pointerId:5,clientX:230,clientY:115});a.emit('pointerup',{pointerId:5});output.pan_boxes=boxes();
  get('reset-view').emit('click');output.reset_boxes=boxes();
  get('display-mode').value='CUMULATIVE';choose('display-mode');output.cumulative_current=a.querySelectorAll('.operation').filter(n=>n.dataset.status==='current').map(n=>n.dataset.operation);
  get('display-mode').value='FULL_PLAN_BY_STATION';choose('display-mode');output.full_states=[...new Set(a.querySelectorAll('.operation').map(n=>n.dataset.status))];
  const op2=a.querySelectorAll('.operation').find(n=>n.dataset.operation!==snapshot.operation_id);
  a.emit('keydown',{key:'Enter',target:op2});output.keyboard_detail=get('operation-detail').textContent;
  process.stdout.write(JSON.stringify(output));
});
}
module.exports={Element};
