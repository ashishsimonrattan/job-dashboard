const STATUS = {
  new:          { label: 'New',            color: 'var(--new)',       hint: 'Just discovered — not reviewed yet' },
  seen:         { label: 'Not applied',    color: 'var(--seen)',      hint: 'Reviewed / imported, not applied' },
  queued:       { label: 'Queued',         color: 'var(--progress)',  hint: 'Waiting for the application worker' },
  tailoring:    { label: 'Tailoring CV',   color: 'var(--progress)',  hint: 'Building the one-page résumé' },
  applying:     { label: 'Applying…',      color: 'var(--progress)',  hint: 'Filling the form in the browser' },
  needs_manual: { label: 'Needs you',      color: 'var(--manual)',    hint: 'Sign-in / extra questions — tab left open, pre-filled' },
  blocked:      { label: 'Blocked',        color: 'var(--blocked)',   hint: 'CAPTCHA / closed posting — apply manually' },
  failed:       { label: 'Error',          color: 'var(--failed)',    hint: 'Automation failed — retry or apply manually' },
  submitted:    { label: 'Submitted ✓',    color: 'var(--submitted)', hint: 'Portal confirmed the application' },
  skipped:      { label: 'Skipped',        color: 'var(--skipped)',   hint: 'You chose not to apply' },
};
const ACTIVE = ['queued', 'tailoring', 'applying'];
const HDRS = { 'Content-Type': 'application/json', 'X-Requested-With': 'job-dashboard' };

