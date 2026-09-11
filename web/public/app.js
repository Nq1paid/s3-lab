/* S3 LAB -- browser front end.
 *
 * This file draws. It computes nothing: every number on screen comes from the
 * engine on your machine, through web/server.py, which is the same engine the
 * terminal app drives. Two front ends that did their own arithmetic would
 * eventually disagree about the same run, and a backtester with two answers is
 * worse than one with none.
 *
 * When the engine is not running the UI stays usable and says so. It never
 * substitutes a placeholder number for a real one.
 */

'use strict';

const DEFAULT_ENGINE = 'http://127.0.0.1:8765';
const KEYS = { d: 'data', s: 'strategies', t: 'test', r: 'run', e: 'results', h: 'history', m: 'settings' };

const S = {
  engine: localStorage.getItem('s3.engine') || DEFAULT_ENGINE,
  online: false,
  screen: 'test',
  state: null,
  results: null,
  view: 'curve',
  runJob: null,
  marked: [],
};

/* ------------------------------------------------------------ utilities */

const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') n.className = v;
    else if (k === 'html') n.innerHTML = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return n;
};
const fmt = (x, d = 2) =>
  x === null || x === undefined || Number.isNaN(x) ? '—'
    : Number(x).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const int = (x) => (x === null || x === undefined || Number.isNaN(x) ? '—' : Number(x).toLocaleString('en-US'));
const bytes = (n) => (n > 1e9 ? (n / 1e9).toFixed(1) + ' GB' : n > 1e6 ? (n / 1e6).toFixed(0) + ' MB' : (n / 1e3).toFixed(0) + ' kB');

function toast(msg, ms = 4200) {
  const t = el('div', {}, msg);
  $('#toast').append(t);
  setTimeout(() => t.remove(), ms);
}

async function api(path, body) {
  const opts = body === undefined
    ? {}
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const res = await fetch(S.engine + path, opts);
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { error: text.slice(0, 300) }; }
  if (!res.ok) throw new Error(data.error || res.status + ' ' + res.statusText);
  return data;
}

/* --------------------------------------------------------- engine status */

async function ping() {
  try {
    await api('/api/health');
    setOnline(true);
  } catch {
    setOnline(false);
  }
}

function setOnline(up) {
  const changed = up !== S.online;
  S.online = up;
  $('#enginedot').classList.toggle('live', up);
  $('#enginetext').textContent = up ? 'ENGINE LIVE' : 'ENGINE OFFLINE';
  if (changed && up) { loadState(); render(); }
  if (changed && !up) render();
  document.querySelectorAll('button.act').forEach((b) => { b.disabled = !up; });
}

function offlineBanner() {
  return el('div', { class: 'banner' },
    el('strong', {}, 'The engine is not running on this machine. '),
    'Start ', el('strong', {}, 'Backtest Lab'), ' from your Desktop and this page will ',
    'connect by itself. Your data never leaves your PC — this page only sends it ',
    'instructions.');
}

/* ---------------------------------------------------------------- charts */

/* Vector, at pixel resolution. The terminal drew these in braille because a
   cell was the smallest thing it had; there is no reason to keep that here.
   Still no colour: brightness and weight carry the meaning, exactly as in the
   panel rules, and a losing fold is never red. */

const NS = 'http://www.w3.org/2000/svg';

function svgEl(w, h) {
  const s = document.createElementNS(NS, 'svg');
  s.setAttribute('viewBox', '0 0 ' + w + ' ' + h);
  s.setAttribute('class', 'chart');
  s.setAttribute('preserveAspectRatio', 'none');
  s.style.height = h + 'px';
  s.add = (tag, attrs, text) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (text !== undefined) n.textContent = text;
    s.append(n);
    return n;
  };
  return s;
}

