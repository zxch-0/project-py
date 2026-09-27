/* ============ zach-runner — frontend ============ */
const $ = (id) => document.getElementById(id);
let VIEW = 'overview';
let SCRIPTS = [];
let DETAIL_ID = null;
let DETAIL_DATA = null;
let CONSOLE_SINCE = 0;
let CONSOLE_PAUSE = false;
let CONSOLE_FOLLOW = true;
let DC_LOGS = [];
let LAUNCH = null;
let NEW_TPL = 'py_blank';
let EDITOR_PATH = null;

const SENSITIVE_RE = /(pass\s*word|mot\s*de\s*passe|\bmdp\b|secret|token|api[\s_\-]*key|clé[\s_\-]*|private|pwd\b|code\s*pin)/i;

/* ---------------- icones SVG ---------------- */
const _P = {
  play: '<path d="M7 4l13 8-13 8z" fill="currentColor" stroke="none"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="1" fill="currentColor" stroke="none"/>',
  restart: '<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v6h-6"/>',
  trash: '<path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>',
  terminal: '<path d="M4 17l6-6-6-6"/><path d="M12 19h8"/>',
  check: '<path d="M20 6L9 17l-5-5"/>',
  alert: '<path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  globe: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M17 8l-5-5-5 5"/><path d="M12 3v12"/>',
  folder: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
  lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  edit: '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.1 2.1 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>',
  refresh: '<path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.5 9a9 9 0 0 1 14.9-3.4L23 10"/><path d="M1 14l4.6 4.4A9 9 0 0 0 20.5 15"/>',
  box: '<path d="M21 16V8a2 2 0 0 0-1-1.7l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.7l7 4a2 2 0 0 0 2 0l7-4a2 2 0 0 0 1-1.7z"/><path d="M3.3 7L12 12l8.7-5M12 22V12"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  zap: '<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  message: '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>',
};
function icon(name, size) {
  size = size || 14;
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px">${_P[name] || _P.info}</svg>`;
}
const ACT_ICON = { play: 'play', stop: 'stop', ok: 'check', error: 'alert', upload: 'upload', project: 'folder', webhook: 'globe', refresh: 'refresh', trash: 'trash', edit: 'edit', lock: 'lock', info: 'info', package: 'box' };
const RT_LABEL = { python: 'Python', node: 'Node.js', bash: 'Shell', custom: 'Perso' };

