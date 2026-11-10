import { browserExecutable } from './browser_runtime.mjs';
import {chromium} from 'playwright';
import path from 'node:path';import fs from 'node:fs/promises';import assert from 'node:assert/strict';import {pathToFileURL} from 'node:url';
const out=path.resolve('information_extract/outputs/finite_material_v02/P001');
const browser=await chromium.launch({executablePath:browserExecutable(),headless:true,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const context=await browser.newContext({viewport:{width:1680,height:1150}}),page=await context.newPage(),errors=[],checks=[];context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto(pathToFileURL(path.join(out,'review.html')).href,{timeout:120000});await page.waitForFunction(()=>window.finiteMaterialReview,{}, {timeout:120000});
 const quality=await page.evaluate(()=>{finiteMaterialReview.redraw3();const c=document.getElementById('scene'),gl=c.getContext('webgl'),pixels=new Uint8Array(c.width*c.height*4);gl.readPixels(0,0,c.width,c.height,gl.RGBA,gl.UNSIGNED_BYTE,pixels);let partial=0;for(let i=3;i<pixels.length;i+=4)if(pixels[i]>0&&pixels[i]<255)partial++;return {state:finiteMaterialReview.getState().display_quality,partial_coverage_pixels:partial};});
 assert.equal(quality.state.mode,'clear');assert.equal(quality.state.antialias,true);assert.ok(quality.state.msaa_samples>1);assert.ok(quality.partial_coverage_pixels>30);
 await page.selectOption('#station','OP40');await page.selectOption('#phase','motion');await page.fill('#tau','37');await page.dispatchEvent('#tau','input');
 const samples=JSON.parse(await fs.readFile(path.join(out,'mapping_samples.json'),'utf8'));
 const gpu=await page.evaluate(samples=>finiteMaterialReview.verifyGpuPoints(samples),samples);
 assert.equal(gpu.count,201);assert.ok(gpu.max_encoded_error_mm<.001);
 checks.push('Actual display vertex shader agrees with independent Python positions at 201 vertices across 67 components');
 for(const sid of ['OP20','OP60']){
  await page.selectOption('#station',sid);await page.selectOption('#phase','completed');
  const result=await page.evaluate(()=>{const review=finiteMaterialReview,d=review.data,s=review.getState(),v=d.variants.find(v=>v.material_id===s.material_id),samples=v.components.map(component=>{const points=d.geometries[component.geometry_id].triangles[0];return {component,flat_points:points,world_points:points.map(p=>review.mapPoint(component,p))};});return review.verifyGpuPoints(samples);});
  assert.ok(result.max_encoded_error_mm<.001);
 }
 checks.push('GPU flat and final CAD states also agree with the unchanged CPU material mapping');
 await page.selectOption('#station','OP40');await page.selectOption('#phase','motion');await page.fill('#tau','37');await page.dispatchEvent('#tau','input');
 const initial=await page.evaluate(()=>finiteMaterialReview.getState()),b=await page.locator('#overlay').boundingBox();
 await page.mouse.move(b.x+b.width/2,b.y+b.height/2);await page.mouse.down();await page.mouse.move(b.x+b.width/2+60,b.y+b.height/2+20,{steps:12});await page.mouse.up();
 let s=await page.evaluate(()=>finiteMaterialReview.getState());assert.equal(s.render_stats.geometry_uploads,initial.render_stats.geometry_uploads);assert.deepEqual(s.fractions,initial.fractions);
 for(const tau of ['39','41','43']){await page.fill('#tau',tau);await page.dispatchEvent('#tau','input');}
 s=await page.evaluate(()=>finiteMaterialReview.getState());assert.equal(s.render_stats.geometry_uploads,initial.render_stats.geometry_uploads);assert.equal(s.render_stats.flat_variants_cached,initial.render_stats.flat_variants_cached);assert.equal(s.triangle_count,initial.triangle_count);
 assert.equal(s.render_stats.flat_frames,initial.render_stats.flat_frames);
 checks.push('Camera and common-progress updates reuse GPU geometry and 2D paths without dropping any triangles');
 const outlines=await page.evaluate(()=>finiteMaterialReview.flatOutlineInfo(true));assert.equal(outlines.length,211);assert.ok(outlines.every(o=>o.boundary_used&&o.area_error_mm2<1e-5));
 checks.push('Cached 2D boundaries close and preserve front-mesh area across all 211 geometries; progress does not redraw unchanged 2D material');
 const popupEvent=page.waitForEvent('popup');await page.click('#popout3');const popup=await popupEvent;await popup.waitForSelector('#overlay');await popup.setViewportSize({width:2488,height:1440});
 const ready=await page.evaluate(()=>finiteMaterialReview.getState()),box=await popup.locator('#overlay').boundingBox(),ratio=ready.camera.scale/ready.display_quality.buffer[0];
 await popup.mouse.move(box.x+box.width/2,box.y+box.height/2);await popup.mouse.down();let clear=await page.evaluate(()=>finiteMaterialReview.getState());assert.equal(clear.display_quality.mode,'clear');assert.deepEqual(clear.display_quality.buffer,ready.display_quality.buffer);assert.equal(clear.display_quality.antialias,true);await popup.mouse.up();
 checks.push('Antialiasing produces fractional edge coverage; default clear mode preserves native resolution throughout full-size popup interaction');
 await popup.selectOption('#renderQuality','fluid');
 await popup.mouse.move(box.x+box.width/2,box.y+box.height/2);await popup.mouse.down();
 const active=await page.evaluate(()=>finiteMaterialReview.getState());assert.equal(active.display_quality.interactive,true);assert.ok(active.display_quality.buffer[0]*active.display_quality.buffer[1]<905000);assert.ok(Math.abs(active.camera.scale/active.display_quality.buffer[0]-ratio)<1e-10);assert.equal(active.display_quality.stroke_css_px,1);
 await popup.mouse.move(box.x+box.width/2+60,box.y+box.height/2+25);await popup.mouse.up();s=await page.evaluate(()=>finiteMaterialReview.getState());assert.equal(s.display_quality.interactive,false);assert.equal(s.display_quality.buffer[0],s.display_quality.css[0]);assert.deepEqual(s.fractions,ready.fractions);assert.equal(s.triangle_count,ready.triangle_count);assert.equal(s.render_stats.geometry_uploads,ready.render_stats.geometry_uploads);assert.ok(Math.abs(s.camera.scale/s.display_quality.buffer[0]-ratio)<1e-10);
 await popup.mouse.wheel(0,60);await page.waitForFunction(()=>finiteMaterialReview.getState().display_quality.interactive===false);s=await page.evaluate(()=>finiteMaterialReview.getState());assert.ok(s.camera.scale<ready.camera.scale);
 await page.click('#play');await popup.bringToFront();assert.equal((await page.evaluate(()=>finiteMaterialReview.getState())).animation.window,'popup3');await page.waitForFunction(()=>finiteMaterialReview.getState().tau>.1);await popup.close();await page.waitForSelector('#scene');s=await page.evaluate(()=>finiteMaterialReview.getState());assert.equal(s.animation.window,'main');await page.waitForFunction(()=>finiteMaterialReview.getState().phase==='completed');assert.equal((await page.evaluate(()=>finiteMaterialReview.getState())).display_quality.interactive,false);
 checks.push('Explicit fluid mode reduces only render samples, preserves antialiasing/zoom/material/triangles, restores native clarity, and schedules playback through window return');
 assert.deepEqual(errors,[]);await fs.writeFile(path.join(out,'performance_qa.json'),JSON.stringify({checks,page_errors:errors,gpu_probe:gpu,edge_quality:quality},null,2));console.log({checks,page_errors:errors,gpu_probe:gpu,edge_quality:quality});
}finally{await browser.close()}
