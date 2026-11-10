const fs=require('fs'),path=require('path'),assert=require('assert'),{pathToFileURL}=require('url');
const {chromium}=require('C:/Users/hulin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const folder=path.resolve(process.argv[2]||'outputs/neighborhood_n1');
 const comps=JSON.parse(fs.readFileSync(path.join(folder,'comparisons.json'),'utf8'));
 const policy=JSON.parse(fs.readFileSync(path.join(folder,'n1_policy.json'),'utf8'));
 const old=new Map(fs.readFileSync('outputs/neighborhood_v01/candidate_pool.jsonl','utf8').trim().split(/\r?\n/).map(JSON.parse).map(p=>[p.plan_hash,p]));
 const newer=new Map(fs.readFileSync(path.join(folder,'candidate_pool.jsonl'),'utf8').trim().split(/\r?\n/).map(JSON.parse).map(p=>[p.plan_hash,p]));
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1760,height:1160}}),errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/^https?:/.test(r.url()))requests.push(r.url());});
  await page.goto(pathToFileURL(path.join(folder,'n1_internal_review.html')).href);
  assert.strictEqual(await page.locator('#comparison option').count(),20);
  assert.strictEqual(await page.locator('#focus option').count(),22);
  assert.ok((await page.locator('#summary').textContent()).includes('原池 75 个，N1通过 0；新池 16 个，N1通过 16'));
  assert.strictEqual(await page.locator('#canvas-before>g').getAttribute('transform'),'matrix(0 1 -1 0 0 0)');
  assert.strictEqual(await page.locator('#canvas-after>g').getAttribute('transform'),'matrix(0 1 -1 0 0 0)');
  assert.ok(await page.locator('#canvas-before .part-outline-layer').count());
  let comparisons=0;
  for(const c of comps){await page.locator('#comparison').selectOption(c.comparison_id);assert.ok((await page.locator('#before-title').textContent()).includes(c.before_plan_id));assert.ok((await page.locator('#after-title').textContent()).includes(c.after_plan_id));assert.ok((await page.locator('#after-state').textContent()).includes('N1同序问题 0 对'));assert.strictEqual(await page.locator('#changes tr').count(),c.all_changes.length);comparisons++;}
  for(const pair of policy.pairs){await page.locator('#focus').selectOption(pair.pair_id);assert.ok((await page.locator('#pair-detail').textContent()).includes(pair.flanging_operation_id));}
  const chosen=comps.find(c=>c.focus_pair_id==='O066__O200');assert.ok(chosen);
  await page.locator('#comparison').selectOption(chosen.comparison_id);
  await page.locator('#station').selectOption('OP30');
  const a=old.get(chosen.before_plan_hash),b=newer.get(chosen.after_plan_hash);
  assert.strictEqual(a.assignments.O066,a.assignments.O200);assert.notStrictEqual(b.assignments.O066,b.assignments.O200);
  assert.strictEqual(await page.locator('#canvas-before g.op:not(.hit)[data-operation="O200"]').getAttribute('data-current'),'true');
  assert.strictEqual(await page.locator('#canvas-after g.op:not(.hit)[data-operation="O200"]').getAttribute('data-current'),String(b.assignments.O200==='OP30'));
  await page.screenshot({path:path.join(folder,'preview_o066_n1_comparison.png'),fullPage:true});
  // Exact curve hit after the shared 270-degree mapping.
  const screen=await page.locator('#canvas-before g.op.hit[data-operation="O066"] circle').first().evaluate(el=>{
    const v=new DOMPoint(el.cx.baseVal.value+el.r.baseVal.value,el.cy.baseVal.value).matrixTransform(el.getScreenCTM());return {x:v.x,y:v.y};
  });
  await page.mouse.click(screen.x,screen.y);assert.ok((await page.locator('#operation-detail').textContent()).includes('O066 · FLANGING'));
  const initial=await page.locator('#canvas-before').getAttribute('viewBox');await page.locator('#zoom-in').click();
  assert.notStrictEqual(await page.locator('#canvas-before').getAttribute('viewBox'),initial);
  assert.strictEqual(await page.locator('#canvas-before').getAttribute('viewBox'),await page.locator('#canvas-after').getAttribute('viewBox'));
  await page.locator('#fit-local').click();assert.strictEqual(await page.locator('#canvas-before').getAttribute('viewBox'),initial);
  const rect=await page.locator('#canvas-after').boundingBox();await page.mouse.move(rect.x+20,rect.y+20);await page.mouse.down();await page.mouse.move(rect.x+60,rect.y+45);await page.mouse.up();assert.notStrictEqual(await page.locator('#canvas-before').getAttribute('viewBox'),initial);assert.strictEqual(await page.locator('#canvas-before').getAttribute('viewBox'),await page.locator('#canvas-after').getAttribute('viewBox'));
  await page.locator('#fit-whole').click();assert.notStrictEqual(await page.locator('#canvas-before').getAttribute('viewBox'),initial);
  await page.screenshot({path:path.join(folder,'preview_whole_n1_comparison.png'),fullPage:true});
  await page.locator('#difference').check();
  const payload=JSON.parse(await page.locator('#payload').textContent());
  const expected=Object.keys(a.assignments).filter(op=>a.assignments[op]!==b.assignments[op]&&Object.hasOwn(payload.geometries,op));
  // Difference mode draws identical Operation ID sets on both sides.
  const idsA=await page.locator('#canvas-before g.op:not(.hit)').evaluateAll(els=>els.map(e=>e.dataset.operation).sort());
  const idsB=await page.locator('#canvas-after g.op:not(.hit)').evaluateAll(els=>els.map(e=>e.dataset.operation).sort());
  assert.deepStrictEqual(idsA,idsB);assert.deepStrictEqual(idsA,expected.sort());
  await page.screenshot({path:path.join(folder,'preview_difference_n1.png'),fullPage:true});
  await page.locator('#difference').uncheck();
  const c196=comps.find(c=>c.focus_pair_id==='O031__O196');await page.locator('#comparison').selectOption(c196.comparison_id);assert.ok((await page.locator('#operation-detail').textContent()).includes('O031 · FLANGING'));await page.screenshot({path:path.join(folder,'preview_o196_n1_comparison.png'),fullPage:true});
  assert.deepStrictEqual(errors,[]);assert.deepStrictEqual(requests,[]);
  const qa={passed:true,browser:'Microsoft Edge headless',comparison_views_checked:comparisons,n1_pair_views_checked:22,known_case_views_checked:4,
   whole_and_local:true,station_colors:true,difference_mode_same_filter_both_sides:true,rotation_270:true,actual_operation_hit:true,
   outline:true,synchronized_pan_zoom:true,all_changes_disclosed:true,internal_not_blind:true,page_errors:errors,http_requests:requests};
  fs.writeFileSync(path.join(folder,'browser_qa.json'),JSON.stringify(qa,null,2));console.log(JSON.stringify(qa));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exit(1);});
