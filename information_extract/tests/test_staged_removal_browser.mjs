import { browserExecutable } from './browser_runtime.mjs';
import {chromium} from 'playwright';
import fs from 'node:fs/promises';import path from 'node:path';import assert from 'node:assert/strict';import {pathToFileURL} from 'node:url';
const out=path.resolve('information_extract/outputs/staged_removal_v01/P001');
const browser=await chromium.launch({executablePath:browserExecutable(),headless:true,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto(pathToFileURL(path.join(out,'review.html')).href);await page.waitForFunction(()=>window.removalReview);
 assert.equal(await page.locator('#station option').count(),7);
 assert.equal(await page.evaluate(()=>removalReview.state.removed_operation_ids.length),0);
 const counts=[];
 for(const sid of ['OP10','OP20','OP30','OP40','OP50','OP60','OP70']){
  await page.selectOption('#station',sid);await page.selectOption('#phase','entry');const entry=await page.evaluate(()=>removalReview.state);
  await page.selectOption('#phase','completed');const done=await page.evaluate(()=>removalReview.state);
  assert.ok(done.volume_mm3<=entry.volume_mm3+1e-6);counts.push(done.removed_operation_ids.length);
  assert.equal(await page.evaluate(()=>removalReview.triangleCount),await page.evaluate(()=>removalReview.data.meshes[removalReview.state.material_id].reduce((n,p)=>n+p.triangles.length,0)));
 }
 assert.equal(counts.at(-1),12);assert.ok(counts.every((n,i)=>!i||n>=counts[i-1]));
 await page.selectOption('#station','OP30');await page.selectOption('#view','top');await page.screenshot({path:path.join(out,'completed_holes.png'),fullPage:true});
 await page.selectOption('#station','OP60');await page.selectOption('#view','iso');const before=await page.locator('#scene').evaluate(c=>c.toDataURL());
 const b=await page.locator('#overlay').boundingBox();await page.mouse.move(b.x+b.width/2,b.y+b.height/2);await page.mouse.down();await page.mouse.move(b.x+b.width/2+40,b.y+b.height/2+20);await page.mouse.up();
 assert.notEqual(await page.locator('#scene').evaluate(c=>c.toDataURL()),before);assert.deepEqual(errors,[]);
 await page.screenshot({path:path.join(out,'formed_holes.png'),fullPage:true});
 const qa={checks:['Seven station entry/completed states','No future holes in initial entry','Monotonic removal and final subset count','Viewer uses checked solid meshes','Camera and views render'],completed_cut_counts:counts,page_errors:errors};
 await fs.writeFile(path.join(out,'browser_qa.json'),JSON.stringify(qa,null,2));console.log(qa);
}finally{await browser.close()}