function lineChart(series, opts = {}) {
  const w = 1040, h = 400, L = 4, R = 4, T = 44, B = 58;
  const svg = svgEl(w, h);
  const all = opts.comparison ? series.concat(opts.comparison) : series;
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const X = (i, n) => L + (i / (n - 1)) * (w - L - R);
  const Y = (v) => h - B - ((v - lo) / span) * (h - T - B);

  for (const b of opts.boundaries || []) {
    const x = L + b * (w - L - R);
    svg.add('line', { class: 'grid', x1: x, x2: x, y1: T - 10, y2: h - B });
  }
  if (opts.baseline !== undefined && opts.baseline >= lo && opts.baseline <= hi) {
    svg.add('line', { class: 'grid', x1: L, x2: w - R, y1: Y(opts.baseline), y2: Y(opts.baseline) });
  }
  const draw = (arr, cls) => svg.add('path', {
    class: cls,
    d: arr.map((v, i) => (i ? 'L' : 'M') + X(i, arr.length).toFixed(1) + ' ' + Y(v).toFixed(1)).join(' '),
  });
  if (opts.comparison) draw(opts.comparison, 'cmp');
  draw(series, 'line');

  svg.add('text', { x: L, y: T - 20 }, int(Math.round(hi)));
  if (opts.from) {
    svg.add('text', { x: L, y: h - 22 }, opts.from);
    svg.add('text', { x: w - R, y: h - 22, 'text-anchor': 'end' }, opts.to);
  }
  return svg;
}

function barChart(labels, values, markIndex) {
  const w = 1040, pad = 8, rowH = 26, T = 16;
  const h = T * 2 + labels.length * rowH;
  const svg = svgEl(w, h);
  const max = Math.max(...values.map(Math.abs)) || 1;
  const nameW = 110, valW = 110;
  const mid = pad + nameW + (w - pad * 2 - nameW - valW) / 2;
  const half = (w - pad * 2 - nameW - valW) / 2;
  svg.add('line', { class: 'axis', x1: mid, x2: mid, y1: T - 4, y2: h - T + 4 });
  labels.forEach((name, i) => {
    const y = T + i * rowH, v = values[i];
    const len = Math.max((Math.abs(v) / max) * half, 1);
    svg.add('text', { x: pad, y: y + 17, class: 'lab' }, name);
    svg.add('rect', { class: 'bar' + (i === markIndex ? ' hi' : ''),
                      x: v >= 0 ? mid : mid - len, y: y + 8, width: len, height: 9 });
    svg.add('text', { x: w - pad, y: y + 17, 'text-anchor': 'end' }, int(Math.round(v)));
  });
  return svg;
}

function histogram(values, bins = 54) {
  const w = 1040, h = 400, pad = 4, T = 30, B = 46;
  const lo = Math.min(...values), hi = Math.max(...values);
  const step = (hi - lo) / bins || 1;
  const counts = new Array(bins).fill(0);
  for (const v of values) counts[Math.min(bins - 1, Math.floor((v - lo) / step))]++;
  const peak = Math.max(...counts) || 1;
  const svg = svgEl(w, h);
  const bw = (w - pad * 2) / bins;
  counts.forEach((c, i) => {
    const bh = (c / peak) * (h - T - B);
    svg.add('rect', { class: 'bar', x: pad + i * bw + 0.6, y: h - B - bh, width: bw - 1.2, height: bh });
  });
  svg.add('line', { class: 'grid', x1: pad, x2: w - pad, y1: h - B, y2: h - B });
  svg.add('text', { x: pad, y: h - 22 }, int(Math.round(lo)));
  svg.add('text', { x: w - pad, y: h - 22, 'text-anchor': 'end' }, int(Math.round(hi)));
  return svg;
}

/* --------------------------------------------------------------- screens */

function show(name) {
  S.screen = name;
  document.querySelectorAll('.screen').forEach((s) => { s.hidden = s.id !== 'screen-' + name; });
  document.querySelectorAll('#nav button').forEach((b) => {
    if (b.dataset.go === name) b.setAttribute('aria-current', 'page');
    else b.removeAttribute('aria-current');
  });
  location.hash = name;
  render();
}

