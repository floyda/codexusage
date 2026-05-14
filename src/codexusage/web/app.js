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
    if (s.includes('gpt-5') || s.includes('gpt5'))               cls = 'gpt5';
    else if (s.includes('gpt-4o-mini') || s.includes('4o-mini')) cls = 'gpt4o-mini';
    else if (s.includes('gpt-4o') || s.includes('4o'))           cls = 'gpt4o';
    else if (s.match(/^o\d/) || s.includes('/o4') || s.includes('/o3') || s.includes('/o1')) cls = 'o-series';
    const label = (m || '').replace(/^(openai|azure|openrouter\/openai|openrouter)\//i, '');
    return `<span class="badge ${cls}">${label}</span>`;
  },
  effortBadge(effort) {
    if (!effort || effort === 'none') return '<span class="muted">—</span>';
    const cls = { xhigh: 'effort-xhigh', high: 'effort-high', medium: 'effort-medium', low: 'effort-low' }[effort] || '';
    return `<span class="badge ${cls}">${effort}</span>`;
  },
};

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

// ── Project filter state ──────────────────────────────────────────────────────
let _activeProject = 'all';

function applyProjectFilter(data) {
  if (_activeProject === 'all') return data;
  const proj = (data.config?.projects || []).find(p => p.name === _activeProject);
  const isApiToken = proj?.auth_type === 'api_token';
  const projUsd = (data.projects || []).find(p => p.name === _activeProject)?.usd ?? 0;
  const filteredModels = data.models.filter(m => (m.projects || []).includes(_activeProject));
  const modelKeys = new Set(filteredModels.map(m => m.model));
  const filteredDaysByModel = Object.fromEntries(
    Object.entries(data.days_by_model || {}).filter(([k]) => modelKeys.has(k))
  );
  const filteredDaysByProject = Object.fromEntries(
    Object.entries(data.days_by_project || {}).filter(([k]) => k === _activeProject)
  );
  const filteredSessions = data.sessions.filter(s => s.project === _activeProject);
  const cwdKeys = new Set(filteredSessions.map(s => s.cwd).filter(Boolean));
  const filteredDaysByCwd = Object.fromEntries(
    Object.entries(data.days_by_cwd || {}).filter(([k]) => cwdKeys.has(k))
  );
  return {
    ...data,
    models:           filteredModels,
    sessions:         filteredSessions,
    projects:         (data.projects || []).filter(p => p.name === _activeProject),
    effort_levels:    [],
    has_effort_data:  false,
    has_oauth:        isApiToken ? false : data.has_oauth,
    has_api_token:    isApiToken,
    api_token_totals: isApiToken ? { usd: projUsd } : data.api_token_totals,
    days_by_model:    filteredDaysByModel,
    days_by_project:  filteredDaysByProject,
    days_by_cwd:      filteredDaysByCwd,
    has_cwd_data:     cwdKeys.size > 1,
  };
}

// ── Project pills ─────────────────────────────────────────────────────────────
function renderProjectPills(cfgProjects) {
  if (!cfgProjects || cfgProjects.length <= 1) return '';
  const pills = [{ name: 'all', auth_type: '' }, ...cfgProjects].map(p => {
    const active = _activeProject === p.name ? ' active' : '';
    const cls    = p.auth_type === 'api_token' ? ' api-token' : '';
    return `<button class="pill-btn${active}${cls}" data-project="${p.name}">${p.name === 'all' ? 'All Projects' : p.name}</button>`;
  }).join('');
  return `<div class="project-pills">${pills}</div>`;
}

function initProjectPills(app, cfgProjects) {
  $$('.pill-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      _activeProject = btn.dataset.project;
      _paintOverview(app, cfgProjects);
    });
  });
}

