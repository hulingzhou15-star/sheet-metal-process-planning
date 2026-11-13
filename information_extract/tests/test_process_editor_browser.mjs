import assert from 'node:assert/strict';
import fs from 'node:fs';
import {chromium} from 'playwright';
import {browserExecutable} from './browser_runtime.mjs';

const browser=await chromium.launch({executablePath:browserExecutable(),headless:true});
const results=[];
try {
  for(const port of [8767,8768,8769]){
    const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];
    page.on('pageerror',e=>errors.push(String(e)));
    let saved=null;
    // Exercise browser save/reload without writing the user's live annotations.
    // Actual on-disk roundtrip is tested by ProcessApiTests on a temp project.
    await page.route('**/api/save',async route=>{const data=route.request().postDataJSON();saved=structuredClone(data.project);saved.revision=data.expected_revision+1;await route.fulfill({json:{project:saved}});});
    await page.route('**/api/project',async route=>saved?route.fulfill({json:{project:saved}}):route.continue());
    await page.goto(`http://127.0.0.1:${port}/`);
    await page.waitForFunction(()=>informationExtract.getState().statistics.total>0);
    const original=await page.evaluate(()=>structuredClone(informationExtract.getState().project));
    await page.click('#edit-stations');
    if(port===8767){
      await page.click('#load-station-defaults');
      await page.waitForFunction(()=>document.querySelectorAll('.station-card').length===10);
      assert.equal(await page.locator('[data-capability="PUNCH"]:checked').count(),4);
      assert.equal(await page.locator('[data-capability="CIRCLE_PUNCH"], [data-capability="TRIM"]').count(),0);
      await page.click('#cancel-stations');
      assert.equal(await page.evaluate(()=>informationExtract.getState().project.station_rows.length),0);
      await page.click('#edit-stations');
    }
    await page.click('#add-station');
    await page.locator('.station-card [data-capability="PUNCH"]').check();
    await page.locator('.station-card [data-capability="NONE"]').check();
    assert.equal(await page.locator('.station-card [data-capability="PUNCH"]').isChecked(),false);
    for(const key of ['PUNCH','FLANGING','BEND','DRAW_FORM','DIMPLE','BRIDGE','MARKING'])await page.locator(`[data-capability="${key}"]`).check();
    assert.equal(await page.locator('[data-capability="NONE"]').isChecked(),false);
    await page.click('#add-station');
    await page.getByLabel('顺序 2',{exact:true}).fill('1');
    await page.click('#apply-stations');assert.equal(await page.locator('#station-dialog').isVisible(),true);
    await page.getByLabel('顺序 2',{exact:true}).fill('2');
    await page.locator('.station-card').nth(1).locator('[data-capability="NONE"]').check();
    await page.click('#apply-stations');
    await page.locator('.operation-item').first().click();
    const oid=await page.locator('#operation-id').inputValue();
    await page.selectOption('#expert-station','OP10');await page.click('#apply-operation');
    assert.match(await page.locator('.operation-item.current').innerText(),/专家工序：OP10/);
    await page.click('#edit-stations');
    await page.locator('.station-card').first().getByText('删除工序',{exact:true}).click();
    assert.match(await page.locator('#station-error').innerText(),/已被/);
    await page.getByLabel('工序编号 1',{exact:true}).fill('OP15');await page.click('#apply-stations');
    assert.equal(await page.locator('#expert-station').inputValue(),'OP15');
    await page.click('#undo');assert.equal(await page.locator('#expert-station').inputValue(),'OP10');
    await page.click('#redo');assert.equal(await page.locator('#expert-station').inputValue(),'OP15');
    await page.click('#save-project');await page.waitForFunction(()=>document.querySelector('#save-status').textContent.startsWith('已保存'));
    assert.equal(saved.operations.find(op=>op.operation_id===oid).expert_station,'OP15');
    assert.deepEqual(saved.fixed_rows,original.fixed_rows);
    assert.deepEqual(saved.operations.map(op=>op.source_segment_ids),original.operations.map(op=>op.source_segment_ids));
    await page.click('#reload-project');await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('重新读取'));
    await page.locator('.operation-item').first().click();assert.equal(await page.locator('#expert-station').inputValue(),'OP15');
    await page.click('#edit-stations');
    await page.screenshot({path:`information_extract/outputs/process_editor_${port}.png`});
    assert.deepEqual(errors,[]);results.push({port,passed:true,actions:saved.operations.length});await page.close();
  }
  console.log(JSON.stringify(results));
}finally{await browser.close();}
