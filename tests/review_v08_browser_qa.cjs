/* Headless browser QA on the shipped file:// page, using a fresh profile. */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const {pathToFileURL} = require('url');
const {chromium} = require('C:/Users/hulin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

(async()=> {
  const folder = path.resolve(process.argv[2]), privateFolder=path.join(folder,'internal');
  const design=JSON.parse(fs.readFileSync(path.join(privateFolder,'comparison_design.json'),'utf8'));
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try {
    const context=await browser.newContext({viewport:{width:1640,height:1150},acceptDownloads:true});
    const page=await context.newPage(), errors=[],requests=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{if(/^https?:/.test(request.url()))requests.push(request.url());});
    await page.goto(pathToFileURL(path.join(folder,'expert_facing/review_pack.html')).href);
    await page.waitForSelector('#canvas-A .operation');
    assert.strictEqual(await page.locator('[data-comparison-index]').count(),29);
    assert.strictEqual(await page.locator('#review-progress').textContent(),'已判断 0 / 29 对');
    assert.strictEqual(await page.locator('#display-orientation').inputValue(),'270');
    assert.strictEqual(await page.locator('#canvas-A .display-scene').getAttribute('transform'),'matrix(0 1 -1 0 0 0)');
    assert.ok(await page.locator('#canvas-A .part-outline-layer .part-outline').count());
    const localIndex=design.comparisons.findIndex(row=>row.group==='LOCAL');
    const local=design.comparisons[localIndex], firstId=local.comparison_id;
    await page.locator(`[data-comparison-index="${localIndex}"]`).click();
    await page.locator('[data-station-index="2"]').click();
    assert.ok((await page.locator('#stage-count-A').textContent()).includes('OP30'));
    assert.ok((await page.locator('#stage-count-B').textContent()).includes('OP30'));
    await page.locator('#difference-only').check();
    const op=await page.locator('#canvas-A .operation[data-focus="primary"]').first().getAttribute('data-operation');
    await page.locator(`#canvas-A .operation[data-operation="${op}"] .hit-geometry`).dispatchEvent('click');
    assert.ok((await page.locator('#operation-detail').textContent()).includes(op));
    const region=await page.locator('#region-filter option').nth(1).getAttribute('value');
    await page.locator('#region-filter').selectOption(region);
    await page.locator('#show-regions').check();
    await page.locator('#fit-region').click();
    assert.strictEqual(await page.locator('#canvas-A').getAttribute('viewBox'),await page.locator('#canvas-B').getAttribute('viewBox'));
    await page.locator('#zoom-in').click();
    await page.locator('#reset-view').click();
    await page.locator('#region-filter').selectOption('ALL');
    await page.locator('#show-regions').uncheck();
    await page.locator('#difference-only').uncheck();
    await page.screenshot({path:path.join(privateFolder,'browser_review.png'),fullPage:true});
    await page.locator(`[name="answer-${firstId}"][value="D"]`).check();
    await page.locator(`[name="confidence-${firstId}"]`).selectOption('5');
    await page.locator(`[name="reason-note-${firstId}"]`).fill('检查O203：包含逗号, 引号"和换行\n需复核。');
    const downloadPromise=page.waitForEvent('download');
    await page.locator('#review-export-csv').click();
    const download=await downloadPromise;
    await download.saveAs(path.join(privateFolder,'browser_export_check.csv'));
    await page.reload();
    await page.locator(`[data-comparison-index="${localIndex}"]`).click();
    assert.ok(await page.locator(`[name="answer-${firstId}"][value="D"]`).isChecked());
    assert.strictEqual(await page.locator(`[name="confidence-${firstId}"]`).inputValue(),'5');
    assert.ok((await page.locator(`[name="reason-note-${firstId}"]`).inputValue()).includes('O203'));
    const single=await context.newPage();
    await single.goto(pathToFileURL(path.join(folder,`expert_facing/plan_cards/${local.blind_plan_A}.html`)).href);
    assert.strictEqual(await single.locator('#display-orientation').inputValue(),'270');
    assert.ok(await single.locator('#canvas-A .operation').count()>0);
    assert.deepStrictEqual(errors,[]);assert.deepStrictEqual(requests,[]);
    const result={passed:true,browser:'Microsoft Edge headless',offline_file_protocol:true,comparisons:29,
      station_sync:true,operation_detail:true,region_fit:true,difference_mode:true,rotation_270:true,
      csv_download:true,draft_restored:true,single_plan:true,page_errors:errors,network_requests:requests,
      tested_comparison_id:firstId,download_filename:download.suggestedFilename()};
    fs.writeFileSync(path.join(privateFolder,'browser_qa.json'),JSON.stringify(result,null,2));
    process.stdout.write(JSON.stringify(result));
    await context.close();
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
