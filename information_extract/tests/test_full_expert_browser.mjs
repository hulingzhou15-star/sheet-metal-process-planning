import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {browserExecutable} from './browser_runtime.mjs';
const browser=await chromium.launch({executablePath:browserExecutable(),headless:true});
try{
 for(const [part,total,pending] of [['p003',120,0],['p004',77,15]]){
  const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(String(e)));
  await page.goto(`http://127.0.0.1:8770/${part}_expert_dxf_v01/review.html`);
  assert.equal(await page.locator('#op option').count(),pending);
  if(part==='p003')assert.match(await page.locator('#summary').innerText(),/OP70仅NONE（0动作）/);
  await page.click('#whole');await page.click('#local');
  await page.selectOption('#filter','all');assert.equal(await page.locator('#op option').count(),total);
  await page.selectOption('#op',part==='p003'?'O069':'O002');
  if(part==='p003'){
   assert.match(await page.locator('#detail').innerText(),/用户确认的分步折弯/);
   assert.match(await page.locator('#detail').innerText(),/135°/);
   await page.selectOption('#op','O120');
   assert.match(await page.locator('#detail').innerText(),/OP50/);
   assert.match(await page.locator('#detail').innerText(),/累计90°/);
   for(const oid of ['O071','O072','O073','O074']){
    await page.selectOption('#op',oid);
    assert.match(await page.locator('#rightTitle').innerText(),/原窗口7 → OP60/);
    assert.match(await page.locator('#detail').innerText(),/横移10.400 mm/);
   }
  }
  await page.screenshot({path:`information_extract/outputs/${part}_expert_dxf_v01/browser_review.png`});
  assert.deepEqual(errors,[]);console.log(JSON.stringify({part,total,pending,errors}));await page.close();
 }
}finally{await browser.close();}
