/* ============ PyRunner v2 — frontend ============ */
const $ = (id) => document.getElementById(id);
let VIEW = 'overview';
let SCRIPTS = [];
let DETAIL_ID = null;
let DETAIL_DATA = null;
let CONSOLE_SINCE = 0;
let CONSOLE_PAUSE = false;
let CONSOLE_FOLLOW = true;
let WH_LOGS = [];
let WH_STATS = null;
let LAUNCH = { id: null, analysis: null, qa: [] };
let NEW_TPL = 'blank';

const SENSITIVE_RE = /(pass\s*word|mot\s*de\s*passe|\bmdp\b|secret|token|api[\s_\-]*key|clé[\s_\-]*|private|pwd\b|code\s*pin)/i;

/* ---------------- utils ---------------- */
function toast(msg, ok = true) {
  const el = $('toast');
  el.textContent = (ok ? '✅ ' : '❌ ') + msg;
  el.classList.remove('hidden');
  el.style.borderColor = ok ? '#34d399' : '#f87171';
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
async function api(url, opts = {}) {
  const r = await fetch(url, opts);
  if (r.status === 401) { location.href = '/login'; throw new Error('Session expirée'); }
  const data = await r.json().catch(() => ({}));
  if (!data.ok && !opts.allowFail) throw new Error(data.error || data.message || 'Erreur serveur');
  return data;
}
function statusPill(s, detached) {
  if (s === 'running') return `<span class="pill pill-running"><span class="pulse">●</span> en cours${detached ? ' (détaché)' : ''}</span>`;
  if (s === 'error') return '<span class="pill pill-error">● erreur</span>';
  return '<span class="pill pill-stopped">● arrêté</span>';
}
function closeModal(id) { $(id).classList.add('hidden'); }

/* ---------------- navigation ---------------- */
const TITLES = {
  overview: ['Vue d\'ensemble', 'Tout ce qui se passe sur ton serveur, en un coup d\'œil.'],
  scripts: ['Scripts', 'Tes programmes Python : ils tournent même si tu fermes cette page.'],
  detail: ['Script', 'Console interactive, configuration et historique.'],
  webhooks: ['Webhooks', 'Chaque appel reçu, journalisé dans les moindres détails.'],
  help: ['Aide', 'Tout comprendre en 2 minutes.'],
};
function showView(name) {
  VIEW = name;
  for (const v of ['overview', 'scripts', 'detail', 'webhooks', 'help']) {
    $('view-' + v).classList.toggle('hidden', v !== name);
    const nav = $('nav-' + v);
    if (nav) nav.classList.toggle('active', v === name || (name === 'detail' && v === 'scripts'));
  }
  $('sidebar').classList.remove('open');
  const [t, s] = TITLES[name];
  $('page-title').textContent = name === 'detail' && DETAIL_DATA ? DETAIL_DATA.name : t;
  $('page-sub').textContent = s;
  if (name === 'overview') loadOverview();
  if (name === 'scripts') loadScripts();
  if (name === 'webhooks') { loadWhCfg(); loadWhLogs(); }
  window.scrollTo({ top: 0 });
}

/* ---------------- vue d'ensemble ---------------- */
function chartHTML(perHour) {
  if (!perHour || !perHour.length) return '<div class="empty-note">Aucune donnée</div>';
  const max = Math.max(...perHour, 1);
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
    $('ov-stats').innerHTML = `
      <div class="stat-card" style="--g:linear-gradient(90deg,#34d399,#22d3ee)"><div class="i">⚡</div><div class="v">${s.running}</div><div class="l">en cours</div></div>
      <div class="stat-card" style="--g:linear-gradient(90deg,#6366f1,#a855f7)"><div class="i">📜</div><div class="v">${s.scripts_total}</div><div class="l">scripts</div></div>
      <div class="stat-card" style="--g:linear-gradient(90deg,#22d3ee,#6366f1)"><div class="i">🏁</div><div class="v">${s.total_runs}</div><div class="l">exécutions</div></div>
      <div class="stat-card" style="--g:linear-gradient(90deg,#a855f7,#ec4899)"><div class="i">🎯</div><div class="v">${s.success_rate == null ? '–' : s.success_rate + '%'}</div><div class="l">réussite</div></div>
      <div class="stat-card" style="--g:linear-gradient(90deg,#fbbf24,#f97316)"><div class="i">🔔</div><div class="v">${s.webhooks_24h}</div><div class="l">webhooks 24h</div></div>
      <div class="stat-card" style="--g:linear-gradient(90deg,#34d399,#a3e635)"><div class="i">💓</div><div class="v" style="font-size:1.2rem;padding:.3rem 0">${fmtDur(s.uptime_s)}</div><div class="l">serveur en ligne</div></div>`;
    SCRIPTS = d.scripts;
    updateNavCounts(d.scripts, s.webhooks_total);
    $('ov-running').innerHTML = d.running.length ? d.running.map(r => `
      <div class="ov-run" onclick="openScript('${r.id}')">
        <span class="dot dot-ok pulse"></span>
        <div class="flex-1"><div class="nm">${esc(r.name)}</div>
        <div class="mt">PID ${r.pid ?? '?'} · depuis ${fmtTime(r.started_at)} · ${r.answers_sent} réponse(s)</div></div>
        ${r.waiting ? '<span class="pill pill-wait">💬 réponds-moi</span>' : statusPill(r.status)}
      </div>`).join('')
      : '<div class="empty-note">Aucun script en cours.<br><button class="link" onclick="showView(\'scripts\')">Lancer un script →</button></div>';
    $('ov-activity').innerHTML = d.activity.length ? d.activity.map(a => `
      <div class="act"><span>${a.icon}</span><span class="flex-1">${esc(a.text)}</span><span class="t">${fmtTime(a.t)}</span></div>`).join('')
      : '<div class="empty-note">Pas encore d\'activité.</div>';
    $('ov-chart').innerHTML = chartHTML(d.webhook.per_hour);
  } catch (e) { console.error(e); setHealth(false); }
}
function updateNavCounts(scripts, whTotal) {
  const running = scripts.filter(s => s.status === 'running').length;
  $('nav-scripts-count').textContent = scripts.length ? `${running}/${scripts.length}` : '';
  $('nav-wh-count').textContent = whTotal || '';
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
    updateNavCounts(d.scripts, d.webhook.stats.total);
    const n = d.counts;
    $('scripts-count').textContent = n.total ? `${n.running} en cours · ${n.total} au total` : '';
    $('scripts-empty').classList.toggle('hidden', n.total > 0);
    $('scripts-grid').innerHTML = d.scripts.map(s => `
      <div class="card script-card ${s.status === 'running' ? 'running' : ''} ${s.has_diagnosis ? 'crashed' : ''}" onclick="openScript('${s.id}')">
        <div class="sc-head"><div class="sc-name">${esc(s.name)}</div>${statusPill(s.status, s.detached)}</div>
        <div class="sc-file">📄 ${esc(s.filename || '')}${s.args ? ' · ' + esc(s.args) : ''}</div>
        <div class="sc-stats"><span>🏁 <b>${s.runs_count}</b> runs</span><span>✅ <b>${s.success_count}</b> succès</span>${s.has_qa ? '<span>💬 réponses mémorisées</span>' : ''}</div>
        ${s.waiting ? '<div class="info-box info-warn" style="margin-bottom:.7rem">💬 Ce script attend ta réponse !</div>' : ''}
        ${s.has_diagnosis && s.status !== 'running' ? '<div class="info-box info-err" style="margin-bottom:.7rem">❌ Dernier run : crash — diagnostic dispo</div>' : ''}
        <div class="sc-actions" onclick="event.stopPropagation()">
          ${s.status === 'running'
            ? `<button onclick="stopScript('${s.id}')" class="btn-ghost">⏹ Stop</button>
               <button onclick="restartScript('${s.id}')" class="btn-ghost">🔁 Restart</button>`
            : `<button onclick="openLaunch('${s.id}')" class="btn-primary">▶ Lancer</button>`}
          <button onclick="openScript('${s.id}')" class="btn-ghost">🖥️ Console</button>
          <button onclick="deleteScript('${s.id}')" class="btn-danger" title="Supprimer">🗑️</button>
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
  if (!confirm(`Supprimer « ${s ? s.name : id} » ? Le script sera arrêté et ses fichiers effacés.`)) return;
  try { await api(`/api/scripts/${id}`, { method: 'DELETE' }); toast('Script supprimé'); }
  catch (e) { toast(e.message, false); }
  if (VIEW === 'detail') showView('scripts'); else refresh();
}

/* ---------------- détail + console ---------------- */
async function openScript(id) {
  DETAIL_ID = id;
  CONSOLE_SINCE = 0; CONSOLE_PAUSE = false;
  $('terminal').innerHTML = '<div class="t-empty">Connexion à la console…</div>';
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
    $('d-waiting').classList.toggle('hidden', !(s.status === 'running'));
    $('d-meta').textContent = `📄 ${s.filename} · 🏁 ${s.runs_count} runs · ✅ ${s.success_count} succès` +
      (s.started_at && s.status === 'running' ? ` · ▶ depuis ${fmtDate(s.started_at)}` : '') +
      (s.pid ? ` · PID ${s.pid}` : '') + (s.detached ? ' · ⚠️ détaché (serveur redémarré)' : '');
    $('d-actions').innerHTML = s.status === 'running'
      ? `<button onclick="stopScript('${s.id}')" class="btn-ghost text-sm">⏹ Stop</button>
         <button onclick="restartScript('${s.id}')" class="btn-ghost text-sm">🔁 Restart</button>`
      : `<button onclick="openLaunch('${s.id}')" class="btn-primary text-sm">▶ Lancer intelligemment 🧠</button>
         <button onclick="quickRun('${s.id}')" class="btn-ghost text-sm">▶ Direct</button>`;
    // config
    $('cfg-name').value = s.name || '';
    $('cfg-args').value = s.args || '';
    $('cfg-env').value = Object.entries(s.env || {}).map(([k, v]) => `${k}=${v}`).join('\n');
    $('cfg-req').value = s.requirements || '';
    if (document.activeElement !== $('cfg-code')) $('cfg-code').value = s.code || '';
    $('term-dl').href = `/api/scripts/${s.id}/logs/download`;
    renderDiag(s);
    renderRuns(s);
  } catch (e) { console.error(e); }
}
function renderDiag(s) {
  const box = $('d-diag');
  const dg = s.diagnosis;
  if (!dg || !dg.found) { box.innerHTML = ''; return; }
  let actionBtn = '';
  if (dg.action && dg.action.type === 'install')
    actionBtn = `<button onclick="quickFix('${s.id}','${esc(dg.action.package)}')" class="btn-primary text-sm mt-2">${esc(dg.action.label)} puis relancer</button>`;
  else if (dg.action && dg.action.type === 'edit')
    actionBtn = `<button onclick="detailTab('code')" class="btn-primary text-sm mt-2">📝 Ouvrir l'éditeur</button>`;
  box.innerHTML = `<div class="diag"><h3>🩺 Diagnostic : ${esc(dg.exception || 'erreur')}</h3>
    ${dg.message ? `<p><code>${esc(dg.message)}</code></p>` : ''}
    <p>${esc(dg.hint || '')}</p>
    ${dg.block ? `<pre>${esc(dg.block)}</pre>` : ''}
    <div class="flex gap-2 flex-wrap">${actionBtn}<button onclick="openLaunch('${s.id}')" class="btn-ghost text-sm mt-2">🔁 Relancer</button></div></div>`;
}
async function quickFix(id, pkg) {
  toast(`Installation de ${pkg}…`);
  try {
    const d = await api(`/api/scripts/${id}/fix-install`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ package: pkg }) });
    toast(d.message, d.ok);
    if (d.ok) { pollConsole(true); openLaunch(id); }
  } catch (e) { toast(e.message, false); }
}
function renderRuns(s) {
  let html = '';
  if (s.qa_history && s.qa_history.length) {
    html += `<div class="label">💬 Dernières réponses mémorisées <span class="hint">(rejouées au redémarrage)</span></div>`;
    html += s.qa_history.slice(-8).map(q =>
      `<div class="qa-item"><div class="q">Q: ${esc(q.prompt || '(sans question)')}</div><div class="a">❯ ${esc(q.answer)}${q.auto ? ' 🤖' : ''}</div></div>`).join('');
  }
  html += `<div class="label mt-3">🏁 Historique d'exécution</div>`;
  html += (s.runs && s.runs.length) ? s.runs.map(r => {
    const ico = r.reason === 'success' ? '✅' : r.reason === 'crash' ? '❌' : '⏹';
    return `<div class="run-item"><span>${ico}</span><div class="flex-1"><div>${r.reason === 'success' ? 'Succès' : r.reason === 'crash' ? `Crash (code ${r.exit_code})` : 'Arrêté'} · ${fmtDur(r.duration_s)}${r.answers ? ` · ${r.answers} réponse(s)` : ''}</div><div class="t">${fmtDate(r.started_at)}</div></div></div>`;
  }).join('') : '<div class="empty-note">Aucune exécution pour le moment.</div>';
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
      body: JSON.stringify({ name: $('cfg-name').value, args: $('cfg-args').value, env, requirements: $('cfg-req').value, code: $('cfg-code').value })
    });
    toast('Enregistré 💾 (redémarre le script pour appliquer)');
    if (d.analysis) showMiniAnalysis(d.analysis);
    loadDetailData();
  } catch (e) { toast(e.message, false); }
}
function showMiniAnalysis(a) {
  $('cfg-analysis').innerHTML = a.ok
    ? `🧠 ${esc(a.summary)}${a.inputs.length ? `<br>💬 Questions : ${a.inputs.map(i => esc(i.label || ('Question ' + i.index))).join(' · ')}` : ''}`
    : `⛔ ${esc(a.summary)}`;
}
async function reanalyze() {
  if (!DETAIL_ID) return;
  try { const d = await api(`/api/scripts/${DETAIL_ID}/analyze`); showMiniAnalysis(d.analysis); toast('Scan terminé 🧠'); }
  catch (e) { toast(e.message, false); }
}
async function installDeps() {
  if (!DETAIL_ID) return;
  toast('Installation… (suivre la console)');
  try { const d = await api(`/api/scripts/${DETAIL_ID}/install-deps`, { method: 'POST' }); toast(d.message, d.ok); }
  catch (e) { toast(e.message, false); }
  pollConsole(true);
}

