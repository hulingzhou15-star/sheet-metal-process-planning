import { browserExecutable } from './browser_runtime.mjs';
import {chromium} from 'playwright';
import path from 'node:path';import fs from 'node:fs/promises';import {pathToFileURL} from 'node:url';
const out=path.resolve('information_extract/outputs/finite_material_v02/P001');
const browser=await chromium.launch({executablePath:browserExecutable(),headless:true,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1680,height:1150}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
 const started=performance.now();await page.goto(pathToFileURL(path.join(out,'review.html')).href,{timeout:120000});await page.waitForFunction(()=>window.finiteMaterialReview,{}, {timeout:120000});const load=performance.now()-started;
 await page.evaluate(()=>{window.rendererTimes=[];const proto=(window.FiniteMaterialRenderer||window.MotionRenderer).prototype,original=proto.render;proto.render=function(...args){const t=performance.now();const result=original.apply(this,args);this.gl.finish();window.rendererTimes.push(performance.now()-t);return result;};});
 const measure=()=>page.evaluate(()=>{
  const time=fn=>{const t=performance.now();fn();return performance.now()-t;},step=[];
  const sid=document.getElementById('station'),phase=document.getElementById('phase'),tau=document.getElementById('tau');sid.value='OP40';phase.value='motion';phase.dispatchEvent(new Event('change'));
  for(const value of [31,37,43,49,55]){tau.value=value;step.push(time(()=>tau.dispatchEvent(new Event('input'))));}
  const canvas=document.querySelector('#overlay')||[...document.querySelectorAll('canvas')].find(c=>c.id==='overlay'),box=canvas.getBoundingClientRect(),camera=[];
  canvas.dispatchEvent(new PointerEvent('pointerdown',{clientX:box.x+100,clientY:box.y+100,pointerId:1}));
  for(let i=1;i<=8;i++)camera.push(time(()=>canvas.dispatchEvent(new PointerEvent('pointermove',{clientX:box.x+100+i*4,clientY:box.y+100+i,pointerId:1}))));
  canvas.dispatchEvent(new PointerEvent('pointerup',{pointerId:1}));
  return {stage_event_ms:step,camera_event_ms:camera,renderer_frame_including_gpu_finish_ms:window.rendererTimes,renderer:document.getElementById('scene').getContext('webgl').getParameter(7937)};
 });
 const main=await measure();
 // Pointer dispatch does not always establish capture; measure public benchmark
 // hooks too when available so RAF scheduling cannot hide the actual draw cost.
 const draw=await page.evaluate(()=>window.finiteMaterialReview.benchmark?.());
 const result={load_ms:load,main,draw:draw||null,page_errors:errors};const name=process.argv[2]||'performance_current.json';await fs.writeFile(path.join(out,name),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
}finally{await browser.close()}
