import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';
import {browserExecutable} from './browser_runtime.mjs';

const regenerated=process.argv.includes('--regenerated');
const directory=regenerated?'p001_motion_regeneration_v01':'p001_stage_workspace_v01';
const stage=new URL('../outputs/'+directory+'/stage/',import.meta.url);
const manifest=JSON.parse(await fs.readFile(new URL('manifest.json',stage),'utf8'));
const browser=await chromium.launch({executablePath:browserExecutable(),headless:true,
  args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});

try {
  const page=await browser.newPage({viewport:{width:1440,height:970}});
  const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(new URL('index.html',stage).href);
  await page.waitForFunction(()=>window.finiteMaterialReview?.getState());
  const state=()=>page.evaluate(()=>window.finiteMaterialReview.getState());
  const select=(id,value)=>page.locator('#'+id).selectOption(value);
  const click=id=>page.locator('#'+id).click();
  const seek=index=>page.locator('#sample').evaluate((input,value)=>{
    input.value=String(value);
    input.dispatchEvent(new Event('input'));
  },index);
  const report=async()=>JSON.parse(await fs.readFile(new URL(await page.locator('#rawReport').getAttribute('href'),stage),'utf8'));
  let current=await state();
  assert.equal(current.phase,'entry');
  assert.equal(current.sample_index,0);
  assert.ok(current.triangle_count>1000);
  assert.equal(await page.locator('#plan option').count(),manifest.candidate_count);
  assert.equal(await page.locator('#stationRows tr').count(),await page.locator('#station option').count());
  const firstReport=await report();
  assert.equal(firstReport.plan_id,current.plan_id);
  const nextMotion=firstReport.runs.findIndex(run=>run.phase==='motion');
  assert.ok(nextMotion>0);
  await seek(nextMotion);
  current=await state();
  assert.equal(current.phase,'motion');
  assert.equal(current.tau,firstReport.runs[nextMotion].tau);
  assert.deepEqual(current.counts,firstReport.runs[nextMotion].counts);
  await click('prevSample');
  assert.equal((await state()).phase,'entry');
  await click('nextSample');
  assert.equal((await state()).sample_index,nextMotion);
  await select('comparison','whole-step');
  current=await state();
  assert.equal(current.comparison,'whole-step');
  assert.ok(current.triangle_count>1000);
  assert.ok(await page.locator('a[href="completed.step"]').count());
  await select('operation',regenerated?'O007':'O196');
  await select('comparison','step');
  assert.equal((await state()).comparison,'step');
  await click('beforeFeature');
  assert.equal((await state()).phase,'entry');
  if(regenerated)assert.ok(!(await state()).executed_forming_ids.includes('O007'));
  await click('afterFeature');
  current=await state();
  assert.equal(current.phase,'completed');
  assert.equal(current.comparison,'stage');
  if(regenerated)assert.ok(current.executed_forming_ids.includes('O007'));
  if(regenerated){
    assert.ok((await page.locator('#planStats').innerText()).includes('P5'));
    assert.ok(await page.locator('#materialReport').isVisible());
  }
  await click('cflat');
  assert.equal((await state()).phase,'entry');
  assert.equal(await page.locator('#operation').inputValue(),'O193');
  await click('cformed');
  assert.equal((await state()).phase,'completed');
  const [popup]=await Promise.all([page.waitForEvent('popup'),click('popout3')]);
  await popup.locator('#popupPanel #scene').waitFor();
  assert.ok((await state()).popouts.three);
  await popup.locator('#popupReturn').click();
  assert.equal((await state()).popouts.three,false);
  await click('whole');
  const choices=await page.locator('#plan option').evaluateAll(options=>options.map(o=>o.value));
  await select('plan',choices.at(-1));
  current=await state();
  assert.equal(current.plan_id,choices.at(-1));
  assert.equal(current.sample_index,0);
  await select('speed','4');
  await page.evaluate(()=>{
    const state=document.getElementById('state');
    window.visitedSamples=new Set();
    const visit=()=>window.visitedSamples.add(window.finiteMaterialReview.getState().sample_index);
    new MutationObserver(visit).observe(state,{childList:true});
    visit();
  });
  await click('playAll');
  const lastReport=await report();
  await page.waitForFunction(expected=>{
    const view=window.finiteMaterialReview.getState();
    return !view.animation.playing&&view.sample_index===expected;
  },lastReport.runs.length-1,{timeout:90_000});
  current=await state();
  assert.equal(current.phase,'completed');
  assert.equal(current.tau,1);
  assert.equal(current.station_id,lastReport.runs.at(-1).station_id);
  assert.equal(manifest.plans[current.plan_id].sample_count,lastReport.runs.length);
  const visited=new Set(await page.evaluate(()=>[...window.visitedSamples]));
  lastReport.runs.forEach((sample,index)=>{
    if(sample.phase!=='motion')assert.ok(visited.has(index),'Playback omitted endpoint sample '+index);
  });
  assert.deepEqual(errors,[]);
  console.log('P001 browser replay verified for '+directory+' with '+manifest.candidate_count+' plans');
} finally {
  await browser.close();
}
