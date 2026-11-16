import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
import {browserExecutable} from './browser_runtime.mjs';

const output = fileURLToPath(new URL('../outputs/', import.meta.url));
const directory = 'p003_motion_regeneration_v01';
const url = process.argv[2] ?? 'http://p003.test/' + directory + '/stage/index.html';
const read = async (...segments) => JSON.parse(await fs.readFile(path.join(output, directory, ...segments), 'utf8'));
const catalogue = await read('plans.json');
const summary = await read('summary.json');
const browser = await chromium.launch({
  executablePath: browserExecutable(),
  headless: true,
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
    await page.route('http://p003.test/**', async route => {
      const requested = decodeURIComponent(new URL(route.request().url()).pathname);
      const file = path.resolve(output, '.' + requested);
      assert.ok(path.relative(output, file).startsWith(directory + path.sep));
      await route.fulfill({path: file});
    });
  }
  await page.addInitScript(() => {
    window.draws = {flat: 0, uploads: 0};
    for (const [prototype, method, count] of [
      [CanvasRenderingContext2D.prototype, 'clearRect', 'flat'],
      [WebGLRenderingContext.prototype, 'bufferData', 'uploads'],
    ]) {
      const original = prototype[method];
      prototype[method] = function (...arguments_) {
        draws[count]++;
        return original.apply(this, arguments_);
      };
    }
  });
  const readState = () => page.evaluate(() => p003StageReview.getState());
  const waitForState = async expected => {
    await page.waitForFunction(values => {
      const state = window.p003StageReview?.getState();
      return state && Object.entries(values).every(([key, value]) => state[key] === value);
    }, expected);
    assert.ok(await page.locator('#error').isHidden(), await page.locator('#error').textContent());
  };
  const text = id => page.locator('#' + id).innerText();
  const seek = async index => {
    await page.locator('#sample').evaluate((slider, value) => {
      slider.value = String(value);
      slider.dispatchEvent(new Event('input'));
    }, index);
    await waitForState({index});
  };

  const response = await page.goto(url);
  assert.equal(response.status(), 200);
  await waitForState({plan: catalogue.ranking[0]});
  const options = await page.locator('#plan option').evaluateAll(nodes => nodes.map(node => node.value));
  assert.deepEqual(options, catalogue.ranking);
  assert.equal(options.length, 16);
  for (const plan of options) {
    await page.selectOption('#plan', plan);
    await waitForState({plan});
    assert.equal(await page.locator('#sample').getAttribute('max'), '393');
    assert.match(await text('planStats'), /394/);
  }
  assert.equal(summary.selected_sample_count, 6304);

  const plan = 'P003_R024';
  const raw = await read('selected', plan, 'collision_report.json');
  await page.selectOption('#plan', plan);
  await waitForState({plan});
  await seek(0);
  const expectedReference = await text('motionReference');
  assert.match(expectedReference, /DXF/);
  const afterPreparation = raw.runs.findIndex(row => row.station_id === 'OP20' && row.phase === 'completed');
  await seek(afterPreparation);
  assert.match(await text('motionReference'), /STEP/);
  assert.notEqual((await readState()).materialId, raw.runs[0].material_id);
  const stations = await page.locator('#stationOverview tbody tr').evaluateAll(rows => rows.map(row =>
    Array.from(row.cells, cell => cell.textContent)));
  const expectedStations = Array.from(new Set(raw.runs.map(row => row.station_id))).map(station => {
    const samples = raw.runs.filter(row => row.station_id === station);
    const operations = samples[0].current_operation_ids;
    const bendCount = samples.some(row => row.phase === 'motion') ? operations.length : 0;
    return [station, String(operations.length), String(bendCount),
      String(samples.length), '0', '0'];
  });
  assert.deepEqual(stations, expectedStations);
  await page.locator('#stationOverview').getByRole('button', {name: 'OP30', exact: true}).click();
  await waitForState({station: 'OP30', phase: 'entry'});

  for (const [id, operation, angle] of [['curveExample', 'O069', 135], ['drawExample', 'O120', 90]]) {
    await page.click('#' + id);
    await waitForState({station: raw.assignments[operation], phase: 'completed', selected: operation});
    assert.equal(Number((await text('jointAngle')).match(/([-+.\d]+)°/)[1]), angle);
  }

  const station = raw.assignments.O120;
  const first = raw.runs.findIndex(row => row.station_id === station && row.phase === 'motion');
  await seek(first + 32);
  const before = await page.evaluate(() => ({...draws}));
  await seek(first + 33);
  const state = await readState();
  assert.equal(state.tau, 34/96);
  assert.deepEqual(await page.evaluate(() => draws), before, 'same-material samples reuse 2D and GPU buffers');
  await page.locator('#stageFeatures > summary').click();
  for (const operation of raw.runs[first + 33].current_operation_ids) {
    await page.locator('#featureList').getByRole('button', {name: operation, exact: true}).waitFor();
  }
  await page.locator('#sampleIssues > summary').click();
  assert.match(await text('collisionStatus'), /45 对材料区域，0 对交叠，0 对未决/);

  await page.selectOption('#speed', '4');
  const start = raw.runs.findIndex(row => row.station_id === station && row.phase === 'entry');
  const finish = raw.runs.findIndex(row => row.station_id === station && row.phase === 'completed');
  await seek(first + 4);
  await page.evaluate(() => {
    window.visited = [];
    new MutationObserver(() => visited.push(p003StageReview.getState().index)).observe(
      document.getElementById('progress'), {childList: true, subtree: true, characterData: true},
    );
  });
  await page.click('#playStation');
  await waitForState({index: finish, phase: 'completed', playing: null});
  const visited = await page.evaluate(() => Array.from(new Set(window.visited)));
  assert.equal(visited[0], start);
  assert.equal(visited.at(-1), finish);
  assert.ok(visited.length > 2 && visited.length < finish - start + 1);

  await seek(finish - 1);
  await page.evaluate(() => { window.visited = []; });
  await page.click('#playPlan');
  await page.waitForFunction(index => window.visited.includes(index), finish + 1);
  await page.click('#playPlan');
  assert.deepEqual((await page.evaluate(() => Array.from(new Set(window.visited)))).slice(0, 3),
    [finish - 1, finish, finish + 1]);

  const links = await page.evaluate(async () => {
    const report = await fetch(document.getElementById('report').href);
    const step = await fetch(document.getElementById('step').href);
    const material = await fetch(document.getElementById('materialAudit').href);
    const firstChunk = await step.body.getReader().read();
    return {
      report: report.status, reportPlan: (await report.json()).plan_id, step: step.status, material: material.status,
      header: new TextDecoder().decode(firstChunk.value).trimStart().slice(0, 13),
    };
  });
  assert.deepEqual(links, {report: 200, reportPlan: plan, step: 200, material: 200, header: 'ISO-10303-21;'});
  await page.selectOption('#comparison', 'step');
  await page.waitForFunction(() => document.getElementById('state').textContent.includes('STEP 成品'));
  await page.selectOption('#comparison', 'stage');
  await page.waitForFunction(() => !document.getElementById('state').textContent.includes('STEP 成品'));
  assert.deepEqual(errors, []);
  console.log('P003 regeneration replay: all 16 plans, DXF and STEP references, 96-grid motion, J297 endpoints, 4x playback, raw report and STEP links passed.');
} finally {
  await browser.close();
}
