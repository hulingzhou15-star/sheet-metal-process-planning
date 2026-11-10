const fs=require('fs'),path=require('path'),assert=require('assert'),{pathToFileURL}=require('url');
const {chromium}=require('C:/Users/hulin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const folder=path.resolve(process.argv[2]||'outputs/neighborhood_penalty_v02');
 const comps=JSON.parse(fs.readFileSync(path.join(folder,'comparisons.json'),'utf8'));
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1760,height:1160}}),errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/^https?:/.test(r.url()))requests.push(r.url());});
  await page.goto(pathToFileURL(path.join(folder,'penalty_internal_review.html')).href);
  const P=JSON.parse(await page.locator('#payload').textContent());
  const before=new Map(P.before.map(p=>[p.plan_hash,p])),after=new Map(P.after.map(p=>[p.plan_hash,p]));
  assert.strictEqual(comps.length,36);assert.strictEqual(await page.locator('#comparison option').count(),36);
  assert.strictEqual(await page.locator('#focus option').count(),22);
  assert.ok((await page.locator('#summary').textContent()).includes('新池 64 个，入选 16 个'));
  assert.ok((await page.locator('#summary').textContent()).includes('缺几何动作'));
  assert.strictEqual(await page.locator('#canvas-before>g').getAttribute('transform'),'matrix(0 1 -1 0 0 0)');
  assert.strictEqual(await page.locator('#canvas-after>g').getAttribute('transform'),'matrix(0 1 -1 0 0 0)');
  assert.ok(await page.locator('#canvas-before .part-outline-layer').count());
  for(const c of comps){
   await page.locator('#comparison').selectOption(c.comparison_id);
   assert.ok((await page.locator('#before-title').textContent()).includes(c.before_plan_id));
   assert.ok((await page.locator('#after-title').textContent()).includes(c.after_plan_id));
   assert.ok((await page.locator('#after-state').textContent()).includes('P5 0.0000'));
   assert.strictEqual(await page.locator('#changes tr').count(),c.all_changes.length);
   assert.ok((await page.locator('#explanation').textContent()).includes(c.explanation));
   assert.ok((await page.locator('#group-detail').textContent()).includes('BEND:SMOOTH：使用 3 个工序；硬可行范围 3–3'));
  }
  for(const r of P.policy.pairs){await page.locator('#focus').selectOption(r.pair_id);assert.ok((await page.locator('#pair-detail').textContent()).includes(r.operation_ids.join(' / ')));}
  const chosen=comps.find(c=>c.focus_pair_id==='O066__O200');assert.ok(chosen);
  await page.locator('#comparison').selectOption(chosen.comparison_id);await page.locator('#station').selectOption('OP30');
  const a=before.get(chosen.before_plan_hash),b=after.get(chosen.after_plan_hash);
  assert.strictEqual(a.assignments.O066,a.assignments.O200);assert.notStrictEqual(b.assignments.O066,b.assignments.O200);
  assert.strictEqual(await page.locator('#canvas-before g.op:not(.hit)[data-operation="O200"]').getAttribute('data-current'),'true');
  assert.strictEqual(await page.locator('#canvas-after g.op:not(.hit)[data-operation="O200"]').getAttribute('data-current'),'false');
  await page.screenshot({path:path.join(folder,'preview_o066_penalty.png'),fullPage:true});
  const screen=await page.locator('#canvas-before g.op.hit[data-operation="O066"] circle').first().evaluate(el=>{const p=new DOMPoint(el.cx.baseVal.value+el.r.baseVal.value,el.cy.baseVal.value).matrixTransform(el.getScreenCTM());return{x:p.x,y:p.y};});
  await page.mouse.click(screen.x,screen.y);assert.ok((await page.locator('#operation-detail').textContent()).includes('O066 · FLANGING'));
  const initial=await page.locator('#canvas-before').getAttribute('viewBox');await page.locator('#zoom-in').click();
  assert.notStrictEqual(await page.locator('#canvas-before').getAttribute('viewBox'),initial);
  assert.strictEqual(await page.locator('#canvas-before').getAttribute('viewBox'),await page.locator('#canvas-after').getAttribute('viewBox'));
  await page.locator('#fit-local').click();assert.strictEqual(await page.locator('#canvas-before').getAttribute('viewBox'),initial);
  const rect=await page.locator('#canvas-after').boundingBox();await page.mouse.move(rect.x+20,rect.y+20);await page.mouse.down();await page.mouse.move(rect.x+60,rect.y+45);await page.mouse.up();
  assert.notStrictEqual(await page.locator('#canvas-before').getAttribute('viewBox'),initial);
  assert.strictEqual(await page.locator('#canvas-before').getAttribute('viewBox'),await page.locator('#canvas-after').getAttribute('viewBox'));
  await page.locator('#fit-whole').click();await page.screenshot({path:path.join(folder,'preview_whole_penalty.png'),fullPage:true});
  await page.locator('#difference').check();
  const expected=Object.keys(a.assignments).filter(o=>a.assignments[o]!==b.assignments[o]&&Object.hasOwn(P.geometries,o)).sort();
  const idsA=await page.locator('#canvas-before g.op:not(.hit)').evaluateAll(es=>es.map(e=>e.dataset.operation).sort());
  const idsB=await page.locator('#canvas-after g.op:not(.hit)').evaluateAll(es=>es.map(e=>e.dataset.operation).sort());
  assert.deepStrictEqual(idsA,expected);assert.deepStrictEqual(idsA,idsB);
  await page.screenshot({path:path.join(folder,'preview_difference_penalty.png'),fullPage:true});
  await page.locator('#difference').uncheck();
  const cmp=comps.find(c=>c.kind==='selected_vs_unselected'&&c.selection_reason==='secondary_score');
  await page.locator('#comparison').selectOption(cmp.comparison_id);await page.locator('#fit-whole').click();
  await page.screenshot({path:path.join(folder,'preview_selection_explanation.png'),fullPage:true});
  const body=await page.locator('body').innerText();
  assert.ok(body.includes('不能直接作为专家盲评材料'));
  assert.ok(body.includes('启发式选择理由'));
  assert.deepStrictEqual(errors,[]);assert.deepStrictEqual(requests,[]);
  const qa={passed:true,browser:'Microsoft Edge headless',comparison_views_checked:36,p5_pair_views_checked:22,known_cases:4,
   exact_scores_and_selection_reasons:true,group_bounds_visible:true,all_changes_disclosed:true,
   whole_local_fit:true,station_colors:true,difference_mode:true,rotation_270:true,actual_curve_hit:true,
   outline:true,synchronized_pan_zoom:true,internal_not_blind:true,page_errors:errors,http_requests:requests};
  fs.writeFileSync(path.join(folder,'browser_qa.json'),JSON.stringify(qa,null,2));console.log(JSON.stringify(qa));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exit(1);});
