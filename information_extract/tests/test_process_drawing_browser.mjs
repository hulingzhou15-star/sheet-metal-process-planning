import { browserExecutable } from './browser_runtime.mjs';
import {chromium} from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
const out=path.resolve('information_extract/outputs/process_drawing_assistance/P001');
const browser=await chromium.launch({executablePath:browserExecutable(),headless:true});
const page=await browser.newPage({viewport:{width:1500,height:1080}}),errors=[];
page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto(pathToFileURL(path.join(out,'review.html')).href);
 assert.equal(await page.locator('#view option').count(),9);
 for(let i=0;i<9;i++){
  await page.selectOption('#view',String(i));
  await page.waitForFunction(()=>{const i=document.querySelector('#drawing');return i.complete&&i.naturalWidth>0});
  if(i===1)assert.match(await page.locator('#stats').innerText(),/未通过/);
  else{
   assert.match(await page.locator('#stats').innerText(),/独立核验/);
   await page.waitForFunction(()=>{const i=document.querySelector('#local');return i.complete&&i.naturalWidth>0});
  }
 }
 await page.selectOption('#view','7');
 await page.waitForFunction(()=>document.querySelector('#drawing').complete&&document.querySelector('#local').complete);
 await page.screenshot({path:path.join(out,'review_preview.png'),fullPage:true});
 await page.locator('#prev').click();assert.equal(await page.locator('#view').inputValue(),'6');
 await page.locator('#next').click();assert.equal(await page.locator('#view').inputValue(),'7');
 assert.deepEqual(errors,[]);
 const result={passed:true,view_count:9,image_loads:'9 full views + 8 registered local views',page_errors:errors,checks:['Offline navigation','Insufficient-anchor message','Registered local views','Previous/next controls']};
 await fs.writeFile(path.join(out,'browser_qa.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{await browser.close()}
