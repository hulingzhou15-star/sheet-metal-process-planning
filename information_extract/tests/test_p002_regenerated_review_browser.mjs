import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
import {browserExecutable} from './browser_runtime.mjs';

const output = fileURLToPath(new URL('../outputs/', import.meta.url));
const directory = process.argv[2] ?? 'p002_motion_regeneration_v01';
const root = path.join(output, directory);
const read = async (...files) => JSON.parse(await fs.readFile(path.join(root, ...files), 'utf8'));
const {ranking,plans} = await read('plans.json');
const summary = await read('summary.json');
assert.equal(summary.selected_count, ranking.length);
const browser = await chromium.launch({
  executablePath: browserExecutable(), headless: true,
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
});

try {
  const page = await browser.newPage({viewport: {width: 1600, height: 1000}});
  page.setDefaultTimeout(30_000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    if (response.status() >= 400 && !response.url().endsWith('/favicon.ico')) {
      errors.push(response.status() + ' ' + response.url());
    }
  });
  await page.route('http://p002.test/**', async route => {
    const requested = decodeURIComponent(new URL(route.request().url()).pathname);
    const file = path.resolve(output, '.' + requested);
    assert.ok([directory, 'p002_candidate_motion_v05'].some(name =>
      path.relative(output, file).startsWith(name + path.sep)));
    await route.fulfill({path: file});
  });
  const base = 'http://p002.test/' + directory + '/';
  assert.equal((await page.goto(base + 'index.html')).status(), 200);
  assert.match(await page.title(), /P002/);
  if (!ranking.length) {
    assert.match(await page.locator('main').innerText(), /入选 0 个/);
    assert.equal(await page.getByRole('link', {name: '入选方案二维与三维回放'}).count(), 0);
    const history = page.getByRole('link', {name: '历史方案回放'});
    const linkCheck = await history.evaluate(a => fetch(a.href).then(response => response.status));
    assert.equal(linkCheck, 200);
    assert.deepEqual(errors, []);
    console.log('P002 screening overview verified with no selected solutions.');
  } else {
    await page.getByRole('link', {name: '入选方案二维与三维回放'}).click();
    const waitForState = async expected => {
      await page.waitForFunction(values => {
        const state = window.p002StageReview?.getState();
        return state && Object.entries(values).every(([key, value]) => state[key] === value);
      }, expected);
      assert.ok(await page.locator('#error').isHidden(), await page.locator('#error').textContent());
    };
    await waitForState({plan: ranking[0]});
    assert.deepEqual(await page.locator('#plan option').evaluateAll(rows => rows.map(row => row.value)), ranking);
    for (const id of ranking) {
      const plan = plans.find(row => row.plan_id === id);
      const record = await read(id,'collision_report.json');
      assert.equal(record.subdivisions, 96);
      assert.equal(record.sample_count, plan.motion_verification.sample_count);
      assert.equal(record.summary.counts.OVERLAP ?? 0, 0);
      assert.equal(record.summary.counts.UNKNOWN_KERNEL_FAILURE ?? 0, 0);
      for (const sample of record.runs) {
        const check = sample.reference_geometry;
        assert.equal(Object.values(check.counts).reduce((sum, value) => sum + value, 0), check.pair_count);
        assert.equal(check.pair_count, 171);
      }
      await page.selectOption('#plan', id);
      await waitForState({plan: id});
    }
    const plan = plans.find(row => row.plan_id === ranking.at(-1));
    const record = await read(plan.plan_id,'collision_report.json');
    await page.click('#curveExample');
    await waitForState({station: plan.assignments.O088, phase: 'motion', selected: 'O088'});
    assert.match(await page.locator('#state').textContent(), /已去料 \d+\/79/);
    await page.click('#after');
    await waitForState({station: plan.assignments.O088, phase: 'completed', selected: 'O088'});
    const first = record.runs.findIndex(row => row.station_id === plan.assignments.O088 && row.phase === 'entry');
    const last = record.runs.findIndex(row => row.station_id === plan.assignments.O088 && row.phase === 'completed');
    await page.selectOption('#speed','4');
    await page.evaluate(() => {
      window.visited = [];
      new MutationObserver(() => visited.push(p002StageReview.getState().index)).observe(
        document.getElementById('progress'), {childList: true, subtree: true, characterData: true},
      );
    });
    await page.click('#playStation');
    await page.waitForFunction(() => window.visited.length > 1);
    await waitForState({index: last, phase: 'completed', playing: null});
    const visited = await page.evaluate(() => Array.from(new Set(window.visited)));
    assert.equal(visited[0], first);
    assert.equal(visited.at(-1), last);
    assert.ok(visited.length > 2 && visited.length < last-first+1);
    await page.selectOption('#comparison', 'step');
    await waitForState({comparison: 'step'});
    const downloads = await page.evaluate(async () => {
      const report = await fetch(document.getElementById('report').href);
      const step = await fetch(document.getElementById('step').href);
      const first = await step.body.getReader().read();
      return [(await report.json()).plan_id, new TextDecoder().decode(first.value).trimStart().slice(0, 13)];
    });
    assert.deepEqual(downloads, [plan.plan_id,'ISO-10303-21;']);
    assert.deepEqual(errors, []);
    console.log('P002 regenerated replays verified: '+ranking.length+' candidates, curved motion, STEP and report.');
  }
} finally {
  await browser.close();
}
