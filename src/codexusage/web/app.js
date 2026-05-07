// codexusage web dashboard

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const fmt = {
  int:    n => (n ?? 0).toLocaleString(),
  usd:    n => n == null ? '—' : '$' + Number(n).toFixed(4),
  cr:     n => n == null ? '—' : Number(n).toFixed(2) + ' cr',
  ts:     t => (t || '').slice(0, 10),
  short:  (s, n = 60) => !s ? '' : s.length > n ? '…' + s.slice(-n) : s,
  modelBadge(m) {
    const s = (m || '').toLowerCase();
    let cls = '';
    if (s.includes('gpt-5') || s.includes('gpt5'))         cls = 'gpt5';
    else if (s.includes('gpt-4o-mini') || s.includes('4o-mini')) cls = 'gpt4o-mini';
    else if (s.includes('gpt-4o') || s.includes('4o'))     cls = 'gpt4o';
    else if (s.match(/^o\d/) || s.includes('/o4') || s.includes('/o3') || s.includes('/o1')) cls = 'o-series';
    const label = (m || '').replace(/^(openai|azure|openrouter\/openai|openrouter)\//i, '');
    return `<span class="badge ${cls}">${label}</span>`;
  },
};

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

// ── Pool bar ──────────────────────────────────────────────────────────────────
function renderPool(pool) {
  const pct = Math.min(pool.pct ?? 0, 100);
  const fillClass = pct >= 90 ? 'bad' : pct >= 70 ? 'warn' : '';
  const d = new Date();
  const daysLeft = 7 - d.getDay() || 7;  // days until next Monday
  return `
    <div class="card pool-card">
      <div class="pool-header">
        <h2>Weekly Credit Pool</h2>
        <span class="pool-meta">resets Monday · ${daysLeft} day${daysLeft !== 1 ? 's' : ''} left</span>
      </div>
      <div class="pool-numbers">
        <span class="pool-used">${fmt.cr(pool.used)}</span>
        <span class="pool-limit">of ${pool.limit} cr</span>
        <span class="pool-pct">${pct.toFixed(1)}%</span>
      </div>
      <div class="pool-track">
        <div class="pool-fill ${fillClass}" style="width:${pct}%"></div>
      </div>
      <div class="pool-reset">${(pool.limit - pool.used).toFixed(2)} cr remaining</div>
    </div>`;
}

// ── KPI cards ─────────────────────────────────────────────────────────────────
function renderKPIs(totals) {
  return `
    <div class="kpi-row">
      <div class="card kpi">
        <div class="label">Total tokens</div>
        <div class="value">${fmt.int(totals.total_tokens)}</div>
      </div>
      <div class="card kpi">
        <div class="label">USD cost</div>
        <div class="value" style="color:var(--good)">${fmt.usd(totals.usd)}</div>
      </div>
      <div class="card kpi">
        <div class="label">Credits used</div>
        <div class="value">${fmt.cr(totals.credits)}</div>
      </div>
    </div>`;
}

// ── Daily chart (ECharts) ─────────────────────────────────────────────────────
function renderChart(days) {
  const dates   = days.map(d => d.date);
  const input   = days.map(d => +(d.credits * (d.input_tokens   / Math.max(d.total_tokens, 1))).toFixed(4));
  const cached  = days.map(d => +(d.credits * (d.cached_tokens  / Math.max(d.total_tokens, 1))).toFixed(4));
  const output  = days.map(d => +(d.credits * (d.output_tokens  / Math.max(d.total_tokens, 1))).toFixed(4));

  const el = $('#daily-chart');
  if (!el) return;
  // Dispose any existing instance attached to a now-replaced DOM node
  const existing = echarts.getInstanceByDom(el);
  if (existing) existing.dispose();
  const _chart = echarts.init(el, null, { renderer: 'canvas' });

  _chart.setOption({
    backgroundColor: 'transparent',
    grid: { top: 10, bottom: 30, left: 50, right: 10 },
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      backgroundColor: '#131922', borderColor: '#1F2630',
      textStyle: { color: '#E6EDF3', fontSize: 12 },
      formatter: params => {
        const total = params.reduce((s, p) => s + p.value, 0);
        const rows = params.map(p => `${p.marker} ${p.seriesName}: ${p.value.toFixed(4)} cr`).join('<br>');
        return `${params[0].name}<br>${rows}<br><b>Total: ${total.toFixed(4)} cr</b>`;
      },
    },
    xAxis: { type: 'category', data: dates, axisLabel: { color: '#8B98A6', fontSize: 11 }, axisLine: { lineStyle: { color: '#1F2630' } } },
    yAxis: { type: 'value', axisLabel: { color: '#8B98A6', fontSize: 11, formatter: v => v.toFixed(2) }, splitLine: { lineStyle: { color: '#1F2630' } } },
    series: [
      { name: 'Input',  type: 'bar', stack: 'total', data: input,  itemStyle: { color: '#4A9EFF' } },
      { name: 'Cached', type: 'bar', stack: 'total', data: cached, itemStyle: { color: '#2A5A99' } },
      { name: 'Output', type: 'bar', stack: 'total', data: output, itemStyle: { color: '#3FB68B' } },
    ],
  });
}

