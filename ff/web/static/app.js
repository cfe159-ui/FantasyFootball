'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const state = { view: 'draft', bias: 0.5, pos: 'ALL', status: null, draft: null,
  ready: false, warmTimer: null };

const TITLES = {
  draft: ['Draft Room', 'Mark picks as they happen; the board re-ranks for your roster'],
  team: ['My Team', 'Optimal lineup, discounted for injury risk'],
  board: ['Rankings', 'Value over replacement under your league rules'],
  waivers: ['Waivers', 'Ranked by improvement to your starting lineup'],
  trade: ['Trade', 'Judged on lineup change, not raw point totals'],
  teams: ['Team Outlook', 'Vegas implied scoring and projected wins'],
  podcasts: ['Podcasts', 'What the shows said about your players'],
  settings: ['Settings', 'League rules and data sources'],
};

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (e) { /* keep status */ }
    throw new Error(msg);
  }
  return res.json();
}

function toast(msg, ms = 2600) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.remove('hidden');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add('hidden'), ms);
}

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const posTag = (p) => `<span class="pos ${esc(p || '')}">${esc(p || '')}</span>`;
const signed = (n, d = 0) => (n > 0 ? '+' : '') + Number(n).toFixed(d);
const cls = (n) => (n > 0 ? 'pos-pos' : n < 0 ? 'pos-neg' : '');
const skeleton = (el) => { el.innerHTML = '<div class="skeleton"></div>'; };
const empty = (el, msg) => { el.innerHTML = `<div class="empty">${esc(msg)}</div>`; };

function table(headers, rows) {
  const head = headers.map((h) =>
    `<th class="${h.num ? 'num' : ''}">${esc(h.label ?? h)}</th>`).join('');
  return `<table><thead><tr>${head}</tr></thead><tbody>${rows.join('')}</tbody></table>`;
}

/* ---------------- navigation ---------------- */

const NEEDS_BOARD = new Set(['team', 'board', 'waivers', 'teams', 'podcasts']);

function show(view) {
  state.view = view;
  $$('.nav-item').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  $$('.view').forEach((v) => v.classList.toggle('hidden', v.id !== `view-${view}`));
  const [title, sub] = TITLES[view] || [view, ''];
  $('#view-title').textContent = title;
  $('#view-sub').textContent = sub;
  if (!state.ready && NEEDS_BOARD.has(view)) return;   // the warming bar explains why
  LOADERS[view] && LOADERS[view]();
}

/* ---------------- status ---------------- */

async function loadStatus() {
  const s = await api('/api/status');
  state.status = s;
  const lg = s.league;
  $('#brand-sub').textContent =
    `${lg.teams}-team · ${lg.ppr === 1 ? 'PPR' : lg.ppr === 0.5 ? '½ PPR' : 'standard'}`;
  $('#src-fp').className = 'dot ' + (s.sources.fantasypros ? 'on' : 'off');
  $('#src-yahoo').className = 'dot ' + (s.sources.yahoo_authorized ? 'on' : 'off');
  renderWarming(s);
  return s;
}

/* The board is built at startup in the background. Show its progress rather
   than leaving a view on a skeleton, and load the current view the moment it
   becomes available. */
function renderWarming(s) {
  const bar = $('#warming');
  const wasReady = state.ready;
  state.ready = !!s.board_ready;

  if (s.warm_error) {
    bar.className = 'warming error';
    $('#warming-text').textContent = `Could not build projections: ${s.warm_error}`;
    stopWarmPolling();
    return;
  }
  if (state.ready) {
    bar.classList.add('hidden');
    stopWarmPolling();
    // Refresh whatever the user is looking at, now that data exists.
    if (!wasReady && LOADERS[state.view]) LOADERS[state.view]();
    return;
  }
  bar.className = 'warming';
  const secs = s.warming_seconds;
  $('#warming-text').textContent = secs != null
    ? `Building projections… ${secs.toFixed(0)}s`
    : 'Building projections…';
  startWarmPolling();
}

function startWarmPolling() {
  if (state.warmTimer) return;
  state.warmTimer = setInterval(async () => {
    try { await loadStatus(); } catch (e) { /* keep trying */ }
  }, 1200);
}

function stopWarmPolling() {
  if (state.warmTimer) { clearInterval(state.warmTimer); state.warmTimer = null; }
}

/* ---------------- draft ---------------- */

async function loadDraft() {
  let d;
  try { d = await api('/api/draft'); } catch (e) { d = { active: false }; }
  state.draft = d;
  $('#draft-setup').classList.toggle('hidden', !!d.active);
  $('#draft-live').classList.toggle('hidden', !d.active);
  if (!d.active) { orbFromDraft(d); return; }
  renderDraftStatus(d);
  const box = $('#d-candidates');
  skeleton(box);
  try {
    const b = await api(`/api/draft/board?limit=20&team_bias=${state.bias}`);
    state.draft = b;
    renderDraftStatus(b);
    renderCandidates(b);
    orbFromDraft(b);
  } catch (e) { empty(box, e.message); }
}