const state = {
  jobs: [], selected: new Set(), statusFilter: new Set(), sort: { key: 'first_seen', dir: -1 },
  knownIds: null, flash: new Set(), lastFinished: null, prevStatus: {}, settingsLoaded: false,
};
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(path, body) {
  const res = await fetch(path, body === undefined ? { headers: HDRS } : { method: 'POST', headers: HDRS, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function ago(iso) {
  if (!iso) return '';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${Math.max(0, Math.round(s))}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function toast(msg, kind = '') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.innerHTML = msg;
  $('#toasts').append(el);
  setTimeout(() => el.remove(), 7000);
}

function beep() {
  try {
    const ctx = new AudioContext(); const o = ctx.createOscillator(); const g = ctx.createGain();
    o.frequency.value = 880; g.gain.value = 0.08; o.connect(g); g.connect(ctx.destination); o.start(); o.stop(ctx.currentTime + 0.25);
  } catch { /* audio blocked until user interacts */ }
}

function notify(newJobs) {
  if (!newJobs.length) return;
  beep();
  const names = newJobs.slice(0, 4).map(j => `${j.company} — ${j.title}`).join('<br>');
  toast(`<b>${newJobs.length} new opening${newJobs.length > 1 ? 's' : ''}</b><br>${esc(names).replace(/&lt;br&gt;/g, '<br>')}`);
  if ('Notification' in window && Notification.permission === 'granted') {
    const n = new Notification(`${newJobs.length} new job opening${newJobs.length > 1 ? 's' : ''}`, {
      body: newJobs.slice(0, 4).map(j => `${j.company}: ${j.title}`).join('\n'),
    });
    n.onclick = () => { window.focus(); n.close(); };
  }
  document.title = `(${newJobs.length} new) Job Radar`;
}

function renderLegend() {
  const counts = {};
  state.jobs.forEach(j => { counts[j.status] = (counts[j.status] || 0) + 1; });
  $('#legend').innerHTML = Object.entries(STATUS).map(([k, v]) => `
    <span class="chip ${state.statusFilter.has(k) ? 'active' : ''}" data-status="${k}" title="${esc(v.hint)}" style="color:${v.color}">
      <span class="dot" style="background:${v.color}"></span><span style="color:var(--text)">${v.label}</span>
      <b>${counts[k] || 0}</b></span>`).join('');
}

function visibleJobs() {
  const q = $('#search').value.trim().toLowerCase();
  const src = $('#sourceFilter').value, fit = $('#fitFilter').value, hide = $('#hideDone').checked;
  const { key, dir } = state.sort;
  const order = Object.keys(STATUS);
  return state.jobs.filter(j => {
    if (state.statusFilter.size && !state.statusFilter.has(j.status)) return false;
    if (!state.statusFilter.size && hide && (j.status === 'submitted' || j.status === 'skipped')) return false;
    if (src && j.source !== src) return false;
    if (fit && j.fit !== fit) return false;
    if (q && !`${j.company} ${j.title} ${j.location}`.toLowerCase().includes(q)) return false;
    return true;
  }).sort((a, b) => {
    const av = key === 'status' ? order.indexOf(a.status) : (a[key] || '');
    const bv = key === 'status' ? order.indexOf(b.status) : (b[key] || '');
    return av < bv ? -dir : av > bv ? dir : 0;
  });
}

function fitBadge(j) {
  const exp = j.exp_min != null ? `${j.exp_min}+ yrs` : '';
  const label = { entry: 'Entry', match: exp || '≤2 yrs', unknown: 'Exp. n/a', stretch: `Stretch ${exp}`, tracked: 'Tracked' }[j.fit] || j.fit || '';
  return `<span class="fit ${esc(j.fit)}" title="${esc(exp ? 'Minimum experience in JD: ' + exp : 'No years mentioned in JD')}">${esc(label)}</span>`;
}

function actions(j) {
  const busy = ACTIVE.includes(j.status);
  const out = [];
  if (j.status !== 'submitted') {
    const label = ['needs_manual', 'blocked', 'failed'].includes(j.status) ? 'Retry' : 'Apply';
    out.push(`<button class="btn small apply" data-act="apply" ${busy ? 'disabled' : ''}>${label}</button>`);
  }
  out.push(`<a class="btn small" href="${esc(j.apply_url || j.url)}" target="_blank" rel="noopener">Open ↗</a>`);
  if (j.resume_path) out.push(`<a class="btn small" href="/api/resume/${encodeURIComponent(j.id)}" target="_blank">CV</a>`);
  if (['needs_manual', 'blocked', 'failed'].includes(j.status))
    out.push(`<button class="btn small warn" data-act="open" title="Bring the pre-filled tab up in the automation browser">Show tab</button>`);
  if (j.status !== 'submitted') out.push(`<button class="btn small" data-act="mark">Mark done</button>`);
  if (!['submitted', 'skipped'].includes(j.status) && !busy) out.push(`<button class="btn small ghost" data-act="skip">Skip</button>`);
  if (j.status === 'skipped') out.push(`<button class="btn small ghost" data-act="reset">Undo</button>`);
  out.push(`<button class="btn small ghost" data-act="details">⋯</button>`);
  return out.join('');
}

function renderTable() {
  const rows = visibleJobs();
  $('#jobs tbody').innerHTML = rows.map(j => {
    const st = STATUS[j.status] || { label: j.status };
    return `<tr class="s-${esc(j.status)} ${state.flash.has(j.id) ? 'flash' : ''}" data-id="${esc(j.id)}">
      <td><input type="checkbox" class="sel" ${state.selected.has(j.id) ? 'checked' : ''} ${j.status === 'submitted' ? 'disabled' : ''}></td>
      <td><span class="pill s-${esc(j.status)}" title="${esc(st.hint)}">${esc(st.label)}</span>
        ${j.status_detail && j.status !== 'new' ? `<div class="detail">${esc(j.status_detail)}</div>` : ''}</td>
      <td><div class="company">${esc(j.company)}</div><div class="src">${esc(j.source)}${j.ats && j.ats !== j.source ? ' · ' + esc(j.ats) : ''}</div></td>
      <td class="role"><a href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.title)}</a>
        ${j.status === 'new' ? '<span class="newbadge">NEW</span>' : ''}</td>
      <td>${esc((j.location || '').slice(0, 80))}</td>
      <td>${fitBadge(j)}</td>
      <td title="Posted: ${esc(j.posted_at || 'unknown')}\nFirst seen: ${esc(j.first_seen)}">${ago(j.first_seen)}
        ${j.posted_at ? `<div class="src">posted ${ago(j.posted_at)}</div>` : ''}</td>
      <td><div class="actions">${actions(j)}</div></td>
    </tr>`;
  }).join('');
  $('#empty').hidden = rows.length > 0;
  const selectable = [...state.selected].filter(id => state.jobs.find(j => j.id === id && j.status !== 'submitted'));
  $('#applySelected').textContent = `Apply to selected (${selectable.length})`;
  $('#applySelected').disabled = !selectable.length;
}

function renderSources(status) {
  $('#sources').innerHTML = (status.report || []).slice().sort((a, b) => (b.error ? 1 : 0) - (a.error ? 1 : 0) || b.found - a.found)
    .map(r => `<li class="${r.error && !r.error.startsWith('rate-limited') ? 'error' : ''}">
      <b>${esc(r.source)}</b> — ${r.error ? esc(r.error) : `${r.found} new · ${r.seconds}s`}</li>`).join('') || '<li class="muted">No scan yet</li>';
}

async function loadJobs() {
  const data = await api('/api/jobs');
  const before = state.knownIds;
  state.jobs = data.jobs;
  if (before) {
    const fresh = data.jobs.filter(j => !before.has(j.id) && j.status === 'new');
    fresh.forEach(j => state.flash.add(j.id));
    notify(fresh);
    setTimeout(() => fresh.forEach(j => state.flash.delete(j.id)), 8000);
  }
  for (const j of data.jobs) {
    const prev = state.prevStatus[j.id];
    if (prev && prev !== j.status) {
      if (j.status === 'submitted') toast(`✅ <b>${esc(j.company)}</b> — submitted`, 'ok');
      if (j.status === 'needs_manual') toast(`🟠 <b>${esc(j.company)}</b> needs you: ${esc(j.status_detail || '')}`, 'warn');
      if (['blocked', 'failed'].includes(j.status)) toast(`🔴 <b>${esc(j.company)}</b>: ${esc(j.status_detail || '')}`, 'err');
    }
    state.prevStatus[j.id] = j.status;
  }
  state.knownIds = new Set(data.jobs.map(j => j.id));
  const sources = [...new Set(data.jobs.map(j => j.source))].sort();
  const current = $('#sourceFilter').value;
  $('#sourceFilter').innerHTML = '<option value="">All sources</option>' + sources.map(s => `<option ${s === current ? 'selected' : ''}>${esc(s)}</option>`).join('');
  renderLegend();
  renderTable();
}

async function loadStatus() {
  const s = await api('/api/status');
  const btn = $('#refreshBtn');
  btn.querySelector('.spin').hidden = !s.refreshing;
  btn.querySelector('.label').textContent = s.refreshing ? 'Scanning…' : '↻ Refresh now';
  btn.disabled = s.refreshing;
  const next = s.settings.poll_enabled ? ` · next auto-scan in ${s.next_poll_in}s` : ' · auto-scan off';
  $('#scanState').textContent = s.refreshing ? 'Scanning all career sites…'
    : `Last scan ${s.last_finished ? ago(s.last_finished) : 'never'}${s.last_finished ? ` (${s.last_new.length} new)` : ''}${next}`
      + (s.error ? ` · error: ${s.error}` : '');
  const cur = s.current && state.jobs.find(j => j.id === s.current);
  $('#queueState').textContent = cur ? `Working on: ${cur.company} — ${cur.title} · ${s.queue} more queued` : (s.queue ? `${s.queue} queued` : 'Worker idle');
  if (!state.settingsLoaded) {
    $('#pollToggle').checked = s.settings.poll_enabled;
    $('#pollInterval').value = String(s.settings.poll_seconds);
    $('#autoSubmit').checked = s.settings.auto_submit;
    state.settingsLoaded = true;
  }
  renderSources(s);
  if (s.last_finished !== state.lastFinished) {
    state.lastFinished = s.last_finished;
    await loadJobs();
  }
}

async function loadActivity() {
  const { events } = await api('/api/events?limit=60');
  $('#activity').innerHTML = events.map(e => `<li class="${e.level === 'error' ? 'error' : ''}">
    <time>${ago(e.ts)}</time><b>${esc(e.company || '')}</b> ${esc(e.message)}</li>`).join('');
}

async function apply(ids) {
  const jobs = ids.map(id => state.jobs.find(j => j.id === id)).filter(Boolean);
  $('#confirmList').innerHTML = jobs.map(j => `<li>${esc(j.company)} — ${esc(j.title)}</li>`).join('');
  $('#confirmOk').textContent = $('#autoSubmit').checked ? `Yes, apply to ${jobs.length}` : `Pre-fill ${jobs.length} (I'll submit)`;
  const dialog = $('#confirmDialog');
  dialog.showModal();
  const result = await new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue), { once: true }));
  if (result !== 'ok') return;
  const { queued } = await api('/api/apply', { ids });
  queued.forEach(id => state.selected.delete(id));
  toast(`Queued ${queued.length} application${queued.length === 1 ? '' : 's'}`, 'ok');
  await loadJobs();
}