// ── Models table ──────────────────────────────────────────────────────────────
function renderModels(models) {
  if (!models.length) return '<p class="muted">No data.</p>';
  const rows = models.map(m => `
    <tr>
      <td>${fmt.modelBadge(m.model)}</td>
      <td class="num">${fmt.int(m.events)}</td>
      <td class="num">${fmt.int(m.total_tokens)}</td>
      <td class="num">${fmt.usd(m.usd)}</td>
      <td class="num">${fmt.cr(m.credits)}</td>
    </tr>`).join('');
  return `
    <table>
      <thead><tr>
        <th>Model</th><th class="num">Events</th><th class="num">Tokens</th>
        <th class="num">USD</th><th class="num">Credits</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// ── Sessions table ────────────────────────────────────────────────────────────
function renderSessionsTable(sessions) {
  if (!sessions.length) return '<p class="muted">No sessions.</p>';
  const rows = sessions.map(s => `
    <tr>
      <td class="mono">${fmt.short(s.session_id)}</td>
      <td class="mono">${fmt.ts(s.last_timestamp)}</td>
      <td class="num">${s.events}</td>
      <td class="num">${fmt.int(s.total_tokens)}</td>
      <td class="num">${fmt.usd(s.usd)}</td>
      <td class="num">${fmt.cr(s.credits)}</td>
    </tr>`).join('');
  return `
    <table>
      <thead><tr>
        <th>Session</th><th>Date</th><th class="num">Events</th>
        <th class="num">Tokens</th><th class="num">USD</th><th class="num">Credits</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// ── Routes ────────────────────────────────────────────────────────────────────
const ROUTES = {
  '/overview': renderOverview,
  '/sessions': renderSessionsRoute,
};

let _lastData = null;

async function renderOverview(app) {
  app.innerHTML = '<p class="muted" style="padding:20px">Loading…</p>';
  try {
    _lastData = await api('/api/week');
  } catch (e) {
    app.innerHTML = `<p style="color:var(--bad);padding:20px">Error: ${e.message}</p>`;
    return;
  }
  const { pool, totals, days, models, sessions } = _lastData;
  app.innerHTML = `
    ${renderPool(pool)}
    ${renderKPIs(totals)}
    <div class="card">
      <h2>Daily breakdown (this week)</h2>
      <div class="chart-box" id="daily-chart"></div>
    </div>
    <div class="card">
      <h2>By model</h2>
      ${renderModels(models)}
    </div>
    <div class="card">
      <h2>This week's sessions</h2>
      ${renderSessionsTable(sessions.slice(0, 20))}
    </div>`;
  renderChart(days);
}

async function renderSessionsRoute(app) {
  const d = new Date();
  const today = d.toISOString().slice(0, 10);
  const weekAgo = new Date(d - 7 * 86400000).toISOString().slice(0, 10);

  app.innerHTML = `
    <div class="card">
      <h2>Sessions</h2>
      <div class="filter-row">
        <label>From <input type="date" id="since-input" value="${weekAgo}"></label>
        <label>To <input type="date" id="until-input" value="${today}"></label>
        <button id="filter-btn">Apply</button>
      </div>
      <div id="sessions-table"><p class="muted">Loading…</p></div>
    </div>`;

  async function load() {
    const since = $('#since-input').value || weekAgo;
    const until = $('#until-input').value || today;
    try {
      const data = await api(`/api/sessions?since=${since}&until=${until}`);
      $('#sessions-table').innerHTML = renderSessionsTable(data.sessions);
    } catch (e) {
      $('#sessions-table').innerHTML = `<p style="color:var(--bad)">Error: ${e.message}</p>`;
    }
  }

  await load();
  $('#filter-btn').addEventListener('click', load);
}

// ── Router + topbar ───────────────────────────────────────────────────────────
function buildTopbar() {
  const header = document.createElement('header');
  header.className = 'topbar';
  header.innerHTML = `
    <div class="brand">Codex Usage</div>
    <nav>
      ${Object.keys(ROUTES).map(r => `<a href="#${r}" data-route="${r}">${r.slice(1)}</a>`).join('')}
    </nav>
    <div class="spacer"></div>
    <button class="refresh-btn" id="refresh-btn">↻ Refresh</button>`;
  document.body.prepend(header);
}

function setActiveTab(routeKey) {
  $$('header.topbar nav a').forEach(a => a.classList.toggle('active', a.dataset.route === routeKey));
}

async function route() {
  const app = $('#app');
  const hash = location.hash.replace('#', '') || '/overview';
  const fn = ROUTES[hash] || ROUTES['/overview'];
  setActiveTab(hash in ROUTES ? hash : '/overview');
  await fn(app);
}

buildTopbar();
route();
window.addEventListener('hashchange', route);
document.getElementById('refresh-btn').addEventListener('click', route);