function renderDraftStatus(d) {
  $('#d-pick').textContent = d.pick_number;
  $('#d-round').textContent = `${d.round}.${String(d.slot).padStart(2, '0')}`;
  $('#d-until').textContent = d.my_turn ? 'NOW' : d.picks_until_mine;

  const needs = d.needs || {};
  const unmet = Object.entries(needs).filter(([, n]) => n > 0);
  $('#d-needs').innerHTML = unmet.length
    ? unmet.map(([p, n]) => `${posTag(p)} <span class="mono">×${n}</span>`).join('  ')
    : '<span class="muted">starters full</span>';

  const alert = $('#d-alert');
  const runs = Object.entries(d.runs || {}).filter(([, n]) => n >= 3);
  if (d.my_turn) {
    alert.className = 'alert';
    alert.textContent = 'You are on the clock.';
    alert.classList.remove('hidden');
  } else if (runs.length) {
    alert.className = 'alert warn';
    alert.textContent = `Run in progress — ${runs.map(([p, n]) => `${n} ${p}s`).join(', ')} in the last 8 picks.`;
    alert.classList.remove('hidden');
  } else { alert.classList.add('hidden'); }

  $('#d-myteam').innerHTML = (d.my_roster || []).length
    ? d.my_roster.map((n) => `<span class="pill">${esc(n)}</span>`).join('')
    : '<span class="muted small">no picks yet</span>';

  $('#d-recent').innerHTML = (d.taken || []).slice(-8).reverse().map((t) =>
    `<div class="feed-item ${t.mine ? 'mine' : ''}">
       <div class="feed-head"><span>${esc(t.name)}</span>
         <span class="muted mono">#${t.pick}</span></div>
       <div class="feed-body">${posTag(t.pos)} ${esc(t.team || '')}${t.mine ? ' · your pick' : ''}</div>
     </div>`).join('') || '<div class="muted small">no picks yet</div>';
}

function renderCandidates(b) {
  const rows = (b.candidates || []).map((c) => {
    const surv = c.survival > 0.6 ? '<span class="badge good">likely</span>'
      : c.survival > 0.3 ? '<span class="badge warn">maybe</span>'
      : '<span class="badge bad">no</span>';
    const tier = c.tier_remaining <= 2
      ? `<span class="badge bad">${c.tier_remaining} left</span>`
      : `<span class="mono">${c.tier_remaining}</span>`;
    return `<tr>
      <td><strong>${esc(c.name)}</strong></td>
      <td>${posTag(c.position)}</td>
      <td class="muted">${esc(c.team || '')}</td>
      <td class="num">${signed(c.vor)}</td>
      <td class="num">${signed(c.lineup_gain)}</td>
      <td class="num"><strong>${c.score.toFixed(0)}</strong></td>
      <td>${tier}</td>
      <td>${surv}</td>
      <td><button class="btn tiny" data-pick="${esc(c.name)}" data-mine="1">Mine</button>
          <button class="btn tiny" data-pick="${esc(c.name)}" data-mine="0">Taken</button></td>
    </tr>`;
  });
  $('#d-candidates').innerHTML = rows.length
    ? table(['Player', 'Pos', 'Team',
             { label: 'VOR', num: true }, { label: '+Lineup', num: true },
             { label: 'Score', num: true }, 'Tier left', 'Lasts?', ''], rows)
    : '<div class="empty">No candidates.</div>';
}

async function recordPick(name, mine) {
  try {
    await api('/api/draft/pick', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, mine }),
    });
    toast(`${mine ? 'You drafted' : 'Off the board:'} ${name}`);
    loadDraft();
  } catch (e) { toast(e.message); }
}

/* ---------------- team ---------------- */