// ── Pool bar ──────────────────────────────────────────────────────────────────
function renderPool(pool, rangeLabel) {
  const pct = Math.min(pool.pct ?? 0, 100);
  const fillClass = pct >= 90 ? 'bad' : pct >= 70 ? 'warn' : '';
  const isDefaultWeek = !rangeLabel || rangeLabel === 'this week';
  const title = isDefaultWeek ? 'Weekly Credit Pool' : `Credits — ${rangeLabel}`;
  const ld = londonDateParts();
  const daysSinceFri = (ld.dayOfWeek + 2) % 7;
  const daysLeft = daysSinceFri === 0 && ld.hour < 17 ? 0 : (7 - daysSinceFri) || 7;
  const resetNote = daysLeft === 0 ? 'resets today at 17:00' : `resets Friday · ${daysLeft} day${daysLeft !== 1 ? 's' : ''} left`;
  const meta = isDefaultWeek ? resetNote : '';
  return `
    <div class="card pool-card">
      <div class="pool-header">
        <h2>${title}</h2>
        ${meta ? `<span class="pool-meta">${meta}</span>` : ''}
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

// ── Weekly pool breakdown ─────────────────────────────────────────────────
function nearestPriorFriday(dateStr) {
  const d = new Date(dateStr.slice(0, 10) + 'T12:00');
  const daysSinceFri = (d.getDay() + 2) % 7;
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() - daysSinceFri);
}

function renderWeeklyPool(days, limit, range) {
  const start = nearestPriorFriday(range.since);
  const todayStr = localDate(new Date());
  const buckets = [];

  for (let fri = new Date(start); ; fri = new Date(fri.getFullYear(), fri.getMonth(), fri.getDate() + 7)) {
    const nextFri = new Date(fri.getFullYear(), fri.getMonth(), fri.getDate() + 7);
    const friStr  = localDate(fri);
    const nextStr = localDate(nextFri);
    buckets.push({ friStr, nextStr, credits: 0, isCurrent: todayStr >= friStr && todayStr < nextStr });
    if (nextStr >= range.until.slice(0, 10)) break;
  }

  for (const day of days) {
    const b = buckets.find(b => day.date >= b.friStr && day.date < b.nextStr);
    if (b) b.credits += day.credits ?? 0;
  }

  const fmtWeekLabel = (fri, next) => `${fmtDay(fri)} – ${fmtDay(next)}`;

  const rows = buckets.map(({ friStr, nextStr, credits, isCurrent }) => {
    const pct = limit > 0 ? Math.min((credits / limit) * 100, 100) : 0;
    const fillClass = pct >= 90 ? 'bad' : pct >= 70 ? 'warn' : '';
    return `
      <div class="week-row">
        <span class="week-label">${fmtWeekLabel(friStr, nextStr)}${isCurrent ? ' ★' : ''}</span>
        <div class="pool-track mini"><div class="pool-fill ${fillClass}" style="width:${pct}%"></div></div>
        <span class="week-cr">${fmt.cr(credits)}</span>
      </div>`;
  }).join('');

  const total = buckets.reduce((s, b) => s + b.credits, 0);
  return `
    <div class="card pool-card">
      <div class="pool-header"><h2>Credits by week</h2></div>
      <div class="week-rows">${rows}</div>
      <div class="pool-reset">Total: ${fmt.cr(total)} &nbsp;·&nbsp; ★ = current week</div>
    </div>`;
}

// ── API token card ────────────────────────────────────────────────────────────
function renderApiTokenCard(api_token_totals, projects) {
  if (!api_token_totals || !api_token_totals.usd) return '';
  const apiProjects = (projects || []).filter(p => p.auth_type === 'api_token');
  const rows = apiProjects.length > 1
    ? apiProjects.map(p => `
        <tr>
          <td class="mono" style="font-size:11px">${p.name}</td>
          <td class="num">${fmt.int(p.events)}</td>
          <td class="num">${fmt.int(p.total_tokens)}</td>
          <td class="num">${fmt.usd(p.usd)}</td>
        </tr>`).join('')
    : '';
  return `
    <div class="card api-token-card">
      <div class="pool-header"><h2>API Token Spend</h2></div>
      <div class="api-token-total">${fmt.usd(api_token_totals.usd)}</div>
      ${rows ? `
      <table style="margin-top:14px">
        <thead><tr>
          <th>Project</th><th class="num">Events</th>
          <th class="num">Tokens</th><th class="num">USD</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>` : ''}
    </div>`;
}

// ── KPI cards ─────────────────────────────────────────────────────────────────
function renderKPIs(totals, hasOauth) {
  const creditsCard = hasOauth ? `
      <div class="card kpi">
        <div class="label">Credits used</div>
        <div class="value">${fmt.cr(totals.credits)}</div>
      </div>` : '';
  return `
    <div class="kpi-row">
      <div class="card kpi">
        <div class="label">Total tokens</div>
        <div class="value">${fmt.int(totals.total_tokens)}</div>
      </div>
      <div class="card kpi">
        <div class="label">${hasOauth ? 'USD cost' : 'Total spend'}</div>
        <div class="value" style="color:var(--good)">${fmt.usd(totals.usd)}</div>
      </div>
      ${creditsCard}
    </div>`;
}

// ── Daily chart (ECharts) ─────────────────────────────────────────────────────
let _chartBreakdown = 'token-type';

const CHART_PALETTE = [
  '#4A9EFF', '#3FB68B', '#FF6B6B', '#FFB347', '#9B59B6',
  '#1ABC9C', '#E74C3C', '#F39C12', '#2ECC71', '#E67E22',
  '#3498DB', '#D35400', '#8E44AD', '#16A085', '#C0392B',
];

function cwdBasename(cwd) {
  if (!cwd) return 'unknown';
  return cwd.replace(/\\/g, '/').split('/').filter(Boolean).pop() || cwd;
}

function renderChart(days, hasOauth, hasApiToken, daysByModel, daysByProject, daysByCwd) {
  const fmtVal  = v => '$' + v.toFixed(4);
  const fmtAxis = v => '$' + v.toFixed(2);
  const metric  = 'usd';

  const dates = days.map(d => d.date);
  let series;

  if (_chartBreakdown === 'token-type') {
    const input  = days.map(d => +(d.input_usd  ?? 0).toFixed(4));
    const cached = days.map(d => +(d.cached_usd ?? 0).toFixed(4));
    const output = days.map(d => +(d.output_usd ?? 0).toFixed(4));
    series = [
      { name: 'Input',  type: 'bar', stack: 'total', data: input,  itemStyle: { color: '#4A9EFF' } },
      { name: 'Cached', type: 'bar', stack: 'total', data: cached, itemStyle: { color: '#2A5A99' } },
      { name: 'Output', type: 'bar', stack: 'total', data: output, itemStyle: { color: '#3FB68B' } },
    ];
  } else if (_chartBreakdown === 'by-cwd') {
    const keys = Object.keys(daysByCwd || {}).sort();
    series = keys.map((key, i) => ({
      name: cwdBasename(key),
      type: 'bar',
      stack: 'total',
      data: dates.map(date => +((daysByCwd[key][date]?.[metric] ?? 0).toFixed(4))),
      itemStyle: { color: CHART_PALETTE[i % CHART_PALETTE.length] },
    }));
  } else {
    const source = _chartBreakdown === 'by-model' ? daysByModel : daysByProject;
    const keys = Object.keys(source || {}).sort();
    const stripPrefix = k => k.replace(/^(openai|azure|openrouter\/openai|openrouter)\//i, '');
    series = keys.map((key, i) => ({
      name: _chartBreakdown === 'by-model' ? stripPrefix(key) : key,
      type: 'bar',
      stack: 'total',
      data: dates.map(date => +((source[key][date]?.[metric] ?? 0).toFixed(4))),
      itemStyle: { color: CHART_PALETTE[i % CHART_PALETTE.length] },
    }));
  }

  const el = $('#daily-chart');
  if (!el) return;
  const existing = echarts.getInstanceByDom(el);
  if (existing) existing.dispose();
  const _chart = echarts.init(el, null, { renderer: 'canvas' });

  _chart.setOption({
    backgroundColor: 'transparent',
    grid: { top: 10, bottom: 30, left: 60, right: 10 },
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      backgroundColor: '#131922', borderColor: '#1F2630',
      textStyle: { color: '#E6EDF3', fontSize: 12 },
      formatter: params => {
        const nonZero = params.filter(p => p.value > 0);
        const total = params.reduce((s, p) => s + p.value, 0);
        const rows = nonZero.map(p => `${p.marker} ${p.seriesName}: ${fmtVal(p.value)}`).join('<br>');
        return `${fmtDay(params[0].name)}<br>${rows}<br><b>Total: ${fmtVal(total)}</b>`;
      },
    },
    xAxis: { type: 'category', data: dates, axisLabel: { color: '#8B98A6', fontSize: 11, formatter: v => fmtDay(v) }, axisLine: { lineStyle: { color: '#1F2630' } } },
    yAxis: { type: 'value', axisLabel: { color: '#8B98A6', fontSize: 11, formatter: fmtAxis }, splitLine: { lineStyle: { color: '#1F2630' } } },
    series,
  });
}

// ── Projects table ────────────────────────────────────────────────────────────
function renderProjectsTable(projects, hasOauth) {
  if (!projects || !projects.length) return '<p class="muted">No data.</p>';
  const creditsHead = hasOauth ? '<th class="num">Credits</th>' : '';
  const rows = projects.map(p => {
    const authBadge   = p.auth_type === 'api_token'
      ? '<span class="badge api-token-badge">api token</span>'
      : '<span class="badge oauth-badge">oauth</span>';
    const creditsCell = hasOauth
      ? `<td class="num">${p.auth_type === 'oauth' ? fmt.cr(p.credits) : '<span class="muted">—</span>'}</td>`
      : '';
    return `<tr>
      <td class="mono">${p.name}</td>
      <td>${authBadge}</td>
      <td class="num">${fmt.int(p.events)}</td>
      <td class="num">${fmt.int(p.total_tokens)}</td>
      <td class="num">${fmt.usd(p.usd)}</td>
      ${creditsCell}
    </tr>`;
  }).join('');
  return `
    <table>
      <thead><tr>
        <th>Project</th><th>Auth</th><th class="num">Events</th>
        <th class="num">Tokens</th><th class="num">USD</th>${creditsHead}
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// ── Models table ──────────────────────────────────────────────────────────────
function renderModels(models, multiProject) {
  if (!models.length) return '<p class="muted">No data.</p>';
  const projHead = multiProject ? '<th>Projects</th>' : '';
  const rows = models.map(m => {
    const projCell = multiProject
      ? `<td class="mono" style="font-size:11px">${(m.projects || []).join(', ')}</td>`
      : '';
    return `<tr>
      <td>${fmt.modelBadge(m.model)}</td>
      ${projCell}
      <td class="num">${fmt.int(m.events)}</td>
      <td class="num">${fmt.int(m.total_tokens)}</td>
      <td class="num">${fmt.usd(m.usd)}</td>
      <td class="num">${fmt.cr(m.credits)}</td>
    </tr>`;
  }).join('');
  return `
    <table>
      <thead><tr>
        <th>Model</th>${projHead}<th class="num">Events</th>
        <th class="num">Tokens</th><th class="num">USD</th><th class="num">Credits</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// ── Effort table ──────────────────────────────────────────────────────────────
function renderEffortTable(effort_levels, hasOauth) {
  if (!effort_levels || !effort_levels.length) return '<p class="muted">No data.</p>';
  const creditsHead = hasOauth ? '<th class="num">Credits</th>' : '';
  const rows = effort_levels.map(e => {
    const creditsCell = hasOauth ? `<td class="num">${fmt.cr(e.credits)}</td>` : '';
    return `<tr>
      <td>${fmt.effortBadge(e.effort)}</td>
      <td class="num">${fmt.int(e.events)}</td>
      <td class="num">${fmt.int(e.total_tokens)}</td>
      <td class="num">${fmt.usd(e.usd)}</td>
      ${creditsCell}
    </tr>`;
  }).join('');
  return `
    <table>
      <thead><tr>
        <th>Effort</th><th class="num">Events</th>
        <th class="num">Tokens</th><th class="num">USD</th>${creditsHead}
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// ── Sessions table ────────────────────────────────────────────────────────────
function renderSessionsTable(sessions) {
  if (!sessions.length) return '<p class="muted">No sessions.</p>';
  const hasSubagents = sessions.some(s => s.subagents && s.subagents.length > 0);
  const multiProject = sessions.some(s => s.project && s.project !== 'default');
  const hasEffort    = sessions.some(s => s.reasoning_effort);
  const hasCwd       = sessions.some(s => s.cwd);
  const projHead  = multiProject  ? '<th>Project</th>' : '';
  const effHead   = hasEffort     ? '<th>Effort</th>'  : '';
  const cwdHead   = hasCwd        ? '<th>Repo</th>'    : '';
  const totalHead = hasSubagents  ? '<th class="num">Total USD</th><th class="num">Total Cr</th>' : '';

  function sessionRow(s, isChild) {
    const projCell = multiProject ? `<td class="mono" style="font-size:11px">${s.project || '—'}</td>` : '';
    const effCell  = hasEffort    ? `<td>${fmt.effortBadge(s.reasoning_effort)}</td>` : '';
    const cwdCell  = hasCwd       ? `<td class="mono" style="font-size:11px" title="${s.cwd || ''}">${cwdBasename(s.cwd)}</td>` : '';
    const ownUsd     = s.own_usd     != null ? s.own_usd     : s.usd;
    const ownCredits = s.own_credits != null ? s.own_credits : s.credits;

    if (isChild) {
      const label = s.agent_nickname || fmt.short(s.session_id);
      const totalCols = hasSubagents ? '<td class="num">—</td><td class="num">—</td>' : '';
      return `<tr class="subagent-row" data-parent="${s._parent_sid}" style="display:none">
        <td class="mono sub-indent">↳ ${label}</td>
        <td class="mono">${fmt.ts(s.last_timestamp)}</td>
        ${projCell}${effCell}${cwdCell}
        <td class="num">${s.events}</td>
        <td class="num">${fmt.int(s.total_tokens)}</td>
        <td class="num">${fmt.usd(ownUsd)}</td>
        <td class="num">${fmt.cr(ownCredits)}</td>
        ${totalCols}
      </tr>`;
    }

    const hasKids   = s.subagents && s.subagents.length > 0;
    const expandBtn = hasKids
      ? `<button class="expand-btn" data-sid="${s.session_id}" title="Show subagents">▶</button>`
      : (hasSubagents ? '<span class="expand-spacer"></span>' : '');
    const totalCols = hasSubagents
      ? `<td class="num">${fmt.usd(s.total_usd != null ? s.total_usd : ownUsd)}</td><td class="num">${fmt.cr(s.total_credits != null ? s.total_credits : ownCredits)}</td>`
      : '';
    return `<tr class="${hasKids ? 'parent-row' : ''}">
      <td class="mono session-id-cell">${expandBtn} ${fmt.short(s.session_id)}</td>
      <td class="mono">${fmt.ts(s.last_timestamp)}</td>
      ${projCell}${effCell}${cwdCell}
      <td class="num">${s.events}</td>
      <td class="num">${fmt.int(s.total_tokens)}</td>
      <td class="num">${fmt.usd(ownUsd)}</td>
      <td class="num">${fmt.cr(ownCredits)}</td>
      ${totalCols}
    </tr>`;
  }

  const rows = [];
  for (const s of sessions) {
    rows.push(sessionRow(s, false));
    if (s.subagents) {
      for (const c of s.subagents) {
        c._parent_sid = s.session_id;
        rows.push(sessionRow(c, true));
      }
    }
  }

  return `
    <table>
      <thead><tr>
        <th>Session</th><th>Date</th>${projHead}${effHead}${cwdHead}
        <th class="num">Events</th><th class="num">Tokens</th>
        <th class="num">USD</th><th class="num">Credits</th>${totalHead}
      </tr></thead>
      <tbody>${rows.join('')}</tbody>
    </table>`;
}

function initExpandButtons(container) {
  (container || document).querySelectorAll('.expand-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const sid = btn.dataset.sid;
      const open = btn.textContent.trim() === '▼';
      btn.textContent = open ? '▶' : '▼';
      btn.title = open ? 'Show subagents' : 'Hide subagents';
      (container || document).querySelectorAll(`.subagent-row[data-parent="${sid}"]`)
        .forEach(r => { r.style.display = open ? 'none' : ''; });
    });
  });
}

