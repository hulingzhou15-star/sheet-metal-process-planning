import { browserExecutable } from './browser_runtime.mjs';
import {chromium} from 'playwright';
import path from 'node:path';import fs from 'node:fs/promises';import assert from 'node:assert/strict';import {pathToFileURL} from 'node:url';
const out=path.resolve('information_extract/outputs/finite_bend_v01/P001'),browser=await chromium.launch({executablePath:browserExecutable(),headless:true,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1550,height:1080}}),errors=[],checks=[];page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto(pathToFileURL(path.join(out,'review.html')).href);await page.waitForFunction(()=>window.finiteReview);
 for(const [i,expected] of [[0,[354.8,.3,0]],[1,[354.8,-4.1,-5.6]],[2,[354.8,-8.9,10]]]){
  await page.selectOption('#stage',String(i));await page.waitForFunction(()=>document.querySelector('#drawing').complete&&document.querySelector('#drawing').naturalWidth>0);
  const s=await page.evaluate(()=>finiteReview.getState());s.marker.forEach((v,j)=>assert.ok(Math.abs(v-expected[j])<1e-7));
  await page.screenshot({path:path.join(out,`${i+1}_state.png`),fullPage:true});checks.push(`Stage ${i}: known position and drawing loaded`);
 }
 await page.selectOption('#stage','1');await page.fill('#tau','50');let s=await page.evaluate(()=>finiteReview.getState());assert.equal(s.fractions.O010,.5);assert.equal(s.fractions.O009,0);checks.push('Fractional finite-band state');
 await page.fill('#tau','0');await page.click('#play');await page.waitForFunction(()=>Number(document.querySelector('#tau').value)>5);await page.click('#play');const n=await page.inputValue('#tau');await page.waitForTimeout(120);assert.equal(await page.inputValue('#tau'),n);checks.push('Animation and pause');
 await page.selectOption('#camera','side');await page.click('#fit');await page.selectOption('#camera','iso');const b=await page.locator('#motion').boundingBox();await page.mouse.move(b.x+100,b.y+100);await page.mouse.down();await page.mouse.move(b.x+130,b.y+120,{steps:3});await page.mouse.up();await page.mouse.wheel(0,-100);checks.push('Camera controls');
 assert.match(await page.locator('.warn').innerText(),/不守恒/);assert.match(await page.locator('.warn').innerText(),/57.1/);assert.equal(await page.locator('#checks tr').count(),7);checks.push('Limits and numeric evidence displayed');
 await page.setViewportSize({width:900,height:800});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);checks.push('Responsive page');
 assert.deepEqual(errors,[]);const result={passed:true,checks,page_errors:errors};await fs.writeFile(path.join(out,'browser_qa.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{await browser.close()}
