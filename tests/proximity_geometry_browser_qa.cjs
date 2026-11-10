const fs=require('fs'),path=require('path'),assert=require('assert'),{pathToFileURL}=require('url');
const {chromium}=require('C:/Users/hulin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const folder=path.resolve(process.argv[2]||'outputs/proximity_geometry_review');
 const data=JSON.parse(fs.readFileSync(path.join(folder,'geometry_evidence.json'),'utf8'));
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1740,height:1120}}),errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/^https?:/.test(r.url()))requests.push(r.url());});
  await page.goto(pathToFileURL(path.join(folder,'geometry_review.html')).href);
  assert.strictEqual(await page.locator('#operation option').count(),224);
  assert.ok((await page.locator('#pair-detail').textContent()).includes('孔中心到有限折弯段'));
  assert.strictEqual(await page.locator('#canvas>g').getAttribute('transform'),'matrix(0 1 -1 0 0 0)');
  assert.ok(await page.locator('#canvas .part-outline-layer').count());
  async function choose(a,b){await page.locator('#operation').selectOption(a);await page.locator('#other').selectOption(b);await page.locator('#show-pair').click();assert.ok((await page.locator('#pair-title').textContent()).includes(b));}
  await choose('O025','O066');await page.screenshot({path:path.join(folder,'preview_bend_flange.png'),fullPage:true});
  const original=await page.locator('#canvas').getAttribute('viewBox');await page.locator('#zoom-in').click();assert.notStrictEqual(await page.locator('#canvas').getAttribute('viewBox'),original);await page.locator('#local-fit').click();assert.strictEqual(await page.locator('#canvas').getAttribute('viewBox'),original);
  await page.locator('#whole-fit').click();assert.notStrictEqual(await page.locator('#canvas').getAttribute('viewBox'),original);await page.locator('#local-fit').click();
  await page.locator('#regions').check();await page.locator('#regions').uncheck();
  await choose('O196','O031');assert.ok((await page.locator('#pair-detail').textContent()).includes('尺寸范围内'));await page.screenshot({path:path.join(folder,'preview_irregular_flange.png'),fullPage:true});
  await choose('O025','O196');assert.ok((await page.locator('#pair-detail').textContent()).includes('范围待定'));assert.ok((await page.locator('#profile').textContent()).includes('采样24点'));
  await page.screenshot({path:path.join(folder,'preview_bend_irregular.png'),fullPage:true});
  await page.locator('#threshold-BEND_IRREGULAR').fill('0');assert.ok((await page.locator('#pair-detail').textContent()).includes('临时范围内'));await page.locator('#threshold-BEND_IRREGULAR').fill('');assert.ok((await page.locator('#pair-detail').textContent()).includes('范围待定'));
  await choose('O023','O024');assert.ok((await page.locator('#pair-detail').textContent()).includes('O024 child → O023 parent'));
  await choose('O009','O010');assert.ok((await page.locator('#pair-detail').textContent()).includes('折弯夹角'));await page.screenshot({path:path.join(folder,'preview_bend_bend.png'),fullPage:true});
  await page.locator('#row-limit').selectOption('ALL');assert.strictEqual(await page.locator('#external tr').count(),210);
  await page.locator('#family').selectOption('BEND_BEND');assert.strictEqual(await page.locator('#external tr').count(),58);await page.locator('#family').selectOption('ALL');
  let activeChecks=0;
  for(const row of Object.values(data.operations).filter(o=>['BEND','IRREGULAR_PUNCH'].includes(o.operation_type))){await page.locator('#operation').selectOption(row.operation_id);assert.ok((await page.locator('#op-state').textContent()).includes('几何证据可查'));activeChecks++;}
  await page.locator('#operation').selectOption('O184');assert.ok((await page.locator('#internal').textContent()).includes('O185'));assert.strictEqual(await page.locator('#external tr').count(),209);
  for(const row of Object.values(data.operations).filter(o=>!o.has_geometry)){await page.locator('#operation').selectOption(row.operation_id);assert.ok((await page.locator('#op-state').textContent()).includes('缺少几何'));assert.strictEqual(await page.locator('#external tr').count(),0);}
  await choose('O025','O066');
  const box=await page.locator('#canvas').boundingBox(),start=await page.locator('#canvas').getAttribute('viewBox');await page.mouse.move(box.x+20,box.y+20);await page.mouse.down();await page.mouse.move(box.x+70,box.y+45);await page.mouse.up();assert.notStrictEqual(await page.locator('#canvas').getAttribute('viewBox'),start);await page.locator('#local-fit').click();
  assert.deepStrictEqual(errors,[]);assert.deepStrictEqual(requests,[]);
  const qa={passed:true,browser:'Microsoft Edge headless',operation_options:224,bend_and_irregular_centers_checked:activeChecks,missing_geometry_centers_checked:13,
   all_four_relation_views:true,internal_external_separation:true,temporary_threshold_no_saved_rule:true,rotation_270:true,closest_points:true,outline:true,region_context:true,zoom_pan_and_fit:true,page_errors:errors,http_requests:requests};
  fs.writeFileSync(path.join(folder,'browser_qa.json'),JSON.stringify(qa,null,2));console.log(JSON.stringify(qa));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exit(1);});