async function loadTeam() {
  const startBox = $('#lineup-starters');
  skeleton(startBox);
  try {
    const r = await api('/api/roster');
    $('#roster-text').value = r.players.map((p) => p.name).join('\n');
  } catch (e) { /* leave editor as-is */ }

  try {
    const l = await api('/api/lineup');
    if (!l.starters.length) {
      empty(startBox, l.note || 'Add players to your roster to see a lineup.');
      $('#lineup-bench').innerHTML = '';
      $('#lineup-total').textContent = '';
      return;
    }
    $('#lineup-total').textContent = `${l.total.toFixed(1)} pts · week ${l.week}`;
    const rows = l.starters.map((s) => `<tr>
      <td class="mono muted">${esc(s.slot)}</td>
      <td><strong>${esc(s.name)}</strong></td>
      <td>${posTag(s.position)}</td>
      <td class="muted">${esc(s.team || '')}</td>
      <td class="num">${s.raw_points != null && s.status
        ? `<span class="muted">${s.raw_points.toFixed(1)}→</span> ` : ''}${s.points.toFixed(1)}</td>
      <td>${s.status ? `<span class="badge warn">${esc(s.status)}</span>` : ''}</td>
    </tr>`);
    startBox.innerHTML = table(['Slot', 'Player', 'Pos', 'Team',
      { label: 'Proj', num: true }, 'Status'], rows);

    const bench = l.bench.map((s) => `<tr>
      <td><strong>${esc(s.name)}</strong></td>
      <td>${posTag(s.position)}</td>
      <td class="muted">${esc(s.team || '')}</td>
      <td class="num">${s.points.toFixed(1)}</td>
      <td>${s.on_bye ? '<span class="badge warn">BYE</span>'
            : s.status ? `<span class="badge warn">${esc(s.status)}</span>` : ''}</td>
    </tr>`);
    $('#lineup-bench').innerHTML = bench.length
      ? table(['Player', 'Pos', 'Team', { label: 'Proj', num: true }, ''], bench)
      : '<div class="empty">Bench is empty.</div>';
    if (l.empty && l.empty.length) {
      $('#lineup-total').innerHTML +=
        ` <span class="badge bad">unfilled: ${esc(l.empty.join(', '))}</span>`;
    }
  } catch (e) { empty(startBox, e.message); }
}

/* ---------------- board ---------------- */

async function loadBoard() {
  const box = $('#board-table');
  skeleton(box);
  try {
    const b = await api(`/api/board?position=${state.pos}&limit=150&team_bias=${state.bias}`);
    $('#scarcity').innerHTML = Object.entries(b.scarcity || {})
      .sort((a, c) => c[1].cliff - a[1].cliff).slice(0, 6)
      .map(([pos, d]) => `<div class="scar-card">
        <div class="scar-pos">${esc(pos)} cliff</div>
        <div class="scar-cliff">${d.cliff.toFixed(0)}</div>
        <div class="scar-note">replacement #${d.replacement_rank}</div></div>`).join('');

    let lastTier = null;
    const rows = b.players.map((p, i) => {
      const brk = lastTier !== null && p.tier !== lastTier;
      lastTier = p.tier;
      return `<tr class="${brk ? 'tier-break' : ''}">
        <td class="num muted">${i + 1}</td>
        <td><strong>${esc(p.name)}</strong>${p.rookie ? ' <span class="badge">R</span>' : ''}</td>
        <td>${posTag(p.position)}</td>
        <td class="muted">${esc(p.team || '')}</td>
        <td class="num">${p.points.toFixed(0)}</td>
        <td class="num ${cls(p.vor)}">${signed(p.vor)}</td>
        <td class="num muted">${p.ppg.toFixed(1)}</td>
        <td class="muted small">${esc(p.basis)}</td>
        <td>${p.injury ? `<span class="badge warn">${esc(p.injury)}</span>` : ''}</td>
      </tr>`;
    });
    box.innerHTML = table(['#', 'Player', 'Pos', 'Team',
      { label: 'Proj', num: true }, { label: 'VOR', num: true },
      { label: 'PPG', num: true }, 'Sources', ''], rows);
  } catch (e) { empty(box, e.message); }
}

/* ---------------- waivers ---------------- */

async function loadWaivers() {
  const box = $('#waiver-table');
  skeleton(box);
  try {
    const w = await api('/api/waivers?limit=30');
    $('#waiver-basis').textContent = `Free-agent pool from ${w.basis}`;
    if (!w.targets.length) { empty(box, w.note || 'No targets.'); return; }
    const rows = w.targets.map((t, i) => `<tr>
      <td class="num muted">${i + 1}</td>
      <td><strong>${esc(t.name)}</strong></td>
      <td>${posTag(t.position)}</td>
      <td class="muted">${esc(t.team || '')}</td>
      <td class="num">${t.ppg.toFixed(1)}</td>
      <td class="num ${cls(t.marginal)}">${signed(t.marginal, 1)}</td>
      <td class="num">${t.faab ? t.faab + '%' : '—'}</td>
      <td class="num muted">${t.adds ? t.adds.toLocaleString() : ''}</td>
      <td class="muted small">${esc((t.reasons || []).slice(0, 2).join('; '))}</td>
    </tr>`);
    box.innerHTML = table(['#', 'Player', 'Pos', 'Team',
      { label: 'PPG', num: true }, { label: '+Lineup', num: true },
      { label: 'FAAB', num: true }, { label: '24h adds', num: true }, 'Why'], rows);
  } catch (e) { empty(box, e.message); }
}

/* ---------------- teams ---------------- */