/* ----- console live ----- */
function termLineHTML(l) {
  const cls = l.type === 'in' ? 't-in' : l.type === 'sys' ? 't-sys' : '';
  let text = l.text ?? '';
  if (l.level === 'secret') text = '••••••••';
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
      term.innerHTML = '<div class="t-empty">Aucune sortie pour le moment.<br>Lance le script pour voir sa console ici. 🚀</div>';
    }
    // barre d'attente
    const wb = $('waitbar');
    if (d.waiting && d.status === 'running') {
      wb.classList.remove('hidden');
      $('wait-text').textContent = d.prompt || 'Le script attend une réponse…';
      $('wait-input').type = SENSITIVE_RE.test(d.prompt || '') ? 'password' : 'text';
      $('wait-chips').innerHTML = (d.options || []).map(o =>
        `<button class="chip" onclick="chipSend('${esc(o).replace(/'/g, "\\'")}')">${esc(o)}</button>`).join('');
      $('d-waiting').classList.remove('hidden');
      if (!wb._ping) { wb._ping = true; setTimeout(() => wb._ping = false, 5000); }
    } else {
      wb.classList.add('hidden');
      $('d-waiting').classList.add('hidden');
    }
    // statut changé ? refresh header
    if (DETAIL_DATA && DETAIL_DATA.status !== d.status) loadDetailData();
    else if (DETAIL_DATA && d.diagnosis && !DETAIL_DATA.diagnosis) loadDetailData();
  } catch (e) { console.error(e); }
}
function filterConsole() {
  const q = ($('term-search').value || '').toLowerCase();
  const lvl = $('term-level').value;
  for (const el of $('terminal').children) {
    if (!el.dataset) continue;
    const okQ = !q || (el.dataset.txt || '').includes(q);
    const okL = !lvl || el.dataset.lvl === lvl || (lvl === 'error' && el.dataset.lvl === 'error');
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
  $('term-pause').textContent = CONSOLE_PAUSE ? '▶' : '⏸';
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
function chipSend(val) { sendInput(val).then(() => setTimeout(() => pollConsole(true), 400)); }
async function sendInput(text) {
  if (!DETAIL_ID) return;
  try { await api(`/api/scripts/${DETAIL_ID}/input`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) }); }
  catch (e) { toast(e.message, false); }
}