/* ---------------- utils ---------------- */
function toast(msg, ok) {
  ok = ok === undefined ? true : ok;
  const el = $('toast');
  el.textContent = msg;
  el.classList.remove('hidden');
  el.style.borderColor = ok ? '#22c55e' : '#737373';
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add('hidden'), 3800);
}
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function fmtDate(iso) {
  if (!iso) return '–';
  try { return new Date(iso).toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }); }
  catch { return iso; }
}
function fmtTime(iso) {
  if (!iso) return '';
  try { return new Date(iso).toLocaleString('fr-FR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }); }
  catch { return ''; }
}
function fmtDur(s) {
  if (s == null) return '–';
  s = Math.round(s);
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm ' + (s % 60) + 's';
  return Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm';
}
function fmtSize(b) {
  if (b == null) return '';
  if (b < 1024) return b + ' o';
  if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' Ko';
  return (b / 1024 / 1024).toFixed(1) + ' Mo';
}
async function api(url, opts) {
  opts = opts || {};
  const r = await fetch(url, opts);
  const data = await r.json().catch(() => ({}));
  if (!data.ok && !opts.allowFail) throw new Error(data.error || data.message || 'Erreur serveur');
  return data;
}
function statusPill(s, detached) {
  if (s === 'running') return `<span class="pill pill-running"><span class="pill-dot pulse"></span> En cours${detached ? ' (detache)' : ''}</span>`;
  if (s === 'error') return '<span class="pill pill-error">Erreur</span>';
  return '<span class="pill pill-stopped">Arrete</span>';
}
function closeModal(id) { $(id).classList.add('hidden'); }

/* ---------------- navigation ---------------- */
const TITLES = {
  overview: ["Vue d'ensemble", "Tout ce qui se passe sur le serveur, en un coup d'oeil."],
  scripts: ['Scripts', 'Programmes et projets : ils tournent meme si vous fermez cette page.'],
  detail: ['Script', 'Console interactive, configuration et historique.'],
  discord: ['Discord', 'Notifications automatiques sur votre salon.'],
  help: ['Aide', 'Tout comprendre en 2 minutes.'],
};
function showView(name) {
  VIEW = name;
  for (const v of ['overview', 'scripts', 'detail', 'discord', 'help']) {
    $('view-' + v).classList.toggle('hidden', v !== name);
    const nav = $('nav-' + v);
    if (nav) nav.classList.toggle('active', v === name || (name === 'detail' && v === 'scripts'));
  }
  $('sidebar').classList.remove('open');
  const t = TITLES[name];
  $('page-title').textContent = name === 'detail' && DETAIL_DATA ? DETAIL_DATA.name : t[0];
  $('page-sub').textContent = t[1];
  if (name === 'overview') loadOverview();
  if (name === 'scripts') loadScripts();
  if (name === 'discord') { loadDcCfg(); loadDcLogs(); }
  window.scrollTo({ top: 0 });
}

/* ---------------- vue d'ensemble ---------------- */
function chartHTML(perHour) {
  if (!perHour || !perHour.length) return '<div class="empty-note">Aucune donnee</div>';
  const max = Math.max.apply(null, perHour.concat([1]));
  return perHour.map((v, i) => {
    const h = v ? Math.max(8, Math.round((v / max) * 100)) : 4;
    const label = i === 23 ? 'cette heure' : `il y a ${23 - i}h`;
    return `<div class="bar${v ? '' : ' zero'}" style="height:${h}%" title="${v} appel(s) ${label}"></div>`;
  }).join('');
}
async function loadOverview() {
  try {
    const d = await api('/api/overview');
    const s = d.stats;
    const cards = [
      ['zap', '#22c55e', s.running, 'en cours'],
      ['file', '#a3a3a3', s.scripts_total, 'scripts'],
      ['clock', '#737373', s.total_runs, 'executions'],
      ['check', '#22c55e', s.success_rate == null ? '–' : s.success_rate + '%', 'reussite'],
      ['message', '#22c55e', s.discord_24h, 'notifs Discord 24h'],
      ['info', '#a3a3a3', `<span style="font-size:1.2rem">${fmtDur(s.uptime_s)}</span>`, 'serveur en ligne'],
    ];
    $('ov-stats').innerHTML = cards.map(c =>
      `<div class="stat-card"><div class="i" style="color:${c[1]}">${icon(c[0], 20)}</div><div class="v">${c[2]}</div><div class="l">${c[3]}</div></div>`).join('');
    SCRIPTS = d.scripts;
    updateNavCounts(d.scripts, s.discord_total);
    $('ov-running').innerHTML = d.running.length ? d.running.map(r => `
      <div class="ov-run" onclick="openScript('${r.id}')">
        <span class="dot dot-ok pulse"></span>
        <div class="flex-1"><div class="nm">${esc(r.name)}</div>
        <div class="mt">PID ${r.pid ?? '?'} · depuis ${fmtTime(r.started_at)} · ${r.answers_sent} reponse(s)</div></div>
        ${r.waiting ? '<span class="pill pill-wait">Reponse requise</span>' : statusPill(r.status)}
      </div>`).join('')
      : '<div class="empty-note">Aucun script en cours.<br><button class="link" onclick="showView(\'scripts\')">Lancer un script</button></div>';
    $('ov-activity').innerHTML = d.activity.length ? d.activity.map(a => `
      <div class="act"><span class="act-ico">${icon(ACT_ICON[a.icon] || 'info', 14)}</span><span class="flex-1">${esc(a.text)}</span><span class="t">${fmtTime(a.t)}</span></div>`).join('')
      : '<div class="empty-note">Pas encore d\'activite.</div>';
    $('ov-chart').innerHTML = chartHTML(d.discord.per_hour);
    const rt = d.runtimes || {};
    $('ov-runtimes').innerHTML = ['python', 'node', 'bash'].map(k => {
      const r = rt[k] || {};
      return `<div class="rt-row"><span class="dot ${r.ok ? 'dot-ok' : 'dot-err'}"></span><strong>${RT_LABEL[k]}</strong><span class="hint">${esc(r.info || '')}</span></div>`;
    }).join('');
    setHealth(true);
  } catch (e) { console.error(e); setHealth(false); }
}
function updateNavCounts(scripts, dcTotal) {
  const running = scripts.filter(s => s.status === 'running').length;
  $('nav-scripts-count').textContent = scripts.length ? `${running}/${scripts.length}` : '';
  $('nav-dc-count').textContent = dcTotal || '';
}
function setHealth(ok) {
  $('health-dot').className = 'dot ' + (ok ? 'dot-ok pulse' : 'dot-err');
  $('health-txt').textContent = ok ? 'Serveur en ligne' : 'Serveur injoignable';
}

/* ---------------- scripts ---------------- */
async function loadScripts() {
  try {
    const d = await api('/api/status');
    SCRIPTS = d.scripts;
    updateNavCounts(d.scripts, d.discord.stats.total);
    const n = d.counts;
    $('scripts-count').textContent = n.total ? `${n.running} en cours · ${n.total} au total` : '';
    $('scripts-empty').classList.toggle('hidden', n.total > 0);
    $('scripts-grid').innerHTML = d.scripts.map(s => `
      <div class="card script-card ${s.status === 'running' ? 'running' : ''} ${s.has_diagnosis ? 'crashed' : ''}" onclick="openScript('${s.id}')">
        <div class="sc-head"><div class="sc-name">${esc(s.name)}</div>${statusPill(s.status, s.detached)}</div>
        <div class="sc-tags">
          <span class="tag">${s.kind === 'project' ? 'Projet' : 'Fichier'}</span>
          ${s.runtime ? `<span class="tag tag-rt">${esc(RT_LABEL[s.runtime] || s.runtime)}</span>` : ''}
        </div>
        <div class="sc-file">${esc(s.entry || s.filename || '')}${s.args ? ' · ' + esc(s.args) : ''}</div>
        <div class="sc-stats"><span><b>${s.runs_count}</b> runs</span><span><b>${s.success_count}</b> succes</span>${s.has_qa ? '<span>reponses memorisees</span>' : ''}</div>
        ${s.waiting ? '<div class="info-box info-warn" style="margin-bottom:.7rem">Ce script attend votre reponse.</div>' : ''}
        ${s.has_diagnosis && s.status !== 'running' ? '<div class="info-box info-err" style="margin-bottom:.7rem">Dernier run en echec — diagnostic disponible.</div>' : ''}
        <div class="sc-actions" onclick="event.stopPropagation()">
          ${s.status === 'running'
            ? `<button onclick="stopScript('${s.id}')" class="btn-ghost">${icon('stop')} Stop</button>
               <button onclick="restartScript('${s.id}')" class="btn-ghost">${icon('restart')} Restart</button>`
            : `<button onclick="openLaunch('${s.id}')" class="btn-primary">${icon('play')} Lancer</button>`}
          <button onclick="openScript('${s.id}')" class="btn-ghost">${icon('terminal')} Console</button>
          <button onclick="deleteScript('${s.id}')" class="btn-danger" title="Supprimer">${icon('trash')}</button>
        </div>
      </div>`).join('');
    setHealth(true);
  } catch (e) { console.error(e); setHealth(false); }
}
async function stopScript(id) {
  try { const d = await api(`/api/scripts/${id}/stop`, { method: 'POST' }); toast(d.message); }
  catch (e) { toast(e.message, false); }
  refresh();
}
async function restartScript(id, answers) {
  try {
    const d = await api(`/api/scripts/${id}/restart`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ answers }) });
    toast(d.message, d.ok);
  } catch (e) { toast(e.message, false); }
  refresh();
}
async function deleteScript(id) {
  const s = SCRIPTS.find(x => x.id === id);
  if (!confirm(`Supprimer « ${s ? s.name : id} » ? Le script sera arrete et ses fichiers effaces.`)) return;
  try { await api(`/api/scripts/${id}`, { method: 'DELETE' }); toast('Supprime'); }
  catch (e) { toast(e.message, false); }
  if (VIEW === 'detail') showView('scripts'); else refresh();
}