async function loadTeams() {
  const box = $('#teams-table');
  skeleton(box);
  try {
    const t = await api(`/api/teams?team_bias=${state.bias}`);
    if (!t.teams.length) { empty(box, t.note || 'No lines posted.'); return; }
    const rows = t.teams.map((x) => `<tr>
      <td><strong>${esc(x.team)}</strong></td>
      <td class="num">${x.implied_total.toFixed(1)}</td>
      <td class="num">${x.projected_wins.toFixed(1)}</td>
      <td>${esc(x.qb || '')}</td>
      <td class="num ${cls(x.rb_tilt)}">${signed(x.rb_tilt, 1)}%</td>
      <td class="num ${cls(x.wr_tilt)}">${signed(x.wr_tilt, 1)}%</td>
      <td class="num muted">${x.games_priced}</td>
    </tr>`);
    box.innerHTML = table(['Team', { label: 'Implied pts', num: true },
      { label: 'Proj wins', num: true }, 'QB',
      { label: 'RB tilt', num: true }, { label: 'WR tilt', num: true },
      { label: 'Games', num: true }], rows);
  } catch (e) { empty(box, e.message); }
}

/* ---------------- podcasts ---------------- */

async function loadPodcasts() {
  const box = $('#pod-list');
  box.innerHTML = '<div class="muted small">loading…</div>';
  const inj = $('#pod-injury').checked, ros = $('#pod-roster').checked;
  try {
    const p = await api(`/api/podcasts?limit=60&injury_only=${inj}&roster_only=${ros}`);
    $('#pod-meta').textContent = p.transcripts
      ? `${p.transcripts} transcripts · ${(p.shows || []).join(', ')}`
      : '';
    if (!p.mentions.length) {
      box.innerHTML = `<div class="empty">${esc(p.note ||
        'No mentions found. Fetch episodes with: ff podcasts --fetch')}</div>`;
      return;
    }
    box.innerHTML = p.mentions.map((m) => `<div class="feed-item">
      <div class="feed-head">
        <span>${esc(m.player)} ${posTag(m.position)}</span>
        <span class="muted small">${esc(m.show)} · ${esc(m.clock)}</span>
      </div>
      <div class="feed-body">${esc(m.context)}</div>
      <div style="margin-top:6px">
        ${m.injury ? '<span class="badge warn">injury</span> ' : ''}
        ${m.opinion ? '<span class="badge">opinion</span>' : ''}
      </div>
    </div>`).join('');
  } catch (e) { empty(box, e.message); }
}

/* ---------------- settings ---------------- */

const SLOT_ORDER = ['QB', 'RB', 'WR', 'TE', 'W/R/T', 'FLEX', 'SUPERFLEX', 'K', 'DST', 'BN'];

async function loadSettings() {
  const s = state.status || await loadStatus();
  $('#s-teams').value = s.league.teams;
  $('#s-ppr').value = s.league.ppr;
  const slots = s.league.slots || {};
  const keys = Array.from(new Set([...SLOT_ORDER, ...Object.keys(slots)]));
  $('#s-slots').innerHTML = keys.map((k) =>
    `<label>${esc(k)}<input type="number" min="0" max="9" data-slot="${esc(k)}"
      value="${slots[k] || 0}"></label>`).join('');
  $('#s-sources').innerHTML = `
    <div class="src-row" style="color:var(--text)"><span>Sleeper (free)</span>
      <span class="badge good">active</span></div>
    <div class="src-row" style="color:var(--text)"><span>nflverse (free)</span>
      <span class="badge good">active</span></div>
    <div class="src-row" style="color:var(--text)"><span>FantasyPros consensus</span>
      ${s.sources.fantasypros ? '<span class="badge good">active</span>'
        : '<span class="badge warn">no API key</span>'}</div>
    <div class="src-row" style="color:var(--text)"><span>Yahoo league</span>
      ${s.sources.yahoo_authorized ? '<span class="badge good">connected</span>'
        : '<span class="badge warn">awaiting approval</span>'}</div>`;
}

const LOADERS = {
  draft: loadDraft, team: loadTeam, board: loadBoard, waivers: loadWaivers,
  teams: loadTeams, podcasts: loadPodcasts, settings: loadSettings, trade: () => {},
};

/* ---------------- events ---------------- */

document.addEventListener('click', async (e) => {
  const nav = e.target.closest('.nav-item');
  if (nav) return show(nav.dataset.view);

  const pick = e.target.closest('[data-pick]');
  if (pick) return recordPick(pick.dataset.pick, pick.dataset.mine === '1');

  const seg = e.target.closest('#pos-filter button');
  if (seg) {
    $$('#pos-filter button').forEach((b) => b.classList.toggle('active', b === seg));
    state.pos = seg.dataset.pos;
    return loadBoard();
  }
});

$('#d-start').addEventListener('click', async () => {
  try {
    await api('/api/draft/start', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        teams: +$('#d-teams').value, my_pick: +$('#d-slot').value,
        rounds: +$('#d-rounds').value, snake: true }),
    });
    toast('Draft started');
    loadDraft();
  } catch (err) { toast(err.message); }
});

$('#d-undo').addEventListener('click', async () => {
  try { await api('/api/draft/undo', { method: 'POST' }); toast('Undid last pick'); loadDraft(); }
  catch (err) { toast(err.message); }
});

