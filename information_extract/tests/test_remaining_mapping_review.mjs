import { browserExecutable } from './browser_runtime.mjs';
import {chromium} from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url),core=require('../three_d/web/mapping_review_core.js');
const out=path.resolve('information_extract/outputs/remaining_mapping_review_v01/P001');
const downloadsPath=path.resolve('information_extract/.runtime/mapping_review_downloads');await fs.mkdir(downloadsPath,{recursive:true});
const browser=await chromium.launch({executablePath:browserExecutable(),headless:true,downloadsPath,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const context=await browser.newContext({viewport:{width:1600,height:1120},acceptDownloads:true});
const page=await context.newPage(),errors=[],checks=[];page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto(pathToFileURL(path.join(out,'review.html')).href);
 await page.waitForFunction(()=>window.mappingReview,{}, {timeout:60000});
 assert.equal(await page.locator('#list button').count(),42);
 assert.equal((await page.evaluate(()=>mappingReview.getState())).operation_id,'O166');
 const data=await page.evaluate(()=>({part_id:mappingReview.data.part_id,asset_id:mappingReview.data.asset_id,topology_fingerprint:mappingReview.data.topology_fingerprint,facts_fingerprint:mappingReview.data.facts_fingerprint,review_source_fingerprint:mappingReview.data.review_source_fingerprint,operations:mappingReview.data.operations.map(o=>({operation_id:o.operation_id,candidates:o.candidates})),faces:mappingReview.data.faces.map(f=>({face_id:f.face_id}))}));
 assert.equal(data.operations.length,42);assert.equal(data.faces.length,1247);
 checks.push('All 42 diagnosed operations and original 1,247 CAD faces loaded offline');
 const images=[];
 for(const [id,classification] of [['O166','部分几何吻合'],['O201','包含在较大'],['O196','板面归属未决']]){
  await page.click(`[data-example="${id}"]`);
  assert.match(await page.locator('#classification').innerText(),new RegExp(classification));
  const state=await page.evaluate(()=>mappingReview.getState());
  assert.equal(state.operation_id,id);assert.equal(state.has_3d_operation,id!=='O196');
  assert.equal(await page.locator('#unmapped').isVisible(),id==='O196');
  images.push(await page.locator('#scene').evaluate(c=>c.toDataURL()));
  await page.screenshot({path:path.join(out,id+'_review.png'),fullPage:true});
 }
 assert.equal(new Set(images).size,3);checks.push('Three representative views differ; unresolved O196 has no invented 3D operation');
 // Every operation switches to finite geometry and consistent candidate UI.
 for(const id of data.operations.map(o=>o.operation_id)){
  const state=await page.evaluate(id=>{mappingReview.choose(id);return mappingReview.getState();},id);
  assert.equal(state.operation_id,id);assert.ok(state.camera.center.every(Number.isFinite));assert.ok(Number.isFinite(state.camera.scale));
 }
 checks.push('All 42 action views switch with finite camera and geometry');
 await page.click('[data-example="O166"]');
 await page.selectOption('#judgment','SAME_FEATURE');await page.selectOption('#material','PUNCH_REMOVES_ALL');
 await page.fill('#note','QA 测试草稿：圆孔对应正确，定位偏差需要核对。');
 // Pick a real visible face through actual mouse events, not direct state mutation.
 const b=await page.locator('#overlay').boundingBox();
 for(const [x,y] of [[.65,.65],[.35,.35],[.5,.7],[.7,.5]]){
  await page.mouse.click(b.x+b.width*x,b.y+b.height*y);
  if((await page.evaluate(()=>mappingReview.getState())).selected_faces.length)break;
 }
 const picked=(await page.evaluate(()=>mappingReview.getState())).selected_faces;assert.ok(picked.length>0);
 await page.click('[data-example="O201"]');assert.equal(await page.locator('#note').inputValue(),'');
 await page.click('[data-example="O166"]');assert.match(await page.locator('#note').inputValue(),/QA 测试草稿/);
 await page.reload();await page.waitForFunction(()=>window.mappingReview);assert.match(await page.locator('#note').inputValue(),/QA 测试草稿/);
 assert.deepEqual((await page.evaluate(()=>mappingReview.getState())).selected_faces,picked);
 checks.push('Real mouse face picking, per-action isolation and local draft reload work');
 const downloadPromise=page.waitForEvent('download');await page.click('#export');const download=await downloadPromise;
 const raw=JSON.parse(await fs.readFile(await download.path(),'utf8'));assert.equal(raw.records.O166.judgment,'SAME_FEATURE');
 core.validate(raw,data);
 const wrong=structuredClone(raw);wrong.asset_id='wrong';assert.throws(()=>core.validate(wrong,data));
 const duplicate=structuredClone(raw);duplicate.records.O166.face_ids=[1,1];assert.throws(()=>core.validate(duplicate,data));
 const invalidWire=structuredClone(raw);invalidWire.records.O166.cad_wire_id='not-a-wire';assert.throws(()=>core.validate(invalidWire,data));
 const changed=structuredClone(raw);changed.review_source_fingerprint='old-source';assert.throws(()=>core.validate(changed,data));
 await page.selectOption('#judgment','UNSURE');page.once('dialog',dialog=>dialog.accept());
 await page.locator('#importFile').setInputFiles({name:'review.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(raw))});
 await page.waitForFunction(()=>document.getElementById('judgment').value==='SAME_FEATURE');
 await page.locator('#importFile').setInputFiles({name:'wrong.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(wrong))});
 await page.waitForFunction(()=>document.getElementById('toast').textContent.includes('导入失败'));
 assert.equal(await page.locator('#judgment').inputValue(),'SAME_FEATURE');
 checks.push('JSON download/import roundtrip; wrong CAD/source, duplicate faces and invalid wires rejected');
 await page.selectOption('#filter','unmapped');assert.equal(await page.locator('#list button').count(),5);
 await page.fill('#search','O196');assert.equal(await page.locator('#list button').count(),1);
 await page.fill('#search','');await page.selectOption('#filter','all');
 await page.click('#whole3');assert.equal((await page.evaluate(()=>mappingReview.getState())).local_mode,false);
 await page.click('#focus3');assert.equal((await page.evaluate(()=>mappingReview.getState())).local_mode,true);
 const before=await page.locator('#scene').evaluate(c=>c.toDataURL());const bb=await page.locator('#overlay').boundingBox();
 await page.mouse.move(bb.x+bb.width*.6,bb.y+bb.height*.5);await page.mouse.down();await page.mouse.move(bb.x+bb.width*.6+50,bb.y+bb.height*.5+30);await page.mouse.up();
 assert.notEqual(await page.locator('#scene').evaluate(c=>c.toDataURL()),before);
 checks.push('Filters, local/full CAD context and 3D camera controls work');
 assert.deepEqual(errors,[]);
 await fs.writeFile(path.join(out,'browser_qa.json'),JSON.stringify({checks,page_errors:errors,qa_drafts_are_isolated:true},null,2));
 console.log(JSON.stringify({checks,page_errors:errors},null,2));
}finally{await browser.close();}
