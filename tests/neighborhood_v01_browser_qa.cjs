const fs=require('fs'),path=require('path'),assert=require('assert'),{pathToFileURL}=require('url');
const {chromium}=require('C:/Users/hulin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const folder=path.resolve(process.argv[2]||'outputs/neighborhood_v01');
 const run=JSON.parse(fs.readFileSync(path.join(folder,'candidate_exploration.json'),'utf8'));
 const known=JSON.parse(fs.readFileSync(path.join(folder,'known_case_audit.json'),'utf8'));
 const data=JSON.parse(fs.readFileSync(path.join(folder,'neighborhood_context.json'),'utf8'));
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1700,height:1120}}),errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/^https?:/.test(r.url()))requests.push(r.url());});
  await page.goto(pathToFileURL(path.join(folder,'neighborhood_review.html')).href);
  assert.strictEqual(await page.locator('#operation option').count(),224);
  assert.strictEqual(await page.locator('#scenario option').count(),5);
  assert.strictEqual(await page.locator('#operation').inputValue(),'O066');
  assert.ok((await page.locator('#internal').textContent()).includes('O067'));
  assert.strictEqual(await page.locator('#external tr').count(),3);
  assert.strictEqual(await page.locator('#canvas-base>g').getAttribute('transform'),'matrix(0 1 -1 0 0 0)');
  assert.ok(await page.locator('#canvas-base .part-outline-layer').count());
  await page.screenshot({path:path.join(folder,'preview_o066_neighborhood.png'),fullPage:true});
  const initial=await page.locator('#canvas-base').getAttribute('viewBox');
  await page.locator('#zoom-in').click();assert.notStrictEqual(await page.locator('#canvas-base').getAttribute('viewBox'),initial);
  await page.locator('#fit-local').click();assert.strictEqual(await page.locator('#canvas-base').getAttribute('viewBox'),initial);
  // Hit geometry has exactly the same transformed parent as visible geometry.
  await page.locator('#operation').selectOption('O065');await page.locator('#fit-all').click();
  const point=await page.locator('#canvas-base g.op.hit[data-operation="O066"] circle').first().evaluate(el=>{
   const p=new DOMPoint(el.cx.baseVal.value+el.r.baseVal.value,el.cy.baseVal.value);const v=p.matrixTransform(el.getScreenCTM());return {x:v.x,y:v.y};
  });
  await page.mouse.click(point.x,point.y);assert.strictEqual(await page.locator('#operation').inputValue(),'O066');
  let checked=0;
  for(const op of Object.keys(data.operations)){await page.locator('#operation').selectOption(op);assert.ok((await page.locator('#op-state').textContent()).includes(op));checked++;}
  await page.locator('#operation').selectOption('O184');assert.strictEqual(await page.locator('#external tr').count(),0);assert.ok((await page.locator('#internal').textContent()).includes('O185'));
  await page.locator('#operation').selectOption('O186');assert.ok((await page.locator('#op-state').textContent()).includes('不主动凑近邻'));
  await page.locator('#operation').selectOption('O001');assert.ok((await page.locator('#op-state').textContent()).includes('缺少几何'));
  for(const scenario of ['size_2','size_4','absolute_5','absolute_15','primary']){await page.locator('#scenario').selectOption(scenario);await page.locator('#operation').selectOption('O025');await page.locator('#limit').selectOption('ALL');assert.ok(await page.locator('#external tr').count());}
  await page.screenshot({path:path.join(folder,'preview_bend_neighborhood.png'),fullPage:true});
  await page.locator('#operation').selectOption('O196');await page.screenshot({path:path.join(folder,'preview_irregular_neighborhood.png'),fullPage:true});
  const t=run.trials.find(t=>t.alternative);assert.ok(t);
  await page.locator('#trial').selectOption(t.trial_id);assert.ok(await page.locator('#alt-drawing').isVisible());
  assert.strictEqual(await page.locator('#canvas-base').getAttribute('viewBox'),await page.locator('#canvas-alt').getAttribute('viewBox'));
  await page.locator('#station').selectOption('OP30');
  const visible=await page.locator('#canvas-alt g.op:not(.hit)').evaluateAll(els=>els.map(el=>el.dataset.operation));
  assert.ok(visible.every(op=>t.alternative.assignments[op]==='OP30'));
  await page.locator('#station').selectOption('ALL');
  const rect=await page.locator('#canvas-base').boundingBox(),before=await page.locator('#canvas-base').getAttribute('viewBox');
  await page.mouse.move(rect.x+20,rect.y+20);await page.mouse.down();await page.mouse.move(rect.x+65,rect.y+50);await page.mouse.up();
  assert.notStrictEqual(await page.locator('#canvas-base').getAttribute('viewBox'),before);
  assert.strictEqual(await page.locator('#canvas-base').getAttribute('viewBox'),await page.locator('#canvas-alt').getAttribute('viewBox'));
  await page.goto(pathToFileURL(path.join(folder,'known_cases/neighborhood_review.html')).href);
  let knownChecks=0;
  for(const c of known.cases){const t=known.trials.find(t=>t.pair_id===c.pair_id&&t.alternative);assert.ok(t);await page.locator('#trial').selectOption(t.trial_id);assert.ok((await page.locator('#trial-detail').textContent()).includes('基础同序 → 目标分序'));assert.ok(await page.locator('#alt-drawing').isVisible());knownChecks++;}
  await page.locator('#trial').selectOption(known.trials.find(t=>t.pair_id==='O066__O200'&&t.alternative).trial_id);
  await page.locator('#station').selectOption('OP30');await page.screenshot({path:path.join(folder,'preview_known_o066_separation.png'),fullPage:true});
  assert.deepStrictEqual(errors,[]);assert.deepStrictEqual(requests,[]);
  const qa={passed:true,browser:'Microsoft Edge headless',operation_centers_checked:checked,sensitivity_scenarios_checked:5,known_same_station_cases_checked:knownChecks,
   internal_external_separation:true,zero_external_match:true,missing_vs_passive_states:true,rotation_270:true,actual_hit_click:true,
   outline:true,station_filter:true,synchronized_comparison:true,zoom_pan_fit:true,internal_page_not_blind_pack:true,page_errors:errors,http_requests:requests};
  fs.writeFileSync(path.join(folder,'browser_qa.json'),JSON.stringify(qa,null,2));console.log(JSON.stringify(qa));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exit(1);});