$('#d-export').addEventListener('click', async () => {
  try { const r = await api('/api/draft/export', { method: 'POST' });
    toast(`Exported ${r.count} players to your roster`); }
  catch (err) { toast(err.message); }
});

$('#roster-save').addEventListener('click', async () => {
  const names = $('#roster-text').value.split('\n').map((s) => s.trim()).filter(Boolean);
  try {
    await api('/api/roster', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ names }),
    });
    $('#roster-msg').textContent = `Saved ${names.length} players.`;
    loadTeam();
  } catch (err) { $('#roster-msg').textContent = err.message; }
});

$('#trade-run').addEventListener('click', async () => {
  const parse = (id) => $(id).value.split('\n').map((s) => s.trim()).filter(Boolean);
  const box = $('#trade-result');
  box.innerHTML = '<div class="card"><div class="skeleton" style="height:80px"></div></div>';
  try {
    const r = await api('/api/trade', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ give: parse('#trade-give'), get: parse('#trade-get') }),
    });
    const kind = r.net > 0.3 ? 'good' : r.net < -0.3 ? 'bad' : 'warn';
    box.innerHTML = `<div class="card">
      <div style="display:flex;align-items:center;gap:14px;margin-bottom:12px">
        <span class="badge ${kind}" style="font-size:14px;padding:6px 14px">
          ${esc(r.verdict.toUpperCase())}</span>
        <strong class="mono" style="font-size:18px">${signed(r.net, 2)} pts/week</strong>
      </div>
      ${table(['', { label: 'Before', num: true }, { label: 'After', num: true }], [
        `<tr><td>Raw points</td><td class="num">${r.outgoing_value.toFixed(1)}</td>
          <td class="num">${r.incoming_value.toFixed(1)}</td></tr>`,
        `<tr><td>Starting lineup</td><td class="num">${r.lineup_before.toFixed(1)}</td>
          <td class="num">${r.lineup_after.toFixed(1)}</td></tr>`,
      ])}
      ${r.starters_gained.length ? `<p class="small" style="color:var(--good)">
        Enters lineup: ${esc(r.starters_gained.join(', '))}</p>` : ''}
      ${r.starters_lost.length ? `<p class="small" style="color:var(--warn)">
        Leaves lineup: ${esc(r.starters_lost.join(', '))}</p>` : ''}
      ${(r.notes || []).map((n) => `<p class="muted small">— ${esc(n)}</p>`).join('')}
    </div>`;
  } catch (err) { box.innerHTML = `<div class="card"><p class="muted">${esc(err.message)}</p></div>`; }
});

$('#bias').addEventListener('input', (e) => {
  state.bias = parseFloat(e.target.value);
  $('#bias-val').textContent = state.bias.toFixed(1);
});
$('#bias').addEventListener('change', () => {
  if (state.view === 'board') loadBoard();
  if (state.view === 'teams') loadTeams();
});

$('#pod-injury').addEventListener('change', loadPodcasts);
$('#pod-roster').addEventListener('change', loadPodcasts);

$('#s-save').addEventListener('click', async () => {
  const slots = {};
  $$('#s-slots input').forEach((i) => { if (+i.value > 0) slots[i.dataset.slot] = +i.value; });
  try {
    await api('/api/league', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ num_teams: +$('#s-teams').value, ppr: +$('#s-ppr').value, slots }),
    });
    $('#s-msg').textContent = 'Saved. Rebuilding projections…';
    state.ready = false;
    await loadStatus();
  } catch (err) { $('#s-msg').textContent = err.message; }
});

$('#refresh').addEventListener('click', async () => {
  toast('Rebuilding projections…');
  try {
    await api(`/api/board?limit=1&refresh=true&team_bias=${state.bias}`);
    toast('Rebuilt');
  } catch (e) { toast(e.message); }
  await loadStatus();
  LOADERS[state.view] && LOADERS[state.view]();
});

let searchTimer;
$('#global-search').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  const pop = $('#search-results');
  if (q.length < 2) { pop.classList.add('hidden'); return; }
  searchTimer = setTimeout(async () => {
    try {
      const r = await api(`/api/search?q=${encodeURIComponent(q)}`);
      if (!r.players.length) { pop.classList.add('hidden'); return; }
      pop.innerHTML = r.players.map((p) => `<div class="search-row">
        <span>${esc(p.name)} ${posTag(p.position)}</span>
        <span class="mono muted">${signed(p.vor)}</span>
        ${state.draft && state.draft.active
          ? `<button class="btn tiny" data-pick="${esc(p.name)}" data-mine="0">Taken</button>` : ''}
      </div>`).join('');
      pop.classList.remove('hidden');
    } catch (err) { pop.classList.add('hidden'); }
  }, 220);
});
document.addEventListener('click', (e) => {
  if (!e.target.closest('.topbar-actions')) $('#search-results').classList.add('hidden');
});

/* ---------------- boot ---------------- */

