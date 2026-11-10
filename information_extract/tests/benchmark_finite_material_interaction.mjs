import { browserExecutable } from './browser_runtime.mjs';
// Measure completed frames at full popup size, including GPU completion.
import {chromium} from 'playwright';
import path from 'node:path';import fs from 'node:fs/promises';import {pathToFileURL} from 'node:url';
const out=path.resolve('information_extract/outputs/finite_material_v02/P001');
const browser=await chromium.launch({executablePath:browserExecutable(),headless:true,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const context=await browser.newContext({viewport:{width:2488,height:1440},deviceScaleFactor:1.25}),page=await context.newPage(),errors=[];
context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));page.on('pageerror',e=>errors.push(e.message));
const result={environment:'Headless Chromium / SwiftShader; 2488x1440 CSS pixels, DPR 1.25',cases:[],page_errors:errors};
try{
 await page.goto(pathToFileURL(path.join(out,'review.html')).href,{timeout:120000});await page.waitForFunction(()=>window.finiteMaterialReview,{}, {timeout:120000});
 await page.evaluate(()=>{window.completedDraws=[];const proto=FiniteMaterialRenderer.prototype,original=proto.render;proto.render=function(...args){const start=performance.now();const value=original.apply(this,args);this.gl.finish();completedDraws.push(performance.now()-start);return value;};});
 async function drag(surface,name,kind=3){
  const selector=kind===3?'#overlay':'#flat',box=await surface.locator(selector).boundingBox(),x=box.x+box.width*.5,y=box.y+box.height*.5;
  await surface.mouse.move(x,y);await surface.mouse.down();await page.evaluate(()=>completedDraws=[]);
  const intervals=await surface.evaluate(async({selector,x,y})=>{const canvas=document.querySelector(selector),frames=[];let last=performance.now();for(let i=0;i<36;i++){await new Promise(requestAnimationFrame);const now=performance.now();frames.push(now-last);last=now;canvas.dispatchEvent(new PointerEvent('pointermove',{clientX:x+Math.sin(i*.12)*90,clientY:y+Math.cos(i*.12)*40,pointerId:1,buttons:1}));}await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);return frames;},{selector,x,y});
  const during=await page.evaluate(()=>finiteMaterialReview.getState().display_quality);await surface.mouse.up();const draws=await page.evaluate(()=>completedDraws),state=await page.evaluate(()=>finiteMaterialReview.getState()),stats=state.render_stats;
  const percentile=(values,p)=>{const a=[...values].sort((a,b)=>a-b);return a[Math.min(a.length-1,Math.floor(p*a.length))]||0;};
  result.cases.push({name,canvas:await surface.locator(kind===3?'#scene':'#flat').evaluate(c=>[c.width,c.height]),during_display_quality:during,settled_display_quality:state.display_quality,frames:intervals.length,completed_draws:draws.length,interval_median_ms:percentile(intervals,.5),interval_p95_ms:percentile(intervals,.95),gpu_completed_draw_median_ms:percentile(draws,.5),gpu_completed_draw_p95_ms:percentile(draws,.95),render_stats:stats});
 }
 await drag(page,'main_3d');let popupEvent=page.waitForEvent('popup');await page.click('#popout3');const popup=await popupEvent;await popup.waitForSelector('#overlay');await popup.setViewportSize({width:2488,height:1440});await popup.bringToFront();
 await drag(popup,'popup_3d_OP20_highlight');await popup.selectOption('#renderQuality','fluid');await drag(popup,'popup_3d_OP20_fluid');await popup.selectOption('#renderQuality','clear');await page.uncheck('#highlightCurrent');await drag(popup,'popup_3d_OP20_plain');await page.check('#highlightCurrent');
 await page.selectOption('#station','OP40');await page.selectOption('#phase','motion');await drag(popup,'popup_3d_OP40_motion');
 await popup.close();await page.waitForSelector('#scene');popupEvent=page.waitForEvent('popup');await page.click('#popout2');const flat=await popupEvent;await flat.waitForSelector('#flat');await flat.setViewportSize({width:2488,height:1440});await flat.bringToFront();await drag(flat,'popup_2d',2);await flat.close();
 const filename=process.argv[2]||'performance_interaction_current.json';await fs.writeFile(path.join(out,filename),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
}finally{await browser.close()}
