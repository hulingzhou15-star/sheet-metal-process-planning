import { browserExecutable } from './browser_runtime.mjs';
import {chromium} from 'playwright';
import path from 'node:path';import fs from 'node:fs/promises';import assert from 'node:assert/strict';import {pathToFileURL} from 'node:url';
const out=path.resolve('information_extract/outputs/whole_panel_collision_v01/P001');
const browser=await chromium.launch({executablePath:browserExecutable(),headless:true,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1700,height:1080}}),errors=[],checks=[];page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto(pathToFileURL(path.join(out,'review.html')).href);await page.waitForFunction(()=>window.wholePanelReview);
 assert.equal(await page.locator('#station option').count(),3);assert.match(await page.locator('header').innerText(),/H1–H7/);
 const images=[];
 for(const sid of ['OP40','OP50','OP60']){
  await page.selectOption('#station',sid);await page.locator('#sample').fill('12');await page.locator('#sample').dispatchEvent('input');
  const s=await page.evaluate(()=>wholePanelReview.getState());assert.equal(s.station_id,sid);assert.equal(s.sample_index,12);
  assert.ok(await page.locator('#issues button').count()>0);images.push(await page.evaluate(()=>document.querySelector('#scene').toDataURL()));
  await page.screenshot({path:path.join(out,`${sid}_mid_motion.png`),fullPage:true});
 }
 assert.equal(new Set(images).size,3);checks.push('Three distinct synchronous mid-stage poses and stage-specific problem lists');
 await page.selectOption('#station','OP40');const first=await page.locator('#issues button').first().getAttribute('data-issue');await page.locator('#issues button').first().click();
 let s=await page.evaluate(()=>wholePanelReview.getState());assert.equal(s.selected_issue,first);assert.equal(s.highlighted_region_ids.length,2);
 assert.match(await page.locator('#detail').innerText(),/本序驱动折弯/);assert.match(await page.locator('#detail').innerText(),/恢复|保留成品内孔/);
 await page.check('#isolate');await page.screenshot({path:path.join(out,'selected_issue_isolated.png'),fullPage:true});await page.uncheck('#isolate');
 const same=await page.evaluate(()=>wholePanelReview.getState());assert.deepEqual(same.fractions,s.fractions);checks.push('Issue click jumps to audited peak state, highlights both regions, focuses and exposes operation associations plus sensitivity');
 await page.selectOption('#mode','formed_reference');assert.equal(await page.locator('#sample').isDisabled(),true);assert.ok(Object.values((await page.evaluate(()=>wholePanelReview.getState())).fractions).every(x=>x===1));
 await page.selectOption('#mode','flat');assert.ok(Object.values((await page.evaluate(()=>wholePanelReview.getState())).fractions).every(x=>x===0));
 await page.selectOption('#mode','stage');assert.deepEqual((await page.evaluate(()=>wholePanelReview.getState())).fractions,s.fractions);
 checks.push('Flat and formed baselines have all-zero/all-one joint fractions; returning restores sampled candidate state');
 await page.selectOption('#filter','nonadjacent');assert.ok(await page.locator('#issues button').count()>0);
 await page.fill('#search','no-such-issue');assert.equal(await page.locator('#issues button').count(),0);await page.fill('#search','');await page.selectOption('#filter','all');
 await page.selectOption('#station','OP50');await page.locator('#sample').fill('0');await page.locator('#sample').dispatchEvent('input');await page.click('#play');await page.waitForFunction(()=>wholePanelReview.getState().sample_index>=2);await page.click('#play');
 s=await page.evaluate(()=>wholePanelReview.getState());const current=await page.evaluate(()=>{const d=wholePanelReview.data;return d.skeleton.joints.filter(j=>d.plan.assignments[d.skeleton.joint_to_operation[j.joint_id]]==='OP50').map(j=>j.joint_id);});assert.ok(current.every(j=>s.fractions[j]===s.sample_index/24));
 checks.push('Filters affect only list; playback advances prechecked samples with same-stage equal progress');
 await page.selectOption('#view','side');await page.click('#fit');const before=await page.evaluate(()=>document.querySelector('#scene').toDataURL());const b=await page.locator('#overlay').boundingBox();await page.mouse.move(b.x+b.width/2,b.y+b.height/2);await page.mouse.down();await page.mouse.move(b.x+b.width/2+55,b.y+b.height/2+10);await page.mouse.up();assert.notEqual(await page.evaluate(()=>document.querySelector('#scene').toDataURL()),before);
 await page.locator('summary').click();assert.match(await page.locator('#coverage').innerText(),/未可靠定位动作/);assert.deepEqual(errors,[]);checks.push('View controls and camera work; missing coverage displayed; no browser errors');
 await fs.writeFile(path.join(out,'browser_qa.json'),JSON.stringify({checks,page_errors:errors},null,2));console.log(JSON.stringify({checks,page_errors:errors},null,2));
}finally{await browser.close();}
