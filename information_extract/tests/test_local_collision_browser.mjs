import { browserExecutable } from './browser_runtime.mjs';
import {chromium} from 'playwright';
import path from 'node:path';import fs from 'node:fs/promises';import assert from 'node:assert/strict';import {pathToFileURL} from 'node:url';
const out=path.resolve('information_extract/outputs/local_collision_v01/P001');
const browser=await chromium.launch({executablePath:browserExecutable(),headless:true,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1550,height:1100}}),errors=[],checks=[];page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto(pathToFileURL(path.join(out,'review.html')).href);await page.waitForFunction(()=>window.localCollisionReview);
 assert.equal(await page.locator('#path option').count(),3);assert.equal(await page.locator('#pairs tr').count(),10);
 assert.match(await page.locator('header').innerText(),/父先子后、同序两项违反 H7/);
 assert.match(await page.locator('#path option').nth(1).innerText(),/违反 H7，仅开发对照/);
 assert.match(await page.locator('#path option').nth(2).innerText(),/违反 H7，仅开发对照/);
 const images=[];
 for(let i=0;i<3;i++){
  await page.selectOption('#path',String(i));const s=await page.evaluate(()=>localCollisionReview.getState());
  if(i===0)assert.deepEqual(s.fractions,{O009:0,O010:1});
  if(i===1)assert.deepEqual(s.fractions,{O009:1,O010:0});
  if(i===2)assert.deepEqual(s.fractions,{O009:.5,O010:.5});
  assert.equal(s.pairs.length,10);
  images.push(await page.evaluate(()=>document.querySelector('#scene').toDataURL()));
  await page.screenshot({path:path.join(out,`${s.kind}.png`),fullPage:true});
  for(const value of ['0',await page.locator('#sample').getAttribute('max')]){
   await page.locator('#sample').fill(value);await page.locator('#sample').dispatchEvent('input');
   const fr=(await page.evaluate(()=>localCollisionReview.getState())).fractions;
   assert.deepEqual(fr,value==='0'?{O009:0,O010:0}:{O009:1,O010:1});
  }
 }
 assert.equal(new Set(images).size,3);checks.push('Three genuinely different intermediate poses; common flat and formed endpoints; all ten pair results visible');
 await page.selectOption('#path','2');await page.selectOption('#view','side');await page.screenshot({path:path.join(out,'simultaneous_side.png'),fullPage:true});
 const before=await page.evaluate(()=>localCollisionReview.getState());const img=await page.evaluate(()=>document.querySelector('#scene').toDataURL());
 const b=await page.locator('#motion').boundingBox();await page.mouse.move(b.x+b.width/2,b.y+b.height/2);await page.mouse.down();await page.mouse.move(b.x+b.width/2+55,b.y+b.height/2+20);await page.mouse.up();await page.mouse.wheel(0,-100);
 assert.notEqual(await page.evaluate(()=>document.querySelector('#scene').toDataURL()),img);assert.deepEqual(await page.evaluate(()=>localCollisionReview.getState()),before);
 checks.push('Rotation/zoom and side view preserve diagnostic sample and fractions');
 await page.locator('#sample').fill('0');await page.locator('#sample').dispatchEvent('input');await page.click('#play');await page.waitForFunction(()=>localCollisionReview.getState().sample_index>=2);await page.click('#play');
 const same=await page.evaluate(()=>localCollisionReview.getState());assert.equal(same.fractions.O009,same.fractions.O010);checks.push('Playback advances checked states and keeps simultaneous progress equal');
 assert.match(await page.locator('body').innerText(),/未发现交叠 ≠ 可制造/);assert.equal(await page.locator('#summary tr').count(),3);
 assert.deepEqual(errors,[]);checks.push('Scope limits and all three aggregate results displayed; no browser errors');
 await fs.writeFile(path.join(out,'browser_qa.json'),JSON.stringify({checks,page_errors:errors},null,2));console.log(JSON.stringify({checks,page_errors:errors},null,2));
}finally{await browser.close();}
