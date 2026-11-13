import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import fs from 'node:fs';
const browser=await chromium.launch({channel:'msedge',headless:true});let checks=[];
try {
 for(const port of [8767,8768,8769]){
  const p=await browser.newPage({viewport:{width:1600,height:1100}});let errors=[];p.on('pageerror',e=>errors.push(String(e)));
  await p.route('**/api/save',()=>{throw new Error('Browser smoke must not save actual annotations');});
  await p.goto(`http://127.0.0.1:${port}/`);await p.waitForFunction(()=>Object.keys(informationExtract.getState().statuses).length>0);
  await p.check('#only-unhandled');assert.equal(await p.locator('#source-layer .bound').first().evaluate(e=>getComputedStyle(e).opacity),'0.2');
  if(port===8769)assert.equal(await p.locator('#source-layer .unhandled').first().evaluate(e=>getComputedStyle(e).opacity),'1');
  await p.uncheck('#only-unhandled');await p.locator('.operation-item').first().click();await p.check('#only-unhandled');assert.equal(await p.locator('#source-layer .selected').first().evaluate(e=>getComputedStyle(e).opacity),'1');
  await p.click('#fit-current');const local=await p.locator('#drawing').getAttribute('viewBox');await p.click('#fit-all');assert.notEqual(await p.locator('#drawing').getAttribute('viewBox'),local);
  await p.click('#view-source');assert.equal(await p.locator('#drawing').getAttribute('viewBox'),local);
  await p.click('#clear-selection');assert.match(await p.locator('#selection-status').innerText(),/已选 0/);await p.click('#undo');assert.doesNotMatch(await p.locator('#selection-status').innerText(),/已选 0/);await p.click('#redo');assert.match(await p.locator('#selection-status').innerText(),/已选 0/);await p.click('#undo');
  await p.fill('#operation-search','O001');assert.ok(await p.locator('.operation-item').count()>0);await p.fill('#operation-search','no-such-action');assert.equal(await p.locator('.operation-item').count(),0);await p.fill('#operation-search','');
  for(const tool of ['pan','box','select']){await p.click(`[data-tool="${tool}"]`);assert.match(await p.locator(`[data-tool="${tool}"]`).getAttribute('class'),/active/);}
  await p.selectOption('#orientation','0');assert.equal(await p.locator('#material-layer').getAttribute('transform'),'matrix(1 0 0 -1 0 0)');await p.selectOption('#orientation','270');
  await p.uncheck('#show-background');if(port!==8769)assert.equal(await p.locator('#source-layer .ignored').first().evaluate(e=>getComputedStyle(e).display),'none');await p.check('#show-background');
  await p.click('#validate-project');await p.waitForSelector('#report-panel',{state:'visible'});await p.click('#close-report');assert.equal(await p.locator('#report-panel').isVisible(),false);
  for(const id of ['import-project','import-operations','import-workbook']){const chooser=p.waitForEvent('filechooser');await p.click('#'+id);await chooser;}
  const download=p.waitForEvent('download');await p.click('#backup-project');await download;
  await p.click('#new-operation');await p.click('#restore-selection');assert.match(await p.locator('#toast').innerText(),/请先选择/);await p.click('#exclude-absorption');assert.match(await p.locator('#toast').innerText(),/请先选择/);
  await p.click('#compile-geometry');assert.match(await p.locator('#toast').innerText(),/先完成整图/);
  await p.click('#reload-project');await p.waitForFunction(()=>document.querySelector('#toast').textContent.includes('重新读取'));
  assert.deepEqual(errors,[]);checks.push({port,result:'passed',stats:await p.evaluate(()=>informationExtract.getState().statistics)});await p.close();
 }
 fs.writeFileSync('information_extract/outputs/editor_controls_qa_20261111.json',JSON.stringify(checks,null,2));console.log(JSON.stringify(checks));
}finally{await browser.close();}