// ── Routes ────────────────────────────────────────────────────────────────────
const ROUTES = {
  '/overview': renderOverview,
  '/sessions': renderSessionsRoute,
};

let _lastData = null;
// Module-level range state — preserved across re-renders so user selections survive innerHTML replacement.
let _since = null;
let _until = null;

// ── Date helpers ──────────────────────────────────────────────────────────────
// Always use local calendar date, never toISOString() which returns UTC and
// diverges from local date in non-UTC timezones.
function localDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
// Format a YYYY-MM-DD string as a short date in en-GB order (D/M) — consistent with London billing timezone.
function fmtDay(dateStr) {
  const [y, m, d] = dateStr.split('-').map(Number);
  return new Intl.DateTimeFormat('en-GB', { month: 'numeric', day: 'numeric' }).format(new Date(y, m - 1, d));
}
// Return current calendar date/hour in London time for billing-week calculations.
function londonDateParts(d = new Date()) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Europe/London',
    weekday: 'short', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).formatToParts(d).reduce((a, p) => ({ ...a, [p.type]: p.value }), {});
  const WD = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
  const dayOfWeek = WD[parts.weekday];
  if (dayOfWeek === undefined) throw new Error(`londonDateParts: unexpected weekday "${parts.weekday}"`);
  return { year: +parts.year, month: +parts.month, day: +parts.day, hour: +parts.hour % 24, dayOfWeek };
}
function todayLabel()    { return localDate(new Date()); }
function tomorrowLabel() { const d = new Date(); return localDate(new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1)); }
function weekLabel() {
  const p = londonDateParts();
  // Billing week resets Friday 17:00 London time.
  let daysSinceFri = (p.dayOfWeek + 2) % 7;
  if (daysSinceFri === 0 && p.hour < 17) daysSinceFri = 7;
  const base = new Date(p.year, p.month - 1, p.day);
  const friday = new Date(base.getFullYear(), base.getMonth(), base.getDate() - daysSinceFri);
  const nextFri = new Date(friday.getFullYear(), friday.getMonth(), friday.getDate() + 7);
  return { since: localDate(friday) + 'T17:00', until: localDate(nextFri) + 'T17:00' };
}