async function rowAction(id, act) {
  const job = state.jobs.find(j => j.id === id);
  if (act === 'apply') return apply([id]);
  if (act === 'details') return showDetails(id);
  if (act === 'mark') {
    $('#markText').value = '';
    const d = $('#markDialog'); d.showModal();
    const r = await new Promise(res => d.addEventListener('close', () => res(d.returnValue), { once: true }));
    if (r !== 'ok') return;
    const out = await api(`/api/jobs/${encodeURIComponent(id)}/mark-submitted`, { confirmation: $('#markText').value });
    toast(out.warning ? esc(out.warning) : `Marked ${esc(job.company)} as submitted`, out.warning ? 'warn' : 'ok');
  } else {
    await api(`/api/jobs/${encodeURIComponent(id)}/${act}`, {});
  }
  await loadJobs();
}

async function showDetails(id) {
  const { job, events } = await api(`/api/jobs/${encodeURIComponent(id)}`);
  if (job.status === 'new') api(`/api/jobs/${encodeURIComponent(id)}/seen`, {}).then(loadJobs);
  $('#drawerBody').innerHTML = `
    <h2>${esc(job.title)}</h2>
    <p><b>${esc(job.company)}</b> · ${esc(job.location)} · <span class="pill s-${esc(job.status)}">${esc(STATUS[job.status]?.label || job.status)}</span></p>
    <p class="muted">${esc(job.status_detail || '')}</p>
    <p><a href="${esc(job.url)}" target="_blank" rel="noopener">Job posting ↗</a> ·
       <a href="${esc(job.apply_url || job.url)}" target="_blank" rel="noopener">Application page ↗</a>
       ${job.resume_path ? ` · <a href="/api/resume/${encodeURIComponent(job.id)}" target="_blank">Tailored résumé (PDF)</a>` : ''}</p>
    ${job.confirmation ? `<p><b>Confirmation:</b> ${esc(job.confirmation)}</p>` : ''}
    <h3>Timeline</h3>
    <ul class="activity">${events.map(e => `<li class="${e.level === 'error' ? 'error' : ''}"><time>${ago(e.ts)}</time>${esc(e.message)}</li>`).join('') || '<li class="muted">No events</li>'}</ul>
    <h3>Description</h3>
    <pre>${esc(job.description || 'No description stored (imported from tracker).')}</pre>`;
  $('#drawer').hidden = false;
}

