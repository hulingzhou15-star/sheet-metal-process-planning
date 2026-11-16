import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {fileURLToPath, pathToFileURL} from 'node:url';
import vm from 'node:vm';

const here = path.dirname(fileURLToPath(import.meta.url));
const stage = path.resolve(process.argv[2] ??
  path.join(here, '../outputs/p003_candidate_motion_v01/stage'));
const fixtureUrl = pathToFileURL(path.join(stage, 'index.html'));
const pageUrl = new URL('http://localhost/replay/stage/index.html');
const html = await fs.readFile(fixtureUrl, 'utf8');
const metrics = {images: 0, clear: 0, pathSegments: 0, uploads: 0, frames: 0};
const fetches = [];
let frameTransforms = [];
const webgl = new Proxy({
  bufferData(_target, data) {
    assert.ok(ArrayBuffer.isView(data));
    metrics.uploads++;
  },
  createBuffer: () => ({}),
  getAttribLocation: (_program, name) => name,
  getProgramParameter: () => true,
  getShaderParameter: () => true,
  getUniformLocation: (_program, name) => name,
  uniformMatrix4fv(_location, _transpose, matrix) {
    assert.equal(matrix.length, 16);
    assert.ok(matrix.every(Number.isFinite));
    frameTransforms.push(Array.from(matrix));
  },
}, {get: (api, name) => api[name] ?? (() => {})});
const canvas2d = new Proxy({
  drawImage(image, ...args) {
    assert.ok(image.width > 0 && image.height > 0);
    assert.ok(args.every(Number.isFinite));
    metrics.images++;
  },
  clearRect() { metrics.clear++; },
}, {get: (api, name) => api[name] ?? (() => {})});

class Element {
  constructor(id = '') {
    this.id = id;
    this.value = '';
    this.children = [];
    this.replacements = 0;
    this.attributes = {};
    this.listeners = new Map();
    this.open = false;
    this.checked = false;
    this.clientWidth = 700;
    this.clientHeight = 520;
    this.width = 300;
    this.height = 150;
    this.ownerDocument = document;
  }

  add(option) {
    this.children.push(option);
    if (this.children.length === 1 && !this.value) this.value = option.value;
  }

  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this.replacements++; }
  setAttribute(name, value) { this.attributes[name] = value; }
  insertRow() { const row = new Element(); this.append(row); return row; }
  insertCell() { return this.insertRow(); }
  createTHead() { return this.insertRow(); }
  createTBody() { return this.insertRow(); }
  querySelector() { return new Element(); }
  getContext(kind) { return kind === 'webgl' ? webgl : canvas2d; }
  setPointerCapture() {}

  addEventListener(name, fn) {
    const listeners = this.listeners.get(name) ?? [];
    this.listeners.set(name, [...listeners, fn]);
  }

  async dispatch(type, extra = {}) {
    const event = {type, target: this, preventDefault() {}, ...extra};
    await this['on' + type]?.(event);
    await Promise.all((this.listeners.get(type) ?? []).map(listener => listener(event)));
  }
}

const nodes = new Map();
const document = {
  createElement: () => new Element(),
  createTextNode: text => text,
  querySelectorAll: () => [...nodes.values()],
  getElementById: id => nodes.get(id),
  defaultView: {devicePixelRatio: 2},
};
for (const match of html.matchAll(/\bid="([^"]+)"/g)) nodes.set(match[1], new Element(match[1]));
const node = id => {
  assert.ok(nodes.has(id), `Missing #${id} in replay page`);
  return nodes.get(id);
};
for (const [id, value] of Object.entries({comparison: 'stage', phase: 'entry', view: 'iso', quality: 'clear'})) {
  node(id).value = value;
}
for (const id of ['highlight', 'showGuides']) node(id).checked = true;

let fetchDelay = 0;
let fetchGate;
async function fetchAsset(url, {signal} = {}) {
  const asset = new URL(url, pageUrl);
  fetches.push(asset.pathname);
  if (fetchDelay) await delay(fetchDelay, undefined, {signal});
  if (fetchGate) await fetchGate;
  try {
    const relative = path.posix.relative('/replay/stage', decodeURIComponent(asset.pathname));
    return new Response(await fs.readFile(path.join(stage, relative), {signal}));
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    return new Response(null, {status: 404});
  }
}