function render() {
  if (S.screen === 'data') renderData();
  if (S.screen === 'strategies') renderStrategies();
  if (S.screen === 'test') renderTest();
  if (S.screen === 'run') renderRun();
  if (S.screen === 'results') renderResults();
  if (S.screen === 'history') renderHistory();
  if (S.screen === 'settings') renderSettings();
}

/* ---- Data */

async function renderData() {
  const box = $('#data-banner');
  box.replaceChildren(S.online ? '' : offlineBanner());
  if (!S.online) { $('#rawlist').replaceChildren(); $('#cachedlist').replaceChildren(); return; }
  let d;
  try { d = await api('/api/data'); } catch (e) { toast(String(e.message)); return; }

  $('#rawlist').replaceChildren(d.raw.length ? table(
    ['File', 'Size', 'State', ''],
    d.raw.map((r) => [
      el('span', { class: 'key' }, r.name),
      bytes(r.bytes),
      r.imported ? 'imported' : 'not imported',
      r.imported ? '' : el('button', { class: 'ghost', onclick: () => startImport(r.path) }, 'Import'),
    ])
  ) : el('p', { class: 'empty' }, 'No files in ' + d.data_dir));

  $('#cachedlist').replaceChildren(d.cached.length ? table(
    ['Series', 'Bars', 'Span', 'Timezone', 'Dupes', 'Bad OHLC', 'Zero vol'],
    d.cached.map((c) => [
      el('span', { class: 'key' }, c.name),
      int(c.bars),
      (c.first || '?') + ' → ' + (c.last || '?'),
      // A timezone that was inferred but not confirmed by the session calendar
      // is flagged, never presented as settled.
      (c.timezone || '?') + (c.tz_conclusive === false ? ' (unconfirmed)' : ''),
      int(c.duplicates), int(c.ohlc_violations), int(c.zero_volume),
    ])
  ) : el('p', { class: 'empty' }, 'Nothing imported yet.'));
}

async function startImport(path) {
  try {
    const job = await api('/api/data/import', { path });
    toast('Importing — this reads the whole file, give it a moment.');
    followJob(job.id, '#import-progress', () => renderData());
  } catch (e) { toast(String(e.message)); }
}

/* ---- Strategies */

async function renderStrategies() {
  if (!S.online) { $('#strategylist').replaceChildren(offlineBanner()); return; }
  let d;
  try { d = await api('/api/strategies'); } catch (e) { toast(String(e.message)); return; }
  $('#strategylist').replaceChildren(d.items.length ? table(
    ['Name', 'Language', 'Version', 'State', 'Source'],
    d.items.map((r) => [
      el('span', { class: 'key' }, r.name || '—'),
      r.language || '—', r.version || '—',
      r.state === 'ok' ? 'ready' : 'FAILED — ' + (r.failed_step || ''),
      el('span', { class: 'caption' }, r.source),
    ])
  ) : el('p', { class: 'empty' }, 'Nothing registered yet.'));
}

async function registerStrategy(path) {
  if (!path) { toast('Give me a path to the strategy folder.'); return; }
  try {
    const job = await api('/api/strategies', { path });
    followJob(job.id, '#intake-progress', () => renderStrategies());
  } catch (e) { toast(String(e.message)); }
}

/* ---- Test */

const TEST_FIELDS = [
  ['instrument', 'Instrument', 'select', ['NQ', 'MNQ', 'ES', 'MES']],
  ['session', 'Session', 'select', ['ETH', 'RTH']],
  ['mode', 'Walk forward', 'select', ['rolling', 'anchored']],
  ['is_sessions', 'In sample', 'number', 'trading sessions per fold'],
  ['oos_sessions', 'Out of sample', 'number', 'sessions tested per fold'],
  ['step_sessions', 'Step', 'number', 'sessions between folds'],
  ['min_trades', 'Min trades', 'number', 'below this a fold is disqualified'],
  ['objective', 'Objective', 'select', ['mar', 'sharpe', 'profit_factor', 'expectancy']],
];