// Split a "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM" string into [date, time] parts.
function splitDT(dt) {
  if (!dt) return ['', '00:00'];
  const i = dt.indexOf('T');
  return i === -1 ? [dt, '00:00'] : [dt.slice(0, i), dt.slice(i + 1, i + 6)];
}

// ── Chart toggle ─────────────────────────────────────────────────────────────
function initChartToggles(app, cfgProjects) {
  $$('.toggle-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      _chartBreakdown = btn.dataset.breakdown;
      _paintOverview(app, cfgProjects);
    });
  });
}

// ── Overview ──────────────────────────────────────────────────────────────────
function buildOverviewControls() {
  const wk = weekLabel();
  const [sinceDate, sinceTime] = splitDT(_since || wk.since);
  const [untilDate, untilTime] = splitDT(_until || wk.until);
  return `
    <div class="filter-row controls">
      <label>From
        <input type="date" id="since-date" value="${sinceDate}">
        <input type="time" id="since-time" value="${sinceTime}">
      </label>
      <label>To
        <input type="date" id="until-date" value="${untilDate}">
        <input type="time" id="until-time" value="${untilTime}">
      </label>
      <button class="preset-btn" data-mode="week">This week</button>
      <button class="preset-btn" data-days="14">2 weeks</button>
      <button class="preset-btn" data-days="21">3 weeks</button>
      <button id="apply-btn">Apply</button>
    </div>`;
}

