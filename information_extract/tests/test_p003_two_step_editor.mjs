import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {browserExecutable} from './browser_runtime.mjs';
const browser=await chromium.launch({executablePath:browserExecutable(),headless:true});
try{
 const page=await browser.newPage({viewport:{width:1700,height:1100}}),errors=[];
 page.on('pageerror',e=>errors.push(String(e)));
 await page.goto('http://127.0.0.1:8768/');
 await page.waitForFunction(()=>window.informationExtract?.getState().project?.operations?.length===120);
 for(const [oid,side,angle,station] of [['O069','SMOOTH','135','OP40'],['O120','ROUGH','45','OP50']]){
  await page.locator('#operation-search').fill(oid);await page.locator('.operation-item').click();
  assert.equal(await page.locator('#operation-name').inputValue(),'光面一般折弯4');
  assert.equal(await page.locator('#surface-side').inputValue(),side);
  assert.equal(await page.locator('#bend-angle').inputValue(),angle);
  assert.equal(await page.locator('#expert-station').inputValue(),station);
  assert.equal(await page.locator('#binding-warning').isVisible(),true);
  assert.equal(await page.locator('#shared-confirmed').isChecked(),true);
  if(oid==='O120')assert.equal(await page.locator('#predecessors').inputValue(),'O069');
 }
 const ops=await page.evaluate(()=>informationExtract.getState().project.operations);
 const by=Object.fromEntries(ops.map(o=>[o.operation_id,o]));
 assert.deepEqual(by.O069.source_segment_ids,by.O120.source_segment_ids);
 assert.equal(by.O069.source_segment_ids.length,6);
 assert.deepEqual(by.O069.shared_source_segment_ids,by.O120.shared_source_segment_ids);
 assert.equal(by.O070.cad_parent_operation_id,'O069');
 await page.click('#fit-current');
 await page.screenshot({path:'information_extract/outputs/p003_two_step_bend_20261112/O120_editor.png'});
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,records:120,sharedSegments:6,errors,mode:'read-only'}));
}finally{await browser.close();}