/* ---------------- lancement intelligent ---------------- */
async function openLaunch(id) {
  LAUNCH = { id, analysis: null, qa: [] };
  $('launch-body').innerHTML = '<div class="empty-note">🧠 Scan du script en cours…</div>';
  $('modal-launch').classList.remove('hidden');
  try {
    const [a, d] = await Promise.all([
      api(`/api/scripts/${id}/analyze`),
      api(`/api/scripts/${id}`),
    ]);
    LAUNCH.analysis = a.analysis;
    LAUNCH.qa = d.script.qa_history || [];
    renderLaunch(d.script.name);
  } catch (e) {
    $('launch-body').innerHTML = `<div class="info-box info-err">❌ ${esc(e.message)}</div>`;
  }
}
function renderLaunch(name) {
  const a = LAUNCH.analysis;
  $('launch-sub').innerHTML = `Script « <strong>${esc(name)}</strong> » · ${esc(a.summary || '')}`;
  let html = '';
  if (!a.ok) {
    html += `<div class="info-box info-err">⛔ <strong>Erreur de syntaxe ligne ${a.syntax_error.line}</strong> : ${esc(a.syntax_error.msg)}<br><code>${esc(a.syntax_error.text)}</code><br>Corrige-la dans l'onglet 📝 Code avant de lancer.</div>`;
    html += `<button onclick="closeModal('modal-launch');openScript('${LAUNCH.id}');detailTab('code')" class="btn-primary w-full">📝 Ouvrir l'éditeur</button>`;
    $('launch-body').innerHTML = html;
    return;
  }
  // risques
  for (const r of (a.risks || []))
    html += `<div class="info-box ${r.level === 'warn' ? 'info-warn' : 'info-info'}">${esc(r.text)}</div>`;
  // librairies
  if (a.suggested_requirements && a.suggested_requirements.length) {
    html += `<div class="info-box info-info">📦 Librairies détectées : <strong>${a.suggested_requirements.map(esc).join(', ')}</strong><br><button class="link" onclick="addReqs()">＋ Ajouter au requirements.txt</button></div>`;
  }
  // questions
  if (a.inputs && a.inputs.length) {
    html += `<div class="label mb-2">💬 Ce script va te poser <strong>${a.inputs.length} question(s)</strong> — remplis-les ici, elles seront envoyées automatiquement :</div>`;
    if (LAUNCH.qa.length)
      html += `<button class="btn-ghost text-sm mb-3" onclick="fillPrevious()">🕘 Reprendre mes dernières réponses (${LAUNCH.qa.length})</button>`;
    html += a.inputs.map((q, i) => `
      <div class="q-card">
        <div class="q-head"><span class="q-num">${q.index}</span><span class="q-label">${esc(q.label || q.prompt || ('Question ' + q.index))}</span></div>
        ${q.context && q.context.length && !(q.prompt && q.context.includes(q.prompt)) ? `<div class="q-menu">${esc(q.context.join('\n'))}</div>` : ''}
        ${q.options && q.options.length ? `<div class="chips mb-2">${q.options.map(o => `<button class="chip" onclick="fillQ(${i},'${esc(o).replace(/'/g, "\\'")}')">${esc(o)}</button>`).join('')}</div>` : ''}
        <input id="q-${i}" class="input font-mono" ${q.sensitive ? 'type="password"' : ''} placeholder="${q.sensitive ? '🔒 réponse masquée' : 'Ta réponse…'}" autocomplete="off">
      </div>`).join('');
  } else {
    html += `<div class="info-box info-ok">🚀 Aucune question détectée — le script démarrera directement. S'il demande quelque chose en cours de route, tu pourras répondre dans la console.</div>`;
  }
  html += `<div class="flex gap-2 mt-4 flex-col sm:flex-row">
    <button onclick="doLaunch(true)" class="btn-primary flex-1">▶ Démarrer${a.inputs.length ? ' avec ces réponses' : ''}</button>
    ${a.inputs.length ? `<button onclick="doLaunch(false)" class="btn-ghost">Répondre en direct dans la console →</button>` : ''}
  </div>`;
  $('launch-body').innerHTML = html;
}
function fillQ(i, val) { $('q-' + i).value = val; }
function fillPrevious() {
  const n = LAUNCH.analysis.inputs.length;
  LAUNCH.qa.slice(0, n).forEach((q, i) => { const el = $('q-' + i); if (el) el.value = q.answer || ''; });
  toast('Réponses précédentes restaurées 🕘');
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
    toast('requirements.txt mis à jour 📦 — pense à installer');
  } catch (e) { toast(e.message, false); }
}
async function doLaunch(withAnswers) {
  const a = LAUNCH.analysis;
  let answers = [];
  if (withAnswers && a.inputs.length) {
    answers = a.inputs.map((_, i) => ($(('q-' + i)) || {}).value ?? '');
    if (answers.every(x => !x)) {
      if (!confirm('Aucune réponse remplie. Démarrer quand même (tu répondras en direct) ?')) return;
      answers = [];
    }
  }
  closeModal('modal-launch');
  toast('Démarrage…');
  try {
    const d = await api(`/api/scripts/${LAUNCH.id}/run`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ answers }) });
    toast(d.message, d.ok);
  } catch (e) { toast(e.message, false); }
  openScript(LAUNCH.id);
}