function renderTest() {
  const host = $('#testfields');
  if (!S.state) { host.replaceChildren(S.online ? el('p', { class: 'empty' }, 'Loading…') : offlineBanner()); return; }
  const t = S.state.test;
  host.replaceChildren(...TEST_FIELDS.map(([key, label, kind, extra]) => {
    let input;
    if (kind === 'select') {
      input = el('select', { onchange: (e) => saveTest(key, e.target.value) },
        ...extra.map((o) => el('option', { value: o, ...(String(t[key]) === o ? { selected: '' } : {}) }, o)));
    } else {
      input = el('input', {
        type: 'number', value: t[key],
        onchange: (e) => saveTest(key, parseInt(e.target.value, 10) || 0),
      });
    }
    return el('div', { class: 'field' },
      el('span', { class: 'label' }, label), input,
      el('span', { class: 'caption' }, Array.isArray(extra) ? '' : extra));
  }));
  const combos = Object.values(t.grid || {}).reduce((n, v) => n * Math.max(1, v.length), 1);
  $('#gridsize').textContent = int(combos) + ' combinations';
  $('#estimate').textContent = S.online ? '~' + Math.max(1, Math.round(combos * 0.13)) + 's' : '—';
}

async function saveTest(key, value) {
  try {
    S.state = await api('/api/state', { test: { [key]: value } });
    renderTest();
  } catch (e) { toast(String(e.message)); }
}

/* ---- Run */

function renderRun() {
  const j = S.runJob;
  if (!j) return;
  const pct = j.total ? Math.round((j.done / j.total) * 100) : 0;
  $('#runbar').style.width = pct + '%';
  $('#runstate').textContent = j.state;
  $('#runfold').textContent = j.total ? j.done + ' / ' + j.total : '—';
  $('#runelapsed').textContent = j.elapsed + 's';
  $('#runtitle').textContent = { running: 'Running', done: 'Finished', failed: 'Failed', cancelled: 'Cancelled' }[j.state] || 'Idle';
  $('#runtape').textContent = j.log.join('\n') || 'Starting…';
  $('#cancelbtn').disabled = j.state !== 'running';
  $('#toresults').disabled = j.state !== 'done';
  $('#m-run').textContent = (j.result && j.result.run_id) || '—';
}

async function startRun() {
  try {
    const job = await api('/api/run/start', {});
    show('run');
    followJob(job.id, null, () => { S.results = null; toast('Run finished — press E for results.'); });
  } catch (e) { toast(String(e.message)); }
}

/* ---- job polling */

function followJob(id, tapeSelector, onDone) {
  const tick = async () => {
    let j;
    try { j = await api('/api/job/' + id); } catch { setOnline(false); return; }
    if (j.kind === 'walkforward') { S.runJob = j; if (S.screen === 'run') renderRun(); }
    if (tapeSelector) {
      $(tapeSelector).replaceChildren(
        el('pre', { class: 'tape' }, j.log.join('\n') || 'working…'));
    }
    if (j.state === 'running') { setTimeout(tick, 600); return; }
    if (j.state === 'failed') toast(j.error);
    if (onDone) onDone(j);
  };
  tick();
}

/* ---- Results */