(async function boot() {
  try { await loadStatus(); } catch (e) { toast('Backend not reachable'); }
  show('draft');
})();

/* ============================================================
   Assistant console
   ------------------------------------------------------------
   The orb is an in-app presence, not a live link to Claude: this
   app runs entirely on your machine. It reacts to two real
   signals -- your microphone level (analysed locally via Web
   Audio, never recorded and never sent anywhere) and the state
   of the draft board.
   ============================================================ */

const orb = {
  el: null, bars: [], analyser: null, data: null, stream: null,
  raf: null, level: 0, state: 'idle',
  BAR_COUNT: 56, R_IN: 58, R_OUT: 74,
};

const SVG_NS = 'http://www.w3.org/2000/svg';

function svgEl(tag, attrs) {
  // innerHTML on an SVG element is parsed by the HTML parser, which neither
  // honours self-closing tags nor puts the result in the SVG namespace -- the
  // elements silently nest and never render. Build them properly.
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  return el;
}

function buildOrb() {
  orb.el = document.querySelector('.console');
  if (!orb.el) return;

  const ticks = document.getElementById('orb-ticks');
  const barsG = document.getElementById('orb-bars');
  if (!ticks || !barsG) return;

  // Tick marks around the outer ring.
  const TICKS = 60;
  ticks.replaceChildren(...Array.from({ length: TICKS }, (_, i) => {
    const a = (i / TICKS) * Math.PI * 2;
    const long = i % 5 === 0;
    const r1 = long ? 118 : 123, r2 = 128;
    return svgEl('line', {
      x1: (150 + Math.cos(a) * r1).toFixed(1),
      y1: (150 + Math.sin(a) * r1).toFixed(1),
      x2: (150 + Math.cos(a) * r2).toFixed(1),
      y2: (150 + Math.sin(a) * r2).toFixed(1),
      opacity: long ? 0.55 : 0.25,
    });
  }));

  // Radial audio bars.
  barsG.replaceChildren(...Array.from({ length: orb.BAR_COUNT },
    () => svgEl('line', { x1: 0, y1: 0, x2: 0, y2: 0 })));
  orb.bars = Array.from(barsG.children);
  drawBars(0);
}

function drawBars(level, spectrum) {
  const n = orb.BAR_COUNT;
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2 - Math.PI / 2;
    // Idle: a slow travelling wave so the orb is alive without input.
    const idle = 0.16 + 0.1 * Math.sin(i * 0.55 + Date.now() / 620);
    const amp = spectrum
      ? Math.max(idle, (spectrum[i % spectrum.length] / 255) * 1.25)
      : idle;
    const r1 = orb.R_IN;
    const r2 = orb.R_IN + (orb.R_OUT - orb.R_IN) * Math.min(amp * (1 + level), 2.6);
    const b = orb.bars[i];
    if (!b) continue;
    b.setAttribute('x1', (150 + Math.cos(a) * r1).toFixed(1));
    b.setAttribute('y1', (150 + Math.sin(a) * r1).toFixed(1));
    b.setAttribute('x2', (150 + Math.cos(a) * r2).toFixed(1));
    b.setAttribute('y2', (150 + Math.sin(a) * r2).toFixed(1));
    b.setAttribute('opacity', (0.35 + Math.min(amp, 1) * 0.55).toFixed(2));
  }
}

function orbLoop() {
  let spectrum = null;
  if (orb.analyser) {
    orb.analyser.getByteFrequencyData(orb.data);
    // Mean of the speech-relevant low band, smoothed.
    let sum = 0;
    const band = Math.min(orb.data.length, 48);
    for (let i = 0; i < band; i++) sum += orb.data[i];
    const raw = sum / band / 255;
    orb.level = orb.level * 0.75 + raw * 0.25;
    spectrum = orb.data;
    const core = document.querySelector('.core');
    if (core) core.style.transform = `scale(${(1 + orb.level * 0.55).toFixed(3)})`;
    setOrbState(orb.level > 0.06 ? 'listening' : orb.baseState || 'idle', true);
  }
  drawBars(orb.level, spectrum);
  orb.raf = requestAnimationFrame(orbLoop);
}

function setOrbState(state, transient) {
  if (!orb.el) return;
  if (!transient) orb.baseState = state;
  if (orb.state === state) return;
  orb.state = state;
  orb.el.dataset.state = state;
  const label = document.getElementById('orb-label');
  if (label) {
    label.textContent = { idle: 'standby', listening: 'listening',
      alert: 'your pick', warn: 'attention' }[state] || state;
  }
}

function say(line, sub) {
  const a = document.getElementById('orb-message');
  const b = document.getElementById('orb-sub');
  if (a && line != null) a.textContent = line;
  if (b && sub != null) b.textContent = sub;
}