async function decodePng(blob) {
  const png = Buffer.from(await blob.arrayBuffer());
  assert.equal(png.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
  return {width: png.readUInt32BE(16), height: png.readUInt32BE(20), close() {}};
}

let pausedFrames;
function requestFrame(callback) {
  if (!pausedFrames) return setTimeout(() => callback(performance.now()), 1);
  const ticket = {callback};
  pausedFrames.push(ticket);
  return ticket;
}
function cancelFrame(ticket) {
  if ('callback' in ticket) delete ticket.callback;
  else clearTimeout(ticket);
}

const runtime = vm.createContext({
  AbortController, AbortSignal, DecompressionStream, Response, URL, URLSearchParams,
  setTimeout, clearTimeout, performance, document, location: pageUrl, fetch: fetchAsset,
  createImageBitmap: decodePng,
  requestAnimationFrame: requestFrame,
  cancelAnimationFrame: cancelFrame,
  addEventListener() {},
  Option: class { constructor(text, value) { Object.assign(this, {text, value}); } },
  Path2D: class {
    moveTo() { metrics.pathSegments++; }
    lineTo() { metrics.pathSegments++; }
    closePath() {}
    addPath() {}
  },
});
runtime.window = runtime;
document.defaultView = runtime;
runtime.devicePixelRatio = 2;

let decodingAssets = 0;
for (const match of html.matchAll(/<script src="([^"]+)"><\/script>/g)) {
  const script = new URL(match[1], fixtureUrl);
  const all = await fs.readFile(script, 'utf8');
  if (script.pathname.endsWith('/review.js')) {
    const render = runtime.FiniteMaterialRenderer.prototype.render;
    runtime.FiniteMaterialRenderer.prototype.render = function (...args) {
      metrics.frames++;
      frameTransforms = [];
      return render.apply(this, args);
    };
  }
  await vm.runInContext(all, runtime, {filename: match[1]});
  if (script.pathname.endsWith('/p003_replay_assets.js')) {
    const assets = runtime.P003ReplayAssets;
    for (const method of ['mesh', 'raster']) {
      const load = assets[method];
      assets[method] = async (...args) => {
        decodingAssets++;
        try { return await load(...args); }
        finally { decodingAssets--; }
      };
    }
  }
}

const getState = () => runtime.p003StageReview?.getState();
function checkNoError() {
  assert.ok(node('error').hidden, node('error').textContent || 'Replay error banner is visible');
}
async function until(condition, description) {
  const deadline = performance.now() + 10_000;
  while (!condition()) {
    checkNoError();
    assert.ok(performance.now() < deadline, `Timed out waiting for ${description}`);
    await delay(5);
  }
  checkNoError();
}
async function seek(index) {
  const sample = node('sample');
  sample.value = String(index);
  await sample.dispatch('input');
  await until(() => getState().index === index, `sample ${index + 1}`);
}
async function openDetails(id) {
  const element = node(id);
  element.open = true;
  await element.dispatch('toggle');
  checkNoError();
}

checkNoError();
assert.ok(getState(), 'Generated replay starts');
assert.ok(metrics.images > 0, '2D material is painted from a prerendered image');
assert.ok(fetches.some(url => url.endsWith('.f32.gz')), '3D material loads packed Float32 arrays');
assert.ok(metrics.pathSegments < 10_000, 'Initial 2D path construction is limited to feature outlines');

const plan = getState().plan;
const collision = JSON.parse(await fs.readFile(new URL(`../${plan}/collision_report.json`, fixtureUrl), 'utf8'));
const runs = collision.runs;
const firstMotion = runs.findIndex(run => run.phase === 'motion');
assert.ok(firstMotion >= 0);
const station = runs[firstMotion].station_id;
const entry = runs.findIndex(run => run.station_id === station && run.phase === 'entry');
const completed = runs.findIndex(run => run.station_id === station && run.phase === 'completed');
const firstPrepared = runs.findIndex(run =>
  run.material_id !== runs[0].material_id && run.material_id !== runs[firstMotion].material_id);
assert.ok(firstPrepared > 0, 'Replay fixture has an intermediate material state');
assert.ok(firstMotion + 4 < completed, 'Replay fixture has consecutive motion frames');

// A burst of changes during a slow asset read must settle on the last sample.
fetchDelay = 80;
const burstFrames = metrics.frames;
const beforeBurst = fetches.length;
const sample = node('sample');
sample.value = String(firstMotion);
const initialSliderRequest = sample.dispatch('input');
await until(() => fetches.length > beforeBurst, 'the first uncached slider load');
const pending = [];
for (let i = 0; i < 54; i++) {
  sample.value = String(i % 2 ? firstPrepared : firstMotion + 2);
  pending.push(sample.dispatch('input'));
  await Promise.resolve();
}
sample.value = String(firstMotion + 1);
pending.push(sample.dispatch('input'));
await Promise.all([initialSliderRequest, ...pending]);
await until(() => getState().index === firstMotion + 1, 'the latest slider sample');
assert.ok(metrics.frames - burstFrames <= 2, 'Slider burst skips intermediate frame renders');
fetchDelay = 0;
const skippedGeometry = Object.values(runs[firstPrepared].geometry_ids).filter(
  id => !Object.values(runs[0].geometry_ids).includes(id) &&
    !Object.values(runs[firstMotion].geometry_ids).includes(id));
