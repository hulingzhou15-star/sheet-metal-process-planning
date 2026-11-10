const fs=require('fs'),path=require('path'),assert=require('assert'),{pathToFileURL}=require('url');
const {chromium}=require('C:/Users/hulin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const folder=path.resolve(process.argv[2]||'outputs/selected_plans_v02');
 const frozen=fs.readFileSync('outputs/neighborhood_penalty_v02/selected_candidates.jsonl','utf8').trim().split(/\r?\n/).map(JSON.parse);
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1760,height:1150}}),errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/^https?:/.test(r.url()))requests.push(r.url());});
  await page.goto(pathToFileURL(path.join(folder,'selected_plans.html')).href);
  const P=JSON.parse(await page.locator('#payload').textContent());
  assert.strictEqual(await page.locator('#plan option').count(),16);assert.strictEqual(await page.locator('.tile').count(),16);
  assert.strictEqual(P.plans.length,16);assert.strictEqual(P.missing_geometry_ids.length,13);
  assert.strictEqual(await page.locator('#canvas>g').getAttribute('transform'),'matrix(0 1 -1 0 0 0)');
  assert.strictEqual(await page.locator('#canvas .part-outline-layer').count(),1);
  assert.ok((await page.locator('#overview').textContent()).includes('224个动作'));
  fs.mkdirSync(path.join(folder,'png'),{recursive:true});
  let stationChecks=0;
  for(let i=0;i<frozen.length;i++){
   const p=frozen[i];await page.locator('#plan').selectOption(p.plan_id);
   assert.strictEqual(P.plans[i].plan_hash,p.plan_hash);assert.deepStrictEqual(P.plans[i].assignments,p.assignments);
   assert.strictEqual(await page.locator('#assignments tr').count(),224);
   assert.strictEqual(await page.locator('#canvas .op:not(.hit)').count(),211);
   const visible=await page.locator('#assignments tr').evaluateAll(rows=>Object.fromEntries(rows.map(tr=>[tr.cells[0].textContent,tr.cells[2].textContent])));
   assert.deepStrictEqual(visible,p.assignments);
   assert.ok((await page.locator('#plan-title').textContent()).includes(`${i+1} / 16 · ${p.plan_id}`));
   for(const s of P.stations){
    await page.locator(`#legend button[data-station="${s.station_id}"]`).click();
    const expected=Object.keys(P.geometries).filter(o=>p.assignments[o]===s.station_id).sort();
    const actual=await page.locator('#canvas .op:not(.hit)[data-current="true"]').evaluateAll(es=>es.map(e=>e.dataset.operation).sort());
    assert.deepStrictEqual(actual,expected);assert.strictEqual(await page.locator('#assignments tr').count(),224);stationChecks++;
   }
   await page.locator('#legend button[data-station="ALL"]').click();await page.locator('#fit').click();
   await page.locator('#plan-sheet').screenshot({path:path.join(folder,'png',p.plan_id+'.png')});
  }
  await page.locator('#gallery').screenshot({path:path.join(folder,'all_16_contact_sheet.png')});
  await page.locator('.tile').nth(0).click();assert.strictEqual(await page.locator('#plan').inputValue(),frozen[0].plan_id);
  await page.locator('#next').click();assert.strictEqual(await page.locator('#plan').inputValue(),frozen[1].plan_id);
  await page.locator('#previous').click();
  // Search and the table retain assignment information even without geometry.
  await page.locator('#search').fill('O215');assert.strictEqual(await page.locator('#assignments tr').count(),1);
  await page.locator('#assignments tr').click();assert.ok((await page.locator('#operation-detail').textContent()).includes('没有可绘制几何'));
  assert.ok((await page.locator('#operation-detail').textContent()).includes(frozen[0].assignments.O215));
  await page.locator('#clear').click();assert.strictEqual(await page.locator('#assignments tr').count(),224);
  await page.locator('#type').selectOption('FLANGING');assert.strictEqual(await page.locator('#assignments tr').count(),46);
  await page.locator('#clear').click();
  await page.locator('#search').fill('O066');await page.locator('#assignments tr').click();
  assert.ok((await page.locator('#operation-detail').textContent()).includes('O066 · FLANGING'));
  const loc=await page.locator('#canvas .hit[data-operation="O066"] circle').first().evaluate(el=>{const p=new DOMPoint(el.cx.baseVal.value+el.r.baseVal.value,el.cy.baseVal.value).matrixTransform(el.getScreenCTM());return{x:p.x,y:p.y};});
  await page.mouse.click(loc.x,loc.y);assert.ok((await page.locator('#operation-detail').textContent()).includes('O066 · FLANGING'));
  await page.locator('#clear').click();await page.locator('#fit').click();const initial=await page.locator('#canvas').getAttribute('viewBox');
  await page.locator('#zoom-in').click();assert.notStrictEqual(await page.locator('#canvas').getAttribute('viewBox'),initial);
  await page.locator('#fit').click();assert.strictEqual(await page.locator('#canvas').getAttribute('viewBox'),initial);
  const r=await page.locator('#canvas').boundingBox();await page.mouse.move(r.x+25,r.y+25);await page.mouse.down();await page.mouse.move(r.x+65,r.y+45);await page.mouse.up();
  assert.notStrictEqual(await page.locator('#canvas').getAttribute('viewBox'),initial);await page.locator('#fit').click();
  const download=page.waitForEvent('download');await page.locator('#download-plan').click();const d=await download;
  assert.strictEqual(d.suggestedFilename(),frozen[0].plan_id+'_complete_assignments.json');
  const decoded=JSON.parse(fs.readFileSync(await d.path(),'utf8'));assert.deepStrictEqual(decoded.assignments,frozen[0].assignments);
  const svgDownload=page.waitForEvent('download');await page.locator('#download-svg').click();const sd=await svgDownload;
  const svg=fs.readFileSync(await sd.path(),'utf8');assert.ok(svg.includes('matrix(0 1 -1 0 0 0)'));assert.strictEqual((svg.match(/data-operation=/g)||[]).length,211);
  await page.screenshot({path:path.join(folder,'gallery_page_preview.png'),fullPage:true});
  assert.deepStrictEqual(errors,[]);assert.deepStrictEqual(requests,[]);
  const qa={passed:true,plans_checked:16,assignment_cells_checked:16*224,station_views_checked:stationChecks,
   visible_geometry_per_plan:211,missing_geometry_preserved:13,all_16_png_exported:true,all_16_tiles:true,
   exact_frozen_assignments:true,order_preserved:true,rotation_270:true,outline:true,actual_curve_hit:true,
   search_and_type_filter:true,pan_zoom_fit:true,json_and_svg_download_verified:true,page_errors:errors,http_requests:requests};
  fs.writeFileSync(path.join(folder,'browser_qa.json'),JSON.stringify(qa,null,2));console.log(JSON.stringify(qa));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exit(1);});