async function toggleMic() {
  const btn = document.getElementById('mic-toggle');
  const note = document.getElementById('mic-note');
  if (orb.stream) {
    orb.stream.getTracks().forEach((t) => t.stop());
    orb.stream = null; orb.analyser = null; orb.level = 0;
    btn.classList.remove('on');
    btn.innerHTML = '<span class="mic-dot"></span> Enable microphone';
    note.textContent = 'Audio stays on this machine.';
    setOrbState(orb.baseState || 'idle');
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const src = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    analyser.smoothingTimeConstant = 0.72;
    src.connect(analyser);
    orb.stream = stream; orb.analyser = analyser;
    orb.data = new Uint8Array(analyser.frequencyBinCount);
    btn.classList.add('on');
    btn.innerHTML = '<span class="mic-dot"></span> Microphone on';
    note.textContent = 'Analysed locally — nothing is recorded or sent.';
  } catch (err) {
    note.textContent = `Microphone unavailable: ${err.name}. The orb still reacts to the draft.`;
  }
}

/* Draft state drives what the assistant says and how it looks. */
function orbFromDraft(d) {
  if (!d || !d.active) {
    setOrbState('idle');
    say('Draft assistant ready.',
        'Start a draft and I will track the board as it empties, flag positional runs, and tell you when a tier is about to break.');
    return;
  }
  const top = (d.candidates || [])[0];
  const runs = Object.entries(d.runs || {}).filter(([, n]) => n >= 3);
  const thin = (d.candidates || []).find((c) => c.tier_remaining <= 2);

  if (d.my_turn) {
    setOrbState('alert');
    say(top ? `Take ${top.name}.` : 'You are on the clock.',
        top ? `${top.position} · ${top.team} — value over replacement ${signed(top.vor)}, `
            + `adds ${signed(top.lineup_gain)} to your starting lineup. `
            + (top.survival < 0.3 ? 'He will not last to your next pick.'
               : 'He may still be there next round.')
            : 'Pick from the board below.');
    return;
  }
  if (runs.length) {
    setOrbState('warn');
    const [pos, n] = runs[0];
    say(`${pos} run in progress.`,
        `${n} of the last 8 picks were ${pos}s. ${d.picks_until_mine} picks until you are up — `
        + `expect the tier to thin before then.`);
    return;
  }
  if (thin) {
    setOrbState('warn');
    say(`${thin.position} tier is nearly gone.`,
        `Only ${thin.tier_remaining} left at ${thin.name}'s tier. `
        + `${d.picks_until_mine} picks until your turn.`);
    return;
  }
  setOrbState('idle');
  say(`${d.picks_until_mine} picks until you are up.`,
      top ? `Best available is ${top.name} (${top.position}, ${top.team}).`
          : 'Tracking the board.');
}

document.addEventListener('DOMContentLoaded', () => {
  buildOrb();
  orbLoop();
  const btn = document.getElementById('mic-toggle');
  if (btn) btn.addEventListener('click', toggleMic);
});

/* ============================================================
   Voice loop
   ------------------------------------------------------------
   speech in   -- the browser's SpeechRecognition (local to the
                  browser; only the resulting text leaves)
   reasoning   -- POST /api/assistant, which calls Claude with
                  the live draft board as context
   speech out  -- the browser's speechSynthesis
   The orb tracks all three phases: listening, thinking, speaking.
   ============================================================ */

const convo = {
  rec: null, active: false, history: [], speaking: false,
  speakTimer: null, restarting: false,
};

function transcriptEl() { return document.getElementById('transcript'); }

function addTurn(who, text, interim) {
  const box = transcriptEl();
  if (!box) return null;
  box.classList.remove('hidden');
  let el = interim ? box.querySelector('.turn.interim') : null;
  if (!el) {
    el = document.createElement('div');
    box.appendChild(el);
  }
  el.className = `turn ${who}${interim ? ' interim' : ''}`;
  el.textContent = text;
  box.scrollTop = box.scrollHeight;
  return el;
}

/* speechSynthesis gives no audio stream to analyse, so the orb is animated
   procedurally while it speaks -- driven by the utterance's own boundary
   events so the motion tracks the actual cadence of the words. */
function startSpeakingAnimation() {
  convo.speaking = true;
  setOrbState('speaking');
  let phase = 0;
  clearInterval(convo.speakTimer);
  convo.speakTimer = setInterval(() => {
    phase += 0.35;
    orb.level = 0.28 + 0.22 * Math.abs(Math.sin(phase)) + Math.random() * 0.08;
  }, 70);
}

function stopSpeakingAnimation() {
  convo.speaking = false;
  clearInterval(convo.speakTimer);
  convo.speakTimer = null;
  if (!orb.analyser) orb.level = 0;
  setOrbState(orb.baseState || 'idle');
}

function speak(text) {
  if (!('speechSynthesis' in window) || !text.trim()) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.rate = 1.06;
  u.pitch = 1.0;
  const voices = window.speechSynthesis.getVoices();
  // Prefer a natural en-US voice when the platform offers one.
  const pick = voices.find((v) => /Samantha|Ava|Serena|Google US English/i.test(v.name))
    || voices.find((v) => v.lang === 'en-US');
  if (pick) u.voice = pick;
  u.onstart = startSpeakingAnimation;
  u.onend = stopSpeakingAnimation;
  u.onerror = stopSpeakingAnimation;
  window.speechSynthesis.speak(u);
}