async function renderResults() {
  if (!S.results) {
    if (!S.online) { $('#chartbox').replaceChildren(offlineBanner()); return; }
    try { S.results = await api('/api/results'); }
    catch (e) { $('#chartbox').replaceChildren(el('p', { class: 'empty' }, String(e.message))); return; }
  }
  const v = S.results;
  const sign = (x, d = 1) => (x >= 0 ? '+' : '−') + fmt(Math.abs(x), d);

  $('#r-hero').textContent = sign(v.return_pct) + '%';
  $('#r-meta').textContent = (S.state ? S.state.test.instrument : 'NQ') + ' · '
    + (S.state ? S.state.test.bar_minutes : 1) + 'm';
  $('#r-span').textContent = v.span.replace('→', '→');

  const be = v.breakeven_per_side === null
    ? 'none — already losing' : '$' + fmt(v.breakeven_per_side) + '/side';
  const rows = [
    ['Max drawdown', sign(-v.max_drawdown_pct) + '%'],
    ['MAR', fmt(v.mar)],
    ['WF efficiency', fmt(v.wfe_mean)],
    ['OOS trades', int(v.n_trades)],
    ['Break-even cost', be],
    ['MC 5th pctile', '$' + int(Math.round(v.mc_p5))],
    ['vs buy & hold', v.excess_pp === null ? '—' : sign(v.excess_pp, 0) + ' pp'],
    ['corr to long', v.corr_to_long === null ? '—' : sign(v.corr_to_long, 2)],
  ];
  $('#r-rows').replaceChildren(...rows.map(([k, val]) =>
    el('div', { class: 'row' }, el('dt', {}, k), el('dd', { class: 'num' }, val))));
  $('#r-note').textContent = 'Before brokerage — slippage only, no commission modelled.';

  const titles = {
    curve: ['Stitched equity', 'Out-of-sample segments only, joined end to end.'],
    drawdown: ['Drawdown', 'Distance below the running equity peak.'],
    diverge: ['Per-fold result', 'Losing folds left of the axis, winning folds right.'],
    histogram: ['Final equity distribution',
                int(v.mc_iterations) + ' resamples of the out-of-sample trade sequence.'],
  }[S.view];
  $('#r-title').textContent = titles[0];
  $('#r-sub').textContent = titles[1];

  $('#r-footnote').textContent = {
    curve: int(v.n_trades) + ' out-of-sample trades across ' + v.n_folds_total
      + ' folds. '
      + (v.baseline_note
          ? v.baseline_note + '. No buy & hold line is drawn. '
          : 'Buy & hold returned ' + sign(v.buy_hold_return_pct, 0)
            + '% over the same window, so the strategy line is compressed '
            + 'against it. ')
      + 'Folds under the minimum-trade threshold are excluded, not zero-filled.',
    drawdown: 'Worst ' + int(Math.round(Math.abs(v.max_drawdown))) + ', '
      + fmt(v.max_drawdown_pct, 1) + '% of the account. Measured on the equity curve, '
      + 'so an open position’s excursion counts.',
    diverge: v.n_folds_profitable + ' of ' + v.n_folds_qualified
      + ' folds profitable. Winning parameters agree on ' + v.worst_param
      + ' across only ' + Math.round(v.worst_param_frac * 100) + '% of folds.',
    histogram: 'P5 ' + int(Math.round(v.mc_p5)) + ' · P50 ' + int(Math.round(v.mc_p50))
      + ' · P95 ' + int(Math.round(v.mc_p95)) + '.',
  }[S.view];

  let chart;
  if (S.view === 'curve') {
    const [from, to] = v.span.split(' → ');
    chart = lineChart(v.equity, { comparison: v.buy_hold || undefined,
                                  baseline: 100000,
                                  boundaries: v.fold_boundaries, from, to });
    // No second line rather than a line drawn over a window the data does not
    // cover. A legend entry for a curve that is not there would be worse.
    $('#r-legend').replaceChildren(
      el('span', {}, el('em', {}), 'Strategy · OOS'),
      v.buy_hold ? el('span', {}, el('em', { class: 'cmp' }), 'Buy & hold') : null);
  } else if (S.view === 'drawdown') {
    chart = lineChart(v.drawdown, { baseline: 0, boundaries: v.fold_boundaries });
    $('#r-legend').replaceChildren();
  } else if (S.view === 'diverge') {
    chart = barChart(v.folds.map((f) => 'FOLD ' + String(f.index).padStart(2, '0')),
                     v.folds.map((f) => f.oos_net), v.median_fold);
    $('#r-legend').replaceChildren();
  } else {
    chart = histogram(v.final_equity);
    $('#r-legend').replaceChildren();
  }
  $('#chartbox').replaceChildren(chart);

  // Last column is a magnitude bar, scaled to the biggest fold either way.
  const worst = Math.max(...v.folds.map((f) => Math.abs(f.oos_net))) || 1;
  const spark = (net) => el('span', {
    class: 'spark',
    style: 'width:' + Math.max(2, (Math.abs(net) / worst) * 70).toFixed(0) + 'px;opacity:'
      + (net >= 0 ? '1' : '.45'),
  });

  $('#foldtable').replaceChildren(table(
    ['Fold', 'OOS from', 'OR / Stop', 'IS MAR', 'OOS ret', 'OOS MAR', 'WFE', 'Trades', ''],
    v.folds.map((f) => [
      String(f.index).padStart(2, '0'),
      el('span', { class: 'dim' }, f.oos_from),
      el('span', { class: 'dim' }, (f.params.or_minutes ?? '—') + ' / ' + (f.params.stop_ticks ?? '—')),
      fmt(f.is_mar),
      el('span', { class: 'key' },
         el('span', { class: 'tri' }, f.oos_ret_pct >= 0 ? '▲' : '▼'),
         fmt(Math.abs(f.oos_ret_pct), 1) + '%'),
      el('span', { style: 'color:var(--text)' }, fmt(f.oos_mar)),
      fmt(f.wfe),
      int(f.oos_n),
      spark(f.oos_net),
    ])));

  const pick = $('#foldpick');
  if (pick.options.length <= 1) {
    for (const f of v.folds) pick.append(el('option', {}, 'Fold ' + String(f.index).padStart(2, '0')));
  }
}

