const fs = require('fs');
const path = require('path');
const assert = require('assert');
const {pathToFileURL} = require('url');
const {chromium} = require('C:/Users/hulin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

(async()=>{
 const folder=path.resolve(process.argv[2]||'outputs/relative_proximity_trial');
 const result=JSON.parse(fs.readFileSync(path.join(folder,'trial_results.json'),'utf8'));
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1660,height:1100}}),errors=[],network=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(/^https?:/.test(request.url()))network.push(request.url());});
  await page.goto(pathToFileURL(path.join(folder,'proximity_trial.html')).href);
  assert.strictEqual(await page.locator('#anchor option').count(),46);
  assert.strictEqual(await page.locator('#anchor').inputValue(),'O184');
  assert.strictEqual(await page.locator('#rows tr').count(),1);
  assert.ok((await page.locator('#rows').textContent()).includes('O185'));
  assert.strictEqual(await page.locator('#canvas>g').getAttribute('transform'),'matrix(0 1 -1 0 0 0)');
  await page.screenshot({path:path.join(folder,'preview_O184.png'),fullPage:true});
  for(const anchor of result.anchors.filter(a=>a.status==='SUPPORTED')){
   await page.locator('#anchor').selectOption(anchor.anchor_id);
   for(const scenario of anchor.scenarios){
    await page.locator('#multiple').selectOption(String(scenario.size_multiple));
    assert.strictEqual(await page.locator('#rows tr').count(),scenario.passed_count);
   }
  }
  await page.locator('#anchor').selectOption('O066');await page.locator('#multiple').selectOption('3');
  const ids=await page.locator('#rows tr').evaluateAll(rows=>rows.map(r=>r.dataset.id));
  assert.deepStrictEqual(ids,['O067','O025','O200','O065']);
  await page.locator('#rows tr[data-id="O200"]').click();
  assert.ok((await page.locator('#detail').textContent()).includes('O200'));
  await page.locator('#canvas [data-operation="O065"]').dispatchEvent('click');
  assert.ok((await page.locator('#detail').textContent()).includes('O065'));
  const original=await page.locator('#canvas').getAttribute('viewBox');
  await page.locator('#zoom-in').click();assert.notStrictEqual(await page.locator('#canvas').getAttribute('viewBox'),original);
  await page.locator('#fit').click();assert.strictEqual(await page.locator('#canvas').getAttribute('viewBox'),original);
  const box=await page.locator('#canvas').boundingBox();
  await page.mouse.move(box.x+30,box.y+30);await page.mouse.down();await page.mouse.move(box.x+70,box.y+50);await page.mouse.up();
  assert.notStrictEqual(await page.locator('#canvas').getAttribute('viewBox'),original);
  await page.locator('#fit').click();
  await page.screenshot({path:path.join(folder,'preview_O066.png'),fullPage:true});
  await page.locator('#excluded').check();assert.strictEqual(await page.locator('#rows tr').count(),223);
  const before=await page.locator('#rows tr[data-id="O035"]').textContent();assert.ok(before.includes('范围外'));
  await page.locator('#excluded').uncheck();
  await page.locator('#anchor').selectOption('O031');
  assert.ok((await page.locator('#cuts').textContent()).includes('O196'));
  assert.strictEqual(await page.locator('#rows tr').count(),2);
  assert.deepStrictEqual(errors,[]);assert.deepStrictEqual(network,[]);
  const qa={passed:true,browser:'Microsoft Edge headless',anchor_scenarios_checked:138,
   o184_only_prehole:true,o066_confirmed_four:true,relative_cut_not_applied:true,
   all_targets_visible_when_requested:true,rotation_270:true,table_and_geometry_details:true,zoom_and_pan:true,
   page_errors:errors,http_requests:network};
  fs.writeFileSync(path.join(folder,'browser_qa.json'),JSON.stringify(qa,null,2));
  console.log(JSON.stringify(qa));
 }finally{await browser.close();}
})().catch(error=>{console.error(error.stack);process.exit(1);});
