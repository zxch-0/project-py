/* PyRunner dashboard */
let currentDetail = null;
let detailTimer = null;
let allScripts = [];

// ---------- utils ----------
function toast(msg, ok = true) {
  const el = document.getElementById('toast');
  el.textContent = (ok ? '✅ ' : '❌ ') + msg;
  el.classList.remove('hidden');
  el.style.borderColor = ok ? '#34d399' : '#f87171';
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add('hidden'), 3500);
}
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function fmtDate(iso) {
  if (!iso) return '–';
  try {
    return new Date(iso).toLocaleString('fr-FR', { day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit' });
  } catch { return iso; }
}
async function api(url, opts = {}) {
  const r = await fetch(url, opts);
  if (r.status === 401) { location.href = '/login'; throw new Error('Non authentifié'); }
  const ct = r.headers.get('content-type') || '';
  if (!ct.includes('application/json')) throw new Error('Réponse inattendue du serveur');
  const data = await r.json();
  if (!data.ok && !opts.allowFail) throw new Error(data.error || data.message || 'Erreur');
  return data;
}

// ---------- onglets ----------
function showTab(name) {
  for (const t of ['scripts', 'webhook', 'help']) {
    document.getElementById('tab-' + t).classList.toggle('hidden', t !== name);
    document.getElementById('tab-btn-' + t).classList.toggle('active', t === name);
  }
  if (name === 'webhook') { loadWebhookCfg(); loadWebhookLogs(); }
}

// ---------- scripts ----------
async function refreshStatus() {
  try {
    const data = await api('/api/status');
    allScripts = data.scripts;
    renderScripts(data.scripts, data.counts);
    renderWebhookStats(data.webhook);
    fillLinkedSelect(data.scripts, data.webhook.linked_script_id);
  } catch (e) { console.error(e); }
}

function statusPill(s) {
  if (s === 'running') return '<span class="pill pill-running"><span class="pulse-dot">●</span> en cours</span>';
  if (s === 'error') return '<span class="pill pill-error">● erreur</span>';
  return '<span class="pill pill-stopped">● arrêté</span>';
}

function renderScripts(scripts, counts) {
  const list = document.getElementById('scripts-list');
  document.getElementById('scripts-count').textContent =
    counts.total ? `(${counts.running} en cours / ${counts.total})` : '';
  document.getElementById('scripts-empty').classList.toggle('hidden', scripts.length > 0);
  list.innerHTML = scripts.map(s => `
    <div class="card p-5">
      <div class="flex flex-wrap items-center gap-3">
        <div class="flex-1 min-w-[200px]">
          <div class="font-bold text-lg flex items-center gap-2 flex-wrap">${esc(s.name)} ${statusPill(s.status)}</div>
          <div class="text-xs text-slate-400 font-mono mt-1">
            📄 ${esc(s.filename || '')}
            ${s.args ? ' · ⚙️ ' + esc(s.args) : ''}
            ${s.pid ? ' · PID ' + s.pid : ''}
            ${s.has_requirements ? ' · 📦 requirements' : ''}
          </div>
          <div class="text-xs text-slate-500 mt-1">
            ${s.started_at ? '▶ Démarré : ' + fmtDate(s.started_at) : ''}
            ${s.stopped_at ? '⏹ Arrêté : ' + fmtDate(s.stopped_at) : ''}
            ${s.exit_code != null ? ' · code ' + s.exit_code : ''}
          </div>
        </div>
        <div class="flex flex-wrap gap-2">
          ${s.status === 'running'
            ? `<button onclick="stopScript('${s.id}')" class="btn-ghost text-sm">⏹ Stop</button>
               <button onclick="restartScript('${s.id}')" class="btn-ghost text-sm">🔁 Restart</button>`
            : `<button onclick="runScript('${s.id}')" class="btn-primary text-sm !py-2">▶ Démarrer</button>`}
          <button onclick="openDetail('${s.id}')" class="btn-ghost text-sm">📟 Logs / Config</button>
          <button onclick="deleteScript('${s.id}', '${esc(s.name)}')" class="btn-ghost text-sm" title="Supprimer">🗑️</button>
        </div>
      </div>
    </div>`).join('');
}

async function runScript(id) { try { const d = await api(`/api/scripts/${id}/run`, { method: 'POST' }); toast(d.message, d.ok); } catch (e) { toast(e.message, false); } refreshStatus(); }
async function stopScript(id) { try { const d = await api(`/api/scripts/${id}/stop`, { method: 'POST' }); toast(d.message); } catch (e) { toast(e.message, false); } refreshStatus(); }
async function restartScript(id) { try { const d = await api(`/api/scripts/${id}/restart`, { method: 'POST' }); toast(d.message, d.ok); } catch (e) { toast(e.message, false); } refreshStatus(); }
async function deleteScript(id, name) {
  if (!confirm(`Supprimer « ${name} » ? Le script sera arrêté et ses fichiers effacés.`)) return;
  try { await api(`/api/scripts/${id}`, { method: 'DELETE' }); toast('Script supprimé'); } catch (e) { toast(e.message, false); }
  refreshStatus();
}
async function installDeps(id) {
  toast('Installation des dépendances… (voir logs)');
  try { const d = await api(`/api/scripts/${id}/install-deps`, { method: 'POST' }); toast(d.message, d.ok); } catch (e) { toast(e.message, false); }
  if (currentDetail) loadDetailLogs(id);
}

// ---------- upload ----------
document.getElementById('upload-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('upload-btn');
  const msg = document.getElementById('upload-msg');
  const form = e.target;
  if (!document.getElementById('file-input').files.length) { msg.innerHTML = '<span class="text-red-300">❌ Choisis un fichier .py d\'abord.</span>'; return; }
  btn.disabled = true; btn.textContent = 'Upload en cours…';
  msg.innerHTML = '<span class="text-slate-400">⏳ Envoi…</span>';
  try {
    const r = await fetch('/api/scripts/upload', { method: 'POST', body: new FormData(form) });
    const data = await r.json();
    if (!data.ok) throw new Error(data.error || 'Échec');
    msg.innerHTML = data.messages.map(m => `<div class="text-emerald-300">${esc(m)}</div>`).join('');
    form.reset();
    toast('Script importé');
    refreshStatus();
  } catch (err) {
    msg.innerHTML = `<span class="text-red-300">❌ ${esc(err.message)}</span>`;
  } finally { btn.disabled = false; btn.textContent = 'Uploader & exécuter 🚀'; }
});
// Drag & drop global
['dragover', 'drop'].forEach(ev => window.addEventListener(ev, e => e.preventDefault()));
window.addEventListener('drop', (e) => {
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (!f) return;
  if (!f.name.endsWith('.py')) { toast('Seuls les .py sont acceptés', false); return; }
  const input = document.getElementById('file-input');
  const dt = new DataTransfer(); dt.items.add(f); input.files = dt.files;
  showTab('scripts');
  document.getElementById('upload-form').scrollIntoView({ behavior: 'smooth' });
  toast(`Fichier « ${f.name} » prêt — clique sur Uploader`);
});

// ---------- détail script ----------
async function openDetail(id) {
  currentDetail = id;
  document.getElementById('detail-panel').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  await loadDetail(id);
  clearInterval(detailTimer);
  detailTimer = setInterval(() => loadDetailLogs(id, true), 3000);
}
function closeDetail() {
  currentDetail = null;
  clearInterval(detailTimer);
  document.getElementById('detail-panel').classList.add('hidden');
  document.body.style.overflow = '';
  refreshStatus();
}
async function loadDetail(id) {
  try {
    const d = await api(`/api/scripts/${id}`);
    const s = d.script;
    document.getElementById('detail-name').textContent = s.name;
    document.getElementById('detail-meta').textContent =
      `${s.filename} · ${s.status}${s.pid ? ' · PID ' + s.pid : ''} · maj ${fmtDate(s.updated_at)}`;
    document.getElementById('edit-name').value = s.name || '';
    document.getElementById('edit-args').value = s.args || '';
    document.getElementById('edit-env').value = Object.entries(s.env || {}).map(([k, v]) => `${k}=${v}`).join('\n');
    document.getElementById('edit-req').value = s.requirements || '';
    document.getElementById('edit-code').value = s.code_preview || '';
    document.getElementById('download-logs').href = `/api/scripts/${id}/logs/download`;
    document.getElementById('detail-actions').innerHTML =
      s.status === 'running'
        ? `<button onclick="stopScript('${id}').then(()=>loadDetail('${id}'))" class="btn-ghost text-sm">⏹ Stop</button>
           <button onclick="restartScript('${id}').then(()=>loadDetail('${id}'))" class="btn-ghost text-sm">🔁 Restart</button>`
        : `<button onclick="runScript('${id}').then(()=>loadDetail('${id}'))" class="btn-primary text-sm !py-2">▶ Démarrer</button>`;
    loadDetailLogs(id);
  } catch (e) { toast(e.message, false); }
}
async function loadDetailLogs(id, silent = false) {
  try {
    const d = await api(`/api/scripts/${id}/logs?tail=300`);
    const box = document.getElementById('detail-logs');
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
    box.textContent = d.logs || '(vide)';
    if (nearBottom || !silent) box.scrollTop = box.scrollHeight;
  } catch (e) { if (!silent) toast(e.message, false); }
}
async function saveDetail() {
  if (!currentDetail) return;
  const env = {};
  for (const line of document.getElementById('edit-env').value.split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#') || !t.includes('=')) continue;
    const i = t.indexOf('=');
    env[t.slice(0, i).trim()] = t.slice(i + 1).trim();
  }
  try {
    await api(`/api/scripts/${currentDetail}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: document.getElementById('edit-name').value,
        args: document.getElementById('edit-args').value,
        env,
        requirements: document.getElementById('edit-req').value,
        code: document.getElementById('edit-code').value,
      })
    });
    toast('Enregistré 💾');
    loadDetail(currentDetail);
  } catch (e) { toast(e.message, false); }
}
async function clearLogs(id) {
  try { await api(`/api/scripts/${id}/logs/clear`, { method: 'POST' }); loadDetailLogs(id); } catch (e) { toast(e.message, false); }
}

// ---------- webhook ----------
function renderWebhookStats(wh) {
  const stats = wh.stats || {};
  document.getElementById('stat-total').textContent = stats.total ?? 0;
  document.getElementById('stat-24h').textContent = stats.last_24h ?? 0;
  document.getElementById('stat-last').textContent = stats.last_call ? fmtDate(stats.last_call.time) : 'jamais';
  const badge = document.getElementById('webhook-badge');
  if (stats.total > 0) { badge.textContent = stats.total; badge.classList.remove('hidden'); }
  else badge.classList.add('hidden');
  if (wh.url) document.getElementById('webhook-url').textContent = wh.url;
}
function fillLinkedSelect(scripts, selected) {
  const sel = document.getElementById('wh-linked');
  const cur = selected || sel.value;
  sel.innerHTML = '<option value="">— Aucun —</option>' +
    scripts.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
  if (cur) sel.value = cur;
}
async function loadWebhookCfg() {
  try {
    const d = await api('/api/webhooks/config');
    document.getElementById('webhook-url').textContent = d.config.url;
    document.getElementById('wh-autorun').checked = !!d.config.auto_run;
    document.getElementById('wh-restart').checked = d.config.restart_if_running !== false;
    fillLinkedSelect(allScripts, d.config.linked_script_id);
    const curl = document.getElementById('help-curl');
    if (curl) curl.textContent = `curl -X POST "${d.config.url}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"signal":"BUY","prix":123.4}'`;
  } catch (e) { console.error(e); }
}
async function saveWebhookCfg() {
  try {
    await api('/api/webhooks/config', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        linked_script_id: document.getElementById('wh-linked').value || null,
        auto_run: document.getElementById('wh-autorun').checked,
        restart_if_running: document.getElementById('wh-restart').checked,
      })
    });
    toast('Config webhook enregistrée');
    refreshStatus();
  } catch (e) { toast(e.message, false); }
}
function copyWebhook() {
  navigator.clipboard.writeText(document.getElementById('webhook-url').textContent.trim())
    .then(() => toast('URL copiée 📋')).catch(() => toast('Copie impossible', false));
}
async function regenWebhook() {
  if (!confirm('Générer une nouvelle URL ? L\'ancienne sera immédiatement invalidée (pense à mettre à jour ton service externe).')) return;
  try {
    const d = await api('/api/webhooks/regenerate', { method: 'POST' });
    document.getElementById('webhook-url').textContent = d.url;
    toast('Nouvelle URL générée 🔄');
  } catch (e) { toast(e.message, false); }
}
async function testWebhook() {
  toast('Envoi d\'un appel de test…');
  try {
    const d = await api('/api/webhooks/test', { method: 'POST', allowFail: true });
    if (d.ok) { toast('Appel de test reçu ✔'); loadWebhookLogs(); refreshStatus(); }
    else toast(d.error || 'Échec du test', false);
  } catch (e) { toast(e.message, false); }
}
function prettyBody(entry) {
  if (entry.body_is_json) {
    try { return esc(JSON.stringify(entry.body, null, 2)); }
    catch { return esc(String(entry.body)); }
  }
  const t = String(entry.body || '');
  return esc(t.slice(0, 3000)) || '<span class="text-slate-500">(corps vide)</span>';
}
async function loadWebhookLogs() {
  try {
    const d = await api('/api/webhooks/logs?limit=100');
    renderWebhookStats({ stats: d.stats, url: document.getElementById('webhook-url').textContent });
    const list = document.getElementById('webhook-list');
    document.getElementById('webhook-empty').classList.toggle('hidden', d.logs.length > 0);
    list.innerHTML = d.logs.map(e => {
      const trig = e.triggered_script
        ? (e.triggered_script.error ? `<div class="text-red-300 text-xs mt-1">⚡ ${esc(e.triggered_script.error)}</div>`
           : `<div class="text-amber-300 text-xs mt-1">⚡ Script « ${esc(e.triggered_script.name)} » → ${esc(e.triggered_script.action)} ${e.triggered_script.ok ? '✔' : '❌'}</div>`)
        : '';
      const q = e.query && Object.keys(e.query).length ? `<div class="text-xs text-slate-400 mt-1">🔗 Query : ${esc(JSON.stringify(e.query))}</div>` : '';
      return `
      <div class="card p-4">
        <div class="flex flex-wrap items-center gap-2">
          <span class="pill ${e.method === 'POST' ? 'pill-running' : 'pill-stopped'}">${esc(e.method)}</span>
          <span class="font-bold text-sm">🕒 ${fmtDate(e.time)}</span>
          <span class="text-xs text-slate-400">· 🌐 ${esc(e.ip)}</span>
          <span class="text-xs text-slate-500">· ${e.body_size} octets · ${e.duration_ms ?? '?'} ms</span>
          <button class="btn-ghost text-xs ml-auto" onclick="this.parentElement.nextElementSibling.classList.toggle('hidden')">🔍 Détails</button>
        </div>
        ${q}${trig}
        <div class="hidden mt-3">
          <div class="text-xs text-slate-400 mb-1">Contenu ${e.body_is_json ? '(JSON)' : '(texte)'} :</div>
          <pre class="log-box wh-body !text-sky-200">${prettyBody(e)}</pre>
          <div class="text-xs text-slate-400 mt-2 mb-1">Headers :</div>
          <pre class="log-box wh-body !text-slate-300 !max-h-[160px]">${esc(JSON.stringify(e.headers || {}, null, 2))}</pre>
          <div class="text-xs text-slate-500 mt-1">User-Agent : ${esc(e.user_agent || '–')} · Content-Type : ${esc(e.content_type || '–')} · event ${esc(e.id)}</div>
        </div>
      </div>`;
    }).join('');
  } catch (e) { console.error(e); }
}
async function clearWebhookLogs() {
  if (!confirm('Effacer tout l\'historique des appels webhook ?')) return;
  try { await api('/api/webhooks/logs', { method: 'DELETE' }); loadWebhookLogs(); refreshStatus(); } catch (e) { toast(e.message, false); }
}

// ---------- settings ----------
function openSettings() { document.getElementById('settings-modal').classList.remove('hidden'); }
function closeSettings() { document.getElementById('settings-modal').classList.add('hidden'); }
async function changeCode() {
  const msg = document.getElementById('set-msg');
  try {
    await api('/api/change-code', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current: document.getElementById('set-current').value, new: document.getElementById('set-new').value })
    });
    msg.innerHTML = '<span class="text-emerald-300">✅ Code mis à jour</span>';
    setTimeout(closeSettings, 1200);
  } catch (e) { msg.innerHTML = `<span class="text-red-300">❌ ${esc(e.message)}</span>`; }
}

// ---------- init ----------
refreshStatus();
setInterval(() => { if (!currentDetail) refreshStatus(); }, 10000);
setInterval(() => {
  if (!document.getElementById('tab-webhook').classList.contains('hidden')) loadWebhookLogs();
}, 8000);
document.addEventListener('keydown', e => { if (e.key === 'Escape') { closeDetail(); closeSettings(); } });