/* ---------------- detail + console ---------------- */
async function openScript(id) {
  DETAIL_ID = id;
  CONSOLE_SINCE = 0; CONSOLE_PAUSE = false;
  EDITOR_PATH = null;
  $('terminal').innerHTML = '<div class="t-empty">Connexion a la console...</div>';
  $('waitbar').classList.add('hidden');
  showView('detail');
  await loadDetailData();
  pollConsole();
}
async function loadDetailData() {
  if (!DETAIL_ID) return;
  try {
    const d = await api(`/api/scripts/${DETAIL_ID}`);
    DETAIL_DATA = d.script;
    const s = d.script;
    $('page-title').textContent = s.name;
    $('d-name').textContent = s.name;
    $('d-status').innerHTML = statusPill(s.status, s.detached);
    $('d-waiting').classList.toggle('hidden', true);
    $('d-meta').textContent = `${s.kind === 'project' ? 'Projet' : 'Fichier'} · ${s.entry || ''} · ${RT_LABEL[s.runtime] || '?'} · ${s.runs_count} runs · ${s.success_count} succes` +
      (s.started_at && s.status === 'running' ? ` · depuis ${fmtDate(s.started_at)}` : '') +
      (s.pid ? ` · PID ${s.pid}` : '') + (s.detached ? ' · DETACHE (serveur redemarre)' : '');
    $('d-actions').innerHTML = s.status === 'running'
      ? `<button onclick="stopScript('${s.id}')" class="btn-ghost text-sm">${icon('stop')} Stop</button>
         <button onclick="restartScript('${s.id}')" class="btn-ghost text-sm">${icon('restart')} Restart</button>`
      : `<button onclick="openLaunch('${s.id}')" class="btn-primary text-sm">${icon('play')} Lancer</button>
         <button onclick="quickRun('${s.id}')" class="btn-ghost text-sm">Lancement direct</button>`;
    $('cfg-name').value = s.name || '';
    $('cfg-args').value = s.args || '';
    $('cfg-env').value = Object.entries(s.env || {}).map(([k, v]) => `${k}=${v}`).join('\n');
    $('cfg-req').value = s.requirements || '';
    $('cfg-runtime').value = s.runtime_mode || 'auto';
    $('cfg-custom').value = s.custom_cmd || '';
    toggleCustomRow();
    $('btn-npm').classList.toggle('hidden', !s.has_package_json && s.runtime !== 'node');
    // entree (projets)
    const er = $('cfg-entry-row');
    if (s.kind === 'project' && s.tree && s.tree.entries) {
      er.classList.remove('hidden');
      $('cfg-entry').innerHTML = s.tree.entries.map(e =>
        `<option value="${esc(e.path)}"${e.path === s.entry ? ' selected' : ''}>${esc(e.path)} (${RT_LABEL[e.runtime] || e.runtime})</option>`).join('');
    } else er.classList.add('hidden');
    renderTree(s);
    if (!EDITOR_PATH) {
      EDITOR_PATH = s.entry;
      if (EDITOR_PATH) loadFile(EDITOR_PATH, true);
    }
    $('term-dl').href = `/api/scripts/${s.id}/logs/download`;
    renderDiag(s);
    renderRuns(s);
  } catch (e) { console.error(e); }
}
function toggleCustomRow() {
  $('cfg-custom-row').classList.toggle('hidden', $('cfg-runtime').value !== 'custom');
}
$('cfg-runtime') && $('cfg-runtime').addEventListener('change', toggleCustomRow);
async function changeEntry() {
  if (!DETAIL_ID) return;
  try {
    const d = await api(`/api/scripts/${DETAIL_ID}/entry`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: $('cfg-entry').value }) });
    toast('Point d\'entree : ' + d.entry);
    EDITOR_PATH = d.entry;
    loadDetailData();
  } catch (e) { toast(e.message, false); loadDetailData(); }
}
function renderDiag(s) {
  const box = $('d-diag');
  const dg = s.diagnosis;
  if (!dg || !dg.found) { box.innerHTML = ''; return; }
  let actionBtn = '';
  if (dg.action && dg.action.type === 'install')
    actionBtn = `<button data-pkg="${esc(dg.action.package)}" data-mgr="${esc(dg.action.manager || 'pip')}" onclick="quickFix(this)" class="btn-primary text-sm mt-2">${esc(dg.action.label)}</button>`;
  else if (dg.action && dg.action.type === 'edit')
    actionBtn = `<button onclick="detailTab('code')" class="btn-primary text-sm mt-2">Ouvrir l'editeur</button>`;
  box.innerHTML = `<div class="diag"><h3>Diagnostic : ${esc(dg.exception || 'erreur')}</h3>
    ${dg.message ? `<p><code>${esc(dg.message)}</code></p>` : ''}
    <p>${esc(dg.hint || '')}</p>
    ${dg.block ? `<pre>${esc(dg.block)}</pre>` : ''}
    <div class="flex gap-2 flex-wrap">${actionBtn}<button onclick="openLaunch('${s.id}')" class="btn-ghost text-sm mt-2">Relancer</button></div></div>`;
}
async function quickFix(btn) {
  const pkg = btn.dataset.pkg, mgr = btn.dataset.mgr || 'pip';
  toast(`Installation de ${pkg} (${mgr})...`);
  try {
    const d = await api(`/api/scripts/${DETAIL_ID}/fix-install`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ package: pkg, manager: mgr }) });
    toast(d.message, d.ok);
    if (d.ok) { pollConsole(true); openLaunch(DETAIL_ID); }
  } catch (e) { toast(e.message, false); }
}
function renderRuns(s) {
  let html = '';
  if (s.qa_history && s.qa_history.length) {
    html += `<div class="label">Dernieres reponses memorisees <span class="hint">(rejouees au redemarrage)</span></div>`;
    html += s.qa_history.slice(-8).map(q =>
      `<div class="qa-item"><div class="q">Q : ${esc(q.prompt || '(sans question)')}</div><div class="a">&gt; ${esc(q.answer)}${q.auto ? ' (auto)' : ''}</div></div>`).join('');
  }
  html += `<div class="label mt-3">Historique d'execution</div>`;
  html += (s.runs && s.runs.length) ? s.runs.map(r => {
    const label = r.reason === 'success' ? 'Succes' : r.reason === 'crash' ? `Echec (code ${r.exit_code})` : 'Arrete';
    const cls = r.reason === 'success' ? 'run-ok' : r.reason === 'crash' ? 'run-err' : 'run-stop';
    return `<div class="run-item ${cls}"><div class="flex-1"><div>${label} · ${fmtDur(r.duration_s)}${r.answers ? ` · ${r.answers} reponse(s)` : ''}</div><div class="t">${fmtDate(r.started_at)}</div></div></div>`;
  }).join('') : '<div class="empty-note">Aucune execution pour le moment.</div>';
  $('d-runs').innerHTML = html;
  $('d-qa').innerHTML = '';
}
function detailTab(t) {
  for (const x of ['config', 'code', 'runs']) {
    $('dtab-' + x).classList.toggle('hidden', x !== t);
    $('mtab-' + x).classList.toggle('active', x === t);
  }
}
async function quickRun(id) {
  try {
    const d = await api(`/api/scripts/${id}/run`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reuse_qa: true }) });
    toast(d.message, d.ok);
  } catch (e) { toast(e.message, false); }
  loadDetailData();
}
async function saveConfig() {
  if (!DETAIL_ID) return;
  const env = {};
  for (const line of $('cfg-env').value.split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#') || !t.includes('=')) continue;
    const i = t.indexOf('=');
    env[t.slice(0, i).trim()] = t.slice(i + 1).trim();
  }
  try {
    const d = await api(`/api/scripts/${DETAIL_ID}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: $('cfg-name').value, args: $('cfg-args').value, env, requirements: $('cfg-req').value, runtime: $('cfg-runtime').value, custom_cmd: $('cfg-custom').value })
    });
    toast('Enregistre (redemarrez le script pour appliquer)');
    if (d.analysis) showMiniAnalysis(d.analysis);
    loadDetailData();
  } catch (e) { toast(e.message, false); }
}
function showMiniAnalysis(a) {
  $('cfg-analysis').innerHTML = a.ok
    ? `${esc(a.summary)}${a.inputs.length ? `<br>Questions : ${a.inputs.map(i => esc(i.label || ('Question ' + i.index))).join(' · ')}` : ''}`
    : `${esc(a.summary)}`;
}
async function reanalyze() {
  if (!DETAIL_ID) return;
  try { const d = await api(`/api/scripts/${DETAIL_ID}/analyze`); showMiniAnalysis(d.analysis); toast('Analyse terminee'); }
  catch (e) { toast(e.message, false); }
}
async function installDeps() {
  if (!DETAIL_ID) return;
  toast('Installation pip... (suivre la console)');
  try { const d = await api(`/api/scripts/${DETAIL_ID}/install-deps`, { method: 'POST' }); toast(d.message, d.ok); }
  catch (e) { toast(e.message, false); }
  pollConsole(true);
}
async function npmInstall() {
  if (!DETAIL_ID) return;
  toast('Installation npm... (suivre la console)');
  try { const d = await api(`/api/scripts/${DETAIL_ID}/npm-install`, { method: 'POST' }); toast(d.message, d.ok); }
  catch (e) { toast(e.message, false); }
  pollConsole(true);
}

/* ----- arborescence + editeur ----- */
function renderTree(s) {
  const box = $('file-tree');
  if (s.kind !== 'project' || !s.tree) { box.innerHTML = ''; return; }
  const files = s.tree.files || [];
  box.innerHTML = `<div class="tree-head">${files.length} fichier(s)${s.tree.truncated ? ' (tronque)' : ''}</div>` +
    files.slice(0, 400).map(f => {
      const pad = Math.min(f.depth, 6) * 14;
      const isEntry = f.path === s.entry;
      const runnable = f.runtime ? ' runnable' : '';
      return `<div class="tree-row${runnable}${f.path === EDITOR_PATH ? ' active' : ''}" style="padding-left:${8 + pad}px" data-path="${esc(f.path)}" onclick="treeClick(this)" title="${esc(f.path)} (${fmtSize(f.size)})">
        <span class="tree-ico">${icon(f.runtime ? 'file' : 'info', 12)}</span>
        <span class="tree-name">${esc(f.path.split('/').pop())}</span>
        ${isEntry ? '<span class="tree-entry">entree</span>' : ''}</div>`;
    }).join('');
}
function treeClick(el) { loadFile(el.dataset.path); }
async function loadFile(path, silent) {
  if (!DETAIL_ID) return;
  EDITOR_PATH = path;
  document.querySelectorAll('.tree-row').forEach(r => r.classList.toggle('active', r.dataset.path === path));
  $('editor-path').textContent = path;
  $('editor-msg').textContent = '';
  try {
    const d = await api(`/api/scripts/${DETAIL_ID}/file?path=${encodeURIComponent(path)}`);
    if (document.activeElement !== $('cfg-code')) $('cfg-code').value = d.content;
  } catch (e) {
    if (!silent) toast(e.message, false);
    $('cfg-code').value = '';
    $('editor-msg').textContent = e.message;
  }
}
async function saveFile() {
  if (!DETAIL_ID || !EDITOR_PATH) return;
  try {
    const d = await api(`/api/scripts/${DETAIL_ID}/file`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: EDITOR_PATH, content: $('cfg-code').value }) });
    if (d.warning) { $('editor-msg').textContent = d.warning; toast(d.warning, true); }
    else { $('editor-msg').textContent = 'Enregistre.'; toast('Fichier enregistre'); }
    if (d.analysis) showMiniAnalysis(d.analysis);
  } catch (e) { toast(e.message, false); }
}

/* ----- console live ----- */
function termLineHTML(l) {
  const cls = l.type === 'in' ? 't-in' : l.type === 'sys' ? 't-sys' : '';
  let text = l.text ?? '';
  if (l.level === 'secret') text = '********';
  return `<div class="tline lvl-${esc(l.level || 'info')} ${cls}" data-lvl="${l.type === 'in' ? 'in' : l.type === 'sys' ? 'sys' : esc(l.level || 'info')}" data-txt="${esc((l.text || '').toLowerCase())}"><span class="ttime">${fmtTime(l.t)}</span><span class="ttext">${esc(text) || ' '}</span></div>`;
}
async function pollConsole(force) {
  if (VIEW !== 'detail' || !DETAIL_ID) return;
  if (CONSOLE_PAUSE && !force) return;
  try {
    const d = await api(`/api/scripts/${DETAIL_ID}/console?since=${CONSOLE_SINCE}`);
    CONSOLE_SINCE = d.next_seq;
    const term = $('terminal');
    if (d.lines.length) {
      const empty = term.querySelector('.t-empty');
      if (empty) empty.remove();
      const nearBottom = term.scrollHeight - term.scrollTop - term.clientHeight < 140;
      term.insertAdjacentHTML('beforeend', d.lines.map(termLineHTML).join(''));
      while (term.children.length > 900) term.firstChild.remove();
      if ((nearBottom && CONSOLE_FOLLOW) || force) term.scrollTop = term.scrollHeight;
      filterConsole();
    } else if (CONSOLE_SINCE === 0 && !term.children.length) {
      term.innerHTML = '<div class="t-empty">Aucune sortie pour le moment.<br>Lancez le script pour voir sa console ici.</div>';
    }
    const wb = $('waitbar');
    if (d.waiting && d.status === 'running') {
      wb.classList.remove('hidden');
      $('wait-text').textContent = d.prompt || 'Le script attend une reponse...';
      $('wait-input').type = SENSITIVE_RE.test(d.prompt || '') ? 'password' : 'text';
      $('wait-chips').innerHTML = (d.options || []).map(o =>
        `<button class="chip" data-v="${esc(o)}" onclick="chipSend(this)">${esc(o)}</button>`).join('');
      $('d-waiting').classList.remove('hidden');
    } else {
      wb.classList.add('hidden');
      $('d-waiting').classList.add('hidden');
    }
    if (DETAIL_DATA && DETAIL_DATA.status !== d.status) {
      loadDetailData();
      if (d.status !== 'running') setTimeout(() => { if (VIEW === 'detail') loadDetailData(); }, 2000);
    }
    else if (DETAIL_DATA && d.diagnosis && !DETAIL_DATA.diagnosis) loadDetailData();
  } catch (e) { console.error(e); }
}
function filterConsole() {
  const q = ($('term-search').value || '').toLowerCase();
  const lvl = $('term-level').value;
  for (const el of $('terminal').children) {
    if (!el.dataset || el.dataset.txt === undefined) continue;
    const okQ = !q || (el.dataset.txt || '').includes(q);
    const okL = !lvl || el.dataset.lvl === lvl;
    el.style.display = okQ && okL ? '' : 'none';
    el.classList.toggle('mark', !!q && okQ && (el.dataset.txt || '').includes(q));
  }
}
function toggleFollow() {
  CONSOLE_FOLLOW = !CONSOLE_FOLLOW;
  $('term-follow').classList.toggle('on', CONSOLE_FOLLOW);
  if (CONSOLE_FOLLOW) { const t = $('terminal'); t.scrollTop = t.scrollHeight; }
}
function togglePause() {
  CONSOLE_PAUSE = !CONSOLE_PAUSE;
  $('term-pause').textContent = CONSOLE_PAUSE ? 'Reprendre' : 'Pause';
  $('term-pause').classList.toggle('on', CONSOLE_PAUSE);
}
function clearConsoleView() { $('terminal').innerHTML = ''; }
async function sendWaitInput() {
  const inp = $('wait-input');
  const text = inp.value;
  inp.value = '';
  await sendInput(text);
  setTimeout(() => pollConsole(true), 400);
}
function chipSend(btn) { sendInput(btn.dataset.v).then(() => setTimeout(() => pollConsole(true), 400)); }
async function sendInput(text) {
  if (!DETAIL_ID) return;
  try { await api(`/api/scripts/${DETAIL_ID}/input`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) }); }
  catch (e) { toast(e.message, false); }
}

/* ---------------- lancement intelligent ---------------- */
async function openLaunch(id) {
  LAUNCH = { id, detail: null, analysis: null, qa: [] };
  $('launch-body').innerHTML = '<div class="empty-note">Analyse en cours...</div>';
  $('launch-sub').textContent = '';
  $('modal-launch').classList.remove('hidden');
  try {
    const [a, d] = await Promise.all([
      api(`/api/scripts/${id}/analyze`),
      api(`/api/scripts/${id}`),
    ]);
    LAUNCH.detail = d.script;
    LAUNCH.analysis = a.analysis;
    LAUNCH.qa = d.script.qa_history || [];
    LAUNCH.entry = d.script.entry;
    LAUNCH.runtime = d.script.runtime_mode || 'auto';
    LAUNCH.custom = d.script.custom_cmd || '';
    renderLaunch();
  } catch (e) {
    $('launch-body').innerHTML = `<div class="info-box info-err">${esc(e.message)}</div>`;
  }
}
async function relaunchAnalyze() {
  try {
    const a = await api(`/api/scripts/${LAUNCH.id}/analyze`);
    LAUNCH.analysis = a.analysis;
    const d = await api(`/api/scripts/${LAUNCH.id}`);
    LAUNCH.detail = d.script;
    LAUNCH.qa = d.script.qa_history || [];
    renderLaunch(true);
  } catch (e) { toast(e.message, false); }
}
function renderLaunch(keepAnswers) {
  const s = LAUNCH.detail, a = LAUNCH.analysis;
  const prev = {};
  if (keepAnswers) document.querySelectorAll('[id^="q-"]').forEach(el => prev[el.id] = el.value);
  $('launch-sub').innerHTML = `<strong>${esc(s.name)}</strong> · ${esc(a.summary || '')}`;
  let html = '';
  if (!a.ok) {
    html += `<div class="info-box info-err"><strong>Erreur de syntaxe ligne ${a.syntax_error.line}</strong> : ${esc(a.syntax_error.msg)}<br><code>${esc(a.syntax_error.text)}</code><br>Corrigez-la dans l'onglet Fichiers avant de lancer.</div>`;
    html += `<button onclick="closeModal('modal-launch');openScript('${LAUNCH.id}');detailTab('code')" class="btn-primary w-full">Ouvrir l'editeur</button>`;
    $('launch-body').innerHTML = html;
    return;
  }
  // Etape 1 : entree + runtime (projets)
  html += `<div class="step"><div class="step-title">1. Point d'entree et runtime</div><div class="grid md:grid-cols-2 gap-3">`;
  if (s.kind === 'project' && s.tree && s.tree.entries) {
    html += `<div><label class="label">Fichier a executer</label><select id="l-entry" class="input" onchange="launchEntry()">` +
      s.tree.entries.map(e => `<option value="${esc(e.path)}"${e.path === LAUNCH.entry ? ' selected' : ''}>${esc(e.path)} (${RT_LABEL[e.runtime] || e.runtime})</option>`).join('') + `</select></div>`;
  } else {
    html += `<div><label class="label">Fichier</label><div class="input input-static font-mono">${esc(s.entry || '')}</div></div>`;
  }
  html += `<div><label class="label">Runtime</label><select id="l-runtime" class="input" onchange="launchRuntime()">
    <option value="auto"${LAUNCH.runtime === 'auto' ? ' selected' : ''}>Auto — ${esc(RT_LABEL[s.runtime] || '?')} detecte</option>
    <option value="python"${LAUNCH.runtime === 'python' ? ' selected' : ''}>Python</option>
    <option value="node"${LAUNCH.runtime === 'node' ? ' selected' : ''}>Node.js</option>
    <option value="bash"${LAUNCH.runtime === 'bash' ? ' selected' : ''}>Shell</option>
    <option value="custom"${LAUNCH.runtime === 'custom' ? ' selected' : ''}>Personnalise</option></select></div></div>
    <div id="l-custom-row" class="${LAUNCH.runtime === 'custom' ? '' : 'hidden'} mt-3"><label class="label">Commande ({file} = script)</label>
    <input id="l-custom" class="input font-mono" value="${esc(LAUNCH.custom)}" placeholder="php -f"></div></div>`;
  // Etape 2 : dependances
  const hasReq = s.has_requirements, hasPkg = s.has_package_json;
  if ((a.suggested_requirements && a.suggested_requirements.length) || hasReq || hasPkg) {
    html += `<div class="step"><div class="step-title">2. Dependances</div>`;
    if (a.suggested_requirements && a.suggested_requirements.length) {
      const mgr = a.eco === 'npm' ? 'npm' : 'pip';
      html += `<div class="info-box info-info">Detectees : <strong>${a.suggested_requirements.map(esc).join(', ')}</strong> (${mgr})<br>
        ${mgr === 'pip' ? '<button class="link" onclick="addReqs()">Ajouter au requirements.txt</button>' : `<button class="link" onclick="launchNpmInstall()">Installer via npm</button>`}</div>`;
    }
    html += `<div class="flex gap-2 flex-wrap">`;
    if (hasReq) html += `<button onclick="launchPipInstall()" class="btn-ghost text-sm">Installer requirements.txt (pip)</button>`;
    if (hasPkg) html += `<button onclick="launchNpmInstall()" class="btn-ghost text-sm">Installer package.json (npm)</button>`;
    html += `</div></div>`;
  }
  for (const r of (a.risks || []))
    html += `<div class="info-box ${r.level === 'warn' ? 'info-warn' : 'info-info'}">${esc(r.text)}</div>`;
  // Etape 3 : questions
  html += `<div class="step"><div class="step-title">3. Reponses</div>`;
  if (a.inputs && a.inputs.length) {
    html += `<div class="label mb-2">Ce script va poser <strong>${a.inputs.length} question(s)</strong> — remplissez-les ici, elles seront envoyees automatiquement :</div>`;
    if (LAUNCH.qa.length)
      html += `<button class="btn-ghost text-sm mb-3" onclick="fillPrevious()">Reprendre mes dernieres reponses (${LAUNCH.qa.length})</button>`;
    html += a.inputs.map((q, i) => `
      <div class="q-card">
        <div class="q-head"><span class="q-num">${q.index}</span><span class="q-label">${esc(q.label || q.prompt || ('Question ' + q.index))}</span></div>
        ${q.context && q.context.length && !(q.prompt && q.context.includes(q.prompt)) ? `<div class="q-menu">${esc(q.context.join('\n'))}</div>` : ''}
        ${q.options && q.options.length ? `<div class="chips mb-2">${q.options.map(o => `<button class="chip" data-i="${i}" data-v="${esc(o)}" onclick="fillQ(this)">${esc(o)}</button>`).join('')}</div>` : ''}
        <input id="q-${i}" class="input font-mono" ${q.sensitive ? 'type="password"' : ''} placeholder="${q.sensitive ? 'Reponse masquee' : 'Votre reponse...'}" autocomplete="off" value="${esc(prev['q-' + i] || '')}">
      </div>`).join('');
  } else {
    html += `<div class="info-box info-ok">Aucune question detectee — demarrage direct. En cas de question imprevue, repondez dans la console.</div>`;
  }
  html += `</div>`;
  html += `<div class="flex gap-2 mt-2 flex-col sm:flex-row">
    <button onclick="doLaunch(true)" class="btn-primary flex-1">Demarrer${a.inputs.length ? ' avec ces reponses' : ''}</button>
    ${a.inputs.length ? `<button onclick="doLaunch(false)" class="btn-ghost">Repondre en direct dans la console</button>` : ''}
  </div>`;
  $('launch-body').innerHTML = html;
}
async function launchEntry() {
  LAUNCH.entry = $('l-entry').value;
  try {
    await api(`/api/scripts/${LAUNCH.id}/entry`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: LAUNCH.entry }) });
    relaunchAnalyze();
  } catch (e) { toast(e.message, false); }
}
function launchRuntime() {
  LAUNCH.runtime = $('l-runtime').value;
  $('l-custom-row').classList.toggle('hidden', LAUNCH.runtime !== 'custom');
}
async function launchPipInstall() {
  toast('Installation pip...');
  try { const d = await api(`/api/scripts/${LAUNCH.id}/install-deps`, { method: 'POST' }); toast(d.message, d.ok); }
  catch (e) { toast(e.message, false); }
}
async function launchNpmInstall() {
  toast('Installation npm...');
  try { const d = await api(`/api/scripts/${LAUNCH.id}/npm-install`, { method: 'POST' }); toast(d.message, d.ok); }
  catch (e) { toast(e.message, false); }
}
function fillQ(btn) { $('q-' + btn.dataset.i).value = btn.dataset.v; }
function fillPrevious() {
  const n = LAUNCH.analysis.inputs.length;
  LAUNCH.qa.slice(0, n).forEach((q, i) => { const el = $('q-' + i); if (el) el.value = q.answer || ''; });
  toast('Reponses precedentes restaurees');
}
async function addReqs() {
  const pkgs = LAUNCH.analysis.suggested_requirements;
  try {
    const d = await api(`/api/scripts/${LAUNCH.id}`);
    let req = d.script.requirements || '';
    for (const p of pkgs) {
      if (!req.toLowerCase().includes(p.toLowerCase())) req += (req && !req.endsWith('\n') ? '\n' : '') + p + '\n';
    }
    await api(`/api/scripts/${LAUNCH.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ requirements: req }) });
    toast('requirements.txt mis a jour — pensez a installer');
    relaunchAnalyze();
  } catch (e) { toast(e.message, false); }
}
async function doLaunch(withAnswers) {
  const a = LAUNCH.analysis;
  let answers = [];
  if (withAnswers && a.inputs.length) {
    answers = a.inputs.map((_, i) => ($('q-' + i) || {}).value || '');
    if (answers.every(x => !x)) {
      if (!confirm('Aucune reponse remplie. Demarrer quand meme (reponses en direct) ?')) return;
      answers = [];
    }
  }
  const payload = { answers, runtime: LAUNCH.runtime };
  if (LAUNCH.runtime === 'custom') payload.custom_cmd = ($('l-custom') || {}).value || LAUNCH.custom;
  closeModal('modal-launch');
  toast('Demarrage...');
  try {
    const d = await api(`/api/scripts/${LAUNCH.id}/run`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    toast(d.message, d.ok);
  } catch (e) { toast(e.message, false); }
  openScript(LAUNCH.id);
}

/* ---------------- nouveau script ---------------- */
function openNewScript() {
  $('modal-new').classList.remove('hidden');
  loadArchiveSupport();
}
function newTab(t) {
  for (const x of ['upload', 'archive', 'create']) {
    $('npage-' + x).classList.toggle('hidden', x !== t);
    $('ntab-' + x).classList.toggle('active', x === t);
  }
}
async function loadArchiveSupport() {
  try {
    const d = await api('/api/runtimes');
    const ar = d.archives || {};
    const parts = ['.zip OK', '.tar.gz OK'];
    parts.push(ar['rar'] ? '.rar OK' : '.rar indisponible sur ce serveur');
    parts.push(ar['7z'] ? '.7z OK' : '.7z indisponible sur ce serveur');
    $('arch-support').textContent = 'Formats : ' + parts.join(' · ');
  } catch (e) { /* silencieux */ }
}
function bindDropzone(dzId, fiId, dzFileId, acceptFn, nameInputId) {
  const dz = $(dzId), fi = $(fiId);
  dz.onclick = () => fi.click();
  fi.onchange = () => { if (fi.files[0]) { acceptFn(fi.files[0], fi); } };
  ['dragover', 'dragenter'].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove('over'); }));
  dz.addEventListener('drop', e => {
    const f = e.dataTransfer.files[0];
    if (f) acceptFn(f, fi);
  });
}
function showDzFile(boxId, f, nameInputId, stripExt) {
  $(boxId).classList.remove('hidden');
  $(boxId).textContent = `${f.name} (${(f.size / 1024).toFixed(1)} Ko)`;
  const ni = nameInputId && $(nameInputId);
  if (ni && !ni.value) {
    let base = f.name.replace(/\.(tar\.gz|tgz|zip|rar|7z|tar|py|js|mjs|cjs|sh|bash)$/i, '');
    ni.value = base.replace(/[_-]+/g, ' ');
  }
}
function bindAll() {
  const codeExts = ['.py', '.js', '.mjs', '.cjs', '.sh', '.bash'];
  const archExts = ['.zip', '.tar', '.tgz', '.gz', '.rar', '.7z'];
  bindDropzone('dropzone', 'file-input', 'dz-file', (f, fi) => {
    const ok = codeExts.some(e => f.name.toLowerCase().endsWith(e));
    if (!ok) { toast('Fichier refuse — utilisez .py, .js, .sh ou une archive', false); return; }
    const dt = new DataTransfer(); dt.items.add(f); fi.files = dt.files;
    showDzFile('dz-file', f, 'up-name');
  });
  bindDropzone('dropzone-arch', 'arch-input', 'dz-arch-file', (f, fi) => {
    const ok = archExts.some(e => f.name.toLowerCase().endsWith(e));
    if (!ok) { toast('Archive refusee — .zip, .tar.gz, .rar, .7z uniquement', false); return; }
    const dt = new DataTransfer(); dt.items.add(f); fi.files = dt.files;
    showDzFile('dz-arch-file', f, 'arch-name');
  });
  window.addEventListener('dragover', e => e.preventDefault());
  window.addEventListener('drop', e => e.preventDefault());
  let depth = 0;
  window.addEventListener('dragenter', e => { if (e.dataTransfer.types.includes('Files')) { depth++; $('drop-overlay').classList.remove('hidden'); } });
  window.addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (!depth) $('drop-overlay').classList.add('hidden'); });
  window.addEventListener('drop', e => {
    depth = 0; $('drop-overlay').classList.add('hidden');
    const f = e.dataTransfer.files[0];
    if (!f) return;
    const low = f.name.toLowerCase();
    openNewScript();
    if (archExts.some(ex => low.endsWith(ex))) {
      newTab('archive');
      const dt = new DataTransfer(); dt.items.add(f); $('arch-input').files = dt.files;
      showDzFile('dz-arch-file', f, 'arch-name');
    } else if (codeExts.some(ex => low.endsWith(ex))) {
      newTab('upload');
      const dt = new DataTransfer(); dt.items.add(f); $('file-input').files = dt.files;
      showDzFile('dz-file', f, 'up-name');
    } else toast('Format non pris en charge', false);
  });
}
async function doUpload() {
  const fi = $('file-input');
  if (!fi.files.length) { $('up-msg').innerHTML = '<span class="text-red-300">Choisissez un fichier d\'abord.</span>'; return; }
  const btn = $('up-btn');
  btn.disabled = true; btn.textContent = 'Upload et analyse...';
  try {
    const fd = new FormData();
    fd.append('file', fi.files[0]);
    fd.append('name', $('up-name').value);
    fd.append('args', $('up-args').value);
    fd.append('env_text', $('up-env').value);
    fd.append('requirements_text', $('up-req').value);
    if ($('up-install').checked) fd.append('install_deps', 'on');
    const r = await fetch('/api/scripts/upload', { method: 'POST', body: fd });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'Echec');
    closeModal('modal-new');
    fi.value = ''; $('dz-file').classList.add('hidden');
    openLaunch(d.id);
    refresh();
  } catch (e) { $('up-msg').innerHTML = `<span class="text-red-300">${esc(e.message)}</span>`; }
  finally { btn.disabled = false; btn.textContent = 'Uploader et analyser'; }
}
async function doUploadArchive() {
  const fi = $('arch-input');
  if (!fi.files.length) { $('arch-msg').innerHTML = '<span class="text-red-300">Choisissez une archive d\'abord.</span>'; return; }
  const btn = $('arch-btn');
  btn.disabled = true; btn.textContent = 'Upload et extraction...';
  try {
    const fd = new FormData();
    fd.append('file', fi.files[0]);
    fd.append('name', $('arch-name').value);
    const r = await fetch('/api/scripts/upload-archive', { method: 'POST', body: fd });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'Echec');
    closeModal('modal-new');
    fi.value = ''; $('dz-arch-file').classList.add('hidden');
    toast(d.message || 'Projet importe');
    openLaunch(d.id);
    refresh();
  } catch (e) { $('arch-msg').innerHTML = `<span class="text-red-300">${esc(e.message)}</span>`; }
  finally { btn.disabled = false; btn.textContent = 'Uploader et analyser le projet'; }
}
function bindTpl() {
  document.querySelectorAll('#tpl-grid .tpl').forEach(b => b.onclick = () => {
    document.querySelectorAll('#tpl-grid .tpl').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    NEW_TPL = b.dataset.t;
  });
}
async function doCreate() {
  const name = $('cr-name').value.trim() || 'Nouveau script';
  try {
    const d = await api('/api/scripts/create', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, template: NEW_TPL }) });
    closeModal('modal-new');
    $('cr-name').value = '';
    openLaunch(d.id);
    refresh();
  } catch (e) { toast(e.message, false); }
}

/* ---------------- discord ---------------- */
const DC_EVENT_LABEL = { started: 'Demarrage', success: 'Succes', error: 'Echec', waiting: 'Attente', test: 'Test' };
async function loadDcCfg() {
  try {
    const d = await api('/api/discord/config');
    $('dc-url').value = d.config.webhook_url || '';
    $('dc-enabled').checked = !!d.config.enabled;
    for (const e of ['started', 'success', 'error', 'waiting']) {
      $('dc-' + e).checked = d.config.events ? d.config.events[e] !== false : true;
    }
  } catch (e) { console.error(e); }
}
async function saveDiscordCfg() {
  try {
    const events = {};
    for (const e of ['started', 'success', 'error', 'waiting']) events[e] = $('dc-' + e).checked;
    await api('/api/discord/config', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ webhook_url: $('dc-url').value.trim(), enabled: $('dc-enabled').checked, events })
    });
    toast('Configuration Discord enregistree');
  } catch (e) { toast(e.message, false); }
}
async function testDiscord() {
  toast("Envoi d'un message de test...");
  try {
    await api('/api/discord/test', { method: 'POST', allowFail: true }).then(d => {
      if (!d.ok) throw new Error(d.error || 'Echec');
    });
    toast('Message de test envoye sur Discord');
    loadDcLogs();
  } catch (e) { toast(e.message, false); }
}
async function loadDcLogs() {
  try {
    const d = await api('/api/discord/logs?limit=100');
    DC_LOGS = d.logs;
    $('dc-total').textContent = d.stats.total ?? 0;
    $('dc-24h').textContent = d.stats.last_24h ?? 0;
    $('dc-last').textContent = d.stats.last_send ? fmtDate(d.stats.last_send.time) : 'jamais';
    const badge = $('nav-dc-count');
    if (badge) badge.textContent = d.stats.total || '';
    renderDcLogs();
  } catch (e) { console.error(e); }
}
function renderDcLogs() {
  const list = DC_LOGS || [];
  $('dc-empty').classList.toggle('hidden', list.length > 0);
  $('dc-list').innerHTML = list.map(e => `
    <div class="card dc-card">
      <div class="dc-head">
        <span class="pill ${e.ok ? (e.event === 'error' ? 'pill-error' : 'pill-running') : 'pill-stopped'}">${esc(DC_EVENT_LABEL[e.event] || e.event)}</span>
        <span class="dc-time">${fmtDate(e.time)}</span>
        <span class="text-sm">${esc(e.text || '')}</span>
        ${e.ok ? '' : `<span class="text-xs text-slate-500">${esc(e.error || 'echec')}</span>`}
      </div>
    </div>`).join('');
}
async function clearDcLogs() {
  if (!confirm("Effacer tout l'historique Discord ?")) return;
  try { await api('/api/discord/logs', { method: 'DELETE' }); loadDcLogs(); } catch (e) { toast(e.message, false); }
}

/* ---------------- refresh global ---------------- */
function refresh() {
  if (VIEW === 'overview') loadOverview();
  else if (VIEW === 'scripts') loadScripts();
  else if (VIEW === 'detail') loadDetailData();
  else if (VIEW === 'discord') loadDcLogs();
}

/* ---------------- init ---------------- */
document.addEventListener('DOMContentLoaded', () => {
  bindAll(); bindTpl();
  $('wait-input').addEventListener('keydown', e => { if (e.key === 'Enter') sendWaitInput(); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') ['modal-new', 'modal-launch'].forEach(closeModal);
  });
  showView('overview');
  setInterval(() => {
    if (VIEW === 'overview') loadOverview();
    else if (VIEW === 'scripts') loadScripts();
    else if (VIEW === 'discord') loadDcLogs();
  }, 10000);
  setInterval(() => { if (VIEW === 'detail') pollConsole(); }, 1000);
});