function _paintOverview(app, cfgProjects) {
  if (!_lastData) return;
  cfgProjects = cfgProjects || _lastData.config?.projects || [];
  const multiProject = cfgProjects.length > 1;

  const filtered = applyProjectFilter(_lastData);
  const { pool, totals, days, models, sessions, range,
          projects, effort_levels, api_token_totals,
          has_oauth, has_api_token, has_effort_data,
          days_by_model, days_by_project, days_by_cwd, has_cwd_data } = filtered;

  const wk = weekLabel();
  const fmtDT = s => s ? s.replace('T', ' ') : s;
  const label = (range.since === wk.since && range.until === wk.until)
    ? 'this week'
    : `${fmtDT(range.since)} .. ${fmtDT(range.until)}`;

  app.innerHTML = `
    ${multiProject ? renderProjectPills(cfgProjects) : ''}
    ${buildOverviewControls()}
    ${has_oauth ? (label !== 'this week' && days.length > 7 ? renderWeeklyPool(days, pool.limit, range) : renderPool(pool, label)) : ''}
    ${renderKPIs(totals, has_oauth)}
    ${has_api_token ? renderApiTokenCard(api_token_totals, filtered.projects) : ''}
    <div class="card">
      <div class="chart-header">
        <h2>Daily breakdown (${label})</h2>
        <div class="chart-toggle">
          <button class="toggle-btn${_chartBreakdown === 'token-type' ? ' active' : ''}" data-breakdown="token-type">Token type</button>
          <button class="toggle-btn${_chartBreakdown === 'by-model'   ? ' active' : ''}" data-breakdown="by-model">By model</button>
          ${multiProject ? `<button class="toggle-btn${_chartBreakdown === 'by-project' ? ' active' : ''}" data-breakdown="by-project">By project</button>` : ''}
          ${has_cwd_data ? `<button class="toggle-btn${_chartBreakdown === 'by-cwd' ? ' active' : ''}" data-breakdown="by-cwd">By repo</button>` : ''}
        </div>
      </div>
      <div class="chart-box" id="daily-chart"></div>
    </div>
    ${multiProject ? `<div class="card"><h2>By project</h2>${renderProjectsTable(filtered.projects, has_oauth)}</div>` : ''}
    <div class="card">
      <h2>By model</h2>
      ${renderModels(filtered.models, multiProject)}
    </div>
    ${has_effort_data ? `<div class="card"><h2>By effort level</h2>${renderEffortTable(effort_levels, has_oauth)}</div>` : ''}
    <div class="card">
      <h2>Sessions (${label})</h2>
      ${renderSessionsTable(filtered.sessions.slice(0, 20))}
    </div>`;

  renderChart(days, has_oauth, has_api_token, days_by_model, days_by_project, days_by_cwd);
  initOverviewControls();
  initChartToggles(app, cfgProjects);
  initExpandButtons(app);
  if (multiProject) initProjectPills(app, cfgProjects);
}