async function askClaude(question) {
  addTurn('you', question);
  convo.history.push({ role: 'user', content: question });
  setOrbState('thinking');
  say('Thinking…', question);

  const el = addTurn('claude', '');
  let full = '';
  try {
    const res = await fetch('/api/assistant', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, history: convo.history.slice(0, -1) }),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (e) { /* keep */ }
      throw new Error(detail);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const frames = buf.split('\n\n');
      buf = frames.pop();
      for (const frame of frames) {
        if (frame.startsWith('event: error')) {
          throw new Error(frame.split('data: ')[1] || 'assistant error');
        }
        if (frame.startsWith('event: done')) continue;
        const line = frame.split('data: ')[1];
        if (line == null) continue;
        full += line.replace(/\\n/g, '\n');
        el.textContent = full;
        transcriptEl().scrollTop = transcriptEl().scrollHeight;
      }
    }
  } catch (err) {
    full = `Assistant unavailable: ${err.message}`;
    el.textContent = full;
    setOrbState(orb.baseState || 'idle');
    say('Assistant unavailable.', err.message);
    return;
  }
  convo.history.push({ role: 'assistant', content: full });
  say(full.split(/(?<=[.!?])\s/)[0] || full, full);
  speak(full);
}

function startConversation() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const note = document.getElementById('mic-note');
  if (!SR) {
    note.textContent = 'Speech recognition is unavailable in this window. '
      + 'Run "ff app --browser" and use Chrome or Safari, or type your question below.';
    showTypeFallback();
    return;
  }
  const rec = new SR();
  rec.continuous = true;
  rec.interimResults = true;
  rec.lang = 'en-US';

  rec.onresult = (e) => {
    let interim = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i];
      const text = r[0].transcript.trim();
      if (r.isFinal) {
        const el = transcriptEl().querySelector('.turn.interim');
        if (el) el.remove();
        if (text.length > 1 && !convo.speaking) askClaude(text);
      } else {
        interim += text + ' ';
      }
    }
    if (interim.trim()) addTurn('you', interim.trim(), true);
  };
  rec.onerror = (e) => {
    if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
      note.textContent = 'Microphone permission denied. '
        + 'Allow it in your browser, or type your question below.';
      showTypeFallback();
      stopConversation();
    }
  };
  // Continuous recognition still stops on its own; restart unless we meant to end.
  rec.onend = () => {
    if (convo.active && !convo.restarting) {
      convo.restarting = true;
      setTimeout(() => { convo.restarting = false; try { rec.start(); } catch (e) {} }, 250);
    }
  };

  convo.rec = rec;
  convo.active = true;
  try { rec.start(); } catch (e) { /* already started */ }

  const btn = document.getElementById('talk-toggle');
  btn.classList.add('on');
  btn.innerHTML = '<span class="mic-dot"></span> End conversation';
  note.textContent = 'Listening. Speech is recognised by your browser — only the transcript is sent to Claude.';
  setOrbState('listening');
  if (!orb.analyser) toggleMic();   // drive the visualiser from the same mic
}

function stopConversation() {
  convo.active = false;
  if (convo.rec) { try { convo.rec.stop(); } catch (e) {} convo.rec = null; }
  window.speechSynthesis && window.speechSynthesis.cancel();
  stopSpeakingAnimation();
  const btn = document.getElementById('talk-toggle');
  btn.classList.remove('on');
  btn.innerHTML = '<span class="mic-dot"></span> Start conversation';
  setOrbState(orb.baseState || 'idle');
}

function showTypeFallback() {
  if (document.getElementById('ask-box')) return;
  const wrap = document.createElement('div');
  wrap.className = 'console-actions';
  wrap.innerHTML = '<input id="ask-box" type="search" placeholder="Ask about the draft…" '
    + 'style="width:340px">';
  document.querySelector('.console-readout').appendChild(wrap);
  document.getElementById('ask-box').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.target.value.trim()) {
      askClaude(e.target.value.trim());
      e.target.value = '';
    }
  });
}

document.addEventListener('DOMContentLoaded', async () => {
  const btn = document.getElementById('talk-toggle');
  if (!btn) return;
  btn.addEventListener('click', () =>
    (convo.active ? stopConversation() : startConversation()));

  try {
    const a = await api('/api/assistant');
    if (!a.available) {
      btn.disabled = true;
      btn.style.opacity = '.5';
      document.getElementById('mic-note').innerHTML =
        'Voice chat needs an Anthropic API key. Add <code>ANTHROPIC_API_KEY</code> '
        + 'to your .env file, then restart the app.';
    }
  } catch (e) { /* status endpoint unavailable; leave the button as-is */ }
});
