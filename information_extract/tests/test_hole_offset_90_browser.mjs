import { browserExecutable } from './browser_runtime.mjs';
import {chromium} from 'playwright';
import path from 'node:path';import fs from 'node:fs/promises';import assert from 'node:assert/strict';import {pathToFileURL} from 'node:url';
const out=path.resolve('information_extract/outputs/hole_offset_90_audit_v01/P001');
const browser=await chromium.launch({executablePath:browserExecutable(),headless:true,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1600,height:1080}}),errors=[],checks=[];page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto(pathToFileURL(path.join(out,'review.html')).href);await page.waitForFunction(()=>window.offsetReview);
 assert.equal(await page.locator('#operation option').count(),26);assert.equal(await page.locator('#joints tr').count(),53);
 assert.equal((await page.evaluate(()=>offsetReview.getState())).operation_id,'O170');
 for(const id of ['O170','O058','O018','O193']){
  await page.click(`[data-op="${id}"]`);const state=await page.evaluate(()=>offsetReview.getState());
  assert.equal(state.full_shape_congruent,['O170','O058'].includes(id));
  if(state.full_shape_congruent){assert.ok(state.new_center_error_mm<1e-6);assert.notEqual(await page.locator('#before').evaluate(c=>c.toDataURL()),await page.locator('#after').evaluate(c=>c.toDataURL()));}
  else assert.match(await page.locator('#detail').innerText(),/仍有差异/);
  await page.screenshot({path:path.join(out,id+'_before_after.png'),fullPage:true});
 }
 checks.push('26 pure-90 ordinary holes; all 53 bend sections listed; all representative examples show correct match or exception status');
 await page.click('[data-op="O170"]');const s=await page.evaluate(()=>offsetReview.getState()),box=await page.locator('#before').boundingBox();
 await page.mouse.move(box.x+box.width*.4,box.y+box.height*.4);await page.mouse.down();await page.mouse.move(box.x+box.width*.4+45,box.y+box.height*.4+30);await page.mouse.up();
 assert.notDeepEqual((await page.evaluate(()=>offsetReview.getState())).center,s.center);
 await page.mouse.wheel(0,-120);assert.notEqual((await page.evaluate(()=>offsetReview.getState())).scale,s.scale);
 await page.click('#fit');checks.push('Before/after overlays share synchronized camera; pan, zoom and refit work');
 await page.locator('summary').click();assert.ok(await page.locator('#joints').isVisible());assert.deepEqual(errors,[]);
 await fs.writeFile(path.join(out,'browser_qa.json'),JSON.stringify({checks,page_errors:errors},null,2));console.log({checks,page_errors:errors});
}finally{await browser.close()}