/* ---- History */

async function renderHistory() {
  if (!S.online) { $('#historylist').replaceChildren(offlineBanner()); return; }
  let d;
  try { d = await api('/api/history'); } catch (e) { toast(String(e.message)); return; }
  $('#historylist').replaceChildren(d.items.length ? table(
    ['Run', 'When', 'Strategy', 'Folds', 'Trades', 'Net', 'MAR', 'Slippage'],
    d.items.map((r) => [
      el('span', { class: 'key' }, r.short_id),
      new Date(r.created_at * 1000).toISOString().slice(0, 16).replace('T', ' '),
      r.strategy, r.n_folds, int(r.n_trades), int(Math.round(r.net_pnl)),
      r.mar === null ? '—' : fmt(r.mar), fmt(r.slippage_ticks, 1) + ' tk',
    ])
  ) : el('p', { class: 'empty' }, 'No runs yet. Configure one on the Test screen.'));
}

/* ---- Settings */

function renderSettings() {
  const host = $('#settingsfields');
  $('#engineurl').value = S.engine;
  if (!S.state) { host.replaceChildren(S.online ? el('p', { class: 'empty' }, 'Loading…') : offlineBanner()); return; }
  const fields = [
    ['slippage_ticks', 'Slippage', 0.5, 'ticks per side, always against you'],
    ['starting_equity', 'Account size', 5000, 'used for MAR, drawdown % and risk of ruin'],
  ];
  host.replaceChildren(...fields.map(([key, label, step, hint]) =>
    el('div', { class: 'field' },
      el('span', { class: 'label' }, label),
      el('div', { class: 'stepper' },
        el('button', { onclick: () => bump(key, -step) }, '−'),
        el('span', { class: 'val num' }, int(S.state[key])),
        el('button', { onclick: () => bump(key, step) }, '+')),
      el('span', { class: 'caption' }, hint))));
  $('#costline').textContent = fmt(S.state.slippage_ticks, 1) + ' tick per side, against you';
}

async function bump(key, delta) {
  const next = Math.max(0, Math.round((S.state[key] + delta) * 100) / 100);
  try { S.state = await api('/api/state', { [key]: next }); renderSettings(); }
  catch (e) { toast(String(e.message)); }
}