/* ---------------- nouveau script ---------------- */
function openNewScript() { $('modal-new').classList.remove('hidden'); }
function newTab(t) {
  $('npage-upload').classList.toggle('hidden', t !== 'upload');
  $('npage-create').classList.toggle('hidden', t !== 'create');
  $('ntab-upload').classList.toggle('active', t === 'upload');
  $('ntab-create').classList.toggle('active', t === 'create');
}
function bindDropzone() {
  const dz = $('dropzone'), fi = $('file-input');
  dz.onclick = () => fi.click();
  fi.onchange = () => showDzFile(fi.files[0]);
  ['dragover', 'dragenter'].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove('over'); }));
  dz.addEventListener('drop', e => {
    const f = e.dataTransfer.files[0];
    if (f && f.name.endsWith('.py')) { const dt = new DataTransfer(); dt.items.add(f); fi.files = dt.files; showDzFile(f); }
    else toast('Seuls les fichiers .py sont acceptés', false);
  });
  // global
  window.addEventListener('dragover', e => e.preventDefault());
  window.addEventListener('drop', e => e.preventDefault());
  let depth = 0;
  window.addEventListener('dragenter', e => { if (e.dataTransfer.types.includes('Files')) { depth++; $('drop-overlay').classList.remove('hidden'); } });
  window.addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (!depth) $('drop-overlay').classList.add('hidden'); });
  window.addEventListener('drop', e => {
    depth = 0; $('drop-overlay').classList.add('hidden');
    const f = e.dataTransfer.files[0];
    if (f && f.name.endsWith('.py')) {
      openNewScript(); newTab('upload');
      const dt = new DataTransfer(); dt.items.add(f); fi.files = dt.files; showDzFile(f);
    }
  });
}
function showDzFile(f) {
  if (!f) return;
  $('dz-file').classList.remove('hidden');
  $('dz-file').textContent = `🐍 ${f.name} (${(f.size / 1024).toFixed(1)} Ko)`;
  if (!$('up-name').value) $('up-name').value = f.name.replace(/\.py$/, '').replace(/[_-]+/g, ' ');
}
async function doUpload() {
  const fi = $('file-input');
  if (!fi.files.length) { $('up-msg').innerHTML = '<span class="text-red-300">❌ Choisis un fichier .py d\'abord.</span>'; return; }
  const btn = $('up-btn');
  btn.disabled = true; btn.textContent = 'Upload & scan…';
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
    if (!d.ok) throw new Error(d.error || 'Échec');
    closeModal('modal-new');
    fi.value = ''; $('dz-file').classList.add('hidden');
    LAUNCH = { id: d.id, analysis: d.analysis, qa: [] };
    $('modal-launch').classList.remove('hidden');
    renderLaunch(d.name);
    refresh();
  } catch (e) { $('up-msg').innerHTML = `<span class="text-red-300">❌ ${esc(e.message)}</span>`; }
  finally { btn.disabled = false; btn.textContent = 'Uploader & analyser 🧠'; }
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
    LAUNCH = { id: d.id, analysis: d.analysis, qa: [] };
    $('modal-launch').classList.remove('hidden');
    renderLaunch(name);
    refresh();
  } catch (e) { toast(e.message, false); }
}

