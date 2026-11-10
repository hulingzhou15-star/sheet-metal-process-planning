import { browserExecutable } from './browser_runtime.mjs';
import {chromium} from 'playwright';
import path from 'node:path';import fs from 'node:fs/promises';import assert from 'node:assert/strict';import {pathToFileURL} from 'node:url';
const out=path.resolve('information_extract/outputs/full_part_review_v01/P001');
const browser=await chromium.launch({executablePath:browserExecutable(),headless:true,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1700,height:1100}}), errors=[],checks=[];
page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto(pathToFileURL(path.join(out,'review.html')).href);await page.waitForFunction(()=>window.review3d);
 const data=await page.evaluate(()=>({inspection:review3d.data.inspection,state:review3d.getState(),faces:review3d.data.asset.face_count}));
 assert.equal(await page.locator('#list button').count(),224);assert.equal(data.state.webgl,true);
 assert.equal(data.inspection.full_part_simulation_ready,false);assert.equal(data.inspection.full_part_collision_checked,false);
 assert.equal(Object.values(data.inspection.counts).reduce((a,b)=>a+b,0),224);
 for(const row of Object.values(data.inspection.operations)){assert.equal(row.automatically_confirmed,false);assert.ok(row.face_ids.every(id=>id>0&&id<=data.faces));}
 checks.push('224 unique actions; valid evidence face IDs; no automatic confirmation or full simulation claim');
 await page.screenshot({path:path.join(out,'whole_part.png'),fullPage:true});
 for(const [type,n] of Object.entries(data.inspection.type_counts)){await page.selectOption('#filter',`TYPE:${type}`);assert.equal(await page.locator('#list button').count(),n);}
 for(const [status,n] of Object.entries(data.inspection.counts)){await page.selectOption('#filter',`STATUS:${status}`);assert.equal(await page.locator('#list button').count(),n);}
 await page.selectOption('#filter','PROPOSAL');assert.equal(await page.locator('#list button').count(),68);checks.push('All type/status filters and 68 evidence records');
 await page.selectOption('#filter','ALL');await page.fill('#search','O009');assert.equal(await page.locator('#list button').count(),1);await page.locator('#list button').click();await page.fill('#search','');
 let state=await page.evaluate(()=>review3d.getState());assert.ok(state.suggested.length>0);assert.deepEqual(state.selected,[]);assert.deepEqual(state.bindings.bindings,{});
 await page.click('#focus-evidence');assert.match(await page.locator('#proposals').innerText(),/物理成形与碰撞未验证/);
 const localUrl=await page.locator('#local-model-link').getAttribute('href');await fs.access(path.resolve(out,localUrl));
 await page.screenshot({path:path.join(out,'O009_evidence.png'),fullPage:true});checks.push('O009 highlights evidence without saving and links to local model');
 await page.check('#autofocus');await page.evaluate(()=>review3d.choose('O011'));assert.equal(data.inspection.operations.O011.motion_model,false);assert.match(await page.locator('#proposals').innerText(),/自身预孔\/翻孔过程/);
 await page.evaluate(()=>review3d.choose('O002'));assert.deepEqual((await page.evaluate(()=>review3d.getState())).suggested,[]);
 await page.evaluate(()=>review3d.choose('O001'));assert.match(await page.locator('#proposals').innerText(),/缺少二维/);checks.push('Position-only, unassociated and missing geometry remain distinct');
 await page.click('#fit3');const hit=await page.evaluate(()=>{const c=document.querySelector('#solid');for(let y=10;y<c.clientHeight;y+=17)for(let x=10;x<c.clientWidth;x+=17){const id=review3d.pickFace(x,y);if(id)return{x,y,id};}return null;});assert.ok(hit);
 const box=await page.locator('#solid').boundingBox();await page.mouse.click(box.x+hit.x,box.y+hit.y);assert.ok((await page.evaluate(()=>review3d.getState())).selected.includes(hit.id));
 await page.click('#save');assert.equal((await page.evaluate(()=>review3d.getState())).bindings.bindings.O001.status,'DRAFT');
 const downloadPromise=page.waitForEvent('download');await page.click('#export');await downloadPromise;checks.push('Actual GPU hit, manual draft save and backup export');
 await page.evaluate(()=>localStorage.clear());
 const legacy=await fs.readFile(path.resolve('information_extract/outputs/three_d_state_v01/P001/review.html'),'utf8');
 const app=await fs.readFile(path.resolve('information_extract/three_d/web/app.js'),'utf8');
 const start=legacy.lastIndexOf('<script>'),end=legacy.indexOf('</script>',start);
 const oldPage=await browser.newPage({viewport:{width:1700,height:1100}});oldPage.on('pageerror',e=>errors.push(e.message));
 await oldPage.setContent(legacy.slice(0,start+8)+app+legacy.slice(end));
 await oldPage.waitForFunction(()=>window.review3d&&!review3d.data.inspection);
 assert.equal(await oldPage.locator('#list button').count(),224);await oldPage.selectOption('#filter','ANCHOR');assert.equal(await oldPage.locator('#list button').count(),7);
 await oldPage.evaluate(()=>review3d.choose('O009'));assert.equal((await oldPage.evaluate(()=>review3d.getState())).webgl,true);
 checks.push('Updated shared app remains compatible with original non-inspection data');assert.deepEqual(errors,[]);
 const result={passed:true,checks,page_errors:errors};await fs.writeFile(path.join(out,'browser_qa.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{await browser.close();}