async function renderOverview(app) {
  // Build the URL from state before touching the DOM — inputs live inside app.
  let url = '/api/week';
  if (_since) {
    url += '?since=' + _since;
    if (_until) url += '&until=' + _until;
  }

  app.innerHTML = '<p class="muted" style="padding:20px">Loading…</p>';
  try {
    _lastData = await api(url);
    updatePricingStatus(_lastData.config);
  } catch (e) {
    app.innerHTML = `<p style="color:var(--bad);padding:20px">Error: ${e.message}</p>`;
    return;
  }

  // Sync state to what the server actually used (respects config/default overrides).
  _since = _lastData.range.since;
  _until = _lastData.range.until;

  _paintOverview(app);
}

async function renderSessionsRoute(app) {
  const d = new Date();
  const weekAgo  = localDate(new Date(d.getFullYear(), d.getMonth(), d.getDate() - 7));
  const tomorrow = tomorrowLabel();

  app.innerHTML = `
    <div class="card">
      <h2>Sessions</h2>
      <div class="filter-row">
        <label>From <input type="date" id="since-input" value="${weekAgo}"></label>
        <label>To   <input type="date" id="until-input" value="${tomorrow}"></label>
        <button id="filter-btn">Apply</button>
      </div>
      <div id="sessions-table"><p class="muted">Loading…</p></div>
    </div>`;

  async function load() {
    const since = $('#since-input').value || weekAgo;
    const until = $('#until-input').value || tomorrow;
    try {
      const data = await api(`/api/sessions?since=${since}&until=${until}`);
      const tbl = $('#sessions-table');
      tbl.innerHTML = renderSessionsTable(data.sessions);
      initExpandButtons(tbl);
    } catch (e) {
      $('#sessions-table').innerHTML = `<p style="color:var(--bad)">Error: ${e.message}</p>`;
    }
  }

  await load();
  $('#filter-btn').addEventListener('click', load);
}

