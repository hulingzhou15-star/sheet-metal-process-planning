import { browserExecutable } from './browser_runtime.mjs';
import {chromium} from 'playwright';
import path from 'node:path';import fs from 'node:fs/promises';import assert from 'node:assert/strict';import {pathToFileURL} from 'node:url';
const out=path.resolve('information_extract/outputs/finite_material_v02/P001');
const browser=await chromium.launch({executablePath:browserExecutable(),headless:true,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const context=await browser.newContext({viewport:{width:1680,height:1150}}),page=await context.newPage(),errors=[],checks=[];context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));
const state=()=>page.evaluate(()=>finiteMaterialReview.getState());
const blue=selector=>page.locator(selector).evaluate(c=>{let data;if(c.id==='scene'){finiteMaterialReview.redraw3();const g=c.getContext('webgl');data=new Uint8Array(c.width*c.height*4);g.readPixels(0,0,c.width,c.height,g.RGBA,g.UNSIGNED_BYTE,data);}else data=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let n=0,neutral=0;for(let i=0;i<data.length;i+=4)if(data[i+3]>200){if(data[i+2]>data[i]+25&&data[i+1]>data[i]+15&&data[i+2]>data[i+1]+8)n++;if(Math.max(data[i],data[i+1],data[i+2])-Math.min(data[i],data[i+1],data[i+2])<18&&data[i]>120)neutral++;}return {blue:n,neutral};});
try{
 await page.goto(pathToFileURL(path.join(out,'review.html')).href,{timeout:120000});await page.waitForFunction(()=>window.finiteMaterialReview,{}, {timeout:120000});
 assert.equal(await page.evaluate(()=>finiteMaterialReview.data.feature_highlights.operations.length),224);
 const expected=await page.evaluate(()=>Object.fromEntries(finiteMaterialReview.data.plans.map(p=>[p.plan_id,Object.fromEntries(['OP40','OP50','OP60'].map(sid=>[sid,finiteMaterialReview.data.model.bends.filter(b=>p.assignments[b.operation_id]===sid).map(b=>b.operation_id).sort()]))])));
 for(const [pid,stations] of Object.entries(expected)){await page.selectOption('#plan',pid);for(const [sid,ids] of Object.entries(stations)){await page.selectOption('#station',sid);const s=await state();assert.deepEqual(s.highlight.colored_band_operation_ids.sort(),ids,pid+' '+sid);}}
 checks.push('All 16 candidates highlight exactly their assigned bends in OP40/OP50/OP60; prior and future bends stay silver');
 await page.selectOption('#plan','NP2_001');await page.selectOption('#station','OP40');await page.selectOption('#phase','motion');await page.fill('#tau','37');await page.dispatchEvent('#tau','input');await page.click('#whole');
 let s=await state();const originalFractions=s.fractions,originalMaterial=s.material_id;
 assert.ok((await blue('#scene')).blue>0);assert.ok((await blue('#flat')).blue>0);await page.screenshot({path:path.join(out,'OP40_current_features_silver.png'),fullPage:true});
 await page.uncheck('#highlightCurrent');assert.equal((await blue('#scene')).blue,0);assert.equal((await blue('#flat')).blue,0);assert.ok((await blue('#scene')).neutral>500);
 s=await state();assert.deepEqual(s.fractions,originalFractions);assert.equal(s.material_id,originalMaterial);assert.equal(s.highlight.colored_band_operation_ids.length,0);
 await page.screenshot({path:path.join(out,'silver_without_highlights.png'),fullPage:true});await page.check('#highlightCurrent');
 checks.push('Pale blue fine lines appear only when enabled; neutral silver material remains visible and highlight toggling leaves geometry/progress unchanged');
 await page.selectOption('#station','OP20');await page.selectOption('#phase','entry');s=await state();assert.ok(!s.executed_removal_ids.includes('O193'));assert.ok(s.highlight.outline_operation_ids.includes('O193'));
 await page.selectOption('#phase','completed');assert.ok((await state()).executed_removal_ids.includes('O193'));await page.screenshot({path:path.join(out,'OP20_current_hole_contours.png'),fullPage:true});
 await page.selectOption('#operation','O193');await page.click('#focus');assert.equal((await state()).highlight.selected_operation_id,'O193');const selectedFractions=(await state()).fractions;await page.click('#clearSelection');assert.equal((await state()).highlight.selected_operation_id,null);assert.deepEqual((await state()).fractions,selectedFractions);
 const source=await page.evaluate(()=>finiteMaterialReview.data.feature_highlights.operations.find(o=>o.operation_id==='O193'));
 assert.ok(source.parts.some(p=>p.component.component_id==='P408_682'));assert.ok(source.parts.some(p=>p.component.component_id==='P273_302'));
 const unknown=await page.evaluate(()=>finiteMaterialReview.data.feature_highlights.operations.filter(o=>o.display_kind==='OUTSIDE_SUPPORTED_REFERENCE').map(o=>o.operation_id));assert.deepEqual(unknown,['O196','O197','O198','O199','O200']);
 checks.push('Punch contours show station assignment without prematurely cutting entry geometry; manual selection clears without changing progress; C-shaped outlines respect multiple material owners and unsupported holes remain explicit');
 await page.selectOption('#station','OP30');await page.click('#featureSummary');assert.match(await page.locator('#featureList').innerText(),/仅定位轮廓，局部实体未建模/);
 await page.selectOption('#station','OP70');assert.equal((await state()).highlight.colored_band_operation_ids.length,0);assert.equal((await state()).highlight.current_operation_ids.length,0);
 checks.push('Unformed local features are labeled as positioning outlines; empty OP70 has no current-stage highlights');
 await page.selectOption('#station','OP40');const popupPromise=page.waitForEvent('popup');await page.click('#popout3');const popup=await popupPromise;await popup.waitForSelector('#scene');const before=await popup.evaluate(()=>opener.finiteMaterialReview.capture3D());await page.selectOption('#station','OP50');assert.match(await popup.locator('#popupStatus').innerText(),/OP50/);assert.notEqual(await popup.evaluate(()=>opener.finiteMaterialReview.capture3D()),before);await popup.close();await page.waitForSelector('#scene');
 checks.push('Independent window follows stage highlight changes and restores normally');
 assert.deepEqual(errors,[]);await fs.writeFile(path.join(out,'highlight_browser_qa.json'),JSON.stringify({checks,page_errors:errors},null,2));console.log({checks,page_errors:errors});
}finally{await browser.close()}