/* ---------------- webhooks ---------------- */
async function loadWhCfg() {
  try {
    const d = await api('/api/webhooks/config');
    $('wh-url').textContent = d.config.url;
    $('wh-autorun').checked = !!d.config.auto_run;
    $('wh-restart').checked = d.config.restart_if_running !== false;
    const sel = $('wh-linked');
    const cur = d.config.linked_script_id || sel.value;
    const st = await api('/api/status');
    SCRIPTS = st.scripts;
    sel.innerHTML = '<option value="">— Aucun —</option>' + st.scripts.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
    if (cur) sel.value = cur;
    const hc = $('help-curl');
    if (hc) hc.textContent = `curl -X POST "${d.config.url}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"signal":"BUY","prix":123.4}'`;
  } catch (e) { console.error(e); }
}
async function loadWhLogs() {
  try {
    const d = await api('/api/webhooks/logs?limit=100');
    WH_LOGS = d.logs; WH_STATS = d.stats;
    $('wh-total').textContent = d.stats.total ?? 0;
    $('wh-24h').textContent = d.stats.last_24h ?? 0;
    $('wh-last').textContent = d.stats.last_call ? fmtDate(d.stats.last_call.time) : 'jamais';
    $('wh-chart').innerHTML = chartHTML(d.stats.per_hour);
    const badge = $('nav-wh-count');
    if (badge) badge.textContent = d.stats.total || '';
    renderWhLogs();
  } catch (e) { console.error(e); }
}
function renderWhLogs() {
  const q = ($('wh-search').value || '').toLowerCase();
  const method = $('wh-method').value;
  const list = WH_LOGS.filter(e => {
    if (method && e.method !== method) return false;
    if (q && !JSON.stringify(e).toLowerCase().includes(q)) return false;
    return true;
  });
  $('wh-empty').classList.toggle('hidden', list.length > 0);
  $('wh-list').innerHTML = list.map(e => {
    const trig = e.triggered_script
      ? (e.triggered_script.error ? `<div class="text-red-300 text-xs mt-1">⚡ ${esc(e.triggered_script.error)}</div>`
        : `<div class="text-amber-300 text-xs mt-1">⚡ « ${esc(e.triggered_script.name)} » → ${esc(e.triggered_script.action)} ${e.triggered_script.ok ? '✔' : '❌'}</div>`)
      : '';
    const query = e.query && Object.keys(e.query).length ? `<div class="text-xs text-slate-400 mt-1">🔗 ${esc(JSON.stringify(e.query))}</div>` : '';
    const body = e.body_is_json ? JSON.stringify(e.body, null, 2) : String(e.body || '');
    return `
    <div class="card wh-card">
      <div class="wh-head">
        <span class="pill ${e.method === 'POST' ? 'pill-running' : 'pill-stopped'}">${esc(e.method)}</span>
        <span class="wh-time">🕒 ${fmtDate(e.time)}</span>
        <span class="text-xs text-slate-400">🌐 ${esc(e.ip)}</span>
        <span class="text-xs text-slate-500">${e.body_size} octets · ${e.duration_ms ?? '?'} ms</span>
        <button class="mini-btn ml-auto" onclick="this.closest('.wh-card').querySelector('.wh-detail').classList.toggle('hidden')">🔍 Détails</button>
      </div>${query}${trig}
      <div class="wh-detail hidden">
        <div class="label mt-2">Contenu ${e.body_is_json ? '(JSON)' : '(texte)'}</div>
        <pre class="wh-pre" style="color:#7dd3fc">${esc(body.slice(0, 4000)) || '(vide)'}</pre>
        <div class="label mt-2">Headers</div>
        <pre class="wh-pre" style="color:#94a3b8;max-height:140px">${esc(JSON.stringify(e.headers || {}, null, 2))}</pre>
        <div class="text-xs text-slate-500 mt-1">UA: ${esc(e.user_agent || '–')} · CT: ${esc(e.content_type || '–')} · #${esc(e.id)}</div>
      </div>
    </div>`;
  }).join('');
}
async function saveWebhookCfg() {
  try {
    await api('/api/webhooks/config', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ linked_script_id: $('wh-linked').value || null, auto_run: $('wh-autorun').checked, restart_if_running: $('wh-restart').checked })
    });
    toast('Config webhook enregistrée');
  } catch (e) { toast(e.message, false); }
}
function copyWebhook() {
  navigator.clipboard.writeText($('wh-url').textContent.trim()).then(() => toast('URL copiée 📋')).catch(() => toast('Copie impossible', false));
}
async function regenWebhook() {
  if (!confirm('Nouvelle URL ? L\'ancienne sera immédiatement invalidée.')) return;
  try { const d = await api('/api/webhooks/regenerate', { method: 'POST' }); $('wh-url').textContent = d.url; toast('Nouvelle URL générée 🔄'); }
  catch (e) { toast(e.message, false); }
}
async function testWebhook() {
  toast('Envoi d\'un appel de test…');
  try {
    const d = await api('/api/webhooks/test', { method: 'POST', allowFail: true });
    if (d.ok) { toast('Appel de test reçu ✔'); loadWhLogs(); } else toast(d.error || 'Échec', false);
  } catch (e) { toast(e.message, false); }
}
async function clearWhLogs() {
  if (!confirm('Effacer tout l\'historique webhook ?')) return;
  try { await api('/api/webhooks/logs', { method: 'DELETE' }); loadWhLogs(); } catch (e) { toast(e.message, false); }
}