/* ---------------------------------------------------------------- tables */

function table(headers, rows) {
  return el('table', {},
    el('thead', {}, el('tr', {}, ...headers.map((h) => el('th', {}, h)))),
    el('tbody', {}, ...rows.map((cells) => el('tr', {}, ...cells.map((c) => el('td', {}, c))))));
}

/* ------------------------------------------------------------------ boot */

async function loadState() {
  try {
    S.state = await api('/api/state');
    $('#m-strategy').textContent = S.state.test.strategy_name;
    renderInstruments();
    render();               // the first paint happened before state arrived
  } catch { /* offline; the banner already says so */ }
}

function renderInstruments() {
  const host = $('#instruments');
  host.replaceChildren(...['NQ', 'MNQ', 'ES', 'MES'].map((sym) =>
    el('button', {
      class: 'inst',
      'aria-pressed': S.state && S.state.test.instrument === sym ? 'true' : 'false',
      onclick: () => saveTest('instrument', sym).then(renderInstruments),
    }, sym)));
}

function wire() {
  document.querySelectorAll('#nav button').forEach((b) =>
    b.addEventListener('click', () => show(b.dataset.go)));

  document.addEventListener('keydown', (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const tag = document.activeElement && document.activeElement.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
    const dest = KEYS[e.key.toLowerCase()];
    if (dest) { e.preventDefault(); show(dest); }
  });

  $('#viewseg').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    S.view = b.dataset.view;
    document.querySelectorAll('#viewseg button').forEach((x) =>
      x.setAttribute('aria-pressed', String(x === b)));
    renderResults();
  });

  $('#startbtn').addEventListener('click', startRun);
  $('#cancelbtn').addEventListener('click', () => api('/api/run/cancel', {}).catch(() => {}));
  $('#toresults').addEventListener('click', () => show('results'));
  $('#registerbtn').addEventListener('click', () => registerStrategy($('#strategypath').value.trim()));

  $('#applybtn').addEventListener('click', () => {
    const f = $('#foldpick').value, sl = $('#slicepick').value;
    if (f === 'All folds' && sl === 'None') { renderResults(); return; }
    // Slicing is computed by the engine, not here. Until that endpoint exists
    // this says so instead of redrawing the same chart and looking applied.
    toast('Fold and slice filters are not wired to the engine yet — the chart '
      + 'still shows all folds. Nothing here will show you a filtered number '
      + 'it did not compute.');
  });

  $('#reconnect').addEventListener('click', () => {
    S.engine = $('#engineurl').value.trim().replace(/\/$/, '') || DEFAULT_ENGINE;
    localStorage.setItem('s3.engine', S.engine);
    ping();
  });

  const drop = $('#drop');
  drop.addEventListener('click', () => $('#strategypath').focus());
  ['dragenter', 'dragover'].forEach((t) => drop.addEventListener(t, (e) => {
    e.preventDefault(); drop.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach((t) => drop.addEventListener(t, () => drop.classList.remove('over')));
  drop.addEventListener('drop', (e) => {
    e.preventDefault();
    // A browser will not reveal a dropped folder's real path. Saying so is
    // better than silently doing nothing, which is what a bare drop zone does.
    const f = e.dataTransfer.files[0];
    $('#strategypath').focus();
    toast(f ? 'Browsers hide the full path of a dropped item. Paste the folder path for "'
      + f.name + '" instead.' : 'Paste the folder path.');
  });
}

function boot() {
  wire();
  const start = (location.hash || '#test').slice(1);
  show(KEYS[start[0]] === start || Object.values(KEYS).includes(start) ? start : 'test');
  ping();
  loadState();
  setInterval(ping, 5000);
  const clock = () => {
    $('#clock').textContent = new Date().toTimeString().slice(0, 8);
  };
  clock();
  setInterval(clock, 1000);
}

document.addEventListener('DOMContentLoaded', boot);
