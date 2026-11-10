import { browserExecutable } from './browser_runtime.mjs';
import {chromium} from 'playwright';
import path from 'node:path';import fs from 'node:fs/promises';import assert from 'node:assert/strict';import {pathToFileURL} from 'node:url';
const out=path.resolve('information_extract/outputs/local_forming_v03/P001');
const browser=await chromium.launch({executablePath:browserExecutable(),headless:true,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1680,height:1150}}),errors=[],checks=[];page.on('pageerror',e=>errors.push(e.message));
const state=()=>page.evaluate(()=>finiteMaterialReview.getState());
try{
 await page.goto(pathToFileURL(path.join(out,'review.html')).href,{timeout:120000});await page.waitForFunction(()=>window.finiteMaterialReview,{}, {timeout:120000});
 assert.equal(await page.locator('#operation option').count(),150);assert.equal(await page.locator('#plan option').count(),16);
 for(const oid of ['O196','O197','O198','O199','O200']){
  await page.selectOption('#operation',oid);await page.click('#beforeFeature');assert.ok(!(await state()).executed_removal_ids.includes(oid));const before=await page.evaluate(()=>finiteMaterialReview.capture3D());
  await page.click('#afterFeature');assert.ok((await state()).executed_removal_ids.includes(oid));assert.notEqual(await page.evaluate(()=>finiteMaterialReview.capture3D()),before);
  await page.selectOption('#comparison','step');assert.match(await page.locator('#selectedInfo').innerText(),/STEP 成品位置/);if(oid==='O196')await page.screenshot({path:path.join(out,'O196_STEP.png'),fullPage:true});await page.selectOption('#comparison','stage');
 }
 checks.push('All five restored holes have visible before/after states and selectable split contours');
 await page.selectOption('#operation','O196');await page.click('#beforeFeature');await page.click('#topView');await page.screenshot({path:path.join(out,'O196_before.png'),fullPage:true});await page.click('#afterFeature');await page.screenshot({path:path.join(out,'O196_after.png'),fullPage:true});
 for(const oid of ['O027','O028','O029','O030','O011','O021','O063','O155']){
  await page.selectOption('#operation',oid);await page.click('#beforeFeature');assert.ok(!(await state()).executed_forming_ids.includes(oid));const before=await page.evaluate(()=>finiteMaterialReview.capture3D());
  await page.click('#afterFeature');assert.ok((await state()).executed_forming_ids.includes(oid));assert.notEqual(await page.evaluate(()=>finiteMaterialReview.capture3D()),before);
  await page.selectOption('#comparison','step');assert.equal((await state()).comparison,'step');assert.match(await page.locator('#selectedInfo').innerText(),/STEP/);await page.click('#normalView');
  await page.selectOption('#comparison','stage');
 }
 checks.push('Four bridges and representative flange directions/radii switch real end states and show STEP local comparisons');
 await page.selectOption('#operation','O027');await page.click('#afterFeature');await page.selectOption('#view','iso');await page.screenshot({path:path.join(out,'O027_formed.png'),fullPage:true});
 await page.selectOption('#comparison','step');await page.screenshot({path:path.join(out,'O027_STEP.png'),fullPage:true});
 await page.selectOption('#operation','O021');await page.click('#afterFeature');await page.selectOption('#view','iso');await page.screenshot({path:path.join(out,'O021_formed.png'),fullPage:true});
 await page.selectOption('#comparison','step');await page.screenshot({path:path.join(out,'O021_STEP.png'),fullPage:true});await page.selectOption('#comparison','stage');
 await page.selectOption('#phase','motion');await page.fill('#tau','37');await page.dispatchEvent('#tau','input');assert.equal((await state()).tau,.37);assert.ok(!(await state()).executed_forming_ids.includes('O021'));assert.equal(await page.locator('#trajectoryNotice').isVisible(),true);
 checks.push('Unknown simultaneous local process is disclosed; no premature forming at intermediate progress');
 const witnesses=JSON.parse(await fs.readFile(path.join(out,'mapping_samples.json'),'utf8'));
 await page.selectOption('#plan',witnesses.plan_id);await page.selectOption('#station',witnesses.station_id);await page.selectOption('#phase','motion');await page.fill('#tau','37');await page.dispatchEvent('#tau','input');
 const points=await page.evaluate(samples=>samples.map(s=>s.flat_points.map(p=>finiteMaterialReview.mapPoint(s.component,p))),witnesses.samples);let maxError=0;
 for(let i=0;i<points.length;i++)for(let j=0;j<3;j++)for(let k=0;k<3;k++)maxError=Math.max(maxError,Math.abs(points[i][j][k]-witnesses.samples[i].world_points[j][k]));assert.ok(maxError<1e-7);
 const gpu=await page.evaluate(samples=>finiteMaterialReview.verifyGpuPoints(samples),witnesses.samples);assert.ok(gpu.max_encoded_error_mm<.001,JSON.stringify(gpu));
 checks.push(`65 local/restored components agree in Python/JS and the GPU; JS error ${maxError} mm`);
 for(const pid of await page.locator('#plan option').evaluateAll(options=>options.map(o=>o.value))){await page.selectOption('#plan',pid);assert.equal((await state()).plan_id,pid);}
 await page.selectOption('#plan','NP2_001');await page.selectOption('#station','OP70');await page.selectOption('#phase','completed');assert.equal((await state()).executed_forming_ids.length,50);assert.equal((await state()).executed_removal_ids.length,100);
 checks.push('All 16 candidates selectable; final state contains 100 cut and 50 local forming events');
 await page.click('#whole');let next=page.waitForEvent('popup');await page.click('#popout3');const popup=await next;await popup.waitForSelector('#scene');await popup.click('#topView');assert.equal((await state()).camera.pitch,Math.PI/2);await popup.click('#normalView');await popup.click('#popupReturn');await page.waitForSelector('#scene');assert.equal((await state()).popouts.three,false);
 next=page.waitForEvent('popup');await page.click('#popout2');const two=await next;await two.waitForSelector('#flat');await two.close();await page.waitForSelector('#flat');
 assert.equal((await state()).display_quality.antialias,true);assert.equal((await state()).display_quality.mode,'clear');assert.equal((await state()).display_quality.stroke_css_px,1);
 checks.push('2D/3D popouts, normal/top view, antialiasing, clear default and thin highlights retained');
 assert.deepEqual(errors,[]);await fs.writeFile(path.join(out,'browser_qa.json'),JSON.stringify({checks,page_errors:errors},null,2));console.log({checks,page_errors:errors});
}finally{await browser.close()}