/* ---------------- paramètres ---------------- */
function openSettings() { $('modal-settings').classList.remove('hidden'); }
async function changeCode() {
  const msg = $('set-msg');
  try {
    await api('/api/change-code', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ current: $('set-current').value, new: $('set-new').value }) });
    msg.innerHTML = '<span class="text-emerald-300">✅ Code mis à jour</span>';
    setTimeout(() => closeModal('modal-settings'), 1200);
  } catch (e) { msg.innerHTML = `<span class="text-red-300">❌ ${esc(e.message)}</span>`; }
}

/* ---------------- refresh global ---------------- */
function refresh() {
  if (VIEW === 'overview') loadOverview();
  else if (VIEW === 'scripts') loadScripts();
  else if (VIEW === 'detail') loadDetailData();
  else if (VIEW === 'webhooks') loadWhLogs();
}

/* ---------------- init ---------------- */
document.addEventListener('DOMContentLoaded', () => {
  bindDropzone(); bindTpl();
  $('wait-input').addEventListener('keydown', e => { if (e.key === 'Enter') sendWaitInput(); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') ['modal-new', 'modal-launch', 'modal-settings'].forEach(closeModal);
  });
  showView('overview');
  setInterval(() => { // boucle douce selon la vue
    if (VIEW === 'overview') loadOverview();
    else if (VIEW === 'scripts') loadScripts();
    else if (VIEW === 'webhooks') loadWhLogs();
  }, 10000);
  setInterval(() => { if (VIEW === 'detail') pollConsole(); }, 1000);
});