// ── Favicon ───────────────────────────────────────────────────────────────────
function buildFavicon() {
  const img = new Image();
  img.onload = () => {
    const size = 64;
    const canvas = document.createElement('canvas');
    canvas.width = size; canvas.height = size;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0, size, size);
    const link = document.querySelector('link[rel="icon"]');
    if (link) link.href = canvas.toDataURL('image/png');
  };
  img.src = '/web/billfish_800_white_transparent.png';
}

// ── Router + topbar ───────────────────────────────────────────────────────────
function buildTopbar() {
  const header = document.createElement('header');
  header.className = 'topbar';
  header.innerHTML = `
    <div class="brand"><img src="/web/billfish_800_white_transparent.png" class="brand-logo" alt="">Billfish</div>
    <nav>
      ${Object.keys(ROUTES).map(r => `<a href="#${r}" data-route="${r}">${r.slice(1)}</a>`).join('')}
    </nav>
    <div class="spacer"></div>
    <span class="pricing-status" id="pricing-status"></span>
    <button class="refresh-btn" id="refresh-btn">↻ Refresh</button>`;
  document.body.prepend(header);
}

function updatePricingStatus(config) {
  const el = document.getElementById('pricing-status');
  if (!el || !config) return;
  if (config.pricing_source === 'live' && config.pricing_fetched_at) {
    const ageMs = Date.now() - config.pricing_fetched_at * 1000;
    const ageH  = Math.floor(ageMs / 3_600_000);
    const ageM  = Math.floor((ageMs % 3_600_000) / 60_000);
    const ago   = ageH > 0 ? `${ageH}h ago` : ageM > 0 ? `${ageM}m ago` : 'just now';
    el.innerHTML = `<span class="pricing-dot live"></span>Pricing live · ${ago}`;
    el.title = `Fetched from LiteLLM at ${new Date(config.pricing_fetched_at * 1000).toLocaleString()}`;
  } else {
    el.innerHTML = `<span class="pricing-dot bundled"></span>Pricing bundled`;
    el.title = 'Using built-in pricing snapshot — network unavailable or fetch failed';
  }
}

