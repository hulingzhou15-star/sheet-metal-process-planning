import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
import {browserExecutable} from './browser_runtime.mjs';

const output = fileURLToPath(new URL('../outputs/', import.meta.url));
const directory = 'p004_motion_regeneration_v01';
const url = process.argv[2] ?? 'http://p004.test/' + directory + '/stage/index.html';
const read = async (...names) => JSON.parse(await fs.readFile(path.join(output, directory, ...names), 'utf8'));
const {ranking} = await read('plans.json');
const model = await read('model.json');
const data = await read('stage', 'data.json');
assert.equal(data.bendJoints.O013.length, 4);
assert.equal(data.features.O013.groups.length, 4);
const browser = await chromium.launch({
  executablePath: browserExecutable(), headless: true,
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
});

try {
  const page = await browser.newPage({viewport: {width: 1600, height: 1000}});
  page.setDefaultTimeout(20_000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    if (response.status() >= 400 && !response.url().endsWith('/favicon.ico')) {
      errors.push(response.status() + ' ' + response.url());
    }
  });
  if (!process.argv[2]) {
    await page.route('http://p004.test/**', async route => {
      const requested = decodeURIComponent(new URL(route.request().url()).pathname);
      const file = path.resolve(output, '.' + requested);
      assert.ok(path.relative(output, file).startsWith(directory + path.sep));
      await route.fulfill({path: file});
    });
  }
  await page.addInitScript(() => {
    window.draws = {flat: 0, uploads: 0};
    for (const [prototype, method, key] of [
      [CanvasRenderingContext2D.prototype, 'clearRect', 'flat'],
      [WebGLRenderingContext.prototype, 'bufferData', 'uploads'],
    ]) {
      const original = prototype[method];
      prototype[method] = function (...args) {
        draws[key]++;
        return original.apply(this, args);
      };
    }
  });
  const text = id => page.locator('#' + id).innerText();
  const waitForState = async expected => {
    await page.waitForFunction(values => {
      const state = window.p004StageReview?.getState();
      return state && Object.entries(values).every(([key, value]) => state[key] === value);
    }, expected);
    assert.ok(await page.locator('#error').isHidden(), await page.locator('#error').textContent());
  };
  async function seek(index) {
    await page.locator('#sample').evaluate((slider, value) => {
      slider.value = String(value);
      slider.dispatchEvent(new Event('input'));
    }, index);
    await waitForState({index});
  }

  assert.equal((await page.goto(url)).status(), 200);
  await waitForState({plan: ranking[0]});
  assert.deepEqual(await page.locator('#plan option').evaluateAll(rows => rows.map(r => r.value)), ranking);
  const bendIds = new Set(model.operations.filter(o => o.operation_type === 'BEND').map(o => o.operation_id));
  let firstWithOP40Motion;
  let lastReport;
  for (const plan of ranking) {
    await page.selectOption('#plan', plan);
    await waitForState({plan});
    const report = await read('selected', plan, 'collision_report.json');
    assert.equal(await page.locator('#sample').getAttribute('max'), String(report.runs.length - 1));
    assert.match(await text('planStats'), /0 个正体积交叠 · 0 个内核未决/);
    const stations = await page.locator('#stationOverview tbody tr').evaluateAll(rows =>
      rows.map(row => Array.from(row.cells, cell => cell.textContent)));
    const expected = model.stations.map(({station_id: id}) => {
      const runs = report.runs.filter(row => row.station_id === id);
      const ops = runs[0].current_operation_ids;
      return [id, String(ops.length), String(ops.filter(oid => bendIds.has(oid)).length), String(runs.length), '0', '0'];
    });
    assert.deepEqual(stations, expected);
    if (!firstWithOP40Motion && report.runs.some(r => r.station_id === 'OP40' && r.phase === 'motion')) {
      firstWithOP40Motion = report;
    }
    lastReport = report;
  }
  const report = firstWithOP40Motion || lastReport;
  await page.selectOption('#plan', report.plan_id);
  await waitForState({plan: report.plan_id});
  await seek(0);
  assert.match(await text('motionReference'), /DXF 展开配准/);
  const op40entry = report.runs.findIndex(r => r.station_id === 'OP40' && r.phase === 'entry');
  const op40complete = report.runs.findIndex(r => r.station_id === 'OP40' && r.phase === 'completed');
  await page.locator('#stationOverview').getByRole('button', {name: 'OP40', exact: true}).click();
  await waitForState({index: op40entry});
  assert.match(await text('motionReference'), /STEP/);
  assert.match(await text('state'), /已局部成形 14\/18/);
  if (firstWithOP40Motion) {
    await seek(op40entry + 33);
    assert.match(await text('motionReference'), /进入态材料/);
    assert.match(await text('state'), /已局部成形 14\/18/);
    const before = await page.evaluate(() => ({...draws}));
    await seek(op40entry + 34);
    assert.match(await text('progress'), /34\/96 等分/);
    assert.deepEqual(await page.evaluate(() => draws), before, 'same-material samples reuse 2D and GPU buffers');
  }
  await seek(op40complete);
  assert.match(await text('state'), /已局部成形 18\/18/);

  await page.selectOption('#operation', 'O013');
  await waitForState({selected: 'O013'});
  await page.click('#after');
  await waitForState({station: report.assignments.O013, phase: 'completed'});
  assert.match(await text('jointAngle'), /10\.00° \/ 10\.00° \/ 10\.00° \/ 10\.00°/);
  const pairCount = model.cells.length * (model.cells.length - 1) / 2;
  await page.locator('#sampleIssues > summary').click();
  assert.match(await text('collisionStatus'), new RegExp(pairCount + ' 对材料区域，0 对交叠，0 对未决'));

  await page.selectOption('#speed', '4');
  const station = report.assignments.O013;
  await page.selectOption('#station', station);
  await waitForState({station,phase:'entry'});
  await page.click('#playStation');
  await waitForState({station,phase:'completed',playing:null});
  const links = await page.evaluate(async () => {
    const [report,step,material] = await Promise.all(['report','step','materialAudit'].map(id =>
      fetch(document.getElementById(id).href)));
    const firstChunk = await step.body.getReader().read();
    return {report:report.status, plan:(await report.json()).plan_id, step:step.status, material:material.status,
      header:new TextDecoder().decode(firstChunk.value).trimStart().slice(0, 13)};
  });
  assert.deepEqual(links, {report:200,plan:report.plan_id,step:200,material:200,header:'ISO-10303-21;'});
  await page.selectOption('#comparison', 'step');
  await page.waitForFunction(() => document.getElementById('state').textContent.includes('STEP 成品对照'));
  assert.match(await text('state'), /STEP 成品对照/);
  assert.deepEqual(errors, []);
  console.log('P004 replay verified: '+ranking.length+' plans, OP40 switching, four O013 supports, playback, report and STEP.');
} finally {
  await browser.close();
}
