/* Exercise the real form, local-draft and download handlers on rendered HTML. */
const {Element}=require('./visual_ui_dom_harness.js');
const Answers=require('../src/visualization/review_answers.js');
let input='';process.stdin.on('data',c=>input+=c);process.stdin.on('end',async()=> {
  const source=JSON.parse(input),doc=new Element('document',{},source.tree);
  const find=(node,id)=>{if(node.attrs.id===id)return node;for(const c of node.children)if(typeof c!=='string'){const hit=find(c,id);if(hit)return hit;}return null;};
  doc.getElementById=id=>find(doc,id);doc.createElement=tag=>new Element(tag);doc.body=doc.querySelector('body');
  const data=JSON.parse(doc.getElementById('visual-data').textContent),memory=new Map();
  const signature=Answers.signature(data),key=Answers.storageKey(signature);
  if(source.saved)memory.set(key,JSON.stringify(source.saved));
  if(source.corrupt)memory.set(key,'broken-json');
  const storage={getItem(k){if(source.read_blocked)throw Error('blocked');return memory.get(k)||null;},
    setItem(k,v){if(source.write_blocked)throw Error('blocked');memory.set(k,v);}};
  let downloaded=null,downloadName=null,revoked=null,timer=null;
  const env={localStorage:storage,Blob,URL:{createObjectURL(blob){if(source.download_blocked)throw Error('blocked');downloaded=blob;return 'blob:test-result';},revokeObjectURL(url){revoked=url;}},
    setTimeout(callback){timer=callback;}};
  const create=doc.createElement;doc.createElement=tag=>{const node=create(tag);if(tag==='a')node.click=()=>{downloadName=node.download;};return node;};
  const controller=Answers.mount(data,doc,env),get=id=>doc.getElementById(id),forms=doc.querySelectorAll('[data-comparison-details]');
  forms.forEach((block,i)=>block.hidden=i!==0);
  const result={initial_rows:controller.collect(),initial_status:get('review-save-status').textContent,
    signature,key,blank_csv:Answers.csv(controller.collect())};
  const controls=block=>({radio:block.querySelectorAll('input'),confidence:block.querySelector('select'),note:block.querySelector('textarea')});
  if(source.fill!==false) {
    const first=controls(forms[0]);first.radio.forEach(r=>r.checked=r.value==='B');first.confidence.value='4';first.note.value=source.note;
    doc.emit('change',{target:first.radio[1]});doc.emit('input',{target:first.note});
    const second=controls(forms[1]);second.radio.forEach(r=>r.checked=r.value==='E');second.confidence.value='2';
    doc.emit('change',{target:second.confidence});
  }
  get('review-focus-form').emit('click');
  result.focused=controls(forms[0]).radio[0].focused===true;result.scrolled=forms[0].querySelector('.form').scrolled===true;
  result.rows=controller.collect();result.progress=get('review-progress').textContent;result.save_status=get('review-save-status').textContent;
  result.answered_nav=get('comparison-nav').querySelectorAll('[data-comparison-index]').filter(n=>(n.attrs.class||'').includes('review-answered')).map(n=>n.dataset.comparisonIndex);
  get('review-export-csv').emit('click');result.export_status=get('review-export-status').textContent;
  result.filename=downloadName;result.csv=downloaded?await downloaded.text():null;
  result.csv_bytes=downloaded?[...new Uint8Array(await downloaded.arrayBuffer()).slice(0,3)]:null;
  if(timer)timer();result.revoked=revoked;
  result.stored=memory.get(key)?JSON.parse(memory.get(key)):null;
  if(!source.read_blocked && !source.write_blocked && source.fill!==false) {
    for(const block of forms){const c=controls(block);c.radio.forEach(r=>r.checked=false);c.confidence.value='';c.note.value='';}
    result.restored_rows=Answers.mount(data,doc,env).collect();
  }
  const changed=JSON.parse(JSON.stringify(data)),op=Object.keys(changed.plans[data.comparisons[0].blind_plan_A].operations)[0];
  changed.plans[data.comparisons[0].blind_plan_A].operations[op].assigned_station='CHANGED';
  result.changed_signature=Answers.signature(changed);
  result.private_signature=Answers.signature({...data,comparisons:data.comparisons.map(p=>({...p,debug:{}}))});
  result.sanitized=Answers.rows(data.comparisons,{[data.comparisons[0].comparison_id]:{answer:'invalid',confidence:'7',reason_note:9}})[0];
  process.stdout.write(JSON.stringify(result));
});