assert.ok(skippedGeometry.length > 0, 'Intermediate sample has a distinct geometry asset');
assert.ok(!fetches.slice(beforeBurst).some(url => skippedGeometry.some(id => url.includes('/' + id + '.'))),
  'Intermediate slider states do not start new material requests');

const first = {...metrics};
const firstTransforms = frameTransforms;
const flat = runs[firstMotion].material_id;
const featureTable = node('featureList').replacements;
const collisionTable = node('collisionRows').replacements;
await seek(firstMotion + 2);
await seek(firstMotion + 3);
assert.equal(getState().materialId, flat);
assert.equal(metrics.images, first.images, 'Unchanged 2D state skips image draws');
assert.equal(metrics.clear, first.clear, 'Unchanged 2D state skips clears');
assert.equal(metrics.uploads, first.uploads, 'Unchanged geometry and rigid outlines skip WebGL uploads');
assert.ok(metrics.frames >= first.frames + 2, '3D redraws the changed transforms');
assert.notDeepEqual(frameTransforms, firstTransforms, 'GPU draws use different motion transforms');
assert.equal(node('featureList').replacements, featureTable, 'Closed feature table stays intact');
assert.equal(node('collisionRows').replacements, collisionTable, 'Closed collision table stays intact');

// The previous asset request can finish while the newer slider event is waiting for its render frame.
const raceTarget = firstMotion + 4;
let releaseFetch;
fetchGate = new Promise(resolve => { releaseFetch = resolve; });
sample.value = String(firstPrepared);
await sample.dispatch('input');
await until(() => decodingAssets > 0, 'the previous material loads');
pausedFrames = [];
sample.value = String(raceTarget);
await sample.dispatch('input');
fetchGate = undefined;
releaseFetch();
await until(() => decodingAssets === 0, 'the previous material finishes loading');
const framesToResume = pausedFrames;
pausedFrames = undefined;
for (const {callback} of framesToResume) callback?.(performance.now());
await until(() => getState().index === raceTarget, 'the newer slider event survives the older asset response');

await openDetails('stageFeatures');
await until(() => node('featureList').replacements > featureTable, 'feature table generation');
assert.equal(node('featureList').children[0].children[1].children.length,
  runs[raceTarget].current_operation_ids.length);
await openDetails('sampleIssues');
await until(() => node('collisionRows').replacements > collisionTable, 'sample diagnosis table generation');
assert.match(node('collisionStatus').textContent,
  new RegExp(`检查 ${runs[raceTarget].reference_geometry.pair_count} 对`));
await openDetails('planIssues');
await until(() => node('planIssueRows').replacements > 0, 'plan diagnosis table generation');
assert.equal(node('planIssueRows').children[0].children[1].children.length, collision.summary.issues.length);
const diagnosticSample = runs.findIndex(run => run.reference_geometry.narrow_phase_pairs.some(
  pair => pair.status === 'OVERLAP' && pair.intersection_world_aabb_mm));
assert.ok(diagnosticSample >= 0, 'Replay fixture has a locatable overlap');
await seek(diagnosticSample);
const diagnosticRows = node('collisionRows').children[0].children[1].children;
const focus = diagnosticRows.map(row => row.children[3].children[0]).find(button => !button.disabled);
assert.ok(focus, 'Overlap detail provides a location action');
const framesBeforeFocus = metrics.frames;
await focus.dispatch('click');
await until(() => metrics.frames > framesBeforeFocus, 'drawing the selected overlap location');

await seek(entry);
await node('playStation').dispatch('click');
await until(() => getState().playing === 'playStation', 'station playback');
await until(() => getState().playing === null && getState().index === completed, 'station playback at completion');
assert.equal(getState().phase, 'completed');
assert.equal(node('playStation').attributes['aria-pressed'], 'false');

node('comparison').value = 'step';
await node('comparison').dispatch('change');
await until(() => node('state').textContent.includes('STEP 成品'), 'STEP comparison');
node('comparison').value = 'stage';
await node('comparison').dispatch('change');
await until(() => !node('state').textContent.includes('STEP 成品'), 'stage comparison');
console.log('P003 replay checks passed: packed loading, raster reuse, WebGL reuse, slider, diagnostics, playback, STEP.');