function setActiveTab(routeKey) {
  $$('header.topbar nav a').forEach(a => a.classList.toggle('active', a.dataset.route === routeKey));
}

// Wire up the Apply button and preset buttons on the overview controls.
// Mutates _since/_until state and calls renderOverview directly (no hash dance).
function initOverviewControls() {
  const applyBtn = $('#apply-btn');
  if (applyBtn) {
    applyBtn.addEventListener('click', () => {
      const sd = $('#since-date').value, st = $('#since-time').value || '00:00';
      const ud = $('#until-date').value, ut = $('#until-time').value || '00:00';
      _since = sd ? `${sd}T${st}` : null;
      _until = ud ? `${ud}T${ut}` : null;
      renderOverview($('#app'));
    });
  }
  $$('.preset-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const d = new Date();
      if (btn.dataset.mode === 'week') {
        // Billing week: Fri 17:00 → next Fri 17:00.
        const wk = weekLabel();
        _since = wk.since;
        _until = wk.until;
      } else {
        const days = parseInt(btn.dataset.days, 10);
        _since = localDate(new Date(d.getFullYear(), d.getMonth(), d.getDate() - (days - 1))) + 'T00:00';
        _until = tomorrowLabel() + 'T00:00';
      }
      renderOverview($('#app'));
    });
  });
}

async function route() {
  const app = $('#app');
  const hash = location.hash.replace('#', '') || '/overview';
  const fn = ROUTES[hash] || ROUTES['/overview'];
  setActiveTab(hash in ROUTES ? hash : '/overview');
  await fn(app);
}

// ── Auto-refresh ──────────────────────────────────────────────────────────────
const REFRESH_INTERVAL_MS = 15 * 60 * 1000;
let _refreshTimer = null;
let _refreshCountdownTimer = null;
let _refreshAt = null;

function startCountdown() {
  clearInterval(_refreshCountdownTimer);
  _refreshCountdownTimer = setInterval(() => {
    const btn = document.getElementById('refresh-btn');
    if (!btn || _refreshAt === null) return;
    const secsLeft = Math.max(0, Math.round((_refreshAt - Date.now()) / 1000));
    const m = Math.floor(secsLeft / 60);
    const s = String(secsLeft % 60).padStart(2, '0');
    btn.textContent = `↻ ${m}:${s}`;
  }, 1000);
}

function scheduleRefresh() {
  clearTimeout(_refreshTimer);
  _refreshAt = Date.now() + REFRESH_INTERVAL_MS;
  startCountdown();
  _refreshTimer = setTimeout(() => { route().then(scheduleRefresh); }, REFRESH_INTERVAL_MS);
}

buildTopbar();
buildFavicon();
route().then(scheduleRefresh);
window.addEventListener('hashchange', route);
document.getElementById('refresh-btn').addEventListener('click', () => { route().then(scheduleRefresh); });
