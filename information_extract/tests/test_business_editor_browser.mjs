import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {browserExecutable} from './browser_runtime.mjs';

const browser=await chromium.launch({executablePath:browserExecutable(),headless:true});
try {
  const page=await browser.newPage({viewport:{width:1700,height:1100}}),errors=[];
  page.on('pageerror',e=>errors.push(String(e)));
  let saved=null;
  // Never save test edits into the user's real project.
  await page.route('**/api/save',async route=>{
    const data=route.request().postDataJSON();saved=structuredClone(data.project);
    saved.revision=data.expected_revision+1;await route.fulfill({json:{project:saved}});
  });
  await page.route('**/api/project',async route=>saved?route.fulfill({json:{project:saved}}):route.continue());
  await page.goto('http://127.0.0.1:8766/');
  await page.waitForFunction(()=>informationExtract.getState().statistics.business_geometry_operations===211);
  assert.equal(await page.locator('.operation-item').count(),224);
  const original=await page.evaluate(()=>structuredClone(informationExtract.getState().project));
  assert.equal(original.station_rows.length,7);
  for(const oid of ['O007','O009','O027','O196']){
    await page.locator(`[data-uid="P001_business_${oid}"]`).click();
    await page.click('#fit-current');
    assert.match(await page.locator('#draft-selection-count').innerText(),/已有业务几何/);
    assert.ok(await page.locator('#geometry-layer path').count()>0);
    assert.equal(await page.evaluate(()=>informationExtract.getState().selected.size),0);
  }
  await page.screenshot({path:'information_extract/outputs/p001_editor_import_v01/editor_existing_geometry.png'});
  await page.locator('#operation-name').fill('浏览器测试：只修改名称');
  await page.click('#apply-operation');
  await page.click('#save-project');
  await page.waitForFunction(()=>document.querySelector('#save-status').textContent.startsWith('已保存'));
  assert.deepEqual(saved.operations.map(o=>o.business_geometry),original.operations.map(o=>o.business_geometry));
  assert.deepEqual(saved.operations.map(o=>o.source_segment_ids),original.operations.map(o=>o.source_segment_ids));
  await page.click('#reload-project');
  await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('重新读取'));
  await page.locator('[data-uid="P001_business_O196"]').click();
  assert.equal(await page.locator('#operation-name').inputValue(),'浏览器测试：只修改名称');
  await page.locator('#missing-geometry-only').check();
  assert.deepEqual(await page.locator('.operation-title strong').allTextContents(),['O175','O026','O001']);
  await page.locator('[data-uid="P001_business_O001"]').click();
  await page.click('#fit-all');
  await page.screenshot({path:'information_extract/outputs/p001_editor_import_v01/editor_missing_geometry.png'});
  // Select a real displayed source segment and apply to a missing action, in-memory only.
  const point=await page.evaluate(()=>{
    const s=informationExtract.getState().drawing.segments.find(s=>s.segment_type==='LINE');
    const path=document.querySelector(`[data-id="${s.id}"]`),p=document.querySelector('#drawing').createSVGPoint();
    p.x=(s.start_x+s.end_x)/2;p.y=(s.start_y+s.end_y)/2;
    const q=p.matrixTransform(path.getScreenCTM());return {x:q.x,y:q.y};
  });
  await page.keyboard.down('Alt');await page.mouse.click(point.x,point.y);await page.keyboard.up('Alt');
  assert.equal(await page.evaluate(()=>informationExtract.getState().selected.size),1);
  await page.click('#apply-operation');
  await page.waitForFunction(()=>document.querySelectorAll('.operation-item').length===2);
  assert.deepEqual(await page.locator('.operation-title strong').allTextContents(),['O175','O026']);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:true,actions:224,businessGeometries:211,missing:['O001','O026','O175'],save:'intercepted; real project unchanged'}));
}finally{await browser.close();}
