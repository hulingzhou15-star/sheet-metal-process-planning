import { browserExecutable } from './browser_runtime.mjs';
import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';

const out=path.resolve('information_extract/outputs/three_d_state_v01/P001');
const testRoot=path.resolve('information_extract/.runtime/three_d_browser_test');
await fs.mkdir(path.join(testRoot,'downloads'),{recursive:true});
const browser=await chromium.launch({executablePath:browserExecutable(),headless:true,downloadsPath:path.join(testRoot,'downloads'),args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const page=await browser.newPage({acceptDownloads:true,viewport:{width:1600,height:1000},deviceScaleFactor:1});
const errors=[],checks=[];
page.on('pageerror',e=>errors.push(e.message));
try{
  await page.goto(pathToFileURL(path.join(out,'review.html')).href);
  await page.waitForFunction(()=>window.review3d?.getState().webgl,{timeout:30000});
  assert.deepEqual(errors,[]);checks.push('WebGL and offline page initialization');
  await page.locator('#filter').selectOption('ANCHOR');
  assert.equal(await page.locator('#list button').count(),7);checks.push('Seven base anchors in filter');
  await page.locator('[data-op="O186"]').click();
  assert.match(await page.locator('#opdetail').innerText(),/O186/);
  await page.locator('#view').selectOption('top');
  // Find a visible triangle of a known CAD face; projection, depth picking,
  // and native pointer clicking must agree on its actual face ID.
  const pick=await page.evaluate(()=>{
    const a=review3d.data.asset;
    const preferred=[471,148,26];
    for(const id of preferred){const f=a.faces[id-1];for(let i=0;i<Math.min(f.triangle_count,80);i++){
      const start=(f.triangle_start+i)*9,p=[0,1,2].map(k=>(a.mesh.positions[start+k]+a.mesh.positions[start+3+k]+a.mesh.positions[start+6+k])/3);
      const xy=review3d.projectPoint(p).map(v=>Math.floor(v)+.5);
      if([-2,0,2].every(dx=>[-2,0,2].every(dy=>review3d.pickFace(xy[0]+dx,xy[1]+dy)===id)))return {id,xy};
    }}return null;
  });
  assert.ok(pick,'Known visible CAD triangle must be pickable');
  const box=await page.locator('#solid').boundingBox();
  await page.mouse.click(box.x+pick.xy[0],box.y+pick.xy[1]);
  assert.ok((await page.evaluate(()=>review3d.getState().selected)).includes(pick.id));checks.push('Projection + depth hit test + native click agree');
  await page.mouse.click(box.x+pick.xy[0],box.y+pick.xy[1]);
  assert.equal((await page.evaluate(()=>review3d.getState().selected)).length,0);
  await page.mouse.click(box.x+pick.xy[0],box.y+pick.xy[1]);
  assert.equal((await page.evaluate(()=>review3d.getState().selected)).length,1);
  await page.locator('#neighbors').click();
  const expanded=await page.evaluate(()=>review3d.getState().selected);
  assert.ok(expanded.length>1);assert.equal(new Set(expanded).size,expanded.length);checks.push('Repeated click toggles; multiple faces stay unique');
  await page.locator('#note').fill('浏览器自动测试关联，仅用于测试上下文');
  await page.locator('#save').click();
  assert.equal(await page.evaluate(()=>review3d.getState().bindings.bindings.O186.status),'DRAFT');
  await page.reload();await page.waitForFunction(()=>window.review3d?.getState().webgl);
  await page.evaluate(()=>review3d.choose('O186'));
  assert.ok((await page.evaluate(()=>review3d.getState().selected)).includes(pick.id));checks.push('Isolated local draft survives reload');
  await page.locator('#confirm').click();
  assert.equal(await page.evaluate(()=>review3d.getState().bindings.bindings.O186.status),'CONFIRMED');
  const downloadEvent=page.waitForEvent('download');await page.locator('#export').click();const download=await downloadEvent;await download.saveAs(path.join(testRoot,'browser_test_bindings.json'));checks.push('Confirmed binding and JSON export');
  // Wrong-part imports must not replace existing data.
  const invalid=JSON.parse(await fs.readFile(path.join(testRoot,'browser_test_bindings.json'),'utf8'));invalid.part_id='P002';
  await page.locator('#file').setInputFiles({name:'wrong_part.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(invalid))});
  await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('导入失败'));
  assert.equal(await page.evaluate(()=>review3d.getState().bindings.part_id),'P001');checks.push('Foreign-part import rejected atomically');
  await page.locator('#hide').click();assert.ok((await page.evaluate(()=>review3d.getState().hidden)).includes(pick.id));
  await page.locator('#show').click();assert.equal((await page.evaluate(()=>review3d.getState().hidden)).length,0);checks.push('Hide and restore faces');
  await page.locator('#filter').selectOption('MISSING');assert.equal(await page.locator('#list button').count(),13);
  await page.locator('#filter').selectOption('ALL');await page.locator('#search').fill('O009');
  assert.equal(await page.locator('#list button').count(),1);await page.locator('#search').fill('');checks.push('Missing geometry and search filters');
  await page.locator('#filter').selectOption('PROPOSAL');assert.equal(await page.locator('#list button').count(),31);
  await page.locator('[data-op="O009"]').click();await page.locator('#proposals button').first().click();checks.push('Bend candidates filter and highlighting');
  const before=await page.evaluate(()=>review3d.getState().camera);
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.wheel(0,-250);
  const zoom=await page.evaluate(()=>review3d.getState().camera);assert.ok(zoom.scale>before.scale);
  await page.mouse.move(box.x+150,box.y+150);await page.mouse.down();await page.mouse.move(box.x+220,box.y+180,{steps:4});await page.mouse.up();
  const rotated=await page.evaluate(()=>review3d.getState().camera);assert.notEqual(rotated.yaw,zoom.yaw);
  await page.mouse.move(box.x+150,box.y+150);await page.mouse.down({button:'right'});await page.mouse.move(box.x+180,box.y+180,{steps:4});await page.mouse.up({button:'right'});
  assert.notDeepEqual((await page.evaluate(()=>review3d.getState().camera)).pan,rotated.pan);checks.push('Zoom, orbit and pan');
  await page.locator('#transparent').check();await page.locator('#edges').uncheck();await page.locator('#edges').check();await page.locator('#transparent').uncheck();checks.push('Transparency and boundary display');
  await page.locator('#view').selectOption('iso');await page.locator('#fit3').click();await page.locator('#fit2').click();
  await page.locator('#toast').waitFor({state:'hidden'});
  await page.screenshot({path:path.join(out,'review_preview.png'),fullPage:true});
  await page.locator('#focus2').click();await page.locator('#focus3').click();await page.locator('#transparent').check();
  await page.screenshot({path:path.join(out,'bend_local_proposal_preview.png'),fullPage:true});
  await page.locator('#transparent').uncheck();
  await page.locator('#filter').selectOption('ANCHOR');await page.locator('[data-op="O186"]').click();await page.locator('#view').selectOption('top');
  await page.screenshot({path:path.join(out,'base_registration_preview.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  const report={passed:true,checks,page_errors:errors,screenshots:['review_preview.png','base_registration_preview.png','bend_local_proposal_preview.png'],test_storage:'Ephemeral Playwright browser context; production user storage untouched; test export under .runtime/three_d_browser_test'};
  await fs.writeFile(path.join(out,'browser_qa.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}catch(e){await page.screenshot({path:path.join(testRoot,'browser_failure.png'),fullPage:true});console.error('Browser state',await page.evaluate(()=>review3d?.getState()));throw e;}finally{await browser.close();}