function bind() {
  $('#refreshBtn').onclick = async () => { await api('/api/refresh', {}); loadStatus(); };
  $('#pollToggle').onchange = e => api('/api/settings', { poll_enabled: e.target.checked });
  $('#pollInterval').onchange = e => api('/api/settings', { poll_seconds: Number(e.target.value) });
  $('#autoSubmit').onchange = e => {
    api('/api/settings', { auto_submit: e.target.checked });
    toast(e.target.checked ? 'Auto-submit ON: complete forms are submitted after you click Apply'
      : 'Review mode: forms are pre-filled and left open for you to press Submit', 'warn');
  };
  $('#notifyBtn').onclick = async () => {
    if (!('Notification' in window)) return toast('Notifications not supported', 'err');
    const p = await Notification.requestPermission();
    toast(p === 'granted' ? 'Desktop alerts enabled' : 'Alerts blocked by the browser', p === 'granted' ? 'ok' : 'warn');
  };
  ['#search', '#sourceFilter', '#fitFilter', '#hideDone'].forEach(s => $(s).addEventListener('input', renderTable));
  $('#legend').onclick = e => {
    const chip = e.target.closest('.chip'); if (!chip) return;
    const k = chip.dataset.status;
    state.statusFilter.has(k) ? state.statusFilter.delete(k) : state.statusFilter.add(k);
    renderLegend(); renderTable();
  };
  document.querySelectorAll('th[data-sort]').forEach(th => th.onclick = () => {
    const key = th.dataset.sort;
    state.sort = { key, dir: state.sort.key === key ? -state.sort.dir : 1 };
    renderTable();
  });
  $('#jobs tbody').addEventListener('click', e => {
    const tr = e.target.closest('tr'); if (!tr) return;
    const id = tr.dataset.id;
    if (e.target.classList.contains('sel')) {
      e.target.checked ? state.selected.add(id) : state.selected.delete(id);
      return renderTable();
    }
    const btn = e.target.closest('button[data-act]');
    if (btn) rowAction(id, btn.dataset.act).catch(err => toast(esc(err.message), 'err'));
  });
  $('#selectAll').onchange = e => {
    visibleJobs().forEach(j => { if (j.status !== 'submitted') e.target.checked ? state.selected.add(j.id) : state.selected.delete(j.id); });
    renderTable();
  };
  $('#selectNew').onclick = () => { state.jobs.filter(j => j.status === 'new').forEach(j => state.selected.add(j.id)); renderTable(); };
  $('#applySelected').onclick = () => apply([...state.selected]).catch(err => toast(esc(err.message), 'err'));
  $('#drawerClose').onclick = () => { $('#drawer').hidden = true; };
  $('#drawer').onclick = e => { if (e.target.id === 'drawer') $('#drawer').hidden = true; };
  document.addEventListener('visibilitychange', () => { if (!document.hidden) document.title = 'Job Radar'; });
}

async function tick() {
  try {
    await loadStatus();
    const busy = state.jobs.some(j => ACTIVE.includes(j.status));
    if (busy) await loadJobs();
    await loadActivity();
  } catch (err) {
    $('#scanState').textContent = `Server unreachable: ${err.message}`;
  }
}

bind();
loadJobs().then(tick);
setInterval(tick, 3000);
setInterval(() => loadJobs().catch(() => {}), 15000);
